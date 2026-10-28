"""Common-gamma search and experiments conditional on a cleared FCR market."""

from __future__ import annotations

from dataclasses import dataclass, replace
from time import perf_counter, process_time
from typing import Callable

import numpy as np
import pandas as pd

from .frequency import LinearFrequencyParameters
from .metrics import summarize_tso_trajectory, summarize_tso_samples, summarize_weighted_tso_samples
from .providers import FIProviderParameters
from .simulation import (AggregateFCRTrajectory, ClearedFCRBlock, realize_fcr_block,
                         simulate_fcr_power_step)


def _validate_bank_settings(settings: dict) -> None:
    for key in ("design_samples", "validation_samples", "seed"):
        value = settings[key]
        if type(value) is not int or value < (0 if key == "seed" else 2):
            raise ValueError(f"{key} must be an integer >= {0 if key == 'seed' else 2}")


def validate_sweep_settings(settings: dict) -> tuple[float, ...]:
    allowed = {"gamma_values", "design_samples", "validation_samples", "seed"}
    if set(settings) != allowed:
        raise ValueError(f"tso requires exactly {sorted(allowed)}")
    gammas = tuple(float(g) for g in settings["gamma_values"])
    if (len(gammas) < 2 or not all(np.isfinite(g) and g >= 0 for g in gammas)
            or len(set(gammas)) != len(gammas) or 0 not in gammas):
        raise ValueError("gamma_values must be distinct, finite, nonnegative and include zero")
    _validate_bank_settings(settings)
    return tuple(sorted(gammas))


def make_availability_banks(cleared: ClearedFCRBlock, settings: dict) -> pd.DataFrame:
    """Draw the configured bounded laws after clearing, in fixed provider order."""
    _validate_bank_settings(settings)
    streams = np.random.SeedSequence(settings["seed"]).spawn(2)
    records = []
    for bank, stream in zip(("design", "validation"), streams, strict=True):
        rng = np.random.default_rng(stream)
        for sample in range(settings[f"{bank}_samples"]):
            records.append({"bank": bank, "sample": sample, **{
                p.provider_id: p.availability.sample_mw(rng) for p in cleared.providers}})
    return pd.DataFrame(records)


def simulate_common_gamma(block, parameters, gamma: float, simulation_options: dict, *, refined=False):
    """Change only FI gamma; preserve awards, capabilities, gains and dynamics."""
    responses = tuple(replace(p, gamma_eur_per_hz_mw_s=gamma)
                      if isinstance(p, FIProviderParameters) else p
                      for p in block.response_parameters)
    options = dict(simulation_options)
    if refined:
        options.update(maximum_solver_step_s=options["maximum_solver_step_s"] / 2,
                       relative_tolerance=options["relative_tolerance"] / 10,
                       absolute_tolerance=options["absolute_tolerance"] / 10)
    return simulate_fcr_power_step(parameters, responses,
                                  provider_operating_power_mw=block.operating_powers_mw, **options)


def _realize_banks(cleared, banks, settings):
    if "fi" not in cleared.participation_modes:
        raise ValueError("TSO requires at least one eligible FI provider; select an FI scenario")
    identities = [p.provider_id for p in cleared.providers]
    if set(banks.columns) != {"bank", "sample", *identities}:
        raise ValueError("sample bank columns must match provider identities")
    if set(banks.bank) != {"design", "validation"}:
        raise ValueError("both design and validation banks are required")
    blocks = {}
    for bank in ("design", "validation"):
        draws = banks[banks.bank == bank].sort_values("sample")
        if draws["sample"].tolist() != list(range(settings[f"{bank}_samples"])):
            raise ValueError("sample bank must have each configured sample exactly once")
        blocks[bank] = [realize_fcr_block(cleared, {i: row[i] for i in identities})
                        for row in draws.to_dict("records")]
    return blocks


def _check_refinement(coarse, fine, event_time_s):
    summarize_tso_trajectory(fine, event_time_s)
    frequency_diff = float(np.max(abs(coarse.frequency_hz - fine.frequency_hz)))
    response_diff = float(np.max(abs(coarse.provider_response_mw - fine.provider_response_mw)))
    error_diff = abs(float(coarse.integrated_squared_frequency_error_hz2_s[-1]
                           - fine.integrated_squared_frequency_error_hz2_s[-1]))
    payment_diff = float(np.max(abs(coarse.fi.net_settlement_eur - fine.fi.net_settlement_eur)))
    cost_diff = float(np.max(abs(coarse.fi.activation_cost_eur - fine.fi.activation_cost_eur)))
    scale = max(1., float(np.max(fine.fi.gross_rewards_eur + fine.fi.gross_penalties_eur
                                + fine.fi.activation_cost_eur)))
    if (frequency_diff > 1e-6 or response_diff > 1e-5 or error_diff > 1e-7
            or max(payment_diff, cost_diff) > 1e-6 * scale):
        raise RuntimeError("TSO numerical refinement exceeded tolerance")
    return {"frequency_max_diff_hz": frequency_diff, "response_max_diff_mw": response_diff,
            "squared_error_diff_hz2_s": error_diff, "payment_max_diff_eur": payment_diff,
            "cost_max_diff_eur": cost_diff}


def evaluate_gamma_curve(
    cleared: ClearedFCRBlock, parameters: LinearFrequencyParameters,
    *, banks: pd.DataFrame, settings: dict, simulation_options: dict,
    on_candidate: Callable[[pd.DataFrame, dict], None] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[dict]]:
    """Run the existing dynamics once per gamma/draw, then limited refinement.

    Fail fast on any failed realization; never average a selectively surviving
    subset. A callback can checkpoint completed candidates and report progress.
    """
    gammas = validate_sweep_settings(settings)
    blocks = _realize_banks(cleared, banks, settings)

    # Numerical checks span zero, a historical reference and the upper endpoint;
    # use both extremes of total FI availability in the design bank.
    fi_ids = [p.provider_id for p, mode in zip(cleared.providers, cleared.participation_modes)
              if mode == "fi"]
    design = banks[banks.bank == "design"].set_index("sample")
    fi_capacity = design[fi_ids].sum(axis=1)
    check_samples = {int(fi_capacity.idxmin()), int(fi_capacity.idxmax())}
    check_gammas = {gammas[0], min(gammas, key=lambda g: abs(g - 50)), gammas[-1]}
    checks, records, timings = [], [], []
    check_runs = {}
    for bank in ("design", "validation"):
        for gamma in gammas:
            candidate_start = perf_counter()
            for sample, block in enumerate(blocks[bank]):
                started = perf_counter()
                try:
                    trajectory = simulate_common_gamma(block, parameters, gamma, simulation_options)
                    values = summarize_tso_trajectory(trajectory, simulation_options["start_time_s"])
                except Exception as error:
                    raise RuntimeError(f"TSO evaluation failed: bank={bank}, gamma={gamma}, sample={sample}") from error
                records.append({"bank": bank, "sample": sample, "gamma": gamma,
                                **values, "simulation_wall_s": perf_counter() - started})
                if bank == "design" and sample in check_samples and gamma in check_gammas:
                    check_runs[(sample, gamma)] = trajectory
            timing = {"phase": bank, "gamma": gamma, "simulations": len(blocks[bank]),
                      "wall_s": perf_counter() - candidate_start}
            timings.append(timing)
            if on_candidate is not None:
                on_candidate(pd.DataFrame(records), timing)

    started = perf_counter()
    for (sample, gamma), coarse in check_runs.items():
        fine = simulate_common_gamma(blocks["design"][sample], parameters, gamma,
                                     simulation_options, refined=True)
        checks.append({"sample": sample, "gamma": gamma,
                       **_check_refinement(coarse, fine, simulation_options["start_time_s"])})
    timings.append({"phase": "numerical_refinement", "simulations": len(check_runs),
                    "wall_s": perf_counter() - started})
    samples = pd.DataFrame(records)
    return samples, summarize_tso_samples(samples), pd.DataFrame(checks), timings


def validate_optimizer_settings(settings: dict) -> None:
    required = {"frequency_weight_eur_per_hz2_s", "gamma_max", "grid_points",
                "gamma_tolerance", "max_refinement_rounds", "design_samples",
                "validation_samples", "seed"}
    if set(settings) != required:
        raise ValueError(f"tso_optimizer requires exactly {sorted(required)}; supply an explicit frequency weight")
    _validate_bank_settings(settings)
    for key in ("frequency_weight_eur_per_hz2_s", "gamma_max", "gamma_tolerance"):
        value = settings[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or value <= 0:
            raise ValueError(f"{key} must be finite and positive")
    if settings["gamma_tolerance"] >= settings["gamma_max"]:
        raise ValueError("gamma_tolerance must be smaller than gamma_max")
    for key, minimum in (("grid_points", 3), ("max_refinement_rounds", 1)):
        if type(settings[key]) is not int or settings[key] < minimum:
            raise ValueError(f"{key} must be an integer >= {minimum}")


def search_common_gamma(objective: Callable[[float], float], settings: dict) -> dict:
    """Bounded grid plus midpoint refinement around every observed local minimum.

    No convexity/unimodality assumption or global-optimality certificate. Narrow
    unobserved wells can be missed between grid points. Exact ties select the
    smaller gamma; cached candidates are evaluated once, including endpoints.
    """
    validate_optimizer_settings(settings)
    upper, tolerance = settings["gamma_max"], settings["gamma_tolerance"]
    grid = sorted(set(np.linspace(0., upper, settings["grid_points"]).tolist())
                  | {g for g in (50., 200.) if g <= upper})
    values = {}

    def evaluate(gamma):
        value = float(objective(gamma))
        if not np.isfinite(value):
            raise RuntimeError(f"non-finite TSO objective at gamma={gamma}")
        values[gamma] = value

    def intervals_to_refine():
        points = sorted(values)
        best = min(points, key=lambda g: (values[g], g))
        intervals = set()
        for i, gamma in enumerate(points):
            neighbors = points[max(0, i - 1):i] + points[i + 1:i + 2]
            local_minimum = (all(values[gamma] <= values[g] for g in neighbors)
                             and any(values[gamma] < values[g] for g in neighbors))
            if local_minimum or gamma == best:
                for g in neighbors:
                    if abs(g - gamma) > tolerance:
                        intervals.add(tuple(sorted((g, gamma))))
        return sorted(intervals)

    for gamma in grid:
        evaluate(gamma)
    rounds = 0
    for _ in range(settings["max_refinement_rounds"]):
        intervals = intervals_to_refine()
        if not intervals:
            break
        midpoints = sorted({a + (b - a) / 2 for a, b in intervals} - values.keys())
        if not midpoints:
            break
        for gamma in midpoints:
            evaluate(gamma)
        rounds += 1
    selected = min(values, key=lambda g: (values[g], g))
    points = sorted(values)
    index = points.index(selected)
    return {"gamma_selected": selected, "design_objective_eur": values[selected],
            "domain": [0., upper], "initial_grid": grid, "gamma_tolerance": tolerance,
            "refinement_rounds": rounds, "candidate_count": len(values),
            "local_resolution_reached": not intervals_to_refine(),
            "selected_neighbors": points[max(0, index - 1):min(len(points), index + 2)],
            "at_lower_bound": selected == 0., "at_upper_bound": selected == upper,
            "status": "best tested candidate; global optimum not certified"}


@dataclass(frozen=True)
class TSOOptimizationResult:
    search: dict
    samples: pd.DataFrame
    summary: pd.DataFrame
    numerical_checks: pd.DataFrame
    timings: list[dict]
    delivery_availability: dict[str, float]
    delivery_trajectory: AggregateFCRTrajectory


def optimize_common_gamma(
    cleared: ClearedFCRBlock, parameters: LinearFrequencyParameters, *, banks: pd.DataFrame,
    settings: dict, simulation_options: dict,
    on_candidate: Callable[[pd.DataFrame, dict], None] | None = None,
) -> TSOOptimizationResult:
    """Select on design draws, assess on validation, then draw one delivery event.

    Validation never enters the search. Any simulation/check failure invalidates
    the run instead of removing samples or treating failed candidates as costly.
    """
    validate_optimizer_settings(settings)
    blocks = _realize_banks(cleared, banks, settings)
    weight = settings["frequency_weight_eur_per_hz2_s"]
    records, timings, checks = [], [], []
    # Retain only the two extreme design-FI-capacity trajectories at the current
    # best candidate; avoid keeping all trajectories from the numerical search.
    fi_ids = [p.provider_id for p, mode in zip(cleared.providers, cleared.participation_modes)
              if mode == "fi"]
    design = banks[banks.bank == "design"].set_index("sample")
    capacity = design[fi_ids].sum(axis=1)
    check_samples = {int(capacity.idxmin()), int(capacity.idxmax())}
    best_key, best_trajectories = (float("inf"), float("inf")), {}

    def evaluate(bank, gamma, event_blocks):
        start, cpu = perf_counter(), process_time()
        outcomes, retained = [], {}
        for sample, block in enumerate(event_blocks):
            before, cpu_before = perf_counter(), process_time()
            try:
                trajectory = simulate_common_gamma(block, parameters, gamma, simulation_options)
                values = summarize_tso_trajectory(trajectory, simulation_options["start_time_s"])
                objective = weight * values["frequency_error_hz2_s"] + values["net_payment_eur"]
                if not np.isfinite(objective):
                    raise RuntimeError("non-finite weighted objective")
            except Exception as error:
                raise RuntimeError(f"TSO evaluation failed: bank={bank}, gamma={gamma}, sample={sample}") from error
            records.append({"bank": bank, "sample": sample, "gamma": gamma, **values,
                            "objective_eur": objective, "simulation_wall_s": perf_counter() - before,
                            "simulation_cpu_s": process_time() - cpu_before})
            outcomes.append(objective)
            if (bank == "design" and sample in check_samples) or bank == "delivery":
                retained[sample] = trajectory
        timing = {"phase": bank, "gamma": gamma, "simulations": len(event_blocks),
                  "wall_s": perf_counter() - start, "cpu_s": process_time() - cpu}
        timings.append(timing)
        if on_candidate is not None:
            on_candidate(pd.DataFrame(records), timing)
        return float(np.mean(outcomes)), retained

    def objective(gamma):
        nonlocal best_key, best_trajectories
        value, trajectories = evaluate("design", gamma, blocks["design"])
        if (value, gamma) < best_key:
            best_key, best_trajectories = (value, gamma), trajectories
        return value

    started, cpu = perf_counter(), process_time()
    search = search_common_gamma(objective, settings)
    search.update(wall_s=perf_counter() - started, cpu_s=process_time() - cpu)
    gamma = search["gamma_selected"]
    start, cpu = perf_counter(), process_time()
    for sample, coarse in sorted(best_trajectories.items()):
        fine = simulate_common_gamma(blocks["design"][sample], parameters, gamma,
                                     simulation_options, refined=True)
        checks.append({"bank": "design", "sample": sample, "gamma": gamma,
                       **_check_refinement(coarse, fine, simulation_options["start_time_s"])})
    timings.append({"phase": "numerical_refinement", "simulations": len(checks),
                    "wall_s": perf_counter() - start, "cpu_s": process_time() - cpu})
    evaluate("validation", gamma, blocks["validation"])
    # Stream 2 is disjoint from design/validation streams 0/1. Draw only AFTER
    # selection and validation; never condition gamma on actual availability.
    rng = np.random.default_rng(np.random.SeedSequence(settings["seed"]).spawn(3)[2])
    delivery = {p.provider_id: p.availability.sample_mw(rng) for p in cleared.providers}
    _, delivered = evaluate("delivery", gamma, [realize_fcr_block(cleared, delivery)])
    samples = pd.DataFrame(records)
    return TSOOptimizationResult(search, samples, summarize_weighted_tso_samples(samples, weight),
                                 pd.DataFrame(checks), timings, delivery, delivered[0])


def optimize_nested_gamma_banks(cleared, parameters, *, design, sample_sizes, settings,
                               simulation_options, on_level=None):
    """Repeat the same search on nested IID prefixes, caching exact gamma/draw pairs.

    No held-out data enters this function. Cached simulations remain conditional
    on this one fixed market, model and seed; larger N never reuses an average
    computed on fewer draws. Numerical checks cover the final selection and both
    extremes of FI availability in the largest bank.
    """
    validate_optimizer_settings(settings)
    sizes = list(sample_sizes)
    if (not sizes or any(type(n) is not int or n < 2 for n in sizes)
            or sizes != sorted(set(sizes))):
        raise ValueError("sample_sizes must be strictly increasing integers >= 2")
    ids = [p.provider_id for p in cleared.providers]
    if (set(design.columns) != {"sample", *ids}
            or design["sample"].tolist() != list(range(sizes[-1]))):
        raise ValueError("design must contain exactly the largest nested bank in sample order")
    fi_ids = [p.provider_id for p, mode in zip(cleared.providers, cleared.participation_modes)
              if mode == "fi"]
    if not fi_ids:
        raise ValueError("stability study requires an eligible FI provider")
    blocks = [realize_fcr_block(cleared, {i: row[i] for i in ids})
              for row in design.to_dict("records")]
    cache, selections, checks = {}, [], []
    weight = settings["frequency_weight_eur_per_hz2_s"]
    for size in sizes:
        started, cpu = perf_counter(), process_time()
        previous_count = len(cache)

        def objective(gamma):
            values = []
            for sample, block in enumerate(blocks[:size]):
                key = (gamma, sample)
                if key not in cache:
                    before, cpu_before = perf_counter(), process_time()
                    try:
                        trajectory = simulate_common_gamma(block, parameters, gamma, simulation_options)
                        metrics = summarize_tso_trajectory(trajectory, simulation_options["start_time_s"])
                    except Exception as error:
                        raise RuntimeError(f"design failed: seed={settings['seed']}, N={size}, "
                                           f"gamma={gamma}, sample={sample}") from error
                    cache[key] = {"sample": sample, "gamma": gamma, **metrics,
                                  "objective_eur": weight * metrics["frequency_error_hz2_s"] + metrics["net_payment_eur"],
                                  "simulation_wall_s": perf_counter() - before,
                                  "simulation_cpu_s": process_time() - cpu_before}
                values.append(cache[key]["objective_eur"])
            return float(np.mean(values))

        search = search_common_gamma(objective, {**settings, "design_samples": size})
        search.update(seed=settings["seed"], design_samples=size,
                      new_simulations=len(cache) - previous_count,
                      wall_s=perf_counter() - started, cpu_s=process_time() - cpu)
        selections.append(search)
        if on_level:
            on_level(search, pd.DataFrame(cache.values()))
    gamma = selections[-1]["gamma_selected"]
    capacities = design[fi_ids].sum(axis=1)
    for sample in sorted({int(capacities.idxmin()), int(capacities.idxmax())}):
        before, cpu_before = perf_counter(), process_time()
        coarse = simulate_common_gamma(blocks[sample], parameters, gamma, simulation_options)
        fine = simulate_common_gamma(blocks[sample], parameters, gamma, simulation_options, refined=True)
        checks.append({"seed": settings["seed"], "sample": sample, "gamma": gamma,
                       **_check_refinement(coarse, fine, simulation_options["start_time_s"]),
                       "wall_s": perf_counter() - before, "cpu_s": process_time() - cpu_before})
    return selections, pd.DataFrame(cache.values()), pd.DataFrame(checks)


def evaluate_fixed_gamma_bank(cleared, parameters, *, draws, gamma, frequency_weight,
                             simulation_options):
    """Assess a frozen policy on a common held-out bank; never select a policy."""
    ids = [p.provider_id for p in cleared.providers]
    records = []
    for row in draws.to_dict("records"):
        before, cpu = perf_counter(), process_time()
        try:
            block = realize_fcr_block(cleared, {i: row[i] for i in ids})
            trajectory = simulate_common_gamma(block, parameters, gamma, simulation_options)
            metrics = summarize_tso_trajectory(trajectory, simulation_options["start_time_s"])
        except Exception as error:
            raise RuntimeError(f"holdout failed: gamma={gamma}, sample={row['sample']}") from error
        records.append({"bank": "holdout", "sample": row["sample"], "gamma": gamma, **metrics,
                        "objective_eur": frequency_weight * metrics["frequency_error_hz2_s"] + metrics["net_payment_eur"],
                        "simulation_wall_s": perf_counter() - before, "simulation_cpu_s": process_time() - cpu})
    return pd.DataFrame(records)
