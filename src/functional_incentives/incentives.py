"""Sampled-frequency bilinear FI payments, prediction and financial records.

Inspired by Matt et al., arXiv:2604.27770v2, equations (3)-(6). The agreed
extension pays actual first-order response over each interval at a held sampled
frequency. Gamma is a fixed input; no TSO optimization is performed here.
"""

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from .providers import FIProviderParameters, first_order_response_integrals


def fi_financial_rates(
    provider: FIProviderParameters, sampled_frequency_error_hz: float, response_mw: float,
) -> tuple[float, float, float]:
    """Gross reward, gross charge and activation-cost rates, all in EUR/s.

    The response argument must be delivered power. Splitting signs BEFORE
    integration preserves both rewards and charges when response changes sign.
    """
    payment = -provider.gamma_eur_per_hz_mw_s * sampled_frequency_error_hz * response_mw
    cost = 0.5 * provider.rho_eur_per_mw2_s * response_mw**2
    return max(payment, 0.0), max(-payment, 0.0), cost


def predict_fi_financials(
    provider: FIProviderParameters, *, sampled_frequency_error_hz: float,
    initial_response_mw: float, target_mw: float, duration_s: float,
) -> tuple[float, float]:
    """Predicted signed payment and activation cost in EUR, using exact moments."""
    a, b, aa, ab, bb = first_order_response_integrals(provider.time_constant_s, duration_s)
    response_integral = a*initial_response_mw + b*target_mw
    squared_integral = (aa*initial_response_mw**2 + 2*ab*initial_response_mw*target_mw
                        + bb*target_mw**2)
    return (-provider.gamma_eur_per_hz_mw_s * sampled_frequency_error_hz * response_integral,
            0.5 * provider.rho_eur_per_mw2_s * max(0.0, squared_integral))


@dataclass(frozen=True)
class FIAccountingTrajectory:
    """FI-only columns aligned with the simulation's output times.

    Actual balances integrate rates along the numerical delivered trajectory.
    Predictions integrate the first-order analytical response independently.
    Interval records include decisions and full interval predicted/actual amounts.
    """

    provider_ids: tuple[str, ...]
    sampled_frequency_error_hz: NDArray[np.float64]
    payment_rate_eur_per_s: NDArray[np.float64]
    gross_rewards_eur: NDArray[np.float64]
    gross_penalties_eur: NDArray[np.float64]
    activation_cost_eur: NDArray[np.float64]
    target_benchmark_eur: NDArray[np.float64]
    predicted_settlement_eur: NDArray[np.float64]
    intervals: tuple[dict, ...]

    @property
    def net_settlement_eur(self) -> NDArray[np.float64]:
        return self.gross_rewards_eur - self.gross_penalties_eur

    @property
    def profit_eur(self) -> NDArray[np.float64]:
        return self.net_settlement_eur - self.activation_cost_eur
