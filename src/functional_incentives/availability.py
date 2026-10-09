"""Scalar Gaussian reserve availability with an explicit physical boundary rule.

The mean and variance (a 1-by-1 covariance, in MW**2) describe the underlying
Gaussian. Truncation conditions it on [0, capacity]; clipping censors draws
at those boundaries. Their resulting moments generally differ from the inputs.
The same bounded law is used for sampling and expected penalties.
"""

from dataclasses import dataclass
from math import isfinite, sqrt
from typing import Literal

import numpy as np
from scipy.integrate import quad
from scipy.special import ndtr
from scipy.stats import truncnorm


@dataclass(frozen=True)
class GaussianReserve:
    mean_mw: float
    variance_mw2: float
    capacity_mw: float
    boundary_mode: Literal["truncated", "clipped"]

    def __post_init__(self) -> None:
        if not all(isfinite(v) for v in
                   (self.mean_mw, self.variance_mw2, self.capacity_mw)):
            raise ValueError("Gaussian parameters and capacity must be finite")
        if self.variance_mw2 < 0 or self.capacity_mw < 0:
            raise ValueError("variance and capacity must be nonnegative")
        if self.boundary_mode not in ("truncated", "clipped"):
            raise ValueError("boundary_mode must be 'truncated' or 'clipped'")
        if self.capacity_mw == 0 and (self.mean_mw != 0 or self.variance_mw2 != 0):
            raise ValueError("zero capacity requires a deterministic zero distribution")
        if self.boundary_mode == "truncated" and self.variance_mw2 == 0:
            if not 0 <= self.mean_mw <= self.capacity_mw:
                raise ValueError("deterministic truncated availability must be in bounds")
        if self.variance_mw2 > 0:
            if not all(isfinite(x) for x in self._standardized_bounds()):
                raise ValueError("Gaussian scale is too small for the specified bounds")

    def _standardized_bounds(self) -> tuple[float, float]:
        sigma = sqrt(self.variance_mw2)
        return -self.mean_mw / sigma, (self.capacity_mw - self.mean_mw) / sigma

    def sample_mw(self, rng: np.random.Generator) -> float:
        """Draw one available reserve for a block from the declared law."""
        if self.variance_mw2 == 0:
            return float(np.clip(self.mean_mw, 0, self.capacity_mw))
        sigma = sqrt(self.variance_mw2)
        if self.boundary_mode == "clipped":
            return float(np.clip(rng.normal(self.mean_mw, sigma), 0, self.capacity_mw))
        a, b = self._standardized_bounds()
        value = float(truncnorm.rvs(a, b, loc=self.mean_mw, scale=sigma,
                                   random_state=rng))
        if not isfinite(value) or not 0 <= value <= self.capacity_mw:
            raise RuntimeError("truncated Gaussian sampler returned invalid availability")
        return value

    def expected_shortfall_mw(self, commitment_mw: float) -> float:
        """Return E[(q-A)+] = integral_0^q F_A(x) dx, deterministically.

        For truncation F_A(x) = [Phi((x-mu)/sigma)-Phi(a)]/[Phi(b)-Phi(a)].
        For clipping F_A(x) = Phi((x-mu)/sigma) in the open physical interval.
        Integrating the CDF avoids cancellation in partial-normal moments and
        includes the clipped law's atom at zero. No realization is sampled.
        """
        q = commitment_mw
        if not isfinite(q) or not 0 <= q <= self.capacity_mw:
            raise ValueError("commitment must lie within physical reserve capacity")
        if q == 0:
            return 0.0
        if self.variance_mw2 == 0:
            available = float(np.clip(self.mean_mw, 0, self.capacity_mw))
            return max(0.0, q - available)

        sigma = sqrt(self.variance_mw2)
        if self.boundary_mode == "truncated":
            a, b = self._standardized_bounds()
            distribution = truncnorm(a, b, loc=self.mean_mw, scale=sigma)
            cdf = distribution.cdf
        else:
            def cdf(x: float) -> float:
                return float(ndtr((x - self.mean_mw) / sigma))

        # Give quadrature landmarks when the Gaussian is narrow relative to q.
        points = sorted({self.mean_mw + z * sigma for z in (-8, -4, 0, 4, 8)
                         if 0 < self.mean_mw + z * sigma < q})
        value, error = quad(cdf, 0, q, points=points, epsabs=1e-10,
                            epsrel=1e-10, limit=150)
        if not isfinite(value) or error > 1e-7 * max(1.0, q):
            raise RuntimeError("expected-shortfall quadrature did not converge")
        return float(np.clip(value, 0, q))
