import os

# Pin BLAS threads before torch imports heavy backends.
# VPS spec: 2 vCPU, 4GB RAM. One thread per vCPU avoids oversubscription.
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("MKL_NUM_THREADS", "2")

import h5py
import math
import numpy as np
import resource
import time
import torch

from config import device, DATA_PATHS
from models import MFPino

CPU_THREADS = int(os.environ.get("HYSEA_CPU_THREADS", "2"))
torch.set_num_threads(CPU_THREADS)
try:
    torch.set_num_interop_threads(2)
except RuntimeError:
    pass  # already initialized

# ── Constants ────────────────────────────────────────────────────────────────
NLAT       = 349
NLON       = 780
BATHY_MEAN = 1762.6815185546875
BATHY_STD  = 2185.7099609375
AT_MAX     = 14399.7294921875  # Updated to real value

FEAT_MEAN = np.array([111.91385650634766, -6.086950302124023, -20971.849609375, 275.5798034667969, 12.05357837677002, 90.0, 25.500001907348633, 674.9999389648438, 125.00000762939453], dtype=np.float32)
FEAT_STD  = np.array([11.87042236328125, 4.469435214996338, 12123.623046875, 52.69306564331055, 6.645068168640137, 11.547004699707031, 14.145081520080566, 360.84393310546875, 54.84827423095703], dtype=np.float32)

COMMON_KWARGS = dict(
    nlat=NLAT, nlon=NLON,
    latent_channels=32,
    num_fno_layers=3,
    num_fno_modes=8,
    decoder_layer_size=128,
    out_channels=1,
    H_raw=None, grid_info=None,
)

G = 9.81
EARTH_RADIUS_M = 6371000.0
ASSUMED_BEACH_DEPTH = 1.0
MAX_WARNING_RUNUP_M = 35.0

# ── Module-level caches (load once per process) ───────────────────────────────
# On a 2vCPU/4GB VPS, reloading the HDF5 bathymetry + rebuilding MFPino on every
# call wastes RAM bandwidth and ~hundreds of ms of torch runtime init. Cache them.
_cache: dict = {}


def _load_grids() -> tuple[np.ndarray, np.ndarray, np.ndarray, torch.Tensor]:
    if "grids" not in _cache:
        with h5py.File(DATA_PATHS["lf"], "r") as hf:
            bathy_np = hf["bathymetry"][:].astype(np.float32)
            lats_np = hf["lat"][:].astype(np.float32)
            lons_np = hf["lon"][:].astype(np.float32)
        bathy_t = ((torch.tensor(bathy_np) - BATHY_MEAN) / (BATHY_STD + 1e-8)).to(device)
        _cache["grids"] = (bathy_np, lats_np, lons_np, bathy_t)
    return _cache["grids"]


def _load_model(task: str, weights_path: str) -> MFPino:
    key = f"model:{task}"
    if key not in _cache:
        model = MFPino(**COMMON_KWARGS, task=task).to(device)
        model.load_state_dict(
            torch.load(weights_path, map_location=device, weights_only=True),
            strict=False,
        )
        model.eval()
        _cache[key] = model
    return _cache[key]


def clear_cache() -> None:
    _cache.clear()


# ── Helper for Nearest Offshore Point ─────────────────────────────────────────

def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in meters."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2.0) ** 2
    a += math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    return EARTH_RADIUS_M * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def estimate_moment_magnitude(eq_params):
    """
    Estimate Mw from rupture geometry and slip. This keeps physics guardrails
    active for historical-like scenarios even when magnitude is not an input.
    """
    slip_m = max(float(eq_params[6]), 0.0)
    length_m = max(float(eq_params[7]) * 1000.0, 1.0)
    width_m = max(float(eq_params[8]) * 1000.0, 1.0)
    shear_modulus_pa = 30e9
    moment_nm = shear_modulus_pa * length_m * width_m * max(slip_m, 0.1)
    return (2.0 / 3.0) * (math.log10(moment_nm) - 9.1)


def estimate_source_wave_floor(eq_params):
    """
    Conservative lower bound for dangerous shallow megathrust events.
    It is not a replacement for the neural model; it prevents false-tiny
    outputs for Pangandaran/Mentawai-like rupture settings.
    """
    depth_km = abs(float(eq_params[2])) / 1000.0
    dip_rad = math.radians(float(eq_params[4]))
    rake_rad = math.radians(float(eq_params[5]))
    slip_m = max(float(eq_params[6]), 0.0)
    rupture_len_km = max(float(eq_params[7]), 1.0)
    rupture_width_km = max(float(eq_params[8]), 1.0)
    mw = estimate_moment_magnitude(eq_params)

    thrust_component = slip_m * max(math.sin(rake_rad), 0.0)
    vertical_slip = thrust_component * max(math.sin(dip_rad), 0.0)
    tsunami_slip = max(vertical_slip, 0.65 * thrust_component)
    saturated_slip = 6.0 * (1.0 - math.exp(-tsunami_slip / 6.0))
    rupture_scale = math.sqrt((rupture_len_km * rupture_width_km) / (200.0 * 100.0))
    rupture_scale = min(rupture_scale, 2.2)
    depth_factor = np.clip((45.0 - depth_km) / 30.0, 0.25, 1.15)
    mw_factor = np.clip(0.60 + (mw - 7.4) * 0.55, 0.35, 1.75)

    return max(0.0, saturated_slip * rupture_scale * depth_factor * mw_factor)


def find_peak_offshore_near_beach(lat, lon, lat_r, lon_r, bathy_raw, pred_mh, min_depth=100.0, search_radius=25):
    """
    Finds the (lat_idx, lon_idx) of the cell with the HIGHEST wave energy near the beach 
    that also meets the offshore depth requirement (to avoid grid boundary masking).
    Vectorized for CPU: replaces a ~6k-iteration Python loop with numpy ops.
    """
    lat_idx = int(np.abs(lat_r - lat).argmin())
    lon_idx = int(np.abs(lon_r - lon).argmin())
    i0, i1 = max(0, lat_idx - search_radius), min(NLAT, lat_idx + search_radius + 1)
    j0, j1 = max(0, lon_idx - search_radius), min(NLON, lon_idx + search_radius + 1)

    bathy_window = bathy_raw[i0:i1, j0:j1]
    mh_window = np.nan_to_num(pred_mh[i0:i1, j0:j1], nan=0.0, posinf=0.0, neginf=0.0)
    deep_mask = bathy_window <= -min_depth

    if not np.any(deep_mask):
        return lat_idx, lon_idx

    masked = np.where(deep_mask, mh_window, -np.inf)
    flat_idx = int(np.argmax(masked))
    bi, bj = np.unravel_index(flat_idx, masked.shape)
    return int(bi) + i0, int(bj) + j0


def summarize_offshore_sector(lat, lon, lat_r, lon_r, bathy_raw, pred_mh, pred_arr, min_depth=50.0, search_radius=35):
    """
    Summarize nearby offshore cells using robust statistics instead of a single
    grid point. Nearshore tsunami predictions are often noisy at land masks.
    """
    lat_idx = np.abs(lat_r - lat).argmin()
    lon_idx = np.abs(lon_r - lon).argmin()
    i0, i1 = max(0, lat_idx - search_radius), min(NLAT, lat_idx + search_radius + 1)
    j0, j1 = max(0, lon_idx - search_radius), min(NLON, lon_idx + search_radius + 1)

    bathy_window = bathy_raw[i0:i1, j0:j1]
    mh_window = np.nan_to_num(pred_mh[i0:i1, j0:j1], nan=0.0, posinf=0.0, neginf=0.0)
    at_window = np.nan_to_num(pred_arr[i0:i1, j0:j1], nan=0.0, posinf=0.0, neginf=0.0)
    water_mask = bathy_window <= -min_depth

    if not np.any(water_mask):
        water_mask = bathy_window < 0.0
    if not np.any(water_mask):
        return lat_idx, lon_idx, float(pred_mh[lat_idx, lon_idx]), float(pred_arr[lat_idx, lon_idx])

    heights = np.clip(mh_window[water_mask], 0.0, None)
    arrivals = at_window[water_mask]
    valid_arrivals = arrivals[arrivals > 60.0]

    robust_height = float(np.percentile(heights, 95)) if heights.size else 0.0
    peak_local = np.argwhere((mh_window == heights.max()) & water_mask)[0]
    peak_i, peak_j = int(peak_local[0] + i0), int(peak_local[1] + j0)
    robust_arrival = float(np.percentile(valid_arrivals, 10)) if valid_arrivals.size else 0.0

    return peak_i, peak_j, robust_height, robust_arrival

# ── Inference Pipeline ────────────────────────────────────────────────────────

def run_local_inference(eq_params, target_lat, target_lon):
    """
    eq_params: [lon, lat, depth_m, strike, dip, rake, slip_m, rup_len, rup_width]
    target_lat: latitude of the coastal city
    target_lon: longitude of the coastal city

    Optimized for a 2vCPU / 4GB VPS:
      - Bathymetry / lat / lon / bathy_t loaded once and cached per process.
      - MFPino weights loaded once and cached per process.
      - torch.inference_mode() avoids no_grad bookkeeping overhead.
    """
    # 1. Prepare Inputs
    x = ((eq_params - FEAT_MEAN) / (FEAT_STD + 1e-8))
    x_t = torch.tensor(x, dtype=torch.float32).unsqueeze(0).to(device)

    # Cached grids & bathy (no per-call HDF5 I/O)
    bathy_np, lats_np, lons_np, bathy_t = _load_grids()

    # 2. Run Models (cached, inference_mode)
    with torch.inference_mode():
        model_arr = _load_model("arrival_times", "./models/pino_arrival.pt")
        pred_arr = model_arr(x_t, bathy_t, fidelity="hf").squeeze().cpu().numpy() * AT_MAX
        pred_arr = np.clip(pred_arr, 0.0, None)

        model_mh = _load_model("max_height", "./models/pino_maxheight.pt")
        pred_mh = np.expm1(model_mh(x_t, bathy_t, fidelity="hf").squeeze().cpu().numpy())
        pred_mh = np.clip(np.nan_to_num(pred_mh, nan=0.0, posinf=0.0, neginf=0.0), 0.0, None)

    # 3. Extract Local Variables
    # Use a robust coastal-sector summary so one masked/quiet cell cannot erase
    # a dangerous local tsunami signal.
    lat_i, lon_i, sector_height, sector_eta_sec = summarize_offshore_sector(
        target_lat, target_lon, lats_np, lons_np, bathy_np, pred_mh, pred_arr
    )

    local_eta_sec = sector_eta_sec if sector_eta_sec > 0.0 else pred_arr[lat_i, lon_i]
    local_offshore_height = max(float(pred_mh[lat_i, lon_i]), sector_height)
    local_offshore_depth = abs(bathy_np[lat_i, lon_i]) # depth must be positive for Green's Law

    # 4. Coastal Amplification and ETA Correction
    # ── 1. ETA Physics Override ────────────────────────────────────────────────
    # Travel time calculation (distance / phase velocity)
    # Haversine distance from epicenter to the target coast. The selected
    # offshore cell may be a nearby energetic sector point, not the coastline.
    dist_m = haversine_m(eq_params[1], eq_params[0], target_lat, target_lon)
    
    # Wave speed using shallow water gravity wave phase velocity: c = sqrt(g*h)
    # Earthquake depth is hypocentral, not water depth, so cap the travel-depth
    # proxy to avoid 2-3 hour ETA artifacts near a local source.
    avg_dist_depth = np.clip((min(abs(float(eq_params[2])), 900.0) + local_offshore_depth) / 2.0, 120.0, 1200.0)
    wave_velocity = math.sqrt(G * avg_dist_depth)
    kinematic_eta_sec = dist_m / wave_velocity
    
    # Keep the model when plausible, but stop zeros and multi-hour outliers from
    # dominating local warning output.
    if local_eta_sec < 60.0:
        local_eta_sec = kinematic_eta_sec
    else:
        local_eta_sec = 0.45 * local_eta_sec + 0.55 * kinematic_eta_sec
        local_eta_sec = float(np.clip(local_eta_sec, 0.90 * kinematic_eta_sec, 1.75 * kinematic_eta_sec))

    # ── 2. Runup Physics Override ──────────────────────────────────────────────
    # Base height via Mansinha and Smylie (1971): Vertical seafloor displacement
    # Vertical displacement scales predominantly with slip * sin(dip)
    slip_m, dip_deg = eq_params[6], eq_params[4]
    vertical_displacement = slip_m * math.sin(math.radians(dip_deg))
    
    source_wave_floor = estimate_source_wave_floor(eq_params)
    local_offshore_height = max(local_offshore_height, 0.55 * source_wave_floor)

    if local_offshore_height < 0.01 and source_wave_floor < 0.05:
        runup_height = 0.0
    else:
        # Combined Green's Law shoreline amplification factoring in seabed uplift dynamics
        green_amplification = (local_offshore_depth / ASSUMED_BEACH_DEPTH) ** 0.25
        green_amplification = float(np.clip(green_amplification, 1.0, 4.2))
        runup_height = local_offshore_height * green_amplification
        runup_height = max(runup_height, 5.0 * source_wave_floor)
        runup_height = min(runup_height, MAX_WARNING_RUNUP_M)
        
    return {
        "eta_minutes": local_eta_sec / 60.0,
        "offshore_height_m": local_offshore_height,
        "shore_runup_height_m": runup_height,
        "offshore_lat": lats_np[lat_i],
        "offshore_lon": lons_np[lon_i],
        "offshore_depth_m": local_offshore_depth
    }

def find_nearest_beach(eq_lat, eq_lon, lat_r, lon_r, bathy_raw):
    """Finds the nearest shallow water cell (proxy for the beach) to the epicenter."""
    lon_grid, lat_grid = np.meshgrid(lon_r, lat_r)
    dist_sq = (lon_grid - eq_lon)**2 + (lat_grid - eq_lat)**2
    
    # Proxy for beach: shallow water between 0 and -30m
    beach_mask = (bathy_raw < 0) & (bathy_raw >= -30)
    
    # Fallback to any water if no shallow water is found
    if not np.any(beach_mask):
        beach_mask = bathy_raw < 0
        
    inf_array = np.full_like(dist_sq, np.inf)
    valid_dists = np.where(beach_mask, dist_sq, inf_array)
    
    min_idx = np.unravel_index(np.argmin(valid_dists), valid_dists.shape)
    return lat_r[min_idx[0]], lon_r[min_idx[1]]

def _peak_rss_mb() -> float:
    """Peak resident set size in MB (Linux: ru_maxrss is in KB)."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


if __name__ == "__main__":
    # ── VPS spec: 2 vCPU, 4GB RAM ──────────────────────────────────────────────
    RAM_BUDGET_MB = 4096.0
    print(f"Config: {CPU_THREADS} torch threads on device={device}")

    # Test with Pangandaran 2006 parameters
    raw_eq = np.array([
    97.15,     # lon [°E]
    2.090,      # lat [°N]
    30000.0,    # depth_m [m]
    331.0,      # strike [°]
    12.0,       # dip [°]
    110.0,      # rake [°]
    6.0,        # slip_m [m]
    300.0,      # rupture_length_km [km]
    150.0,      # rupture_width_km [km]
    ], dtype=np.float32)

    eq_lon, eq_lat = raw_eq[0], raw_eq[1]

    # Reuse cached bathy (loaded once, shared with run_local_inference)
    bathy_arr, lats_arr, lons_arr, _ = _load_grids()
    beach_lat, beach_lon = find_nearest_beach(eq_lat, eq_lon, lats_arr, lons_arr, bathy_arr)

    print(f"\nEpicenter: (Lat: {eq_lat:.4f}, Lon: {eq_lon:.4f})")
    print(f"Nearest Beach Found: (Lat: {beach_lat:.4f}, Lon: {beach_lon:.4f})")

    # Warm-up (first call loads weights into cache; not representative of steady state)
    _ = run_local_inference(raw_eq, beach_lat, beach_lon)

    # Timed run (models + grids now hot in cache)
    t0 = time.perf_counter()
    res = run_local_inference(raw_eq, beach_lat, beach_lon)
    dt = time.perf_counter() - t0

    print("\n=== Prediction at Nearest Beach ===")
    print(f"Offshore reference cell : ({res['offshore_lat']:.4f}, {res['offshore_lon']:.4f})")
    print(f"Offshore depth          : {res['offshore_depth_m']:.1f} m")
    print(f"Offshore wave height    : {res['offshore_height_m']:.2f} m")
    print(f"ETA at coast            : {res['eta_minutes']:.1f} min")
    print(f"Estimated Shore Runup   : {res['shore_runup_height_m']:.2f} m")

    # ── Spec report ────────────────────────────────────────────────────────────
    peak = _peak_rss_mb()
    print("\n=== VPS Fit Report (2 vCPU / 4 GB) ===")
    print(f"Torch threads           : {torch.get_num_threads()}")
    print(f"Steady-state latency    : {dt*1000:.0f} ms / prediction")
    print(f"Peak RSS                : {peak:.0f} MB / {RAM_BUDGET_MB:.0f} MB budget")
    print(f"Headroom                : {RAM_BUDGET_MB - peak:.0f} MB ({(peak/RAM_BUDGET_MB)*100:.0f}% used)")
    print(f"Verdict                 : {'FITS' if peak < RAM_BUDGET_MB else 'OVER BUDGET'}")
