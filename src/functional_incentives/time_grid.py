"""Shared output sampling for analytical and numerical trajectories."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


def make_output_times(
    final_time_s: float, time_step_s: float
) -> NDArray[np.float64]:
    """Sample from zero to the exact endpoint, with a shorter last interval if needed.

    Both simulators use this grid so samples can be compared at identical
    timestamps. Approximate equality must not replace the requested endpoint:
    even a short final interval can contain the disturbance.
    """

    if not np.isfinite(final_time_s) or final_time_s <= 0.0:
        raise ValueError("final_time_s must be finite and positive")
    if not np.isfinite(time_step_s) or time_step_s <= 0.0:
        raise ValueError("time_step_s must be finite and positive")

    time_s = np.arange(0.0, final_time_s, time_step_s, dtype=float)
    # Floating-point arange may include an endpoint equal to or beyond stop.
    # Keep only earlier samples, then append the exact endpoint once.
    return np.append(time_s[time_s < final_time_s], final_time_s)
