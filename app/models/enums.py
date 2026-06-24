from __future__ import annotations

import enum


class TsunamiPotential(str, enum.Enum):
    NO_THREAT = "NO_THREAT"
    THREAT = "THREAT"
