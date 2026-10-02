"""First-order primary frequency-control provider model.

Provider ``i`` follows the Week 3 teaching law

    T_i du_i/dt = -u_i + sat(-K_i delta_f, -q_i_star, q_i_star),

where ``delta_f = f - f_nominal`` is in Hz, ``u_i`` is in MW,
``K_i`` is in MW/Hz, ``T_i`` is in seconds, and ``q_i_star`` is in MW.
A positive ``u_i`` adds active power and therefore supports frequency.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class FCRProviderParameters:
    """Parameters of one symmetric, first-order FCR provider."""

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
        """Return ``sat(-K_i delta_f, -q_i_star, q_i_star)`` in MW."""

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
        """Evaluate the provider differential equation at one instant.
            du_i/dt = (-u_i + sat(-K_i delta_f, -q_i_star, q_i_star) ) / T_i"""

        requested_response_mw = float(
            self.requested_response_mw(delta_frequency_hz)
        )
        return (requested_response_mw - response_mw) / self.time_constant_s


def load_fcr_provider_parameters(
    config_path: str | Path,
) -> tuple[FCRProviderParameters, ...]:
    """Load a list of FCR providers from a TOML experiment configuration."""

    path = Path(config_path)
    with path.open("rb") as config_file:
        config = tomllib.load(config_file)

    raw_providers = config.get("providers")
    if not isinstance(raw_providers, list) or not raw_providers:
        raise ValueError("The TOML configuration must contain at least one [[providers]] entry")

    providers = tuple(FCRProviderParameters(**values) for values in raw_providers)
    provider_ids = tuple(provider.provider_id for provider in providers)
    if len(set(provider_ids)) != len(provider_ids):
        raise ValueError("provider_id values must be unique")
    return providers
