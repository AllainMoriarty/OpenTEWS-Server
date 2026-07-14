from __future__ import annotations

import datetime
import hashlib
import json
import logging
import math

import h5py
import numpy as np
import torch
from redis.asyncio import Redis

from app.core.config import get_settings
from app.models.earthquake import Earthquake
from app.models.prediction import Prediction
from app.models.enums import TsunamiPotential
from config import DATA_PATHS, device
from models.pino import MFPino


logger = logging.getLogger(__name__)

# Constants identical to infer_*.py scripts
NLAT = 349
NLON = 780

BATHY_MEAN = 1762.6815185546875
BATHY_STD = 2185.7099609375
AT_MAX = 14399.7294921875

FEAT_MEAN = np.array(
    [
        111.91385650634766,
        -6.086950302124023,
        -20971.849609375,
        275.5798034667969,
        12.05357837677002,
        90.0,
        25.500001907348633,
        674.9999389648438,
        125.00000762939453,
    ],
    dtype=np.float32,
)
FEAT_STD = np.array(
    [
        11.87042236328125,
        4.469435214996338,
        12123.623046875,
        52.69306564331055,
        6.645068168640137,
        11.547004699707031,
        14.145081520080566,
        360.84393310546875,
        54.84827423095703,
    ],
    dtype=np.float32,
)

G = 9.81
EARTH_RADIUS_M = 6371000.0
ASSUMED_BEACH_DEPTH = 1.0
MAX_WARNING_RUNUP_M = 35.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2.0) ** 2
    a += math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    return EARTH_RADIUS_M * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def estimate_moment_magnitude(eq_params: np.ndarray) -> float:
    slip_m = max(float(eq_params[6]), 0.0)
    length_m = max(float(eq_params[7]) * 1000.0, 1.0)
    width_m = max(float(eq_params[8]) * 1000.0, 1.0)
    shear_modulus_pa = 30e9
    moment_nm = shear_modulus_pa * length_m * width_m * max(slip_m, 0.1)
    return (2.0 / 3.0) * (math.log10(moment_nm) - 9.1)


def estimate_source_wave_floor(eq_params: np.ndarray) -> float:
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


def summarize_offshore_sector(
    lat: float,
    lon: float,
    lat_r: np.ndarray,
    lon_r: np.ndarray,
    bathy_raw: np.ndarray,
    pred_mh: np.ndarray,
    pred_arr: np.ndarray,
    min_depth: float = 50.0,
    search_radius: int = 35,
) -> tuple[int, int, float, float]:
    lat_idx = int(np.abs(lat_r - lat).argmin())
    lon_idx = int(np.abs(lon_r - lon).argmin())
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


def find_nearest_beach(
    eq_lat: float,
    eq_lon: float,
    lat_r: np.ndarray,
    lon_r: np.ndarray,
    bathy_raw: np.ndarray,
) -> tuple[float, float]:
    lon_grid, lat_grid = np.meshgrid(lon_r, lat_r)
    dist_sq = (lon_grid - eq_lon) ** 2 + (lat_grid - eq_lat) ** 2
    beach_mask = (bathy_raw < 0) & (bathy_raw >= -30)

    if not np.any(beach_mask):
        beach_mask = bathy_raw < 0

    valid_dists = np.where(beach_mask, dist_sq, np.inf)
    min_idx = np.unravel_index(np.argmin(valid_dists), valid_dists.shape)
    return float(lat_r[min_idx[0]]), float(lon_r[min_idx[1]])


class PredictionService:
    def __init__(self, redis: Redis | None = None):
        self._redis = redis
        self._cache_ttl = get_settings().PREDICTION_CACHE_TTL_SECONDS
        logger.info("Initializing PredictionService models on %s...", device)

        with h5py.File(DATA_PATHS["lf"], "r") as hf:
            self.bathy_np = hf["bathymetry"][:].astype(np.float32)
            self.lats_np = hf["lat"][:].astype(np.float32)
            self.lons_np = hf["lon"][:].astype(np.float32)

        self.bathy_t = ((torch.tensor(self.bathy_np) - BATHY_MEAN) / (BATHY_STD + 1e-8)).to(device)

        # 1. Load Max Height Model
        kwargs_mh = dict(
            nlat=NLAT,
            nlon=NLON,
            latent_channels=32,
            num_fno_layers=3,
            num_fno_modes=8,
            decoder_layer_size=128,
            out_channels=1,
            task="max_height",
            H_raw=None,
            grid_info=None,
        )
        self.model_mh = MFPino(**kwargs_mh).to(device)
        self.model_mh.load_state_dict(
            torch.load("./models/pino_maxheight.pt", map_location=device, weights_only=True),
            strict=False,
        )
        self.model_mh.eval()

        # 2. Load Arrival Times Model
        kwargs_at = dict(
            nlat=NLAT,
            nlon=NLON,
            latent_channels=32,
            num_fno_layers=3,
            num_fno_modes=8,
            decoder_layer_size=128,
            out_channels=1,
            task="arrival_times",
            H_raw=None,
            grid_info=None,
        )
        self.model_at = MFPino(**kwargs_at).to(device)
        self.model_at.load_state_dict(
            torch.load("./models/pino_arrival.pt", map_location=device, weights_only=True),
            strict=False,
        )
        self.model_at.eval()

        logger.info("PredictionService models loaded successfully.")

    def _determine_tsunami_potential(self, magnitude: float, depth_km: float) -> TsunamiPotential:
        if magnitude >= 7.5 and depth_km <= 30.0:
            return TsunamiPotential.THREAT
        else:
            return TsunamiPotential.NO_THREAT

    def _target_coast_for_earthquake(self, earthquake: Earthquake) -> tuple[float, float]:
        observation_point = earthquake.__dict__.get("observation_point")
        if observation_point is not None:
            return float(observation_point.latitude), float(observation_point.longitude)

        return find_nearest_beach(
            float(earthquake.latitude),
            float(earthquake.longitude),
            self.lats_np,
            self.lons_np,
            self.bathy_np,
        )

    def _run_local_inference(
        self,
        eq_params: np.ndarray,
        target_lat: float,
        target_lon: float,
        x_t: torch.Tensor,
    ) -> dict[str, float]:
        with torch.no_grad():
            pred_arr = (
                self.model_at(x_t, self.bathy_t, fidelity="hf").squeeze().cpu().numpy() * AT_MAX
            )
            pred_arr = np.clip(pred_arr, 0.0, None)

            pred_mh = np.expm1(
                self.model_mh(x_t, self.bathy_t, fidelity="hf").squeeze().cpu().numpy()
            )
            pred_mh = np.clip(np.nan_to_num(pred_mh, nan=0.0, posinf=0.0, neginf=0.0), 0.0, None)

        lat_i, lon_i, sector_height, sector_eta_sec = summarize_offshore_sector(
            target_lat,
            target_lon,
            self.lats_np,
            self.lons_np,
            self.bathy_np,
            pred_mh,
            pred_arr,
        )

        local_eta_sec = sector_eta_sec if sector_eta_sec > 0.0 else float(pred_arr[lat_i, lon_i])
        local_offshore_height = max(float(pred_mh[lat_i, lon_i]), sector_height)
        local_offshore_depth = max(abs(float(self.bathy_np[lat_i, lon_i])), ASSUMED_BEACH_DEPTH)

        dist_m = haversine_m(float(eq_params[1]), float(eq_params[0]), target_lat, target_lon)
        avg_dist_depth = np.clip(
            (min(abs(float(eq_params[2])), 900.0) + local_offshore_depth) / 2.0,
            120.0,
            1200.0,
        )
        kinematic_eta_sec = dist_m / math.sqrt(G * float(avg_dist_depth))

        if local_eta_sec < 60.0:
            local_eta_sec = kinematic_eta_sec
        else:
            local_eta_sec = 0.45 * local_eta_sec + 0.55 * kinematic_eta_sec
            local_eta_sec = float(
                np.clip(local_eta_sec, 0.90 * kinematic_eta_sec, 1.75 * kinematic_eta_sec)
            )

        source_wave_floor = estimate_source_wave_floor(eq_params)
        local_offshore_height = max(local_offshore_height, 0.55 * source_wave_floor)

        if local_offshore_height < 0.01 and source_wave_floor < 0.05:
            runup_height = 0.0
        else:
            green_amplification = (local_offshore_depth / ASSUMED_BEACH_DEPTH) ** 0.25
            green_amplification = float(np.clip(green_amplification, 1.0, 4.2))
            runup_height = local_offshore_height * green_amplification
            runup_height = max(runup_height, 5.0 * source_wave_floor)
            runup_height = min(runup_height, MAX_WARNING_RUNUP_M)

        return {
            "eta_minutes": float(local_eta_sec / 60.0),
            "offshore_height_m": float(local_offshore_height),
            "shore_runup_height_m": float(runup_height),
            "offshore_lat": float(self.lats_np[lat_i]),
            "offshore_lon": float(self.lons_np[lon_i]),
            "offshore_depth_m": float(local_offshore_depth),
        }

    def _cache_key(self, earthquake: Earthquake) -> str:
        parts = [
            earthquake.magnitude,
            earthquake.depth_km,
            earthquake.latitude,
            earthquake.longitude,
            earthquake.strike,
            earthquake.dip,
            earthquake.rake,
            earthquake.slip_m,
            earthquake.rupture_length_km,
            earthquake.rupture_width_km,
        ]
        payload = "|".join(f"{p:.6f}" for p in parts)
        digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()
        return f"prediction:v1:{digest}"

    async def _cache_get(self, earthquake: Earthquake) -> dict | None:
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(self._cache_key(earthquake))
        except Exception:
            logger.warning("Prediction cache read failed", exc_info=True)
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Prediction cache payload corrupt; ignoring")
            return None

    async def _cache_set(self, earthquake: Earthquake, prediction: Prediction) -> None:
        if self._redis is None:
            return
        payload = json.dumps(
            {
                "tsunami_potential": prediction.tsunami_potential.value,
                "max_height": prediction.max_height,
                "arrival_time": prediction.arrival_time.isoformat()
                if prediction.arrival_time
                else None,

            }
        )
        try:
            await self._redis.set(self._cache_key(earthquake), payload, ex=self._cache_ttl)
        except Exception:
            logger.warning("Prediction cache write failed", exc_info=True)

    async def predict_for_earthquake(self, earthquake: Earthquake) -> Prediction:
        cached = await self._cache_get(earthquake)
        if cached is not None:
            arrival_time = (
                datetime.datetime.fromisoformat(cached["arrival_time"])
                if cached.get("arrival_time")
                else None
            )
            return Prediction(
                earthquake_id=earthquake.id,
                tsunami_potential=TsunamiPotential(cached["tsunami_potential"]),
                max_height=cached["max_height"],
                arrival_time=arrival_time,
            )

        prediction = await self._compute_prediction(earthquake)
        await self._cache_set(earthquake, prediction)
        return prediction

    async def _compute_prediction(self, earthquake: Earthquake) -> Prediction:
        tsunami_potential = self._determine_tsunami_potential(
            earthquake.magnitude, earthquake.depth_km
        )
        if tsunami_potential is TsunamiPotential.NO_THREAT:
            return Prediction(
                earthquake_id=earthquake.id,
                tsunami_potential=tsunami_potential,
                max_height=None,
                arrival_time=None,
            )

        # Build features array exactly as expected
        raw_input = np.array(
            [
                earthquake.longitude,
                earthquake.latitude,
                earthquake.depth_km * 1000.0,  # convert to m
                earthquake.strike,
                earthquake.dip,
                earthquake.rake,
                earthquake.slip_m,
                earthquake.rupture_length_km,
                earthquake.rupture_width_km,
            ],
            dtype=np.float32,
        )

        x = (raw_input - FEAT_MEAN) / (FEAT_STD + 1e-8)
        x_t = torch.tensor(x, dtype=torch.float32).unsqueeze(0).to(device)

        target_lat, target_lon = self._target_coast_for_earthquake(earthquake)
        local_result = self._run_local_inference(raw_input, target_lat, target_lon, x_t)
        max_height_val = local_result["shore_runup_height_m"]
        arrival_time_val = earthquake.timestamp + datetime.timedelta(
            minutes=local_result["eta_minutes"]
        )

        prediction = Prediction(
            earthquake_id=earthquake.id,
            tsunami_potential=tsunami_potential,
            max_height=max_height_val,
            arrival_time=arrival_time_val,
        )
        return prediction
