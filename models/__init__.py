from .fno import MFFno
from .pino import MFPino
from .swe_residuals import swe_spatial_loss, eikonal_loss

__all__ = ["MFFno", "MFPino", "swe_spatial_loss", "eikonal_loss"]
