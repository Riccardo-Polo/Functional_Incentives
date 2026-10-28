"""Run provider decisions, FCR capacity allocation and aggregate frequency dynamics."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import platform
from time import perf_counter, process_time
import tomllib

import numpy as np
import pandas as pd

from .availability import GaussianReserve
from .decision import evaluate_offer
from .frequency import LinearFrequencyParameters, damping_from_load_sensitivity, analytical_no_fcr
from .grid import AndesOperatingPoint, FleetGenerator, solve_operating_point
from .market import FCRCapacityOffer
from .metrics import (summarize_fcr_trajectories, summarize_tso_trajectory,
                      summarize_weighted_tso_samples, summarize_paired_tso_objectives)
from .plotting import (plot_frequency_comparison, plot_provider_responses,
                       plot_scenario_comparison, plot_fi_financials, plot_tso_tradeoff,
                       plot_tso_weight_comparison, plot_tso_stability)
from .providers import BlockProvider, QuadraticCommitmentCost
from .simulation import AggregateFCRTrajectory, clear_fcr_block, realize_fcr_block, simulate_fcr_power_step
from .tso import (validate_sweep_settings, make_availability_banks, evaluate_gamma_curve,
                  validate_optimizer_settings, optimize_common_gamma, simulate_common_gamma,
                  optimize_nested_gamma_banks, evaluate_fixed_gamma_bank)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs/fcr.toml"
DEFAULT_OUTPUT_DIRECTORY = PROJECT_ROOT / "results/fcr"


def load_operating_point(config: dict) -> AndesOperatingPoint:
    """Select case/fleet parameters without introducing a second physical solver."""
    operating_point_mode = config["scenario"].get("operating_point", "bundled_pjm5")
    if operating_point_mode == "bundled_pjm5":
        operating_point = solve_operating_point()
    elif operating_point_mode == "configured_fleet":
        fleet = tuple(FleetGenerator(
            generator_id=str(raw["generator_id"]), bus_id=raw["bus_id"],
            technology=raw["technology"], rating_mva=float(raw["rating_mva"]),
            power_setpoint_mw=float(raw["power_setpoint_mw"]),
            inertia_s=float(raw["inertia_s"]),
            minimum_power_mw=float(raw["minimum_power_mw"]),
            maximum_power_mw=float(raw["maximum_power_mw"]),
            is_slack=raw.get("is_slack", False),
        ) for raw in config["providers"])
        operating_point = solve_operating_point(fleet)
    else:
        raise ValueError("unknown scenario operating_point mode")
    return operating_point


def load_block_providers(
    config: dict, operating_point: AndesOperatingPoint
) -> tuple[BlockProvider, ...]:
    """Join scenario inputs to solved powers and machine bases by generator ID."""
    if config["scenario"]["power_base"] != "machine_rating":
        raise ValueError("this experiment requires the explicit machine_rating power base")
    physical = {
        str(i): (power, rating)
        for i, power, rating in zip(
            operating_point.generator_ids, operating_point.generator_active_power_mw,
            operating_point.generator_ratings_mva, strict=True,
        )
    }
    raw_providers = config["providers"]
    fixed_decisions = config.get("decision", {}).get("mode", "optimize") == "fixed"
    specified = [str(raw["generator_id"]) for raw in raw_providers]
    if len(specified) != len(set(specified)) or set(specified) != set(physical):
        raise ValueError("configure exactly one provider per solved generator ID")
    providers = []
    for raw in raw_providers:
        if {"price_candidates_eur_per_mw_block", "quantity_candidates_mw"}.intersection(raw):
            raise ValueError("candidate grids are obsolete; configure bid price bounds and market.minimum_bid_mw")
        generator_id = str(raw["generator_id"])
        operating_power, power_base = physical[generator_id]
        providers.append(BlockProvider(
            provider_id=raw["provider_id"], generator_id=generator_id,
            operating_power_mw=operating_power,
            minimum_power_mw=float(raw["minimum_power_mw"]),
            maximum_power_mw=float(raw["maximum_power_mw"]),
            power_base_mw=power_base, time_constant_s=float(raw["time_constant_s"]),
            availability=GaussianReserve(
                mean_mw=float(raw["mean_mw"]), variance_mw2=float(raw["variance_mw2"]),
                capacity_mw=float(raw["reserve_capacity_mw"]),
                boundary_mode=config["availability"]["boundary_mode"],
            ),
            cost=QuadraticCommitmentCost(
                float(raw["linear_cost_eur_per_mw_block"]),
                float(raw["quadratic_cost_eur_per_mw2_block"]),
            ),
            penalty_eur_per_missing_mw_block=float(raw["penalty_eur_per_missing_mw_block"]),
            minimum_bid_price_eur_per_mw_block=float(
                raw.get("minimum_bid_price_eur_per_mw_block", 0.0) if fixed_decisions
                else raw["minimum_bid_price_eur_per_mw_block"]),
            maximum_bid_price_eur_per_mw_block=float(
                raw.get("maximum_bid_price_eur_per_mw_block", 0.0) if fixed_decisions
                else raw["maximum_bid_price_eur_per_mw_block"]),
            reserve_safety_margin_fraction=float(raw.get("reserve_safety_margin_fraction", 0.0)),
            technology=raw.get("technology", "unspecified"),
            gain_mw_per_hz=(float(raw["gain_mw_per_hz"]) if "gain_mw_per_hz" in raw else None),
            participation=raw.get("participation", "fcr"),
            fi_gamma_eur_per_hz_mw_s=(float(raw["fi_gamma_eur_per_hz_mw_s"])
                                    if "fi_gamma_eur_per_hz_mw_s" in raw else None),
            fi_rho_eur_per_mw2_s=(float(raw["fi_rho_eur_per_mw2_s"])
                                if "fi_rho_eur_per_mw2_s" in raw else None),
        ))
    return tuple(providers)


def save_fi_accounting(trajectory: AggregateFCRTrajectory, output_dir: Path) -> dict:
    """Save FI-only ledgers; event transfers are separate from FCR block payments."""
    fi = trajectory.fi
    names = ("target_benchmark_eur", "predicted_settlement_eur", "gross_rewards_eur",
             "gross_penalties_eur", "net_settlement_eur", "activation_cost_eur", "profit_eur")
    totals = {name: 0.0 for name in names}
    rows, intervals = [], []
    series = {"time_s": trajectory.time_s}
    for name in ("payment_rate_eur_per_s", *names):
        values = np.zeros((len(trajectory.time_s), 0)) if fi is None else getattr(fi, name)
        series[f"total_{name}"] = np.sum(values, axis=1)
        if name in totals:
            totals[name] = float(np.sum(values[-1]))
        if fi is not None:
            for j, identity in enumerate(fi.provider_ids):
                series[f"{identity}_{name}"] = values[:, j]
    if fi is not None:
        intervals = fi.intervals
        for j, identity in enumerate(fi.provider_ids):
            series[f"{identity}_sampled_frequency_error_hz"] = fi.sampled_frequency_error_hz[:, j]
            rows.append({"provider_id": identity, **{name: float(getattr(fi, name)[-1, j]) for name in names}})
    pd.DataFrame(rows, columns=("provider_id", *names)).to_csv(output_dir / "fi_outcomes.csv", index=False)
    pd.DataFrame(intervals, columns=(None if intervals else (
        "provider_id", "start_time_s", "end_time_s", "sampled_frequency_error_hz",
        "initial_response_mw", "target_mw", "final_response_mw", "target_benchmark_eur",
        "predicted_settlement_eur", "predicted_activation_cost_eur", "predicted_profit_eur",
        "gross_rewards_eur", "gross_penalties_eur", "net_settlement_eur", "activation_cost_eur", "profit_eur"))).to_csv(
            output_dir / "fi_decisions.csv", index=False)
    pd.DataFrame(series).to_csv(output_dir / "fi_time_series.csv", index=False)
    return {"provider_count": 0 if fi is None else len(fi.provider_ids), **totals}


def prepare_experiment(config: dict):
    """Solve the physical point and market once, before any availability draw."""
    scenario = config["scenario"]
    block_hours = float(config["market"]["block_duration_hours"])
    if not np.isfinite(block_hours) or block_hours <= 0:
        raise ValueError("block_duration_hours must be finite and positive")
    operating_point = load_operating_point(config)
    providers = load_block_providers(config, operating_point)
    decision_mode = config.get("decision", {}).get("mode", "optimize")
    if decision_mode not in ("optimize", "fixed"):
        raise ValueError("decision.mode must be optimize or fixed")
    fixed_offers = None
    if decision_mode == "fixed":
        fixed_offers = tuple(FCRCapacityOffer(
            raw["provider_id"], float(raw["fixed_bid_eur_per_mw_block"]),
            float(raw["fixed_offer_mw"]),
        ) for raw in config["providers"])
    market_mode = config["market"].get("mode", "clear")
    if market_mode not in ("clear", "fixed"):
        raise ValueError("market.mode must be clear or fixed")
    fixed_awards = None
    fixed_price = None
    if market_mode == "fixed":
        fixed_awards = {raw["provider_id"]: float(raw["fixed_award_mw"])
                        for raw in config["providers"]}
        fixed_price = float(config["market"]["fixed_price_eur_per_mw_block"])
    cleared = clear_fcr_block(
        providers, requirement_mw=float(config["market"]["requirement_mw"]),
        nominal_frequency_hz=operating_point.nominal_frequency_hz,
        droop_pu=float(scenario["droop_pu"]),
        minimum_bid_mw=float(config["market"].get("minimum_bid_mw", 1.0)),
        quantity_tolerance_mw=float(config.get("decision", {}).get("quantity_tolerance_mw", 1e-8)),
        fixed_offers=fixed_offers, fixed_awards_mw=fixed_awards,
        fixed_price_eur_per_mw_block=fixed_price,
    )
    parameters = LinearFrequencyParameters(
        nominal_frequency_hz=operating_point.nominal_frequency_hz,
        equivalent_inertia_s=operating_point.equivalent_inertia_s,
        synchronous_rating_mva=operating_point.total_synchronous_rating_mva,
        load_damping_mw_per_hz=damping_from_load_sensitivity(
            load_mw=operating_point.total_load_mw,
            nominal_frequency_hz=operating_point.nominal_frequency_hz,
            per_unit_load_change_per_unit_frequency_change=float(
                scenario["load_frequency_sensitivity"]),
        ),
    )
    return operating_point, cleared, parameters


def run_experiment(config: dict, output_dir: Path, seed_override: int | None = None) -> dict:
    """Run one complete block and save its effective inputs and outputs."""
    scenario = config["scenario"]
    seed = scenario["seed"] if seed_override is None else seed_override
    if not isinstance(seed, int) or seed < 0:
        raise ValueError("seed must be a nonnegative integer")
    operating_point, cleared, parameters = prepare_experiment(config)
    rng = np.random.default_rng(seed)
    block = realize_fcr_block(cleared, {p.provider_id: p.availability.sample_mw(rng)
                                      for p in cleared.providers})
    block_hours = float(config["market"]["block_duration_hours"])
    decision_mode = config.get("decision", {}).get("mode", "optimize")
    market_mode = config["market"].get("mode", "clear")
    event = dict(
        power_deficit_mw=float(scenario["power_deficit_mw"]),
        start_time_s=float(scenario["event_time_s"]),
        final_time_s=float(scenario["final_time_s"]),
    )
    dt = float(scenario["output_time_step_s"])
    solver_step = float(scenario.get("maximum_solver_step_s", dt))
    fi_interval = config.get("fi", {}).get("decision_interval_s")
    if fi_interval is not None:
        fi_interval = float(fi_interval)
    controlled = simulate_fcr_power_step(
        parameters, block.response_parameters, **event, output_time_step_s=dt,
        provider_operating_power_mw=block.operating_powers_mw,
        maximum_solver_step_s=solver_step, fi_decision_interval_s=fi_interval,
    )
    numerical_no_fcr = simulate_fcr_power_step(
        parameters, (), **event, output_time_step_s=dt)
    reference = analytical_no_fcr(parameters, **event, time_step_s=dt)
    # Reuse the SAME draw, award and gain for solver refinement.
    refined = simulate_fcr_power_step(
        parameters, block.response_parameters, **event, output_time_step_s=dt,
        maximum_solver_step_s=solver_step/2, relative_tolerance=1e-10, absolute_tolerance=1e-12,
        fi_decision_interval_s=fi_interval,
    )
    metrics = summarize_fcr_trajectories(
        controlled, numerical_no_fcr, reference, refined)

    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for p, decision, settlement, response, mode in zip(
        block.providers, block.decisions, block.settlements, block.response_parameters,
        block.participation_modes,
        strict=True,
    ):
        rows.append({
            **asdict(settlement),
            "generator_id": p.generator_id,
            "technology": p.technology,
            "operating_power_mw": p.operating_power_mw,
            "power_base_mw": p.power_base_mw,
            "gain_mw_per_hz": getattr(response, "gain_mw_per_hz", 0.0),
            "configured_participation": p.participation,
            "participation": mode,
            "response_limit_mw": response.reserve_mw,
            "fi_gamma_eur_per_hz_mw_s": p.fi_gamma_eur_per_hz_mw_s,
            "fi_rho_eur_per_mw2_s": p.fi_rho_eur_per_mw2_s,
            "offered_mw": decision.offer.quantity_mw,
            "bid_eur_per_mw_block": decision.offer.price_eur_per_mw_block,
            "clearing_price_eur_per_mw_block": block.market.clearing_price_eur_per_mw_block,
            "gaussian_mean_mw": p.availability.mean_mw,
            "gaussian_variance_mw2": p.availability.variance_mw2,
            "physical_reserve_cap_mw": p.availability.capacity_mw,
            "reserve_safety_margin_fraction": p.reserve_safety_margin_fraction,
            "maximum_offer_mw": p.maximum_offer_mw,
            "penalty_eur_per_missing_mw_block": p.penalty_eur_per_missing_mw_block,
            "expected_offer_shortfall_mw": decision.expected_shortfall_mw,
            "offer_commitment_cost_eur": decision.commitment_cost_eur,
            "expected_offer_penalty_eur": decision.expected_penalty_eur,
            "expected_offer_reward_eur": decision.expected_reward_eur,
        })
    pd.DataFrame(rows).to_csv(output_dir / "market_outcomes.csv", index=False)
    scores = []
    for p, decision in zip(block.providers, block.decisions, strict=True):
        # Diagnostic values only: these points do not drive optimization.
        quantities = {0.0, decision.offer.quantity_mw}
        minimum_bid = float(config["market"].get("minimum_bid_mw", 1.0))
        if p.participation in ("fcr", "fcr_or_fi") and p.maximum_offer_mw >= minimum_bid:
            quantities.update((minimum_bid, p.maximum_offer_mw))
        price = (p.maximum_bid_price_eur_per_mw_block if decision_mode == "optimize"
                 else decision.offer.price_eur_per_mw_block)
        for q in sorted(quantities):
            score = evaluate_offer(
                FCRCapacityOffer(p.provider_id, price, q),
                availability=p.availability, cost=p.cost,
                penalty_eur_per_missing_mw_block=p.penalty_eur_per_missing_mw_block)
            scores.append({
                "provider_id": p.provider_id, "quantity_mw": q,
                "bid_eur_per_mw_block": score.offer.price_eur_per_mw_block,
                "selected": q == decision.offer.quantity_mw,
                "expected_shortfall_mw": score.expected_shortfall_mw,
                "commitment_cost_eur": score.commitment_cost_eur,
                "expected_penalty_eur": score.expected_penalty_eur,
                "expected_reward_eur": score.expected_reward_eur,
            })
    pd.DataFrame(scores).to_csv(output_dir / "offer_rewards.csv", index=False)
    series = {
        "time_s": controlled.time_s,
        "power_deficit_mw": controlled.power_deficit_mw,
        "frequency_fcr_hz": controlled.frequency_hz,
        "frequency_hz": controlled.frequency_hz,
        "rocof_hz_per_s": controlled.rocof_hz_per_s,
        "frequency_no_fcr_numerical_hz": numerical_no_fcr.frequency_hz,
        "frequency_no_fcr_analytical_hz": reference.frequency_hz,
        "total_response_mw": controlled.total_response_mw,
        "total_fcr_response_mw": controlled.total_fcr_response_mw,
        "total_fi_response_mw": controlled.total_fi_response_mw,
    }
    for j, provider_id in enumerate(controlled.provider_ids):
        series[f"{provider_id}_requested_mw"] = controlled.provider_requested_response_mw[:, j]
        series[f"{provider_id}_response_mw"] = controlled.provider_response_mw[:, j]
        series[f"{provider_id}_active_power_mw"] = controlled.provider_active_power_mw[:, j]
    pd.DataFrame(series).to_csv(output_dir / "time_series.csv", index=False)
    fi_totals = save_fi_accounting(controlled, output_dir)
    summary = {
        "seed": seed, "boundary_mode": config["availability"]["boundary_mode"],
        "block_duration_hours": block_hours,
        "price_units": "EUR/MW for the whole block",
        "offer_rule": ("continuous bounded reward optimization; no acceptance prediction"
                       if decision_mode == "optimize" else "explicit fixed offers"),
        "nominal_frequency_hz": parameters.nominal_frequency_hz,
        "operating_point_mode": scenario.get("operating_point", "bundled_pjm5"),
        "decision_mode": decision_mode, "market_mode": market_mode,
        "synchronous_rating_mva": operating_point.total_synchronous_rating_mva,
        "equivalent_inertia_s": operating_point.equivalent_inertia_s,
        "droop_pu": float(scenario["droop_pu"]),
        "clearing_price_eur_per_mw_block": block.market.clearing_price_eur_per_mw_block,
        "capacity_payments_eur": block.market.total_payment_eur,
        "fi": {"decision_interval_s": fi_interval, "settlement": "sampled frequency; delivered power",
               "gamma_policy": "fixed user input", "accounting_horizon_s": event["final_time_s"], **fi_totals},
        "metrics": metrics,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    effective_config = {**config, "scenario": {**scenario, "seed": seed}}
    (output_dir / "effective_config.json").write_text(
        json.dumps(effective_config, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (output_dir / "operating_point.json").write_text(
        json.dumps(vars(operating_point), indent=2, default=str, allow_nan=False)
        + "\n", encoding="utf-8")
    plot_frequency_comparison(controlled, numerical_no_fcr, reference,
                              output_dir / "frequency_comparison.png")
    plot_provider_responses(controlled, output_dir / "provider_responses.png",
                            reserve_symbol=r"q^{\mathrm{eff}}", participation_modes=block.participation_modes)
    plot_fi_financials(controlled, output_dir / "fi_financials.png")
    print("PJM 5-bus FCR/FI experiment with Gaussian reserve availability")
    print(f"  Boundary rule: {summary['boundary_mode']}; seed: {seed}")
    print(f"  Clearing price: {block.market.clearing_price_eur_per_mw_block:g} EUR/MW/block")
    print(f"  Decisions: {decision_mode}; allocation: {market_mode}")
    for row in rows:
        print(f"  {row['provider_id']} ({row['participation']}): bid={row['bid_eur_per_mw_block']:g}, "
              f"offer={row['offered_mw']:g} MW, award={row['awarded_mw']:g} MW, "
              f"available={row['available_mw']:.4f} MW, "
              f"K={row['gain_mw_per_hz']:.4f} MW/Hz, FCR block profit={row['profit_eur']:.2f} EUR")
    print(f"  FI event settlement: {fi_totals['net_settlement_eur']:.4f} EUR; "
          f"FI event profit: {fi_totals['profit_eur']:.4f} EUR")
    print(f"  Sampled nadir: {metrics['nadir_sampled_hz']:.6f} Hz")
    print(f"  Final frequency: {metrics['final_frequency_hz']:.6f} Hz")
    print(f"  Refined solver frequency difference: {metrics['refined_frequency_max_error_hz']:.3e} Hz")
    print(f"  Results: {output_dir}")
    return summary


def _tso_simulation_options(config: dict) -> dict:
    scenario = config["scenario"]
    return dict(power_deficit_mw=float(scenario["power_deficit_mw"]),
                start_time_s=float(scenario["event_time_s"]), final_time_s=float(scenario["final_time_s"]),
                output_time_step_s=float(scenario["output_time_step_s"]),
                maximum_solver_step_s=float(scenario.get("maximum_solver_step_s", scenario["output_time_step_s"])),
                fi_decision_interval_s=float(config["fi"]["decision_interval_s"]),
                relative_tolerance=1e-11, absolute_tolerance=1e-13)


def run_tso_experiment(config: dict, output_dir: Path, seed_override: int | None = None) -> dict:
    """Export a reproducible common-gamma curve, separate from single-event plots."""
    started = perf_counter()
    settings = deepcopy(config.get("tso", {}))
    if seed_override is not None:
        settings["seed"] = seed_override
    validate_sweep_settings(settings)
    operating_point, cleared, parameters = prepare_experiment(config)
    if "fi" not in cleared.participation_modes:
        raise ValueError("TSO sweep requires FI; use --scenario fi_endogenous")
    setup_s = perf_counter() - started
    bank_start = perf_counter()
    banks = make_availability_banks(cleared, settings)
    bank_s = perf_counter() - bank_start
    options = _tso_simulation_options(config)
    output_dir.mkdir(parents=True, exist_ok=True)

    def save_json(name, value):
        (output_dir / name).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    # Mark old results incomplete before replacing any reproducibility artifact.
    save_json("run_status.json", {"status": "running"})
    export_s = 0.
    export_start = perf_counter()
    banks.to_csv(output_dir / "availability_samples.csv", index=False)
    save_json("effective_config.json", {**config, "tso": settings})
    save_json("market.json", {"market": asdict(cleared.market),
                              "decisions": [asdict(d) for d in cleared.decisions],
                              "roles": dict(zip((p.provider_id for p in cleared.providers), cleared.participation_modes)),
                              "physical_parameters": asdict(parameters)})
    save_json("operating_point.json", {k: str(v) if isinstance(v, Path) else v
                                       for k, v in vars(operating_point).items()})
    export_s += perf_counter() - export_start
    print(f"TSO curve: {len(settings['gamma_values'])} gamma values, "
          f"{settings['design_samples']} design + {settings['validation_samples']} validation draws", flush=True)

    def checkpoint(samples, timing):
        nonlocal export_s
        before = perf_counter()
        samples.to_csv(output_dir / "sample_outcomes.csv", index=False)
        export_s += perf_counter() - before
        print(f"  {timing['phase']}: gamma={timing['gamma']:g}, "
              f"{timing['simulations']} simulations in {timing['wall_s']:.2f} s", flush=True)

    try:
        samples, summary, checks, timings = evaluate_gamma_curve(
            cleared, parameters, banks=banks, settings=settings, simulation_options=options,
            on_candidate=checkpoint)
    except Exception as error:
        save_json("run_status.json", {"status": "failed", "error": repr(error),
                                      "cause": repr(error.__cause__)})
        raise
    export_start = perf_counter()
    summary.to_csv(output_dir / "gamma_summary.csv", index=False)
    checks.to_csv(output_dir / "numerical_checks.csv", index=False)
    plot_tso_tradeoff(summary, output_dir / "tradeoff.png")
    export_s += perf_counter() - export_start
    metadata = {
        "status": "complete", "gamma_selected": None,
        "scope": "fixed disturbance; availability uncertainty; conditional on cleared market",
        "sampling": "independent SeedSequence children (design=0, validation=1); IID bounded laws",
        "intervals": "pointwise Student-t 95% intervals for means, not realization limits",
        "settings": settings, "simulation_options": options,
        "execution": {"python": platform.python_version(), "platform": platform.platform(),
                      "processor": platform.processor(), "parallel_workers": 1,
                      "numpy": np.__version__},
        "timing": {"setup_wall_s": setup_s, "bank_wall_s": bank_s, "evaluations": timings,
                   "export_and_plot_wall_s": export_s, "total_wall_s": perf_counter() - started,
                   "scope": "run_tso_experiment only; interpreter and module import startup excluded"},
        "simulation_count": len(samples) + len(checks), "failed_simulations": 0,
        "fcr_block_payment_eur": cleared.market.total_payment_eur,
    }
    save_json("summary.json", metadata)
    save_json("run_status.json", {"status": "complete"})
    print(f"  Total: {metadata['timing']['total_wall_s']:.2f} s; results: {output_dir}", flush=True)
    return metadata


def run_tso_optimization(config: dict, output_dir: Path, seed_override: int | None = None,
                         frequency_weight: float | None = None) -> dict:
    """Optimize a common gamma and export one delivery event, without plot batches."""
    started, cpu = perf_counter(), process_time()
    settings = deepcopy(config.get("tso_optimizer", {}))
    if seed_override is not None:
        settings["seed"] = seed_override
    if frequency_weight is not None:
        settings["frequency_weight_eur_per_hz2_s"] = frequency_weight
    validate_optimizer_settings(settings)
    # Give providers a valid placeholder while clearing; their bids do not
    # depend on gamma. The common candidate then replaces it only for FI roles.
    effective = deepcopy(config)
    effective["tso_optimizer"] = settings
    for raw in effective["providers"]:
        if raw.get("participation") in ("fi", "fcr_or_fi"):
            raw["fi_gamma_eur_per_hz_mw_s"] = 0.
    output_dir.mkdir(parents=True, exist_ok=True)

    def save_json(name, value):
        (output_dir / name).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    save_json("run_status.json", {"status": "running"})
    export_wall, export_cpu = 0., 0.
    try:
        operating_point, cleared, parameters = prepare_experiment(effective)
        if "fi" not in cleared.participation_modes:
            raise ValueError("TSO optimization requires FI; select an FI scenario")
        setup_wall, setup_cpu = perf_counter() - started, process_time() - cpu
        before, cpu_before = perf_counter(), process_time()
        banks = make_availability_banks(cleared, settings)
        bank_wall, bank_cpu = perf_counter() - before, process_time() - cpu_before
        options = _tso_simulation_options(effective)
        before, cpu_before = perf_counter(), process_time()
        banks.to_csv(output_dir / "availability_samples.csv", index=False)
        save_json("effective_config.json", effective)
        save_json("market.json", {"market": asdict(cleared.market),
                                  "decisions": [asdict(d) for d in cleared.decisions],
                                  "roles": dict(zip((p.provider_id for p in cleared.providers), cleared.participation_modes)),
                                  "physical_parameters": asdict(parameters)})
        save_json("operating_point.json", {k: str(v) if isinstance(v, Path) else v
                                           for k, v in vars(operating_point).items()})
        export_wall += perf_counter() - before
        export_cpu += process_time() - cpu_before
        print(f"TSO optimization: w_f={settings['frequency_weight_eur_per_hz2_s']:g}, "
              f"gamma in [0, {settings['gamma_max']:g}], {settings['design_samples']} design + "
              f"{settings['validation_samples']} validation draws; one later delivery event", flush=True)

        def checkpoint(samples, timing):
            nonlocal export_wall, export_cpu
            before, cpu_before = perf_counter(), process_time()
            samples.to_csv(output_dir / "sample_outcomes.csv", index=False)
            export_wall += perf_counter() - before
            export_cpu += process_time() - cpu_before
            print(f"  {timing['phase']}: gamma={timing['gamma']:g}, "
                  f"{timing['simulations']} simulations, wall={timing['wall_s']:.2f} s, "
                  f"CPU={timing['cpu_s']:.2f} s", flush=True)

        result = optimize_common_gamma(cleared, parameters, banks=banks, settings=settings,
                                       simulation_options=options, on_candidate=checkpoint)
        before, cpu_before = perf_counter(), process_time()
        result.summary.to_csv(output_dir / "gamma_summary.csv", index=False)
        result.numerical_checks.to_csv(output_dir / "numerical_checks.csv", index=False)
        save_json("delivery_availability.json", result.delivery_availability)
        gamma = result.search["gamma_selected"]
        # The optimization replay retains a zero placeholder. This second config
        # records the announced gamma for the delivered event; draws are separate.
        delivery_config = deepcopy(effective)
        for raw in delivery_config["providers"]:
            if raw.get("participation") in ("fi", "fcr_or_fi"):
                raw["fi_gamma_eur_per_hz_mw_s"] = gamma
        save_json("selected_config.json", delivery_config)
        trajectory = result.delivery_trajectory
        series = {"time_s": trajectory.time_s, "frequency_hz": trajectory.frequency_hz,
                  "power_deficit_mw": trajectory.power_deficit_mw,
                  "integrated_squared_frequency_error_hz2_s": trajectory.integrated_squared_frequency_error_hz2_s,
                  "total_fcr_response_mw": trajectory.total_fcr_response_mw,
                  "total_fi_response_mw": trajectory.total_fi_response_mw}
        for j, identity in enumerate(trajectory.provider_ids):
            series[f"{identity}_requested_mw"] = trajectory.provider_requested_response_mw[:, j]
            series[f"{identity}_response_mw"] = trajectory.provider_response_mw[:, j]
            series[f"{identity}_active_power_mw"] = trajectory.provider_active_power_mw[:, j]
        pd.DataFrame(series).to_csv(output_dir / "time_series.csv", index=False)
        fi_totals = save_fi_accounting(trajectory, output_dir)
        export_wall += perf_counter() - before
        export_cpu += process_time() - cpu_before
        selected = result.summary[result.summary.gamma == gamma]
        # pandas null conversion prevents NaN in the single-delivery uncertainty.
        estimates = json.loads(selected.to_json(orient="records", double_precision=15))
        metadata = {
            "status": "complete", "gamma_selected": gamma, "settings": settings,
            "search": result.search, "selected_estimates": estimates,
            "scope": "fixed disturbance, availability uncertainty; conditional on configured cleared market",
            "sampling": "IID bounded laws; independent SeedSequence children design=0, validation=1, delivery=2",
            "intervals": "Student-t 95% intervals for means; design selection bias uncorrected; no interval for one delivery",
            "simulation_options": options, "simulation_count": len(result.samples) + len(result.numerical_checks),
            "failed_simulations": 0, "fcr_block_payment_eur": cleared.market.total_payment_eur,
            "delivery_fi": fi_totals,
            "execution": {"python": platform.python_version(), "platform": platform.platform(),
                          "processor": platform.processor(), "numpy": np.__version__, "parallel_workers": 1},
            "timing": {"setup_wall_s": setup_wall, "setup_cpu_s": setup_cpu,
                       "bank_wall_s": bank_wall, "bank_cpu_s": bank_cpu, "evaluations": result.timings,
                       "export_wall_s": export_wall, "export_cpu_s": export_cpu,
                       "total_wall_s": perf_counter() - started, "total_cpu_s": process_time() - cpu,
                       "scope": "runner only; imports excluded; search includes checkpoints, export overlaps search"},
        }
        save_json("summary.json", metadata)
        save_json("run_status.json", {"status": "complete"})
    except Exception as error:
        save_json("run_status.json", {"status": "failed", "error": repr(error), "cause": repr(error.__cause__)})
        raise
    print(f"  Selected gamma={gamma:g}; upper boundary={result.search['at_upper_bound']}; "
          f"local resolution reached={result.search['local_resolution_reached']}", flush=True)
    print(f"  Total wall={metadata['timing']['total_wall_s']:.2f} s, "
          f"CPU={metadata['timing']['total_cpu_s']:.2f} s; results: {output_dir}", flush=True)
    return metadata


def _stability_seed_job(cleared, parameters, design, sizes, settings, options, directory):
    """Process entry point; all physical simulations use the existing TSO API."""
    directory.mkdir(parents=True, exist_ok=True)

    def checkpoint(selection, outcomes):
        outcomes.to_csv(directory / "design_outcomes.csv", index=False)
        print(f"  seed={settings['seed']}, N={selection['design_samples']}: "
              f"gamma={selection['gamma_selected']:g}, J_design={selection['design_objective_eur']:.4f}, "
              f"{selection['new_simulations']} new simulations, {selection['wall_s']:.1f} s wall", flush=True)

    selections, outcomes, checks = optimize_nested_gamma_banks(
        cleared, parameters, design=design, sample_sizes=sizes, settings=settings,
        simulation_options=options, on_level=checkpoint)
    outcomes.to_csv(directory / "design_outcomes.csv", index=False)
    checks.to_csv(directory / "numerical_checks.csv", index=False)
    (directory / "selections.json").write_text(json.dumps(selections, indent=2) + "\n", encoding="utf-8")
    return selections, len(outcomes), float(outcomes.simulation_cpu_s.sum() + checks.cpu_s.sum())


def run_tso_stability(config: dict, output_dir: Path, frequency_weight: float | None = None) -> dict:
    """Nested sample-size/seed study, then common independent policy assessment."""
    started, cpu = perf_counter(), process_time()
    study = deepcopy(config.get("tso_stability", {}))
    required = {"sample_sizes", "seeds", "holdout_samples", "holdout_seed", "reference_gamma", "workers"}
    if set(study) != required:
        raise ValueError(f"tso_stability requires exactly {sorted(required)}")
    sizes, seeds = study["sample_sizes"], study["seeds"]
    if (len(sizes) < 2 or any(type(n) is not int or n < 2 for n in sizes)
            or sizes != sorted(set(sizes))):
        raise ValueError("sample_sizes must contain at least two increasing integers >= 2")
    if (len(seeds) < 2 or any(type(s) is not int or s < 0 for s in seeds)
            or len(set(seeds)) != len(seeds)):
        raise ValueError("seeds must contain at least two distinct nonnegative integers")
    for key, minimum in (("holdout_samples", 2), ("holdout_seed", 0), ("workers", 1)):
        if type(study[key]) is not int or study[key] < minimum:
            raise ValueError(f"{key} must be an integer >= {minimum}")
    if study["holdout_seed"] in seeds:
        raise ValueError("holdout_seed must differ from design seeds")
    effective = deepcopy(config)
    settings = effective["tso_optimizer"]
    if frequency_weight is not None:
        settings["frequency_weight_eur_per_hz2_s"] = frequency_weight
    validate_optimizer_settings(settings)
    reference = study["reference_gamma"]
    if (isinstance(reference, bool) or not isinstance(reference, (int, float))
            or not np.isfinite(reference) or not 0 <= reference <= settings["gamma_max"]):
        raise ValueError("reference_gamma must be finite and inside the search domain")
    # A fresh directory prevents stale successful outputs from masking failed runs.
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("stability output directory must be empty")
    output_dir.mkdir(parents=True, exist_ok=True)

    def save(name, data):
        (output_dir / name).write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    save("run_status.json", {"status": "running", "phase": "design"})
    for raw in effective["providers"]:
        if raw.get("participation") in ("fi", "fcr_or_fi"):
            raw["fi_gamma_eur_per_hz_mw_s"] = 0.
    save("effective_config.json", effective)
    save("protocol.json", {**study, "frequency_weight_eur_per_hz2_s": settings["frequency_weight_eur_per_hz2_s"],
                           "design_stream": 0, "holdout_stream": 1,
                           "comparison": "paired differences versus predeclared reference, not holdout selection",
                           "sampling": "nested prefixes within each seed; independent seeds; shared independent holdout",
                           "scope": "same physical/market model and search; no delivery event or new policy constraint"})
    try:
        operating_point, cleared, parameters = prepare_experiment(effective)
        options = _tso_simulation_options(effective)
        save("market.json", {"market": asdict(cleared.market),
                             "decisions": [asdict(d) for d in cleared.decisions],
                             "roles": dict(zip((p.provider_id for p in cleared.providers), cleared.participation_modes)),
                             "physical_parameters": asdict(parameters)})
        save("operating_point.json", {k: str(v) if isinstance(v, Path) else v
                                      for k, v in vars(operating_point).items()})
        jobs, bank_records = [], []
        for seed in seeds:
            seed_settings = {**settings, "seed": seed, "design_samples": sizes[-1], "validation_samples": 2}
            bank = make_availability_banks(cleared, seed_settings)
            design = bank[bank.bank == "design"].drop(columns="bank").reset_index(drop=True)
            bank_records.append(design.assign(seed=seed))
            jobs.append((cleared, parameters, design, sizes, seed_settings, options, output_dir / f"seed_{seed}"))
        pd.concat(bank_records, ignore_index=True).to_csv(output_dir / "design_availability.csv", index=False)
        workers = min(study["workers"], len(seeds))
        selections, simulation_count, worker_cpu = [], 0, 0.
        design_start = perf_counter()
        # Processes are computational workers, not separate model/agent implementations.
        with ProcessPoolExecutor(max_workers=workers) if workers > 1 else _SerialExecutor() as pool:
            futures = [pool.submit(_stability_seed_job, *job) for job in jobs]
            for future in as_completed(futures):
                selected, count, consumed_cpu = future.result()
                selections.extend(selected)
                simulation_count += count
                worker_cpu += consumed_cpu
        design_wall = perf_counter() - design_start
        selections.sort(key=lambda row: (row["design_samples"], row["seed"]))
        save("selections.json", selections)
        # Freeze candidates BEFORE generating or evaluating held-out draws.
        candidates = sorted({reference, *(row["gamma_selected"] for row in selections)})
        save("frozen_candidates.json", {"gammas": candidates, "reference_gamma": reference})
        save("run_status.json", {"status": "running", "phase": "holdout", "frozen_gammas": candidates})
        holdout_settings = {**settings, "seed": study["holdout_seed"], "design_samples": 2,
                            "validation_samples": study["holdout_samples"]}
        bank = make_availability_banks(cleared, holdout_settings)
        holdout = bank[bank.bank == "validation"].drop(columns="bank").reset_index(drop=True)
        holdout.to_csv(output_dir / "holdout_availability.csv", index=False)
        outcomes = []
        holdout_start = perf_counter()
        with ProcessPoolExecutor(max_workers=workers) if workers > 1 else _SerialExecutor() as pool:
            futures = [pool.submit(evaluate_fixed_gamma_bank, cleared, parameters, draws=holdout,
                                   gamma=gamma, frequency_weight=settings["frequency_weight_eur_per_hz2_s"],
                                   simulation_options=options) for gamma in candidates]
            for future in as_completed(futures):
                outcome = future.result()
                outcomes.append(outcome)
                pd.concat(outcomes, ignore_index=True).to_csv(output_dir / "holdout_outcomes.csv", index=False)
                worker_cpu += float(outcome.simulation_cpu_s.sum())
                simulation_count += len(outcome)
                print(f"  holdout gamma={outcome.gamma.iloc[0]:g}: {len(outcome)} simulations complete", flush=True)
        outcomes = pd.concat(outcomes, ignore_index=True).sort_values(["gamma", "sample"])
        outcomes.to_csv(output_dir / "holdout_outcomes.csv", index=False)
        holdout_wall = perf_counter() - holdout_start
        assessed = summarize_paired_tso_objectives(outcomes, settings["frequency_weight_eur_per_hz2_s"], reference)
        assessed.to_csv(output_dir / "holdout_summary.csv", index=False)
        selection_frame = pd.DataFrame(selections)
        comparison = selection_frame.merge(assessed, left_on="gamma_selected", right_on="gamma", validate="many_to_one")
        comparison.to_csv(output_dir / "selection_summary.csv", index=False)
        plot_tso_stability(comparison, assessed, output_dir)
        check_count = sum(len(pd.read_csv(output_dir / f"seed_{seed}" / "numerical_checks.csv")) for seed in seeds)
        summary = {"status": "complete", "study": study, "search_settings": settings,
                   "simulation_options": options, "selections": selections, "unique_policies": candidates,
                   "simulation_count": simulation_count + 2 * check_count, "numerical_checks": check_count,
                   "failed_simulations": 0, "parallel_workers": workers,
                   "intervals": "pointwise paired Student-t 95%; no multiplicity correction or equivalence threshold",
                   "timing": {"design_wall_s": design_wall, "holdout_wall_s": holdout_wall,
                              "total_wall_s": perf_counter() - started,
                              "simulation_and_check_cpu_s": worker_cpu,
                              "parent_cpu_s": process_time() - cpu,
                              "scope": "wall excludes imports; simulation CPU sums workers, not elapsed time"}}
        save("summary.json", summary)
        save("run_status.json", {"status": "complete"})
        return summary
    except Exception as error:
        save("run_status.json", {"status": "failed", "error": repr(error), "cause": repr(error.__cause__)})
        raise


class _SerialExecutor:
    """The same job interface without processes for small studies and tests."""
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def submit(self, function, *args, **kwargs):
        from concurrent.futures import Future
        future = Future()
        try:
            future.set_result(function(*args, **kwargs))
        except Exception as error:
            future.set_exception(error)
        return future


def _load_tso_report_cases(run_dirs: list[Path]) -> list[dict]:
    """Require a controlled weight comparison, including identical random draws."""
    if len(run_dirs) < 2:
        raise ValueError("TSO report requires at least two completed optimizer runs")
    cases = []
    for directory in run_dirs:
        def read(name):
            return json.loads((directory / name).read_text(encoding="utf-8"))
        metadata = read("summary.json")
        if read("run_status.json")["status"] != "complete" or metadata["status"] != "complete":
            raise ValueError(f"incomplete optimizer run: {directory}")
        config = read("effective_config.json")
        settings = deepcopy(metadata["settings"])
        validate_optimizer_settings(settings)
        weight = settings.pop("frequency_weight_eur_per_hz2_s")
        case = {"directory": directory.resolve(), "metadata": metadata, "weight": weight,
                "config": config, "market": read("market.json"),
                "availability": read("delivery_availability.json"),
                "banks": pd.read_csv(directory / "availability_samples.csv"),
                "samples": pd.read_csv(directory / "sample_outcomes.csv"),
                "trajectory": pd.read_csv(directory / "time_series.csv"),
                "finances": pd.read_csv(directory / "fi_time_series.csv"),
                "decisions": pd.read_csv(directory / "fi_decisions.csv")}
        # Compare effective physical/economic inputs, not unused comparison or
        # sweep definitions. Weight is the only permitted optimizer difference.
        case["shared_inputs"] = {key: config.get(key) for key in
                                 ("scenario", "decision", "market", "availability", "fi", "providers")}
        case["shared_inputs"].update(optimizer=settings, simulation_options=metadata["simulation_options"])
        if cases:
            first = cases[0]
            for name in ("shared_inputs", "market", "availability"):
                if case[name] != first[name]:
                    raise ValueError(f"weight comparison requires identical {name}")
            if not case["banks"].equals(first["banks"]):
                raise ValueError("weight comparison requires identical availability banks")
            if not case["trajectory"].time_s.equals(first["trajectory"].time_s):
                raise ValueError("weight comparison requires identical output times")
        gamma = metadata["gamma_selected"]
        for bank, expected in (("design", settings["design_samples"]),
                               ("validation", settings["validation_samples"]), ("delivery", 1)):
            rows = case["samples"].query("bank == @bank and gamma == @gamma").sort_values("sample")
            if rows["sample"].tolist() != list(range(expected)):
                raise ValueError("selected-candidate samples are incomplete")
        cases.append(case)
    if len({c["weight"] for c in cases}) != len(cases):
        raise ValueError("frequency weights must be distinct")
    return sorted(cases, key=lambda c: c["weight"])


def run_tso_report(run_dirs: list[Path], output_dir: Path) -> dict:
    """Compare saved optimizer runs; add only matched gamma-zero references.

    This does not choose or refine a gamma, or copy existing run directories.
    It reuses saved validation/delivery availability for the FCR-only baseline.
    """
    if any(output_dir.resolve() == path.resolve() or path.resolve() in output_dir.resolve().parents
           for path in run_dirs):
        raise ValueError("put the comparison report outside individual run directories")
    output_dir.mkdir(parents=True, exist_ok=True)

    def save_json(name, value):
        (output_dir / name).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    save_json("report_status.json", {"status": "running"})
    started, cpu = perf_counter(), process_time()
    try:
        cases = _load_tso_report_cases(run_dirs)
        first = cases[0]
        _, cleared, parameters = prepare_experiment(first["config"])
        saved_market = first["market"]
        roles = dict(zip((p.provider_id for p in cleared.providers), cleared.participation_modes))
        if (json.loads(json.dumps(asdict(cleared.market))) != saved_market["market"]
                or asdict(parameters) != saved_market["physical_parameters"] or roles != saved_market["roles"]):
            raise ValueError("current model does not reproduce the saved market/physical parameters")
        options = first["metadata"]["simulation_options"]
        baseline_records = []
        reference_draws = first["banks"].query("bank == 'validation'").to_dict("records")
        reference_draws.append({"bank": "delivery", "sample": 0, **first["availability"]})
        before, cpu_before = perf_counter(), process_time()
        for row in reference_draws:
            block = realize_fcr_block(cleared, {p.provider_id: row[p.provider_id] for p in cleared.providers})
            trajectory = simulate_common_gamma(block, parameters, 0., options)
            values = summarize_tso_trajectory(trajectory, options["start_time_s"])
            baseline_records.append({"bank": row["bank"], "sample": row["sample"], "gamma": 0., **values})
            if row["bank"] == "delivery":
                baseline = pd.DataFrame({"time_s": trajectory.time_s, "frequency_hz": trajectory.frequency_hz,
                                         "total_fcr_response_mw": trajectory.total_fcr_response_mw,
                                         "total_fi_response_mw": trajectory.total_fi_response_mw})
        reference_timing = {"simulations": len(reference_draws), "wall_s": perf_counter() - before,
                            "cpu_s": process_time() - cpu_before}
        baseline_samples = pd.DataFrame(baseline_records)
        baseline_samples.to_csv(output_dir / "baseline_samples.csv", index=False)
        baseline.to_csv(output_dir / "baseline_delivery.csv", index=False)
        estimates, differences = [], []
        for case in cases:
            weight = case["weight"]
            gamma = case["metadata"]["gamma_selected"]
            selected = case["samples"].query("gamma == @gamma and bank in ['validation', 'delivery']")
            estimates.extend(summarize_weighted_tso_samples(selected, weight).assign(
                frequency_weight_eur_per_hz2_s=weight).to_dict("records"))
            # Paired comparisons preserve the common draws, including covariance
            # of frequency error and expenditure in each sample's J difference.
            paired = selected.merge(baseline_samples, on=["bank", "sample"], suffixes=("", "_baseline"), validate="one_to_one")
            paired["error_reduction_hz2_s"] = paired.frequency_error_hz2_s_baseline - paired.frequency_error_hz2_s
            paired["objective_reduction_eur"] = (weight * paired.error_reduction_hz2_s
                                                + paired.net_payment_eur_baseline - paired.net_payment_eur)
            paired["frequency_weight_eur_per_hz2_s"] = weight
            differences.extend(paired[["bank", "sample", "frequency_weight_eur_per_hz2_s", "gamma",
                                       "error_reduction_hz2_s", "objective_reduction_eur"]].to_dict("records"))
        pd.DataFrame(estimates).to_csv(output_dir / "comparison_summary.csv", index=False)
        pd.DataFrame(differences).to_csv(output_dir / "paired_baseline_differences.csv", index=False)
        plot_tso_weight_comparison(cases, baseline, output_dir)
        metadata = {"status": "complete", "runs": [str(c["directory"]) for c in cases],
                    "weights": [c["weight"] for c in cases],
                    "selected_gammas": [c["metadata"]["gamma_selected"] for c in cases],
                    "matching": "same physical/market inputs, availability banks and delivery draw",
                    "scope": "four single-delivery figures; separate validation statistics, no new optimization",
                    "objective_note": "J values use different weights; compare each policy to its own weighted baseline",
                    "baseline_metrics": baseline_records, "reference_evaluation": reference_timing,
                    "total_wall_s": perf_counter() - started, "total_cpu_s": process_time() - cpu}
        save_json("comparison.json", metadata)
        save_json("report_status.json", {"status": "complete"})
    except Exception as error:
        save_json("report_status.json", {"status": "failed", "error": repr(error)})
        raise
    print(f"TSO report: {len(cases)} weights, {len(reference_draws)} matched gamma-zero runs, "
          f"four PNG/PDF figures; results: {output_dir}", flush=True)
    return metadata


def configured_scenarios(config: dict) -> list[tuple[str, str, dict]]:
    """Materialize independent overrides: section, all providers, then provider ID.

    This is a shallow update of the existing flat configuration tables. Each
    comparison starts from base; optional fields may be introduced explicitly.
    Reject misspelled fields and identities before starting any simulation.
    """
    variants = [("base", "Base", deepcopy(config))]
    names = {"base"}
    optional_fields = {
        "scenario": {"operating_point", "maximum_solver_step_s"},
        "decision": {"mode", "quantity_tolerance_mw"},
        "market": {"mode", "minimum_bid_mw", "fixed_price_eur_per_mw_block"},
        "availability": set(),
        "fi": {"decision_interval_s"},
        "providers": {"reserve_safety_margin_fraction", "gain_mw_per_hz", "is_slack",
                      "minimum_bid_price_eur_per_mw_block", "maximum_bid_price_eur_per_mw_block",
                      "fixed_offer_mw", "fixed_bid_eur_per_mw_block", "fixed_award_mw",
                      "participation", "fi_gamma_eur_per_hz_mw_s", "fi_rho_eur_per_mw2_s"},
    }

    def update(target: dict, overrides: dict, section: str) -> None:
        unknown = set(overrides) - set(target) - optional_fields[section]
        if section == "providers":
            unknown |= set(overrides).intersection({"provider_id", "generator_id"})
        if unknown:
            raise ValueError(f"unknown or immutable {section} override fields: {sorted(unknown)}")
        target.update(overrides)

    for variant in config.get("comparisons", []):
        unknown = set(variant) - {"name", "label", "scenario", "decision", "market",
                                  "availability", "fi", "all_providers", "providers"}
        if unknown:
            raise ValueError(f"unknown comparison fields: {sorted(unknown)}")
        name = variant["name"]
        if not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in name) or name in names:
            raise ValueError("comparison names must be unique, simple lowercase directory names")
        names.add(name)
        changed = deepcopy(config)
        for section in ("scenario", "decision", "market", "availability", "fi"):
            update(changed.setdefault(section, {}), variant.get(section, {}), section)
        by_id = {p["provider_id"]: p for p in changed["providers"]}
        if len(by_id) != len(changed["providers"]):
            raise ValueError("provider identities must be unique")
        for provider in changed["providers"]:
            update(provider, variant.get("all_providers", {}), "providers")
        for identity, overrides in variant.get("providers", {}).items():
            if identity not in by_id:
                raise ValueError(f"unknown comparison provider: {identity}")
            update(by_id[identity], overrides, "providers")
        variants.append((name, variant.get("label", name), changed))
    if len({label for _, label, _ in variants}) != len(variants):
        raise ValueError("comparison labels must be unique")
    return variants


def run_comparisons(config: dict, output_dir: Path, seed: int | None = None) -> None:
    """Run base and every configured scenario through the same implementation."""
    variants = configured_scenarios(config)
    summaries, offers, trajectories = [], [], {}
    for name, label, changed in variants:
        result = run_experiment(changed, output_dir / name, seed)
        summaries.append({"case": label, "clearing_price_eur_per_mw_block":
                          result["clearing_price_eur_per_mw_block"], **result["metrics"],
                          **{f"fi_{key}": result["fi"][key] for key in (
                              "provider_count", "gross_rewards_eur", "gross_penalties_eur",
                              "net_settlement_eur", "activation_cost_eur", "profit_eur")}})
        table = pd.read_csv(output_dir / name / "market_outcomes.csv")
        offers.append(table.assign(case=label))
        trajectories[label] = pd.read_csv(output_dir / name / "time_series.csv")
    pd.DataFrame(summaries).to_csv(output_dir / "comparison_summary.csv", index=False)
    offers = pd.concat(offers, ignore_index=True)
    offers.to_csv(output_dir / "comparison_providers.csv", index=False)
    plot_scenario_comparison(trajectories, offers, output_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    parser.add_argument("--seed", type=int, help="override the availability seed")
    tso_mode = parser.add_mutually_exclusive_group()
    tso_mode.add_argument("--fpp-benchmark", action="store_true",
                         help="fixed FCR/FI/FPP comparison using an FPP overlay as --config")
    tso_mode.add_argument("--fpp-report", type=Path, metavar="STUDY_DIR",
                         help="regenerate the saved FPP comparison without integration")
    tso_mode.add_argument("--tso-sweep", action="store_true", help="estimate the common-gamma trade-off using [tso]")
    tso_mode.add_argument("--tso-pareto", type=Path, metavar="SWEEP_DIR",
                         help="replay a completed sweep to add RoCoF, tail diagnostics and Pareto reports")
    tso_mode.add_argument("--tso-pareto-report", type=Path, metavar="STUDY_DIR",
                         help="regenerate a completed Pareto report without simulations")
    tso_mode.add_argument("--tso-optimize", action="store_true", help="choose common gamma using [tso_optimizer]")
    tso_mode.add_argument("--tso-stability", action="store_true", help="nested sample/seed study using [tso_stability]")
    tso_mode.add_argument("--tso-fleet", action="store_true", help="controlled physical comparison using a fleet manifest as --config")
    tso_mode.add_argument("--tso-fleet-report", type=Path, metavar="STUDY_DIR", help="regenerate a saved fleet comparison without optimization")
    tso_mode.add_argument("--tso-procurement", action="store_true", help="reduce FCR requirement using a manifest and preserved fleet runs")
    tso_mode.add_argument("--tso-procurement-report", type=Path, metavar="STUDY_DIR", help="report saved FCR-requirement comparisons without optimization")
    tso_mode.add_argument("--tso-report", nargs="+", type=Path, metavar="RUN_DIR",
                         help="compare completed optimizer runs differing only in frequency weight")
    parser.add_argument("--frequency-weight", type=float,
                        help="explicit w_f in EUR/(Hz^2 s), overriding tso_optimizer.frequency_weight_eur_per_hz2_s")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--compare", action="store_true", help="run base and all configured comparisons")
    selection.add_argument("--scenario", help="run one named comparison, or base")
    arguments = parser.parse_args()
    if arguments.fpp_benchmark or arguments.fpp_report:
        if arguments.compare or arguments.scenario or arguments.seed is not None or arguments.frequency_weight is not None:
            parser.error("FPP modes use their fixed overlay; do not supply scenario/seed/weight/compare overrides")
        from .fpp_study import run_fpp_study, run_fpp_report
        if arguments.fpp_report:
            if arguments.config != DEFAULT_CONFIG_PATH or arguments.output_dir != DEFAULT_OUTPUT_DIRECTORY:
                parser.error("--fpp-report writes to STUDY_DIR; do not supply config/output-dir")
            run_fpp_report(arguments.fpp_report)
        else:
            if arguments.config == DEFAULT_CONFIG_PATH or arguments.output_dir == DEFAULT_OUTPUT_DIRECTORY:
                parser.error("--fpp-benchmark requires explicit overlay --config and study --output-dir")
            run_fpp_study(arguments.config, arguments.output_dir)
        return
    if (arguments.tso_sweep or arguments.tso_optimize or arguments.tso_stability) and arguments.compare:
        parser.error("TSO modes cannot be combined with --compare; select one FI scenario")
    if arguments.frequency_weight is not None and not (arguments.tso_optimize or arguments.tso_stability):
        parser.error("--frequency-weight requires --tso-optimize or --tso-stability")
    if arguments.tso_stability and arguments.seed is not None:
        parser.error("--tso-stability uses its explicit seed list; do not supply --seed")
    if arguments.tso_pareto or arguments.tso_pareto_report:
        if arguments.compare or arguments.scenario or arguments.seed is not None or arguments.config != DEFAULT_CONFIG_PATH:
            parser.error("Pareto modes use saved inputs; do not supply config/scenario/seed/compare overrides")
        from .pareto import run_pareto_study, run_pareto_report
        if arguments.tso_pareto_report:
            if arguments.output_dir != DEFAULT_OUTPUT_DIRECTORY:
                parser.error("--tso-pareto-report writes to STUDY_DIR; do not supply output-dir")
            run_pareto_report(arguments.tso_pareto_report)
        else:
            if arguments.output_dir == DEFAULT_OUTPUT_DIRECTORY:
                parser.error("--tso-pareto requires an explicit separate --output-dir")
            run_pareto_study(arguments.tso_pareto, arguments.output_dir)
        return
    if arguments.tso_procurement or arguments.tso_procurement_report:
        if arguments.compare or arguments.scenario or arguments.seed is not None or arguments.frequency_weight is not None:
            parser.error("procurement modes use frozen inputs; do not supply scenario/seed/weight/compare overrides")
        if arguments.tso_procurement_report:
            if arguments.config != DEFAULT_CONFIG_PATH or arguments.output_dir != DEFAULT_OUTPUT_DIRECTORY:
                parser.error("--tso-procurement-report writes to STUDY_DIR; do not supply config/output-dir")
            from .procurement import run_procurement_report
            run_procurement_report(arguments.tso_procurement_report)
        else:
            if arguments.config == DEFAULT_CONFIG_PATH or arguments.output_dir == DEFAULT_OUTPUT_DIRECTORY:
                parser.error("--tso-procurement requires explicit manifest --config and study --output-dir")
            from .procurement import run_procurement_study
            run_procurement_study(arguments.config, arguments.output_dir)
        return
    if arguments.tso_fleet or arguments.tso_fleet_report:
        if arguments.compare or arguments.scenario or arguments.seed is not None or arguments.frequency_weight is not None:
            parser.error("fleet modes use their frozen manifest; do not supply scenario/seed/weight/compare overrides")
        from .fleet import run_fleet_study, run_fleet_report
        if arguments.tso_fleet_report:
            if arguments.config != DEFAULT_CONFIG_PATH or arguments.output_dir != DEFAULT_OUTPUT_DIRECTORY:
                parser.error("--tso-fleet-report writes to STUDY_DIR; do not supply config/output-dir")
            run_fleet_report(arguments.tso_fleet_report)
        else:
            if arguments.config == DEFAULT_CONFIG_PATH or arguments.output_dir == DEFAULT_OUTPUT_DIRECTORY:
                parser.error("--tso-fleet requires an explicit manifest --config and study --output-dir")
            run_fleet_study(arguments.config, arguments.output_dir)
        return
    if arguments.tso_report:
        if arguments.compare or arguments.scenario or arguments.seed is not None or arguments.config != DEFAULT_CONFIG_PATH:
            parser.error("--tso-report reads saved runs; do not supply config/scenario/seed/compare overrides")
        run_tso_report(arguments.tso_report, arguments.output_dir)
        return
    source = arguments.config.read_text(encoding="utf-8")
    config = json.loads(source) if arguments.config.suffix == ".json" else tomllib.loads(source)
    if arguments.compare:
        run_comparisons(config, arguments.output_dir, arguments.seed)
    else:
        if arguments.scenario:
            variants = {name: changed for name, _, changed in configured_scenarios(config)}
            if arguments.scenario not in variants:
                parser.error(f"unknown scenario {arguments.scenario!r}; choose from {', '.join(variants)}")
            config = variants[arguments.scenario]
        if arguments.tso_sweep:
            run_tso_experiment(config, arguments.output_dir, arguments.seed)
        elif arguments.tso_optimize:
            run_tso_optimization(config, arguments.output_dir, arguments.seed, arguments.frequency_weight)
        elif arguments.tso_stability:
            run_tso_stability(config, arguments.output_dir, arguments.frequency_weight)
        else:
            run_experiment(config, arguments.output_dir, arguments.seed)
