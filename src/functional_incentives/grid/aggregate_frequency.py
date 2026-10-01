"""First-order aggregate linear frequency dynamics.

Model sources
-------------
* ``references/PSDCO_Script_2026 (1).pdf``, Chapter 8, especially equations
  8.8--8.10 (inertia aggregation) and 8.24 (frequency dynamics).
* ``references/week3_eth.pdf``, slides 7 and 12--17.

State and sign convention
-------------------------
The only state is ``delta_frequency_hz = f - f_nominal``.  A positive
``power_deficit_mw`` means either an increase in load or a loss of generation,
so it makes frequency fall.  A positive ``power_response_mw`` counteracts that
deficit.  With MW, Hz and seconds, the model is

    M_f d(delta_f)/dt = -D delta_f - deficit + response,

where ``M_f = 2 H_eq S_sync / f_nominal`` in MW s/Hz and ``D`` is in MW/Hz.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class LinearFrequencyParameters:
    """Physical parameters of the aggregate linear model."""

    nominal_frequency_hz: float
    equivalent_inertia_s: float
    synchronous_rating_mva: float
    load_damping_mw_per_hz: float

    def __post_init__(self) -> None:
        if self.nominal_frequency_hz <= 0.0:
            raise ValueError("nominal_frequency_hz must be positive")
        if self.equivalent_inertia_s <= 0.0:
            raise ValueError("equivalent_inertia_s must be positive")
        if self.synchronous_rating_mva <= 0.0:
            raise ValueError("synchronous_rating_mva must be positive")
        if self.load_damping_mw_per_hz < 0.0:
            raise ValueError("load_damping_mw_per_hz cannot be negative")

    @property
    def frequency_mass_mw_s_per_hz(self) -> float:
        """Coefficient multiplying d(delta_f)/dt in the swing equation. 
           It is computed as 2 * Heq* Ssync / fnominal, in MW s/Hz."""

        return (
            2.0
            * self.equivalent_inertia_s
            * self.synchronous_rating_mva
            / self.nominal_frequency_hz
        )


@dataclass(frozen=True)
class FrequencyTrajectory:
    """Exact solution of the linear frequency model for a constant power step."""
    time_s: NDArray[np.float64]
    delta_frequency_hz: NDArray[np.float64]
    frequency_hz: NDArray[np.float64]
    power_deficit_mw: NDArray[np.float64]
    power_response_mw: NDArray[np.float64]


def frequency_derivative_hz_per_s(
    delta_frequency_hz: float,
    *,
    power_deficit_mw: float,
    power_response_mw: float,
    parameters: LinearFrequencyParameters,
) -> float:
    """Evaluate the linear differential equation at one instant."""

    numerator_mw = (
        -parameters.load_damping_mw_per_hz * delta_frequency_hz # natural response of the load
        - power_deficit_mw                                      # external disturbance (load increase or generation loss)
        + power_response_mw                                     # external control response (e.g., from reserves)
    )
    return numerator_mw / parameters.frequency_mass_mw_s_per_hz


def simulate_power_step(
    parameters: LinearFrequencyParameters,
    *,
    power_deficit_mw: float,
    start_time_s: float,
    final_time_s: float,
    time_step_s: float,
    power_response_mw: float = 0.0,
) -> FrequencyTrajectory:
    """Return the exact trajectory for constant power steps.

    The deficit and response both start at ``start_time_s``.  The closed-form
    solution keeps this first learning model transparent and avoids numerical
    integration error.  A later FCR controller with a time-varying response can
    reuse :func:`frequency_derivative_hz_per_s` in an ODE solver.
    """

    if power_deficit_mw < 0.0:
        raise ValueError("power_deficit_mw cannot be negative")
    if power_response_mw < 0.0:
        raise ValueError("power_response_mw cannot be negative")
    if start_time_s < 0.0:
        raise ValueError("start_time_s cannot be negative")
    if final_time_s <= start_time_s:
        raise ValueError("final_time_s must be greater than start_time_s")
    if time_step_s <= 0.0:
        raise ValueError("time_step_s must be positive")

    time_s = np.arange(0.0, final_time_s + 0.5 * time_step_s, time_step_s)
    elapsed_s = np.maximum(time_s - start_time_s, 0.0) # time elapsed since the disturbance started
    step_is_active = time_s >= start_time_s

    net_deficit_mw = power_deficit_mw - power_response_mw
    damping = parameters.load_damping_mw_per_hz
    frequency_mass = parameters.frequency_mass_mw_s_per_hz

    if damping > 0.0:
        steady_state_delta_hz = -net_deficit_mw / damping
        delta_frequency_hz = steady_state_delta_hz * (
            1.0 - np.exp(-damping * elapsed_s / frequency_mass)
        )
    else:
        delta_frequency_hz = -net_deficit_mw * elapsed_s / frequency_mass

    power_deficit = np.where(step_is_active, power_deficit_mw, 0.0)
    power_response = np.where(step_is_active, power_response_mw, 0.0)

    return FrequencyTrajectory(
        time_s=time_s,
        delta_frequency_hz=delta_frequency_hz,
        frequency_hz=parameters.nominal_frequency_hz + delta_frequency_hz,
        power_deficit_mw=power_deficit,
        power_response_mw=power_response,
    )


def damping_from_load_sensitivity(
    *,
    load_mw: float,
    nominal_frequency_hz: float,
    per_unit_load_change_per_unit_frequency_change: float,
) -> float:
    """Convert a dimensionless load-frequency sensitivity to MW/Hz.

    A sensitivity of 1.0 means that a 1% frequency change produces a 1%
    change in load. Selecting a value remains an explicit
    modelling assumption, not an output of the power flow.
    D = alpha * P_load / f_nominal, in MW/Hz, where alpha is the per-unit sensitivity.
    """

    if load_mw < 0.0:
        raise ValueError("load_mw cannot be negative")
    if nominal_frequency_hz <= 0.0:
        raise ValueError("nominal_frequency_hz must be positive")
    if per_unit_load_change_per_unit_frequency_change < 0.0:
        raise ValueError("load-frequency sensitivity cannot be negative")

    return (
        per_unit_load_change_per_unit_frequency_change
        * load_mw
        / nominal_frequency_hz
    )
