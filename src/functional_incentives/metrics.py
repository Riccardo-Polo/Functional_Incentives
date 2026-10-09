"""Numerical regression, convergence and response-limit checks."""

from __future__ import annotations

import numpy as np

from .frequency import FrequencyTrajectory
from .simulation import AggregateFCRTrajectory


def summarize_fcr_trajectories(
    controlled: AggregateFCRTrajectory,
    numerical_no_fcr: AggregateFCRTrajectory,
    analytical_no_fcr: FrequencyTrajectory,
    refined: AggregateFCRTrajectory,
) -> dict[str, float]:
    for other in (numerical_no_fcr, analytical_no_fcr, refined):
        if not np.array_equal(controlled.time_s, other.time_s):
            raise ValueError("trajectory comparisons require identical sample times")
    if controlled.provider_ids != refined.provider_ids:
        raise ValueError("refinement must use the same provider identities")
    if not np.array_equal(controlled.provider_reserve_mw, refined.provider_reserve_mw):
        raise ValueError("refinement must reuse the same effective reserves")
    for trajectory in (controlled, numerical_no_fcr, analytical_no_fcr, refined):
        if not np.all(np.isfinite(trajectory.frequency_hz)):
            raise RuntimeError("non-finite frequency trajectory")
    if not np.all(np.isfinite(refined.provider_response_mw)):
        raise RuntimeError("non-finite refined provider response")
    limits = controlled.provider_reserve_mw[np.newaxis, :]
    for values in (controlled.provider_response_mw,
                   controlled.provider_requested_response_mw):
        if not np.all(np.isfinite(values)) or np.any(np.abs(values) > limits + 1e-8):
            raise RuntimeError("non-finite response or effective-reserve violation")
    analytical_error = float(np.max(np.abs(
        numerical_no_fcr.frequency_hz - analytical_no_fcr.frequency_hz)))
    frequency_error = float(np.max(np.abs(controlled.frequency_hz - refined.frequency_hz)))
    response_error = float(np.max(np.abs(
        controlled.provider_response_mw - refined.provider_response_mw), initial=0))
    if analytical_error > 1e-8 or frequency_error > 1e-6 or response_error > 1e-5:
        raise RuntimeError("analytical regression or solver refinement tolerance exceeded")
    return {
        "nadir_sampled_hz": float(np.min(controlled.frequency_hz)),
        "final_frequency_hz": float(controlled.frequency_hz[-1]),
        "final_no_fcr_frequency_hz": float(numerical_no_fcr.frequency_hz[-1]),
        "analytical_no_fcr_max_error_hz": analytical_error,
        "refined_frequency_max_error_hz": frequency_error,
        "refined_provider_max_error_mw": response_error,
    }
