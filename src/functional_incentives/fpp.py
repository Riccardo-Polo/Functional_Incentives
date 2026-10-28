"""Single-area FPP measurement and settlement, independent of provider control.

Source profile: FCF Procedure v1.2 (8 June 2025), sections 4--7; Factor
Calculation Guide (9 December 2024), section 5.1.1 and Appendix A1.1.
See docs/AEMO_FPP.md for source versions, exact indexing and adaptations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd


SAMPLE_SECONDS = 4
INTERVAL_SECONDS = 300
SAMPLES_PER_INTERVAL = 75
HISTORY_SAMPLES = 30
FM_ALPHA = 2 / 9
MISALIGNMENT_BAND_HZ = 0.015
RELIABILITY_AMPLITUDE_HZ = 0.01
MIN_DIRECTIONAL_SAMPLES = 7
SOURCE_PROFILE = "aemo_fcf_v1.2_2025-06-08"
RESIDUAL_ID = "residual"


def complete_interval_count(final_time_s: float) -> int:
    if (not np.isfinite(final_time_s) or final_time_s <= 0
            or final_time_s % INTERVAL_SECONDS != 0):
        raise ValueError("FPP requires complete positive 300-second intervals; no prorating")
    return int(final_time_s / INTERVAL_SECONDS)


@dataclass(frozen=True)
class DispatchSchedule:
    """Prescribed settlement-reference targets, initialized at solved powers.

    All units use the scheduled/semi-scheduled reference category. Balance and
    full availability support are checked at every knot, sufficient for each
    linear segment. Arrays are copied and made read-only to freeze the inputs.
    These targets do not command the physical model or construct its meter data.
    """

    provider_ids: tuple[str, ...]
    targets_mw: np.ndarray  # initial point, then one target per 300-second interval
    initial_operating_power_mw: np.ndarray
    minimum_power_mw: np.ndarray
    maximum_power_mw: np.ndarray
    capacity_mw: np.ndarray
    reference_category: str = "scheduled_semi_scheduled"

    def __post_init__(self):
        n = len(self.provider_ids)
        if not n or len(set(self.provider_ids)) != n or RESIDUAL_ID in self.provider_ids:
            raise ValueError("schedule needs unique metered provider IDs, excluding residual")
        if self.reference_category != "scheduled_semi_scheduled":
            raise ValueError("unsupported FPP reference category")
        for name in ("targets_mw", "initial_operating_power_mw", "minimum_power_mw",
                     "maximum_power_mw", "capacity_mw"):
            values = np.array(getattr(self, name), dtype=float, copy=True)
            if not np.all(np.isfinite(values)):
                raise ValueError(f"missing/non-finite schedule data: {name}")
            if name == "targets_mw":
                if values.ndim != 2 or values.shape[1] != n or values.shape[0] < 2:
                    raise ValueError("targets require initial and interval-boundary rows for every unit")
            elif values.shape != (n,):
                raise ValueError(f"{name} must contain one value per provider")
            values.setflags(write=False)
            object.__setattr__(self, name, values)
        if not np.allclose(self.targets_mw[0], self.initial_operating_power_mw, rtol=0, atol=1e-9):
            raise ValueError("initialize dispatch with the solved ANDES operating powers")
        if np.any(self.capacity_mw < 0):
            raise ValueError("availability support must be nonnegative")
        if np.any(abs(np.sum(self.targets_mw - self.initial_operating_power_mw, axis=1)) > 1e-9):
            raise ValueError("unbalanced dispatch schedule is unsupported by this aggregate benchmark")
        if (np.any(self.targets_mw - self.capacity_mw < self.minimum_power_mw - 1e-9)
                or np.any(self.targets_mw + self.capacity_mw > self.maximum_power_mw + 1e-9)):
            raise ValueError("schedule lacks headroom for the complete availability support")

    @property
    def final_time_s(self):
        return (len(self.targets_mw) - 1) * INTERVAL_SECONDS

    def reference_mw(self, time_s):
        times = np.asarray(time_s, dtype=float)
        if times.ndim != 1 or not np.all(np.isfinite(times)) or np.any(
                (times < 0) | (times > self.final_time_s)):
            raise ValueError("reference times must lie within the prescribed schedule")
        knots = np.arange(len(self.targets_mw)) * INTERVAL_SECONDS
        return np.column_stack([np.interp(times, knots, column) for column in self.targets_mw.T])

    def check_total_power(self, measured_power_mw):
        values = np.asarray(measured_power_mw, dtype=float)
        if values.ndim != 2 or values.shape[1] != len(self.provider_ids) or not np.all(np.isfinite(values)):
            raise ValueError("missing/invalid metered power; zero substitution is unsupported")
        if (np.any(values < self.minimum_power_mw - 1e-8)
                or np.any(values > self.maximum_power_mw + 1e-8)):
            raise ValueError("delivered total power violates declared active-power limits")


@dataclass(frozen=True)
class RegulationPrices:
    """External hourly prices, explicitly independent of FCR block prices."""

    raise_per_mw_h: float
    lower_per_mw_h: float
    currency: str
    source: str
    date: str
    scope: str
    evidence: str  # observed, illustrative, proxy
    price_basis: str  # adjusted_constraint, regional_market_proxy, synthetic
    units: str = "currency/(MW h)"

    def __post_init__(self):
        if not all(np.isfinite(p) for p in (self.raise_per_mw_h, self.lower_per_mw_h)):
            raise ValueError("both directional prices must be finite")
        if self.currency not in ("AUD", "EUR"):
            raise ValueError("declare AUD or EUR; no implicit currency conversion")
        if not all(isinstance(x, str) and x.strip() for x in (self.source, self.date, self.scope)):
            raise ValueError("price source, ISO date and geographic/constraint scope are required")
        date.fromisoformat(self.date)
        if self.units != "currency/(MW h)":
            raise ValueError("FPP prices require hourly MW units, not whole-block FCR prices")
        if self.evidence not in ("observed", "illustrative", "proxy"):
            raise ValueError("unsupported price evidence category")
        if self.price_basis not in ("adjusted_constraint", "regional_market_proxy", "synthetic"):
            raise ValueError("unsupported price basis")
        if self.price_basis == "regional_market_proxy" and self.evidence != "proxy":
            raise ValueError("a regional market price must be labelled a proxy")
        if self.price_basis == "synthetic" and self.evidence != "illustrative":
            raise ValueError("synthetic prices must be labelled illustrative")


def frequency_measure(history_and_interval_hz):
    """Guide Appendix A1.1 convention: first history FM=0; recurse from row 2.

    The 105 rows are at start-116, ..., start, start+4, ..., start+300.
    Thus the counted 75 samples exclude the start boundary. The first history
    frequency is deliberately not used in the recurrence, as in Table 15.
    """
    errors = np.asarray(history_and_interval_hz, dtype=float)
    if errors.shape != (HISTORY_SAMPLES + SAMPLES_PER_INTERVAL,) or not np.all(np.isfinite(errors)):
        raise ValueError("FM requires 30 preceding and 75 current finite synchronized samples")
    fm = np.zeros_like(errors)
    for k in range(1, len(fm)):
        fm[k] = (1 - FM_ALPHA) * fm[k - 1] - FM_ALPHA * errors[k]
    return fm


def directional_reliability(fm_hz, direction):
    if direction not in ("raise", "lower"):
        raise ValueError("direction must be raise or lower")
    fm = np.asarray(fm_hz, dtype=float)
    if fm.shape != (SAMPLES_PER_INTERVAL,) or not np.all(np.isfinite(fm)):
        raise ValueError("directional reliability requires 75 finite FM values")
    directed = fm if direction == "raise" else -fm
    count = int(np.count_nonzero(directed > 0))
    amplitude = float(max(0., directed.max()))
    reasons = []
    if count < MIN_DIRECTIONAL_SAMPLES:
        reasons.append("fewer_than_seven_directional_FM_samples")
    if amplitude <= RELIABILITY_AMPLITUDE_HZ:
        reasons.append("no_directional_FM_strictly_beyond_0.01_Hz")
    return not reasons, count, amplitude, ";".join(reasons) or "reliable"


def same_sign_contribution_factors(performance):
    """FCFP 6.2.2/6.2.4. Valid zero maps to CF=0; NULL remains undefined.

    Historical fallback is out of scope, so mixed NULL/valid populations are
    rejected. For an all-NULL unreliable direction the factors remain NaN.
    No denominator tolerance or adjustable stabilizer changes the ratios.
    """
    values = np.asarray(performance, dtype=float)
    if values.ndim != 1 or not len(values) or np.any(np.isinf(values)):
        raise ValueError("performance must be a nonempty vector without infinities")
    if np.all(np.isnan(values)):
        return np.full_like(values, np.nan)
    if np.any(np.isnan(values)):
        raise ValueError("partial NULL performance requires unsupported historical default factors")
    factors = np.zeros_like(values)
    for mask in (values > 0, values < 0):
        if np.any(mask):
            denominator = abs(float(np.sum(values[mask])))
            if not np.isfinite(denominator) or denominator == 0:
                raise ValueError("invalid same-sign performance denominator")
            factors[mask] = values[mask] / denominator
    return factors


def settle_fpp(time_s, delta_frequency_hz, measured_power_mw, schedule: DispatchSchedule,
               prices: RegulationPrices | None = None, *, initial_history_hz=None) -> dict[str, pd.DataFrame]:
    """Settle complete trajectories without knowing their physical controllers.

    Exact 4-second measurements must be present; interpolation, missing-data
    recovery, other reference categories and historical defaults are unsupported.
    Nominal prehistory is explicit in the benchmark; optional supplied history
    supports independent external trajectory/initialization tests.
    """
    times = np.asarray(time_s, dtype=float)
    frequency = np.asarray(delta_frequency_hz, dtype=float)
    power = np.asarray(measured_power_mw, dtype=float)
    if (times.ndim != 1 or len(times) < 2 or not np.all(np.isfinite(times))
            or times[0] != 0 or np.any(np.diff(times) <= 0)):
        raise ValueError("trajectory times must be finite, increasing and start at zero")
    intervals = complete_interval_count(times[-1])
    if times[-1] != schedule.final_time_s:
        raise ValueError("trajectory and dispatch horizons differ")
    if frequency.shape != times.shape or not np.all(np.isfinite(frequency)):
        raise ValueError("missing/invalid frequency; telemetry recovery is unsupported")
    if power.shape != (len(times), len(schedule.provider_ids)):
        raise ValueError("metered-power shape must match times and schedule provider order")
    schedule.check_total_power(power)
    counted_times = np.arange(1, intervals * SAMPLES_PER_INTERVAL + 1) * SAMPLE_SECONDS
    indices = np.searchsorted(times, counted_times)
    if np.any(indices >= len(times)) or not np.allclose(times[indices], counted_times, rtol=0, atol=1e-9):
        raise ValueError("exact synchronized 4-second samples are required; no measurement interpolation")
    errors = frequency[indices]
    measured = power[indices]
    references = schedule.reference_mw(counted_times)
    deviations = measured - references
    history = np.zeros(HISTORY_SAMPLES) if initial_history_hz is None else np.asarray(initial_history_hz, dtype=float)
    if history.shape != (HISTORY_SAMPLES,) or not np.all(np.isfinite(history)):
        raise ValueError("initial history requires 30 finite 4-second frequency deviations")
    if history[-1] != frequency[0]:
        raise ValueError("initial history endpoint must equal the measured frequency at t=0")
    full_errors = np.concatenate((history, errors))
    sample_rows, history_rows, direction_rows, account_rows = [], [], [], []
    identities = (*schedule.provider_ids, RESIDUAL_ID)
    for interval in range(intervals):
        start = interval * SAMPLES_PER_INTERVAL
        stop = start + SAMPLES_PER_INTERVAL
        window_errors = full_errors[start:stop + HISTORY_SAMPLES]
        window_fm = frequency_measure(window_errors)
        fm = window_fm[HISTORY_SAMPLES:]
        fd = errors[start:stop]
        excluded = (fm * fd > 0) & (abs(fd) > MISALIGNMENT_BAND_HZ)
        dev = np.column_stack((deviations[start:stop], -deviations[start:stop].sum(axis=1)))
        sum_pos = np.maximum(dev, 0).sum(axis=1)
        sum_neg = np.minimum(dev, 0).sum(axis=1)
        for k in range(len(window_fm)):
            history_rows.append(dict(interval=interval + 1, time_s=interval * INTERVAL_SECONDS
                                     + (k - HISTORY_SAMPLES + 1) * SAMPLE_SECONDS,
                                     delta_frequency_hz=window_errors[k], fm_hz=window_fm[k],
                                     counted=k >= HISTORY_SAMPLES))
        for k in range(SAMPLES_PER_INTERVAL):
            row = dict(interval=interval + 1, sample=k + 1, time_s=counted_times[start + k],
                       delta_frequency_hz=fd[k], fm_hz=fm[k], performance_excluded=bool(excluded[k]),
                       sum_positive_deviations_mw=sum_pos[k], sum_negative_deviations_mw=sum_neg[k],
                       residual_deviation_mw=dev[k, -1])
            for j, identity in enumerate(schedule.provider_ids):
                row.update({f"{identity}_reference_mw": references[start + k, j],
                            f"{identity}_measured_mw": measured[start + k, j],
                            f"{identity}_deviation_mw": dev[k, j]})
            sample_rows.append(row)
        for direction in ("raise", "lower"):
            reliable, count, amplitude, reason = directional_reliability(fm, direction)
            directional = fm > 0 if direction == "raise" else fm < 0
            # Misalignment excludes PERFORMANCE only. Reliability and RCR use raw FM.
            weights = np.where(directional & ~excluded, fm, 0.)
            performance = weights @ dev if reliable else np.full(len(identities), np.nan)
            factors = same_sign_contribution_factors(performance)
            rcr = (float(np.max(sum_pos[directional])) if direction == "raise"
                   else float(-np.min(sum_neg[directional]))) if reliable else 0.
            coefficients = factors * rcr / 12 if reliable else np.zeros(len(identities))
            direction_rows.append(dict(interval=interval + 1, direction=direction, reliable=reliable,
                                       status=reason, directional_sample_count=count,
                                       directional_fm_peak_hz=amplitude, rcr_mw=rcr,
                                       excluded_performance_samples=int(np.sum(directional & excluded))))
            price = None if prices is None else getattr(prices, direction + "_per_mw_h")
            for j, identity in enumerate(identities):
                payment = None if price is None else float(coefficients[j] * price)
                account_rows.append(dict(interval=interval + 1, direction=direction, provider_id=identity,
                    performance_mw_hz=performance[j], performance_is_null=not reliable,
                    contribution_factor=factors[j], rcr_mw=rcr, coefficient_mw_h=coefficients[j],
                    currency=None if prices is None else prices.currency,
                    regulation_price_per_mw_h=price, net_settlement=payment,
                    gross_reward=None if payment is None else max(payment, 0.),
                    charge_magnitude=None if payment is None else max(-payment, 0.),
                    status="unreliable_RCR_zero" if not reliable else
                           ("valid_zero_performance" if performance[j] == 0 else "valid")))
    return {"samples": pd.DataFrame(sample_rows), "filter_history": pd.DataFrame(history_rows),
            "directions": pd.DataFrame(direction_rows), "accounts": pd.DataFrame(account_rows)}
