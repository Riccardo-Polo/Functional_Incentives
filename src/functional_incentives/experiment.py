"""Run provider decisions, FCR capacity allocation and aggregate frequency dynamics."""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import tomllib

import numpy as np
import pandas as pd

from .availability import GaussianReserve
from .decision import evaluate_offer
from .frequency import LinearFrequencyParameters, damping_from_load_sensitivity, analytical_no_fcr
from .grid import AndesOperatingPoint, FleetGenerator, solve_operating_point
from .market import FCRCapacityOffer
from .metrics import summarize_fcr_trajectories
from .plotting import plot_frequency_comparison, plot_provider_responses, plot_scenario_comparison
from .providers import BlockProvider, QuadraticCommitmentCost
from .simulation import prepare_fcr_block, simulate_fcr_power_step


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
        ))
    return tuple(providers)


def run_experiment(config: dict, output_dir: Path, seed_override: int | None = None) -> dict:
    """Run one complete block and save its effective inputs and outputs."""
    scenario = config["scenario"]
    block_hours = float(config["market"]["block_duration_hours"])
    if not np.isfinite(block_hours) or block_hours <= 0:
        raise ValueError("block_duration_hours must be finite and positive")
    seed = scenario["seed"] if seed_override is None else seed_override
    if not isinstance(seed, int) or seed < 0:
        raise ValueError("seed must be a nonnegative integer")

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
    block = prepare_fcr_block(
        providers, requirement_mw=float(config["market"]["requirement_mw"]),
        nominal_frequency_hz=operating_point.nominal_frequency_hz,
        droop_pu=float(scenario["droop_pu"]), rng=np.random.default_rng(seed),
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
    event = dict(
        power_deficit_mw=float(scenario["power_deficit_mw"]),
        start_time_s=float(scenario["event_time_s"]),
        final_time_s=float(scenario["final_time_s"]),
    )
    dt = float(scenario["output_time_step_s"])
    controlled = simulate_fcr_power_step(
        parameters, block.response_parameters, **event, output_time_step_s=dt,
        provider_operating_power_mw=block.operating_powers_mw,
    )
    numerical_no_fcr = simulate_fcr_power_step(
        parameters, (), **event, output_time_step_s=dt)
    reference = analytical_no_fcr(parameters, **event, time_step_s=dt)
    # Reuse the SAME draw, award and gain for solver refinement.
    refined = simulate_fcr_power_step(
        parameters, block.response_parameters, **event, output_time_step_s=dt,
        maximum_solver_step_s=dt/2, relative_tolerance=1e-10, absolute_tolerance=1e-12,
    )
    metrics = summarize_fcr_trajectories(
        controlled, numerical_no_fcr, reference, refined)

    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for p, decision, settlement, response in zip(
        block.providers, block.decisions, block.settlements, block.response_parameters,
        strict=True,
    ):
        rows.append({
            **asdict(settlement),
            "generator_id": p.generator_id,
            "technology": p.technology,
            "operating_power_mw": p.operating_power_mw,
            "power_base_mw": p.power_base_mw,
            "gain_mw_per_hz": response.gain_mw_per_hz,
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
        if p.maximum_offer_mw >= minimum_bid:
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
        "frequency_no_fcr_numerical_hz": numerical_no_fcr.frequency_hz,
        "frequency_no_fcr_analytical_hz": reference.frequency_hz,
        "total_fcr_response_mw": controlled.total_response_mw,
    }
    for j, provider_id in enumerate(controlled.provider_ids):
        series[f"{provider_id}_requested_mw"] = controlled.provider_requested_response_mw[:, j]
        series[f"{provider_id}_response_mw"] = controlled.provider_response_mw[:, j]
        series[f"{provider_id}_active_power_mw"] = controlled.provider_active_power_mw[:, j]
    pd.DataFrame(series).to_csv(output_dir / "time_series.csv", index=False)
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
                            reserve_symbol=r"q^{\mathrm{eff}}")
    print("PJM 5-bus FCR market with Gaussian reserve availability")
    print(f"  Boundary rule: {summary['boundary_mode']}; seed: {seed}")
    print(f"  Clearing price: {block.market.clearing_price_eur_per_mw_block:g} EUR/MW/block")
    print(f"  Decisions: {decision_mode}; allocation: {market_mode}")
    for row in rows:
        print(f"  {row['provider_id']}: bid={row['bid_eur_per_mw_block']:g}, "
              f"offer={row['offered_mw']:g} MW, award={row['awarded_mw']:g} MW, "
              f"available={row['available_mw']:.4f} MW, "
              f"K={row['gain_mw_per_hz']:.4f} MW/Hz, profit={row['profit_eur']:.2f} EUR")
    print(f"  Sampled nadir: {metrics['nadir_sampled_hz']:.6f} Hz")
    print(f"  Final frequency: {metrics['final_frequency_hz']:.6f} Hz")
    print(f"  Refined solver frequency difference: {metrics['refined_frequency_max_error_hz']:.3e} Hz")
    print(f"  Results: {output_dir}")
    return summary


def configured_scenarios(config: dict) -> list[tuple[str, str, dict]]:
    """Materialize independent overrides: section, all providers, then provider ID.

    This is a shallow update of the existing flat configuration tables. Each
    comparison starts from base; optional fields may be introduced explicitly.
    Reject misspelled fields and identities before starting any simulation.
    """
    variants = [("base", "Base", deepcopy(config))]
    names = {"base"}
    optional_fields = {
        "scenario": {"operating_point"},
        "decision": {"mode", "quantity_tolerance_mw"},
        "market": {"mode", "minimum_bid_mw", "fixed_price_eur_per_mw_block"},
        "availability": set(),
        "providers": {"reserve_safety_margin_fraction", "gain_mw_per_hz", "is_slack",
                      "minimum_bid_price_eur_per_mw_block", "maximum_bid_price_eur_per_mw_block",
                      "fixed_offer_mw", "fixed_bid_eur_per_mw_block", "fixed_award_mw"},
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
                                  "availability", "all_providers", "providers"}
        if unknown:
            raise ValueError(f"unknown comparison fields: {sorted(unknown)}")
        name = variant["name"]
        if not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in name) or name in names:
            raise ValueError("comparison names must be unique, simple lowercase directory names")
        names.add(name)
        changed = deepcopy(config)
        for section in ("scenario", "decision", "market", "availability"):
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
                          result["clearing_price_eur_per_mw_block"], **result["metrics"]})
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
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--compare", action="store_true", help="run base and all configured comparisons")
    selection.add_argument("--scenario", help="run one named comparison, or base")
    arguments = parser.parse_args()
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
        run_experiment(config, arguments.output_dir, arguments.seed)
