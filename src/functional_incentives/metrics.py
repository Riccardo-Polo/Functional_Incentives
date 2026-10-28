"""Numerical regression, convergence and response-limit checks."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import t as student_t

from .frequency import FrequencyTrajectory
from .simulation import AggregateFCRTrajectory


def frequency_performance_metrics(trajectory: AggregateFCRTrajectory) -> dict[str, float]:
    """Sampled extrema and exact one-sided event RoCoF, even off the output grid.

    RoCoF is the unfiltered model RHS, not a finite difference or a windowed
    instrument estimate. Later events retain the live frequency and response state.
    """
    rocof = np.append(trajectory.rocof_hz_per_s, trajectory.initial_rocof_hz_per_s)
    for event in trajectory.disturbance_limits:
        rocof = np.append(rocof, [event["rocof_left_hz_per_s"], event["rocof_right_hz_per_s"]])
    if not np.all(np.isfinite(rocof)):
        raise RuntimeError("non-finite model RoCoF")
    return {
        "nadir_sampled_hz": float(trajectory.frequency_hz.min()),
        "final_frequency_hz": float(trajectory.frequency_hz[-1]),
        "final_absolute_error_hz": float(abs(trajectory.delta_frequency_hz[-1])),
        "initial_rocof_hz_per_s": float(trajectory.initial_rocof_hz_per_s),
        "rocof_min_hz_per_s": float(rocof.min()),
        "rocof_max_hz_per_s": float(rocof.max()),
        "rocof_max_abs_hz_per_s": float(np.max(abs(rocof))),
    }


def summarize_fpp_benchmark(trajectory, schedule, event_time_s):
    """Fixed-policy metrics with model-RHS RoCoF and live numerical ledgers.

    Saturation fractions are trapezoidal estimates on saved post-event samples,
    with 1e-9 MW target and 1e-6 MW delivered-bound diagnostic tolerances.
    Zero-capacity responses are marked unavailable, not saturated.
    """
    schedule.check_total_power(trajectory.provider_active_power_mw)
    for values in (trajectory.provider_response_mw, trajectory.provider_requested_response_mw):
        if not np.all(np.isfinite(values)) or np.any(abs(values) > trajectory.provider_reserve_mw + 1e-8):
            raise RuntimeError("fixed-policy trajectory violates finite response bounds")
    integral = trajectory.integrated_squared_frequency_error_hz2_s
    if not np.all(np.isfinite(integral)) or np.any(np.diff(integral) < -1e-10):
        raise RuntimeError("invalid live frequency-error ledger")
    fi_metrics = (summarize_tso_trajectory(trajectory, event_time_s)
                  if trajectory.fi is not None else None)
    cost = 0. if fi_metrics is None else fi_metrics["activation_cost_eur"]
    if trajectory.fpp_activation_cost_eur is not None:
        ledger = trajectory.fpp_activation_cost_eur
        if not np.all(np.isfinite(ledger)) or np.any(np.diff(ledger, axis=0) < -1e-10):
            raise RuntimeError("invalid FPP activation-cost ledger")
        cost += float(ledger[-1].sum())
    metrics = {**frequency_performance_metrics(trajectory),
        "zenith_sampled_hz": float(trajectory.frequency_hz.max()),
        "terminal_error_hz": float(trajectory.delta_frequency_hz[-1]),
        "frequency_error_hz2_s": float(integral[-1]),
        "switched_activation_cost_eur": cost,
        "fi_gross_rewards_eur": 0. if fi_metrics is None else fi_metrics["gross_rewards_eur"],
        "fi_charge_magnitudes_eur": 0. if fi_metrics is None else fi_metrics["gross_charges_eur"],
        "fi_net_settlement_eur": 0. if fi_metrics is None else fi_metrics["net_payment_eur"]}
    mask = trajectory.time_s >= event_time_s
    times = trajectory.time_s[mask]
    saturation = []
    for j, identity in enumerate(trajectory.provider_ids):
        bound = trajectory.provider_reserve_mw[j]
        target = trajectory.provider_requested_response_mw[mask, j]
        delivered = trajectory.provider_response_mw[mask, j]
        fraction = lambda values, tolerance: (float(np.trapezoid(
            (abs(abs(values) - bound) <= tolerance).astype(float), times) / (times[-1] - times[0]))
            if bound > 0 and len(times) > 1 else 0.)
        saturation.append(dict(provider_id=identity, response_bound_mw=bound, unavailable=bound == 0,
            target_bound_fraction=fraction(target, 1e-9), delivered_bound_fraction=fraction(delivered, 1e-6),
            peak_target_mw=float(np.max(abs(target))), peak_delivered_mw=float(np.max(abs(delivered)))))
    metrics["any_target_bound"] = float(any(s["target_bound_fraction"] > 0 for s in saturation))
    return metrics, saturation


def summarize_fpp_assessment(metrics, schedule="constant"):
    """Exploratory Student-t intervals and paired differences on assessment only."""
    assessment = metrics[(metrics.bank == "assessment") & (metrics.schedule == schedule)]
    fields = [name for name in metrics.select_dtypes(include=np.number) if name != "sample"]
    rows, pairs = [], []
    def statistics(values):
        values = np.asarray(values, dtype=float)
        if len(values) < 2 or not np.all(np.isfinite(values)):
            raise ValueError("assessment statistics need at least two finite paired outcomes")
        mean = float(values.mean())
        se = float(values.std(ddof=1) / np.sqrt(len(values)))
        width = float(student_t.ppf(.975, len(values) - 1) * se)
        return dict(n=len(values), mean=mean, standard_error=se, ci95_low=mean - width,
                    ci95_high=mean + width, minimum=float(values.min()), maximum=float(values.max()))
    for policy, group in assessment.groupby("policy"):
        for field in fields:
            rows.append(dict(policy=policy, metric=field, **statistics(group[field])))
    for a, b in (("bilinear_fi", "fcr_alone"), ("fpp", "fcr_alone"), ("fpp", "bilinear_fi")):
        left, right = [assessment[assessment.policy == p].set_index("sample").sort_index() for p in (a, b)]
        if left.index.has_duplicates or not left.index.equals(right.index):
            raise ValueError("fixed-policy assessment draws are not paired")
        for field in fields:
            pairs.append(dict(policy=a, reference=b, metric=field,
                              **statistics(left[field].to_numpy() - right[field].to_numpy())))
    return pd.DataFrame(rows), pd.DataFrame(pairs)


def tail_frequency_metrics(trajectory: AggregateFCRTrajectory, event_time_s: float,
                          window_s: float = 5., range_tolerance_hz: float = 1e-4,
                          rocof_tolerance_hz_per_s: float = 1e-4) -> dict[str, float]:
    """Finite-horizon stationarity screen; never a proof of asymptotic convergence.

    Use samples in the last window (clipped to post-event time). A short or
    sparsely sampled window cannot pass. Thresholds are diagnostic choices,
    not frequency-security limits. Terminal error retains its terminal label.
    """
    if not all(np.isfinite(v) and v > 0 for v in
               (window_s, range_tolerance_hz, rocof_tolerance_hz_per_s)):
        raise ValueError("tail window and diagnostic tolerances must be finite and positive")
    times = trajectory.time_s
    left = max(event_time_s, times[-1] - window_s)
    selected = times >= left
    frequency = trajectory.frequency_hz[selected]
    rocof = trajectory.rocof_hz_per_s[selected]
    if not len(frequency) or not np.all(np.isfinite(frequency)):
        raise ValueError("tail must contain finite post-event frequency samples")
    duration = float(times[-1] - times[selected][0])
    span, peak = float(np.ptp(frequency)), float(np.max(abs(rocof)))
    full = len(frequency) >= 3 and duration >= window_s - 1e-10
    return {"tail_duration_s": duration, "tail_frequency_range_hz": span,
            "tail_rocof_max_abs_hz_per_s": peak,
            "tail_stationarity_pass": float(full and span <= range_tolerance_hz
                                            and peak <= rocof_tolerance_hz_per_s)}


def summarize_tso_trajectory(trajectory: AggregateFCRTrajectory, event_time_s: float) -> dict:
    """Event metrics from numerical ledgers; saturation refers to held targets."""
    fi = trajectory.fi
    if fi is None:
        raise ValueError("TSO metrics require FI accounting (including at gamma zero)")
    integral = trajectory.integrated_squared_frequency_error_hz2_s
    for values in (trajectory.frequency_hz, integral, fi.net_settlement_eur, fi.predicted_settlement_eur):
        if not np.all(np.isfinite(values)):
            raise RuntimeError("non-finite TSO trajectory")
    if np.any(np.diff(integral) < -1e-10) or np.any(integral < -1e-10):
        raise RuntimeError("invalid integrated frequency error")
    for values in (trajectory.provider_response_mw, trajectory.provider_requested_response_mw):
        if not np.all(np.isfinite(values)) or np.any(abs(values) > trajectory.provider_reserve_mw + 1e-8):
            raise RuntimeError("TSO trajectory violates response limits")
    for values in (fi.gross_rewards_eur, fi.gross_penalties_eur, fi.activation_cost_eur):
        if (not np.all(np.isfinite(values)) or np.any(values < -1e-8)
                or np.any(np.diff(values, axis=0) < -1e-8)):
            raise RuntimeError("invalid FI cumulative ledger")
    scale = max(1., float(np.max(fi.gross_rewards_eur + fi.gross_penalties_eur + fi.activation_cost_eur)))
    if np.max(abs(fi.net_settlement_eur - fi.predicted_settlement_eur)) > 1e-6 * scale:
        raise RuntimeError("FI analytical prediction and actual settlement disagree")
    capacities = dict(zip(trajectory.provider_ids, trajectory.provider_reserve_mw))
    bound_time = 0.
    for record in fi.intervals:
        capacity = capacities[record["provider_id"]]
        if capacity > 0 and abs(abs(record["target_mw"]) - capacity) <= 1e-9:
            bound_time += max(0., record["end_time_s"] - max(event_time_s, record["start_time_s"]))
    payment, cost = float(fi.net_settlement_eur[-1].sum()), float(fi.activation_cost_eur[-1].sum())
    return {
        "frequency_error_hz2_s": float(integral[-1]), "net_payment_eur": payment,
        "gross_rewards_eur": float(fi.gross_rewards_eur[-1].sum()),
        "gross_charges_eur": float(fi.gross_penalties_eur[-1].sum()), "activation_cost_eur": cost,
        "payment_minus_twice_cost_eur": payment - 2 * cost,
        **frequency_performance_metrics(trajectory),
        "target_bound_fraction": bound_time / (len(fi.provider_ids) * (trajectory.time_s[-1] - event_time_s)),
        "any_target_bound": float(bound_time > 0),
        "zero_fi_capacity_fraction": sum(capacities[i] == 0 for i in fi.provider_ids) / len(fi.provider_ids),
    }


def summarize_weighted_tso_samples(samples: pd.DataFrame, frequency_weight: float) -> pd.DataFrame:
    """Pointwise uncertainty, composing J per draw to retain E/payment covariance.

    Validation contains only the selected gamma. A single delivery observation
    has no estimated sampling standard error; its interval fields remain null.
    Design intervals do not correct for selecting gamma on those same draws.
    """
    if not np.isfinite(frequency_weight) or frequency_weight <= 0:
        raise ValueError("frequency_weight must be finite and positive")
    rows = []
    for (bank, gamma), group in samples.groupby(["bank", "gamma"], sort=True):
        if not group["sample"].is_unique:
            raise ValueError("duplicate samples for a candidate")
        metrics = [c for c in group if c not in (
            "bank", "sample", "gamma", "simulation_wall_s", "simulation_cpu_s", "objective_eur")]
        values = {name: group[name].to_numpy() for name in metrics}
        values["objective_eur"] = (frequency_weight * group.frequency_error_hz2_s
                                   + group.net_payment_eur).to_numpy()
        row = {"bank": bank, "gamma": float(gamma), "samples": len(group)}
        for name, vector in values.items():
            if not np.all(np.isfinite(vector)):
                raise ValueError(f"non-finite sample metric: {name}")
            se = float(np.std(vector, ddof=1) / np.sqrt(len(vector))) if len(vector) > 1 else None
            row.update({f"{name}_mean": float(np.mean(vector)), f"{name}_se": se,
                        f"{name}_ci95_halfwidth": (
                            float(student_t.ppf(.975, len(vector) - 1) * se) if se is not None else None)})
        rows.append(row)
    return pd.DataFrame(rows)


def summarize_paired_tso_objectives(samples: pd.DataFrame, frequency_weight: float,
                                   reference_gamma: float) -> pd.DataFrame:
    """Pointwise paired mean differences, preserving error/payment covariance.

    Reference and policies must be fixed before observing this evaluation bank.
    Intervals are not simultaneous and do not certify practical equivalence or
    regret relative to the unknown population optimum.
    """
    summary = summarize_weighted_tso_samples(samples, frequency_weight)
    if samples.bank.nunique() != 1:
        raise ValueError("paired objective assessment requires one shared bank")
    reference = samples[samples.gamma == reference_gamma].set_index("sample").sort_index()
    if len(reference) < 2 or not reference.index.is_unique:
        raise ValueError("reference requires at least two unique samples")
    reference_j = frequency_weight * reference.frequency_error_hz2_s + reference.net_payment_eur
    rows = []
    for gamma, frame in samples.groupby("gamma", sort=True):
        frame = frame.set_index("sample").sort_index()
        if not frame.index.is_unique or not frame.index.equals(reference.index):
            raise ValueError("paired policies must have identical sample IDs")
        differences = frequency_weight * frame.frequency_error_hz2_s + frame.net_payment_eur - reference_j
        se = float(differences.std(ddof=1) / np.sqrt(len(frame)))
        rows.append({"gamma": gamma, "reference_gamma": reference_gamma,
                     "delta_objective_eur_mean": float(differences.mean()),
                     "delta_objective_eur_se": se,
                     "delta_objective_eur_ci95_halfwidth": float(student_t.ppf(.975, len(frame)-1) * se),
                     "delta_objective_percent_of_reference": (
                         100 * float(differences.mean()) / float(reference_j.mean())
                         if reference_j.mean() != 0 else np.nan)})
    return summary.merge(pd.DataFrame(rows), on="gamma", validate="one_to_one")


def summarize_tso_samples(samples: pd.DataFrame) -> pd.DataFrame:
    """Pointwise Monte Carlo intervals and paired changes; no optimum is selected.

    Student-t intervals describe uncertainty in means, not bounds on individual
    realizations. Frontier membership refers only to the estimated candidate set.
    """
    metrics = [c for c in samples if c not in ("bank", "sample", "gamma", "simulation_wall_s", "simulation_cpu_s")]
    rows = []
    for bank, frame in samples.groupby("bank", sort=False):
        previous = None
        baseline = frame[frame.gamma == 0].set_index("sample").sort_index()
        if baseline.empty or not baseline.index.is_unique:
            raise ValueError("each bank needs unique gamma-zero baseline samples")
        for gamma, group in frame.groupby("gamma", sort=True):
            group = group.set_index("sample").sort_index()
            if not group.index.equals(baseline.index) or len(group) < 2:
                raise ValueError("candidate comparisons require the same samples and N >= 2")
            values = {c: group[c] for c in metrics}
            values["paired_error_reduction_hz2_s"] = baseline.frequency_error_hz2_s - group.frequency_error_hz2_s
            for metric, sign in (("nadir_sampled_hz", 1), ("final_absolute_error_hz", -1),
                                 ("rocof_max_abs_hz_per_s", -1)):
                if metric in group:
                    values[f"paired_improvement_{metric}"] = sign * (group[metric] - baseline[metric])
                    if previous is not None:
                        values[f"incremental_improvement_{metric}"] = sign * (group[metric] - previous[metric])
            if previous is not None:
                values["incremental_error_reduction_hz2_s"] = previous.frequency_error_hz2_s - group.frequency_error_hz2_s
                values["incremental_payment_eur"] = group.net_payment_eur - previous.net_payment_eur
            row = {"bank": bank, "gamma": gamma, "samples": len(group)}
            for name, vector in values.items():
                if not np.all(np.isfinite(vector)):
                    raise ValueError(f"non-finite sample metric: {name}")
                se = float(vector.std(ddof=1) / np.sqrt(len(vector)))
                row.update({f"{name}_mean": float(vector.mean()), f"{name}_se": se,
                            f"{name}_ci95_halfwidth": float(student_t.ppf(.975, len(vector) - 1) * se)})
            rows.append(row)
            previous = group
    summary = pd.DataFrame(rows)
    summary["empirical_pareto"] = False
    for _, frame in summary.groupby("bank"):
        error, payment = frame.frequency_error_hz2_s_mean, frame.net_payment_eur_mean
        for index in frame.index:
            dominated = ((error <= error[index]) & (payment <= payment[index])
                         & ((error < error[index]) | (payment < payment[index]))).any()
            summary.loc[index, "empirical_pareto"] = not dominated
        for metric, maximize in (("frequency_error_hz2_s", False), ("nadir_sampled_hz", True),
                                 ("final_absolute_error_hz", False), ("rocof_max_abs_hz_per_s", False)):
            if f"{metric}_mean" in frame:
                column = f"empirical_pareto_{metric}"
                if column not in summary:
                    summary[column] = False
                summary.loc[frame.index, column] = empirical_pareto_flags(
                    frame.net_payment_eur_mean, frame[f"{metric}_mean"], maximize=maximize)
    return summary


def empirical_pareto_flags(cost, performance, *, maximize: bool = False) -> np.ndarray:
    """Nondominated estimated points for one metric; equal points remain tied."""
    cost, performance = np.asarray(cost, dtype=float), np.asarray(performance, dtype=float)
    if cost.ndim != 1 or performance.shape != cost.shape or not (
            np.all(np.isfinite(cost)) and np.all(np.isfinite(performance))):
        raise ValueError("Pareto coordinates must be matching finite vectors")
    loss = -performance if maximize else performance
    return np.array([not np.any((cost <= c) & (loss <= p) & ((cost < c) | (loss < p)))
                     for c, p in zip(cost, loss, strict=True)])


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
    metrics = {
        **frequency_performance_metrics(controlled),
        "final_no_fcr_frequency_hz": float(numerical_no_fcr.frequency_hz[-1]),
        "analytical_no_fcr_max_error_hz": analytical_error,
        "refined_frequency_max_error_hz": frequency_error,
        "refined_provider_max_error_mw": response_error,
    }
    if (controlled.fi is None) != (refined.fi is None):
        raise ValueError("refinement must use the same FI providers")
    if controlled.fi is not None:
        fi = controlled.fi
        if fi.provider_ids != refined.fi.provider_ids:
            raise ValueError("refinement must use the same FI identities")
        clocks = [tuple((r["provider_id"], r["start_time_s"], r["end_time_s"]) for r in f.intervals)
                  for f in (fi, refined.fi)]
        if clocks[0] != clocks[1]:
            raise ValueError("refinement must keep the FI decision clock fixed")
        for accounting in (fi, refined.fi):
            for values in (accounting.gross_rewards_eur, accounting.gross_penalties_eur,
                           accounting.activation_cost_eur):
                if (not np.all(np.isfinite(values)) or np.any(values < -1e-8)
                        or np.any(np.diff(values, axis=0) < -1e-8)):
                    raise RuntimeError("invalid FI cumulative ledger")
            for values in (accounting.predicted_settlement_eur, accounting.target_benchmark_eur,
                           accounting.payment_rate_eur_per_s):
                if not np.all(np.isfinite(values)):
                    raise RuntimeError("non-finite FI payment or prediction")
        prediction_error = float(np.max(np.abs(fi.net_settlement_eur - fi.predicted_settlement_eur)))
        refinement_error = max(float(np.max(np.abs(a - b))) for a, b in (
            (fi.gross_rewards_eur, refined.fi.gross_rewards_eur),
            (fi.gross_penalties_eur, refined.fi.gross_penalties_eur),
            (fi.activation_cost_eur, refined.fi.activation_cost_eur),
        ))
        scale = max(1.0, float(np.max(fi.gross_rewards_eur + fi.gross_penalties_eur + fi.activation_cost_eur)))
        if prediction_error > 1e-6 * scale or refinement_error > 1e-6 * scale:
            raise RuntimeError("FI prediction or financial refinement tolerance exceeded")
        metrics.update(fi_prediction_max_error_eur=prediction_error,
                       fi_refined_ledger_max_error_eur=refinement_error)
    return metrics
