"""Provider capability, cost parameters and first-order FCR/FI response."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

import numpy as np
from numpy.typing import NDArray

from .availability import GaussianReserve


def droop_gain_mw_per_hz(
    power_base_mw: float,
    nominal_frequency_hz: float,
    droop_pu: float = 0.05,
) -> float:
    """Convert normalized governor droop to K = P_base / (R * f_nominal).

    NERC's percentage-droop convention relates per-unit frequency change to
    per-unit power change. At R=0.05, K*f_nominal/P_base = 20. A machine's MVA
    rating may supply the numerical per-unit power base; it is not P_max.
    The reserve award and availability do not enter this gain.
    """
    for name, value in (
        ("power_base_mw", power_base_mw),
        ("nominal_frequency_hz", nominal_frequency_hz),
        ("droop_pu", droop_pu),
    ):
        if not np.isfinite(value) or value <= 0.0:
            raise ValueError(f"{name} must be finite and positive")
    gain = power_base_mw / droop_pu / nominal_frequency_hz
    if not np.isfinite(gain):
        raise ValueError("droop conversion produced a non-finite gain")
    return gain


@dataclass(frozen=True)
class FCRProviderParameters:
    """Parameters of one symmetric, first-order FCR provider.

    reserve_mw is the effective physical saturation bound min(award, availability).
    """

    provider_id: str
    gain_mw_per_hz: float
    time_constant_s: float
    reserve_mw: float

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("provider_id cannot be empty")
        for name in ("gain_mw_per_hz", "time_constant_s", "reserve_mw"):
            if not np.isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be finite")
        if self.gain_mw_per_hz < 0.0:
            raise ValueError("gain_mw_per_hz cannot be negative")
        if self.time_constant_s <= 0.0:
            raise ValueError("time_constant_s must be positive")
        if self.reserve_mw < 0.0:
            raise ValueError("reserve_mw cannot be negative")

    def requested_response_mw(
        self, delta_frequency_hz: float | NDArray[np.float64]
    ) -> float | NDArray[np.float64]:
        """Return the droop request clipped to +/- reserve_mw, in MW."""

        return np.clip(
            -self.gain_mw_per_hz * delta_frequency_hz,
            -self.reserve_mw,
            self.reserve_mw,
        )

    def response_derivative_mw_per_s(
        self,
        response_mw: float,
        delta_frequency_hz: float,
    ) -> float:
        """Evaluate du_i/dt = (-u_i + clip(-K_i*delta_f, +/-q_eff)) / T_i."""

        requested_response_mw = float(
            self.requested_response_mw(delta_frequency_hz)
        )
        return (requested_response_mw - response_mw) / self.time_constant_s


def symmetric_reserve_capacity_mw(
    operating_power_mw: float,
    minimum_power_mw: float,
    maximum_power_mw: float,
) -> float:
    """Return min(P_max - P_0, P_0 - P_min); no MVA-to-limit inference."""
    if not all(isfinite(x) for x in
               (operating_power_mw, minimum_power_mw, maximum_power_mw)):
        raise ValueError("active-power inputs must be finite")
    if not minimum_power_mw <= operating_power_mw <= maximum_power_mw:
        raise ValueError("operating power must lie inside declared active-power limits")
    return min(maximum_power_mw - operating_power_mw,
               operating_power_mw - minimum_power_mw)


@dataclass(frozen=True)
class QuadraticCommitmentCost:
    """C(q) = a*q + beta*q**2/2, in EUR for one entire delivery block."""

    linear_eur_per_mw_block: float
    quadratic_eur_per_mw2_block: float

    def __post_init__(self) -> None:
        for value in (self.linear_eur_per_mw_block, self.quadratic_eur_per_mw2_block):
            if not isfinite(value) or value < 0:
                raise ValueError("cost coefficients must be finite and nonnegative")

    def total_eur(self, commitment_mw: float) -> float:
        if not isfinite(commitment_mw) or commitment_mw < 0:
            raise ValueError("commitment_mw must be finite and nonnegative")
        result = (self.linear_eur_per_mw_block * commitment_mw
                  + 0.5 * self.quadratic_eur_per_mw2_block * commitment_mw**2)
        if not isfinite(result):
            raise ValueError("commitment cost must be finite")
        return result


@dataclass(frozen=True)
class BlockProvider:
    provider_id: str
    generator_id: str
    operating_power_mw: float
    minimum_power_mw: float
    maximum_power_mw: float
    power_base_mw: float
    time_constant_s: float
    availability: GaussianReserve
    cost: QuadraticCommitmentCost
    penalty_eur_per_missing_mw_block: float
    minimum_bid_price_eur_per_mw_block: float
    maximum_bid_price_eur_per_mw_block: float
    reserve_safety_margin_fraction: float = 0.0
    technology: str = "unspecified"
    gain_mw_per_hz: float | None = None
    participation: str = "fcr"
    fi_gamma_eur_per_hz_mw_s: float | None = None
    fi_rho_eur_per_mw2_s: float | None = None

    def __post_init__(self) -> None:
        if not self.provider_id.strip() or not self.generator_id.strip():
            raise ValueError("provider and generator identities cannot be empty")
        if self.participation not in ("fcr", "fi", "fcr_or_fi", "inactive"):
            raise ValueError("participation must be fcr, fi, fcr_or_fi or inactive")
        for name, positive in (("fi_gamma_eur_per_hz_mw_s", False),
                               ("fi_rho_eur_per_mw2_s", True)):
            value = getattr(self, name)
            if value is None:
                if self.participation in ("fi", "fcr_or_fi"):
                    raise ValueError(f"{name} is required for FI participation")
            elif not isfinite(value) or value < 0 or (positive and value == 0):
                raise ValueError(f"invalid {name}")
        if (not isfinite(self.reserve_safety_margin_fraction)
                or not 0 <= self.reserve_safety_margin_fraction <= 1):
            raise ValueError("reserve_safety_margin_fraction must lie in [0, 1]")
        if self.gain_mw_per_hz is not None:
            if not isfinite(self.gain_mw_per_hz) or self.gain_mw_per_hz < 0:
                raise ValueError("explicit gain must be finite and nonnegative")
        headroom = symmetric_reserve_capacity_mw(
            self.operating_power_mw, self.minimum_power_mw, self.maximum_power_mw
        )
        if self.availability.capacity_mw > headroom + 1e-9:
            raise ValueError("reserve capacity exceeds declared symmetric headroom")
        for name in ("power_base_mw", "time_constant_s"):
            if not isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be finite and positive")

    @property
    def maximum_offer_mw(self) -> float:
        """Extra bidding margin on a reserve cap already checked against headroom.

        This does not change the availability law, dispatch, inertia or droop.
        """
        return (1.0 - self.reserve_safety_margin_fraction) * self.availability.capacity_mw


@dataclass(frozen=True)
class FIProviderParameters:
    """Fixed incentive and realized capability for T*dz/dt = -z + target.

    reserve_mw is available physical modulation A, not an FCR award. Gamma
    is fixed by the user; rho is the coefficient of the cost rate rho*z^2/2.
    """

    provider_id: str
    time_constant_s: float
    reserve_mw: float
    gamma_eur_per_hz_mw_s: float
    rho_eur_per_mw2_s: float

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("provider_id cannot be empty")
        for name, positive in (("time_constant_s", True), ("reserve_mw", False),
                               ("gamma_eur_per_hz_mw_s", False), ("rho_eur_per_mw2_s", True)):
            value = getattr(self, name)
            if not isfinite(value) or value < 0 or (positive and value == 0):
                raise ValueError(f"invalid FI {name}")

    def response_derivative_mw_per_s(self, response_mw: float, target_mw: float) -> float:
        return (target_mw - response_mw) / self.time_constant_s

    def predicted_response_mw(self, initial_mw: float, target_mw: float, elapsed_s: float) -> float:
        return float(initial_mw + (target_mw - initial_mw)
                     * -np.expm1(-elapsed_s / self.time_constant_s))


def first_order_response_integrals(time_constant_s: float, duration_s: float) -> tuple[float, ...]:
    """Integrals of a, b, a^2, a*b, b^2 for z=a*z0+b*v, a=exp(-t/T).

    Small-interval series avoid cancellation in the target's linear/quadratic
    coefficients. These physical moments serve both prediction and decisions.
    """
    if not isfinite(time_constant_s) or time_constant_s <= 0:
        raise ValueError("time constant must be finite and positive")
    if not isfinite(duration_s) or duration_s < 0:
        raise ValueError("duration must be finite and nonnegative")
    x = duration_s / time_constant_s
    decay_change = -np.expm1(-x)
    a = time_constant_s * decay_change
    aa = time_constant_s * (-np.expm1(-2*x)) / 2
    ab = time_constant_s * decay_change**2 / 2
    if x < 1e-3:
        b = duration_s * x * (1/2 + x*(-1/6 + x*(1/24 + x*(-1/120 + x/720))))
        bb = duration_s * x*x * (1/3 + x*(-1/4 + x*(7/60 + x*(-1/24 + x*31/2520))))
    else:
        b = duration_s - a
        bb = duration_s - 2*a + aa
    return float(a), float(b), float(aa), float(ab), float(bb)
