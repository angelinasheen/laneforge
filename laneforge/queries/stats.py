"""Small statistics helpers (no database)."""
from __future__ import annotations

import math

Z_95 = 1.96


def wilson_interval(wins: int, games: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion; (0.0, 0.0) when games == 0."""
    if games <= 0:
        return (0.0, 0.0)
    if wins < 0 or wins > games:
        raise ValueError(f"wins must be in [0, games], got {wins}/{games}")
    p = wins / games
    z2 = z * z
    denom = 1 + z2 / games
    centre = (p + z2 / (2 * games)) / denom
    margin = z * math.sqrt(p * (1 - p) / games + z2 / (4 * games * games)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))
