"""Controlled fleet comparisons through the existing market and TSO optimizer.

Only physical provider fields vary. The frozen plan supports resuming completed
cases and regenerating figures without repeating optimization.
"""

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
import json
from pathlib import Path
from time import perf_counter
import tomllib

import pandas as pd

from . import experiment
from .metrics import summarize_tso_trajectory, summarize_weighted_tso_samples
from .plotting import plot_tso_fleet_comparison
from .simulation import realize_fcr_block
from .tso import simulate_common_gamma, validate_optimizer_settings


PHYSICAL_FIELDS = {"rating_mva", "power_setpoint_mw", "inertia_s",
                   "minimum_power_mw", "maximum_power_mw", "time_constant_s"}


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def load_fleet_plan(path: Path) -> dict:
    """Resolve a small manifest; prohibit accidental economic confounding."""
    manifest = tomllib.loads(path.read_text(encoding="utf-8"))
    if set(manifest) - {"base_config", "base_scenario", "workers", "optimizer", "cases"}:
        raise ValueError("unknown fleet manifest fields")
    base_path = path.parent / manifest["base_config"]
    base = tomllib.loads(base_path.read_text(encoding="utf-8"))
    variants = {name: config for name, _, config in experiment.configured_scenarios(base)}
    base = variants[manifest["base_scenario"]]
    for unused in ("comparisons", "tso", "tso_stability"):
        base.pop(unused, None)
    unknown = set(manifest["optimizer"]) - set(base["tso_optimizer"]) - {"frequency_weight_eur_per_hz2_s"}
    if unknown:
        raise ValueError(f"unknown optimizer fields: {sorted(unknown)}")
    base["tso_optimizer"].update(manifest["optimizer"])
    validate_optimizer_settings(base["tso_optimizer"])
    for raw in base["providers"]:
        if raw.get("participation") in ("fi", "fcr_or_fi"):
            raw["fi_gamma_eur_per_hz_mw_s"] = 0.
    cases = manifest["cases"]
    if len(cases) < 2:
        raise ValueError("fleet study requires at least two cases")
    for case in cases:
        if set(case) - {"name", "label", "providers"}:
            raise ValueError("fleet cases only allow physical provider overrides")
        for changes in case.get("providers", {}).values():
            if set(changes) - PHYSICAL_FIELDS:
                raise ValueError("fleet cases only allow physical provider overrides")
    base["comparisons"] = cases
    resolved = []
    for name, label, config in experiment.configured_scenarios(base)[1:]:
        config.pop("comparisons")
        resolved.append({"name": name, "label": label, "config": config})
    workers = manifest.get("workers", 1)
    if type(workers) is not int or workers < 1:
        raise ValueError("workers must be a positive integer")
    return {"cases": resolved, "workers": min(workers, len(resolved)),
            "scope": "physical parameters vary; common economics, reserve laws, draws and TSO weight"}


def run_fleet_study(manifest_path: Path, output_dir: Path) -> dict:
    plan = load_fleet_plan(manifest_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    plan_path = output_dir / "study_plan.json"
    if plan_path.exists() and _read(plan_path) != plan:
        raise ValueError("saved fleet plan differs; use a new output directory")
    # Check every solved dispatch, headroom and clearing before expensive work.
    first_market = None
    pending = []
    for case in plan["cases"]:
        _, cleared, _ = experiment.prepare_experiment(case["config"])
        if "fi" not in cleared.participation_modes:
            raise ValueError("each fleet requires FI participation")
        market = (asdict(cleared.market), cleared.participation_modes)
        if first_market is not None and market != first_market:
            raise ValueError("controlled fleet study requires identical market and roles")
        first_market = market
        directory = output_dir / case["name"]
        if directory.exists() and any(directory.iterdir()):
            if (not plan_path.exists() or not (directory / "run_status.json").exists()
                    or _read(directory / "run_status.json")["status"] != "complete"
                    or _read(directory / "effective_config.json") != case["config"]):
                raise ValueError(f"incomplete or conflicting run: {directory}; preserve it and use a new output directory")
        else:
            pending.append((case, directory))
    _save(plan_path, plan)
    started = perf_counter()
    if plan["workers"] == 1:
        for case, directory in pending:
            experiment.run_tso_optimization(case["config"], directory)
    elif pending:
        with ProcessPoolExecutor(max_workers=plan["workers"]) as pool:
            jobs = {pool.submit(experiment.run_tso_optimization, c["config"], d): c["name"]
                    for c, d in pending}
            for future in as_completed(jobs):
                future.result()
                print(f"Fleet completed: {jobs[future]}", flush=True)
    if pending:
        _save(output_dir / "execution.json", {"workers": plan["workers"],
              "new_cases": [c["name"] for c, _ in pending],
              "optimization_wall_s": perf_counter() - started})
    return run_fleet_report(output_dir)


def run_fleet_report(output_dir: Path) -> dict:
    """Regenerate a report, marking failures instead of leaving a success flag."""
    _save(output_dir / "report_status.json", {"status": "running"})
    try:
        result = _run_fleet_report(output_dir)
    except Exception as error:
        _save(output_dir / "report_status.json", {"status": "failed", "error": repr(error)})
        raise
    _save(output_dir / "report_status.json", {"status": "complete"})
    return result


def _run_fleet_report(output_dir: Path) -> dict:
    """Read completed cases; add one matched gamma-zero delivery per fleet."""
    started = perf_counter()
    plan = _read(output_dir / "study_plan.json")
    cases = []
    for item in plan["cases"]:
        directory = output_dir / item["name"]
        metadata = _read(directory / "summary.json")
        if (_read(directory / "run_status.json")["status"] != "complete"
                or metadata["status"] != "complete"):
            raise ValueError(f"incomplete fleet run: {directory}")
        config = _read(directory / "effective_config.json")
        if config != item["config"]:
            raise ValueError("saved fleet config differs from frozen plan")
        case = {**item, "directory": directory, "metadata": metadata,
                "market": _read(directory / "market.json"),
                "availability": _read(directory / "delivery_availability.json"),
                "banks": pd.read_csv(directory / "availability_samples.csv"),
                "samples": pd.read_csv(directory / "sample_outcomes.csv"),
                "trajectory": pd.read_csv(directory / "time_series.csv"),
                "finances": pd.read_csv(directory / "fi_time_series.csv"),
                "decisions": pd.read_csv(directory / "fi_decisions.csv")}
        if cases:
            first = cases[0]
            if (case["availability"] != first["availability"] or not case["banks"].equals(first["banks"])
                    or case["market"]["market"] != first["market"]["market"]
                    or case["market"]["roles"] != first["market"]["roles"]):
                raise ValueError("fleet comparison requires identical draws, market and roles")
        gamma = metadata["gamma_selected"]
        for bank, count in (("validation", metadata["settings"]["validation_samples"]), ("delivery", 1)):
            rows = case["samples"].query("bank == @bank and gamma == @gamma")
            if sorted(rows["sample"].tolist()) != list(range(count)):
                raise ValueError("incomplete selected fleet samples")
        cases.append(case)

    summaries, providers, physical, baselines = [], [], [], []
    new_baselines = 0
    for case in cases:
        config, directory = case["config"], case["directory"]
        point, cleared, parameters = experiment.prepare_experiment(config)
        if (json.loads(json.dumps(asdict(cleared.market))) != case["market"]["market"]
                or asdict(parameters) != case["market"]["physical_parameters"]):
            raise ValueError("current model does not reproduce saved fleet")
        block = realize_fcr_block(cleared, case["availability"])
        baseline_path = directory / "baseline_delivery.csv"
        baseline_summary = directory / "baseline_summary.json"
        if not baseline_path.exists() or not baseline_summary.exists():
            trajectory = simulate_common_gamma(block, parameters, 0., case["metadata"]["simulation_options"])
            pd.DataFrame({"time_s": trajectory.time_s, "frequency_hz": trajectory.frequency_hz}).to_csv(
                baseline_path, index=False)
            _save(baseline_summary, summarize_tso_trajectory(trajectory, config["scenario"]["event_time_s"]))
            new_baselines += 1
        case["baseline"] = pd.read_csv(baseline_path)
        baselines.append({"case": case["name"], **_read(baseline_summary)})
        gamma = case["metadata"]["gamma_selected"]
        selected = case["samples"].query("gamma == @gamma and bank in ['validation', 'delivery']")
        estimates = summarize_weighted_tso_samples(selected, case["metadata"]["settings"]["frequency_weight_eur_per_hz2_s"])
        estimates["fi_profit_eur_mean"] = estimates.net_payment_eur_mean - estimates.activation_cost_eur_mean
        estimates["case"], estimates["label"] = case["name"], case["label"]
        estimates["fcr_capacity_payment_eur_per_block"] = cleared.market.total_payment_eur
        estimates["fcr_block_hours"] = config["market"]["block_duration_hours"]
        estimates["fi_horizon_s"] = config["scenario"]["final_time_s"]
        summaries.extend(estimates.to_dict("records"))
        point_power = dict(zip(point.generator_ids, point.generator_active_power_mw))
        settlements = {s.provider_id: s for s in block.settlements}
        roles = dict(zip((p.provider_id for p in block.providers), block.participation_modes))
        gains = dict(zip((p.provider_id for p in cleared.providers), cleared.gains_mw_per_hz))
        for raw in config["providers"]:
            identity = raw["provider_id"]
            settlement, role = settlements[identity], roles[identity]
            series = case["trajectory"][f"{identity}_response_mw"]
            providers.append({"case": case["name"], "provider_id": identity, "role": role,
                              "peak_abs_response_mw": float(series.abs().max()), "final_response_mw": float(series.iloc[-1]),
                              **{f"fcr_{key}": value for key, value in asdict(settlement).items() if key != "provider_id"}})
            physical.append({"case": case["name"], "provider_id": identity, "role": role,
                             **{key: raw[key] for key in sorted(PHYSICAL_FIELDS)},
                             "solved_power_mw": point_power[raw["generator_id"]],
                             "symmetric_headroom_mw": min(raw["maximum_power_mw"] - point_power[raw["generator_id"]],
                                                          point_power[raw["generator_id"]] - raw["minimum_power_mw"]),
                             "droop_gain_mw_per_hz": gains[identity],
                             "available_resource_mw": case["availability"][identity]})
        case["fi_outcomes"] = pd.read_csv(directory / "fi_outcomes.csv")
    summary = pd.DataFrame(summaries)
    summary.to_csv(output_dir / "comparison_summary.csv", index=False)
    pd.DataFrame(physical).to_csv(output_dir / "machine_parameters.csv", index=False)
    pd.DataFrame(providers).to_csv(output_dir / "provider_delivery.csv", index=False)
    pd.concat([c["fi_outcomes"].assign(case=c["name"]) for c in cases]).to_csv(output_dir / "fi_delivery.csv", index=False)
    pd.DataFrame(baselines).to_csv(output_dir / "baseline_delivery_summary.csv", index=False)
    plot_tso_fleet_comparison(cases, summary, output_dir)
    report = {"status": "complete", "cases": [c["name"] for c in cases],
              "gamma_selected": [c["metadata"]["gamma_selected"] for c in cases],
              "optimizer_simulations": sum(c["metadata"]["simulation_count"] for c in cases),
              "baseline_simulations": len(cases), "new_baseline_simulations": new_baselines,
              "optimizer_cpu_s": sum(c["metadata"]["timing"]["total_cpu_s"] for c in cases),
              "report_wall_s": perf_counter() - started,
              "scope": "single matched delivery trajectories; separate independent validation means; FCR per block, FI per event"}
    _save(output_dir / "comparison.json", report)
    _write_analysis(output_dir, cases, summary, report)
    return report


def _write_analysis(output_dir, cases, summary, report):
    settings = cases[0]["metadata"]["settings"]
    horizon = cases[0]["config"]["scenario"]["final_time_s"]
    hours = cases[0]["config"]["market"]["block_duration_hours"]
    lines = ["# Machine configuration comparison", "",
             f"{len(cases)} mixed fleets; w_f={settings['frequency_weight_eur_per_hz2_s']:g}. "
             f"Per configuration: {settings['design_samples']} availability draws to select gamma, "
             f"{settings['validation_samples']} independent draws to assess it, then one illustrative event.", "",
             "Availability draws are matched across configurations; γ is selected separately. The figures show "
             "the illustrative event, not a mean trajectory. Dashed frequency curves "
             "show γ=0 with the same machines and availability.", "",
             "## Mean results over independent availability draws", "",
             "| Case | γ | Mean nadir [Hz] | E[E_f] [Hz² s] | Net FI [EUR/event] | FI cost | FI profit | J [EUR] |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in summary.query("bank == 'validation'").itertuples():
        lines.append(f"| {r.label} | {r.gamma:.4f} | {r.nadir_sampled_hz_mean:.5f} | {r.frequency_error_hz2_s_mean:.6f} | "
                     f"{r.net_payment_eur_mean:.2f} | {r.activation_cost_eur_mean:.2f} | {r.fi_profit_eur_mean:.2f} | {r.objective_eur_mean:.2f} |")
    lines += ["", "## Event shown in the figures", "",
              "| Case | Nadir [Hz] | Final frequency [Hz] | FCR [EUR/block] | Net FI [EUR/event] | FI cost | FI profit |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for r in summary.query("bank == 'delivery'").itertuples():
        lines.append(f"| {r.label} | {r.nadir_sampled_hz_mean:.5f} | {r.final_frequency_hz_mean:.5f} | {r.fcr_capacity_payment_eur_per_block:.2f} | "
                     f"{r.net_payment_eur_mean:.2f} | {r.activation_cost_eur_mean:.2f} | {r.fi_profit_eur_mean:.2f} |")
    baseline = pd.read_csv(output_dir / "baseline_delivery_summary.csv").set_index("case")
    lines += ["", "FI versus γ=0 for the same single availability draw:", ""]
    for r in summary.query("bank == 'delivery'").itertuples():
        reference = baseline.loc[r.case]
        reduction = 100 * (1 - r.frequency_error_hz2_s_mean / reference.frequency_error_hz2_s)
        lines.append(f"- {r.label}: nadir without FI {reference.nadir_sampled_hz:.5f} Hz, "
                     f"with FI {r.nadir_sampled_hz_mean:.5f} Hz; integrated error reduced by {reduction:.1f}%. "
                     f"FI target at its bound in {100*r.target_bound_fraction_mean:.1f}% of post-disturbance intervals.")
    lines += ["", f"FCR: gross payment per {hours:g}-hour block, identical because quantities, offers and clearing are identical. "
              f"FI: net settlement over the {horizon:g}-second event. These amounts cover different horizons; "
              "they do not estimate FI expenditure over the whole block. FI costs are not an additional TSO expense; "
              "provider profit equals net payment minus activation cost.", "",
              "## Files", "", "- `frequency.png`: frequency, γ=0 comparison and initial-response detail.",
              "- `provider_responses.png`: response in MW of all five providers.",
              "- `fi_response.png`: target, delivered power and dashed available resource.",
              "- `fi_finances.png`: cumulative net payment, FI cost and profit; gross rewards and charges in the FI CSV files.",
              "- `tso_spending.png`: FCR and FI expenditure, with event and validation mean shown separately.",
              "- `machine_parameters.csv`: ratings, limits, inertia, T, solved dispatch, droop and headroom.",
              "- `provider_delivery.csv` / `fi_delivery.csv`: physical and financial detail by provider.",
              "- `comparison_summary.csv`: full statistics and pointwise 95% intervals for means.",
              "- `../../docs/FLEET_STUDY.md`: individual machine tables, common availability/economic inputs and source provenance.",
              "", "## Interpretation and limitations", "",
              "A→B jointly changes ratings, dispatch, aggregate inertia, droop gains and PV response speed; "
              "it does not isolate renewable share alone. B→C changes only response times. "
              "Service capacity remains 4 MW per provider: this is a controlled-comparison assumption, "
              "not a rule linking reserve to machine rating.", "",
              "Parameters are inspired by technical sources, with explicit reductions in `docs/FLEET_STUDY.md`. "
              "Costs, availability and w_f remain common experimental assumptions. This is neither a calibration "
              "of real prices nor a reproduction of Nordic32. Aggregate frequency at 60 Hz; "
              "no secondary control, energy limits or full turbine/converter dynamics.", "",
              f"Completed {report['optimizer_simulations']} integrations for optimization "
              f"(including numerical checks), plus {report['baseline_simulations']} γ=0 references. "
              f"Cumulative optimization CPU time: {report['optimizer_cpu_s'] / 60:.2f} minutes. "
              "Per-case timings and checks are saved in the corresponding directories. "
              "Selected γ is the best tested candidate; there is no guarantee of a global optimum or universal statistical accuracy."]
    (output_dir / "ANALYSIS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
