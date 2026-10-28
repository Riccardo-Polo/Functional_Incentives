"""FCR-requirement sensitivity using frozen fleet runs and the existing optimizer.

Only requirement_mw changes. Reference runs are read in place, never rewritten.
"""

from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import asdict
import hashlib
import math
from pathlib import Path
from time import perf_counter, process_time
import tomllib

import numpy as np
import pandas as pd
from scipy.stats import t as student_t

from . import experiment
from .fleet import _read, _save
from .metrics import summarize_weighted_tso_samples, summarize_tso_trajectory
from .simulation import realize_fcr_block
from .tso import simulate_common_gamma


def _hash_tree(directory):
    return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob("*")) if p.is_file()}


def load_procurement_plan(path: Path) -> dict:
    """Derive every input from completed references; allow no other overrides."""
    manifest = tomllib.loads(path.read_text(encoding="utf-8"))
    if set(manifest) != {"baseline_study", "requirements_mw", "workers"}:
        raise ValueError("procurement manifest requires only baseline_study, requirements_mw and workers")
    root = (path.parent / manifest["baseline_study"]).resolve()
    source = _read(root / "study_plan.json")
    levels = manifest["requirements_mw"]
    if (not levels or any(type(q) not in (int, float) or not math.isfinite(q) or q < 0 for q in levels)
            or len(set(levels)) != len(levels)):
        raise ValueError("requirements must be distinct finite nonnegative numbers")
    workers = manifest["workers"]
    if type(workers) is not int or workers < 1:
        raise ValueError("workers must be a positive integer")
    cases = []
    for fleet in source["cases"]:
        directory = root / fleet["name"]
        config = _read(directory / "effective_config.json")
        if (config != fleet["config"] or _read(directory / "run_status.json")["status"] != "complete"
                or _read(directory / "summary.json")["status"] != "complete"):
            raise ValueError("baseline must be complete and match its frozen fleet plan")
        reference = config["market"]["requirement_mw"]
        if any(q >= reference for q in levels):
            raise ValueError("new requirements must be below each reference requirement")
        for q in [reference, *levels]:
            changed = deepcopy(config)
            changed["market"]["requirement_mw"] = float(q)
            cases.append({"name": f"{fleet['name']}_q{q:g}".replace(".", "p"),
                          "fleet": fleet["name"], "label": fleet["label"],
                          "requirement_mw": float(q), "config": changed,
                          "reference_directory": str(directory) if q == reference else None})
    return {"cases": cases, "workers": min(workers, len(cases)),
            "baseline_study": str(root), "baseline_sha256": _hash_tree(root),
            "scope": "only FCR requirement changes; gamma reoptimized on identical design draws"}


def case_directory(case, output_dir):
    return Path(case["reference_directory"]) if case["reference_directory"] else output_dir / case["name"]


def run_procurement_study(manifest_path: Path, output_dir: Path) -> dict:
    """Preflight, optimize new cases and resume completed cases without rerunning."""
    plan = load_procurement_plan(manifest_path)
    output_dir = output_dir.resolve()
    baseline_root = Path(plan["baseline_study"])
    if output_dir == baseline_root or baseline_root in output_dir.parents or output_dir in baseline_root.parents:
        raise ValueError("procurement output must be separate from the preserved baseline study")
    output_dir.mkdir(parents=True, exist_ok=True)
    plan_path = output_dir / "study_plan.json"
    if plan_path.exists() and _read(plan_path) != plan:
        raise ValueError("saved procurement plan differs; use a new output directory")
    pending, markets = [], []
    for case in plan["cases"]:
        _, cleared, _ = experiment.prepare_experiment(case["config"])
        if "fi" not in cleared.participation_modes:
            raise ValueError("each procurement case requires FI participation")
        markets.append({"case": case["name"], "market": asdict(cleared.market),
                        "roles": dict(zip((p.provider_id for p in cleared.providers), cleared.participation_modes))})
        directory = case_directory(case, output_dir)
        if directory.exists() and any(directory.iterdir()):
            if (not (directory / "run_status.json").exists()
                    or _read(directory / "run_status.json")["status"] != "complete"
                    or _read(directory / "summary.json")["status"] != "complete"
                    or _read(directory / "effective_config.json") != case["config"]):
                raise ValueError(f"incomplete or conflicting run: {directory}; preserve it and use a new output directory")
        else:
            pending.append((case, directory))
    _save(plan_path, plan)
    _save(output_dir / "preflight.json", markets)
    _save(output_dir / "study_status.json", {"status": "running"})
    started = perf_counter()
    try:
        if plan["workers"] == 1:
            for case, directory in pending:
                experiment.run_tso_optimization(case["config"], directory)
        elif pending:
            with ProcessPoolExecutor(max_workers=plan["workers"]) as pool:
                jobs = {pool.submit(experiment.run_tso_optimization, c["config"], d): c["name"]
                        for c, d in pending}
                for future in as_completed(jobs):
                    future.result()
                    print(f"Procurement case completed: {jobs[future]}", flush=True)
        if _hash_tree(baseline_root) != plan["baseline_sha256"]:
            raise ValueError("baseline artifacts changed during procurement study")
        ensure_fcr_only_validation(plan, output_dir)
    except Exception as error:
        _save(output_dir / "study_status.json", {"status": "failed", "error": repr(error)})
        raise
    if pending:
        _save(output_dir / "execution.json", {"workers": plan["workers"],
              "new_cases": [c["name"] for c, _ in pending],
              "optimization_wall_s": perf_counter() - started})
    _save(output_dir / "study_status.json", {"status": "complete", "report": "run --tso-procurement-report"})
    return plan


def ensure_fcr_only_validation(plan, output_dir):
    """Evaluate the predeclared full-FCR, zero-gamma policy on validation draws.

    Cache only in the new study directory; never add files to reference runs.
    No candidate is selected or refined using these outcomes.
    """
    for case in plan["cases"]:
        if not case["reference_directory"]:
            continue
        path = output_dir / f"{case['fleet']}_fcr_only_validation.csv"
        if path.exists():
            continue
        config = case["config"]
        _, cleared, parameters = experiment.prepare_experiment(config)
        directory = Path(case["reference_directory"])
        metadata = _read(directory / "summary.json")
        banks = pd.read_csv(directory / "availability_samples.csv").query("bank == 'validation'")
        rows = []
        for draw in banks.to_dict("records"):
            sample = draw.pop("sample")
            draw.pop("bank")
            before, cpu = perf_counter(), process_time()
            trajectory = simulate_common_gamma(realize_fcr_block(cleared, draw), parameters, 0., metadata["simulation_options"])
            values = summarize_tso_trajectory(trajectory, config["scenario"]["event_time_s"])
            rows.append({"sample": sample, "bank": "validation", "gamma": 0., **values,
                         "simulation_wall_s": perf_counter()-before, "simulation_cpu_s": process_time()-cpu})
        pd.DataFrame(rows).to_csv(path, index=False)
        print(f"Full-FCR zero-gamma validation completed: {case['fleet']}, {len(rows)} draws", flush=True)


def paired_procurement_differences(candidate, reference):
    """Candidate minus reference on matched validation IDs, retaining covariance."""
    left = candidate.set_index("sample").sort_index()
    right = reference.set_index("sample").sort_index()
    if (len(left) < 2 or not left.index.is_unique or not right.index.is_unique
            or not left.index.equals(right.index)):
        raise ValueError("paired comparison requires identical unique sample IDs")
    fields = ["frequency_error_hz2_s", "net_payment_eur", "activation_cost_eur",
              "fi_profit_eur", "nadir_sampled_hz", "final_frequency_hz", "objective_eur"]
    difference = left[fields] - right[fields]
    if not np.isfinite(difference.to_numpy()).all():
        raise ValueError("non-finite paired metrics")
    row = {"samples": len(left)}
    for name in fields:
        values = difference[name]
        row[f"delta_{name}_mean"] = float(values.mean())
        row[f"delta_{name}_ci95_halfwidth"] = float(student_t.ppf(.975, len(values)-1)
                                                   * values.std(ddof=1) / np.sqrt(len(values)))
    return row, difference.reset_index()


def run_procurement_report(output_dir: Path) -> dict:
    """Read saved trajectories and matched validation results; no integrations."""
    _save(output_dir / "report_status.json", {"status": "running"})
    try:
        result = _run_procurement_report(output_dir)
    except Exception as error:
        _save(output_dir / "report_status.json", {"status": "failed", "error": repr(error)})
        raise
    _save(output_dir / "report_status.json", {"status": "complete"})
    return result


def _run_procurement_report(output_dir):
    started = perf_counter()
    plan = _read(output_dir / "study_plan.json")
    if _hash_tree(Path(plan["baseline_study"])) != plan["baseline_sha256"]:
        raise ValueError("preserved baseline artifacts differ from the frozen hashes")
    cases, summaries, providers, fi_rows = [], [], [], []
    for item in plan["cases"]:
        directory = case_directory(item, output_dir)
        metadata = _read(directory / "summary.json")
        config = _read(directory / "effective_config.json")
        if (_read(directory / "run_status.json")["status"] != "complete"
                or metadata["status"] != "complete" or config != item["config"]):
            raise ValueError("procurement run must be complete and match its frozen config")
        case = {**item, "directory": directory, "metadata": metadata,
                "market": _read(directory / "market.json"),
                "availability": _read(directory / "delivery_availability.json"),
                "banks": pd.read_csv(directory / "availability_samples.csv"),
                "samples": pd.read_csv(directory / "sample_outcomes.csv"),
                "trajectory": pd.read_csv(directory / "time_series.csv"),
                "finances": pd.read_csv(directory / "fi_time_series.csv"),
                "decisions": pd.read_csv(directory / "fi_decisions.csv")}
        if cases and (case["availability"] != cases[0]["availability"]
                      or not case["banks"].equals(cases[0]["banks"])):
            raise ValueError("procurement comparison requires identical availability draws")
        if not item["reference_directory"]:
            reference = next(c for c in cases if c["fleet"] == item["fleet"] and c["reference_directory"])
            expected = deepcopy(reference["config"])
            expected["market"]["requirement_mw"] = item["requirement_mw"]
            if config != expected or case["market"]["market"]["offers"] != reference["market"]["market"]["offers"]:
                raise ValueError("only FCR requirement may change within a fleet; offers must remain identical")
        _, cleared, parameters = experiment.prepare_experiment(config)
        if (asdict(parameters) != case["market"]["physical_parameters"]
                or _read_json_value(asdict(cleared.market)) != case["market"]["market"]
                or dict(zip((p.provider_id for p in cleared.providers), cleared.participation_modes)) != case["market"]["roles"]):
            raise ValueError("current model does not reproduce saved procurement inputs")
        gamma = metadata["gamma_selected"]
        selected = case["samples"].query("gamma == @gamma and bank in ['validation', 'delivery']").copy()
        for bank, count in [("validation", metadata["settings"]["validation_samples"]), ("delivery", 1)]:
            if sorted(selected.query("bank == @bank")["sample"].tolist()) != list(range(count)):
                raise ValueError("incomplete selected-gamma samples")
        selected["fi_profit_eur"] = selected.net_payment_eur - selected.activation_cost_eur
        weight = metadata["settings"]["frequency_weight_eur_per_hz2_s"]
        selected["objective_eur"] = weight * selected.frequency_error_hz2_s + selected.net_payment_eur
        case["validation"] = selected.query("bank == 'validation'")
        estimates = summarize_weighted_tso_samples(selected, weight)
        for key in ["name", "fleet", "label", "requirement_mw"]:
            estimates["case" if key == "name" else key] = case[key]
        estimates["fcr_capacity_payment_eur_per_block"] = cleared.market.total_payment_eur
        estimates["fcr_price_eur_per_mw_block"] = cleared.market.clearing_price_eur_per_mw_block
        estimates["fcr_block_hours"] = config["market"]["block_duration_hours"]
        estimates["fi_horizon_s"] = config["scenario"]["final_time_s"]
        estimates["validation_nadir_q05_hz"] = case["validation"].nadir_sampled_hz.quantile(.05)
        summaries.extend(estimates.to_dict("records"))
        block = realize_fcr_block(cleared, case["availability"])
        for settlement in block.settlements:
            identity = settlement.provider_id
            series = case["trajectory"][f"{identity}_response_mw"]
            providers.append({"case": case["name"], "fleet": case["fleet"], "requirement_mw": case["requirement_mw"],
                "provider_id": identity, "role": case["market"]["roles"][identity],
                "available_resource_mw": case["availability"][identity],
                "peak_abs_response_mw": float(series.abs().max()), "final_response_mw": float(series.iloc[-1]),
                **{f"fcr_{k}": v for k, v in asdict(settlement).items() if k != "provider_id"}})
        fi_rows.append(pd.read_csv(directory / "fi_outcomes.csv").assign(
            case=case["name"], fleet=case["fleet"], requirement_mw=case["requirement_mw"]))
        cases.append(case)
    summary = pd.DataFrame(summaries)
    paired, sample_deltas, economics, fcr_only_summaries, fcr_only_pairs = [], [], [], [], []
    baseline_simulations, baseline_cpu = 0, 0.
    for reference in [c for c in cases if c["reference_directory"]]:
        reference_samples = pd.read_csv(output_dir / f"{reference['fleet']}_fcr_only_validation.csv")
        count = reference["metadata"]["settings"]["validation_samples"]
        if (sorted(reference_samples["sample"].tolist()) != list(range(count))
                or not (reference_samples.gamma == 0).all()
                or not (reference_samples.bank == "validation").all()):
            raise ValueError("incomplete full-FCR zero-gamma validation")
        baseline_simulations += len(reference_samples)
        baseline_cpu += float(reference_samples.simulation_cpu_s.sum())
        reference_samples["fi_profit_eur"] = reference_samples.net_payment_eur - reference_samples.activation_cost_eur
        weight = reference["metadata"]["settings"]["frequency_weight_eur_per_hz2_s"]
        reference_samples["objective_eur"] = weight*reference_samples.frequency_error_hz2_s + reference_samples.net_payment_eur
        estimate = summarize_weighted_tso_samples(reference_samples, weight).assign(fleet=reference["fleet"])
        fcr_only_summaries.extend(estimate.to_dict("records"))
        for case in [c for c in cases if c["fleet"] == reference["fleet"]]:
            difference, _ = paired_procurement_differences(case["validation"], reference_samples)
            fcr_only_pairs.append({"case": case["name"], "fleet": case["fleet"],
                                   "requirement_mw": case["requirement_mw"], **difference})
    validation = summary.query("bank == 'validation'").set_index("case")
    for case in cases:
        if case["reference_directory"]:
            continue
        reference = next(c for c in cases if c["fleet"] == case["fleet"] and c["reference_directory"])
        difference, raw = paired_procurement_differences(case["validation"], reference["validation"])
        identifiers = {"case": case["name"], "fleet": case["fleet"], "requirement_mw": case["requirement_mw"],
                       "reference_case": reference["name"]}
        paired.append({**identifiers, **difference})
        sample_deltas.append(raw.assign(**identifiers))
        row, ref = validation.loc[case["name"]], validation.loc[reference["name"]]
        savings = float(ref.fcr_capacity_payment_eur_per_block - row.fcr_capacity_payment_eur_per_block)
        extra = difference["delta_net_payment_eur_mean"]
        economics.append({**identifiers, "fcr_savings_eur_per_block": savings,
            "extra_fi_eur_per_event": extra,
            "extra_fi_ci95_halfwidth": difference["delta_net_payment_eur_ci95_halfwidth"],
            "saving_if_one_event_vs_reference_fi_eur": savings - extra,
            "saving_if_one_event_vs_reference_fi_ci95_halfwidth": difference["delta_net_payment_eur_ci95_halfwidth"],
            "break_even_events_vs_reference_fi": savings / extra if extra > 0 else None,
            "saving_if_one_event_vs_reference_fcr_only_eur": savings - row.net_payment_eur_mean,
            "saving_if_one_event_vs_reference_fcr_only_ci95_halfwidth": row.net_payment_eur_ci95_halfwidth,
            "break_even_events_vs_reference_fcr_only": savings / row.net_payment_eur_mean if row.net_payment_eur_mean > 0 else None})
    summary.to_csv(output_dir / "comparison_summary.csv", index=False)
    pd.DataFrame(paired).to_csv(output_dir / "paired_validation.csv", index=False)
    pd.concat(sample_deltas).to_csv(output_dir / "paired_sample_differences.csv", index=False)
    pd.DataFrame(economics).to_csv(output_dir / "economic_sensitivity.csv", index=False)
    pd.DataFrame(providers).to_csv(output_dir / "provider_delivery.csv", index=False)
    pd.concat(fi_rows).to_csv(output_dir / "fi_delivery.csv", index=False)
    pd.DataFrame(fcr_only_summaries).to_csv(output_dir / "fcr_only_validation_summary.csv", index=False)
    pd.DataFrame(fcr_only_pairs).to_csv(output_dir / "paired_vs_fcr_only.csv", index=False)
    from .plotting import plot_tso_procurement_comparison
    plot_tso_procurement_comparison(cases, summary, output_dir)
    new_cases = [c for c in cases if not c["reference_directory"]]
    report = {"status": "complete", "new_cases": len(new_cases), "referenced_cases": len(cases)-len(new_cases),
              "new_optimizer_simulations": sum(c["metadata"]["simulation_count"] for c in new_cases),
              "new_optimizer_cpu_s": sum(c["metadata"]["timing"]["total_cpu_s"] for c in new_cases),
              "fcr_only_validation_simulations": baseline_simulations, "fcr_only_validation_cpu_s": baseline_cpu,
              "report_simulations": 0, "report_wall_s": perf_counter()-started,
              "baseline_files_unchanged": len(plan["baseline_sha256"]),
              "scope": "paired validation and separate matched delivery; block FCR versus event FI"}
    _save(output_dir / "comparison.json", report)
    _write_procurement_analysis(output_dir, cases, summary, pd.DataFrame(economics), report)
    return report


def _read_json_value(value):
    # Stored dataclass tuples become JSON lists.
    import json
    return json.loads(json.dumps(value))


def _write_procurement_analysis(output_dir, cases, summary, economics, report):
    settings = cases[0]["metadata"]["settings"]
    hours = cases[0]["config"]["market"]["block_duration_hours"]
    horizon = cases[0]["config"]["scenario"]["final_time_s"]
    lines = ["# FCR requirement sensitivity", "",
        "Only market.requirement_mw changes within each saved A/B/C fleet. Machines, offers, participation eligibility, "
        "availability laws, banks, delivery draw, disturbance, horizon, numerical tolerances and frequency weight remain unchanged. "
        "Gamma is independently reoptimized after each clearing using w_f E[E_f] + E[S_FI]. "
        "FCR spending is constant with respect to gamma within each case, so adding it would not change that inner minimizer.", "",
        f"w_f={settings['frequency_weight_eur_per_hz2_s']:g}; {settings['design_samples']} design and "
        f"{settings['validation_samples']} independent validation draws per policy. "
        "The reference runs are read in place. No earlier result is modified. "
        "The tested requirements are sensitivity cases, not an optimized procurement policy.", "",
        "## Independent validation means", "",
        "| Fleet | FCR MW | Gamma | Nadir Hz | E_f Hz² s | FCR EUR/block | FI EUR/event | FI cost | FI profit | Target-bound time |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in summary.query("bank == 'validation'").itertuples():
        lines.append(f"| {r.label.split(':')[0]} | {r.requirement_mw:g} | {r.gamma:.4f} | {r.nadir_sampled_hz_mean:.5f} | "
            f"{r.frequency_error_hz2_s_mean:.6f} | {r.fcr_capacity_payment_eur_per_block:.2f} | {r.net_payment_eur_mean:.2f} | "
            f"{r.activation_cost_eur_mean:.2f} | {r.fi_profit_eur_mean:.2f} | {100*r.target_bound_fraction_mean:.1f}% |")
    lines += ["", "Intervals for means and paired candidate-minus-reference differences are in the CSV files. "
        "They are pointwise Student-t 95% intervals, not simultaneous bounds or guarantees for a future event. "
        "The 5th percentile of 32 sampled nadirs is exploratory, not a reliability certificate. Validation never selects gamma.", "",
        "## Economics across different accounting horizons", "",
        f"Gross FCR capacity payments cover the full {hours:g}-hour block. FI net settlements and provider costs cover one {horizon:g}-second event. "
        "Provider activation cost is already paid out of FI income; it is not added again to TSO spending.", "",
        "For an explicitly hypothetical N comparable, non-overlapping events per block, the reduced-case saving "
        "relative to reference FCR+FI is FCR_saving - N * (mean_FI_reduced - mean_FI_reference). "
        "Relative to reference FCR without FI, it is FCR_saving - N * mean_FI_reduced. "
        "These are accounting sensitivities, not repeated-event simulations or assumptions about actual event frequency. "
        "The model does not establish repeated-event state recovery, energy feasibility or an occurrence law.", "",
        "| Case | FCR saving EUR/block | Extra FI EUR/event vs reference FI | Saving if N=1 vs reference FI | Break-even N vs reference FI | Saving if N=1 vs reference FCR only | Break-even N vs reference FCR only |",
        "|---|---:|---:|---:|---:|---:|---:|"]
    for r in economics.itertuples():
        def fmt(x):
            return f"{x:.2f}" if pd.notna(x) else "no finite crossing"
        lines.append(f"| {r.case} | {r.fcr_savings_eur_per_block:.2f} | {r.extra_fi_eur_per_event:.2f} | "
            f"{r.saving_if_one_event_vs_reference_fi_eur:.2f} | {fmt(r.break_even_events_vs_reference_fi)} | "
            f"{r.saving_if_one_event_vs_reference_fcr_only_eur:.2f} | {fmt(r.break_even_events_vs_reference_fcr_only)} |")
    lines += ["", "The CSV includes pointwise 95% halfwidths for the conditional N=1 saving. "
        "Break-even counts are ratios of sample means, without uncertainty intervals. "
        "An economic saving alone does not establish acceptable frequency performance. No frequency-security threshold was imposed.", "",
        "## Independent full-FCR reference without FI", "",
        "A predeclared gamma=0 policy at the original FCR requirement is assessed on the same independent validation "
        "draws. These additional evaluations do not select any policy. The paired_vs_fcr_only.csv table reports "
        "paired differences for every frozen policy against this reference.", "",
        "| Fleet | Mean nadir without FI [Hz] | E[E_f] without FI [Hz² s] |", "|---|---:|---:|"]
    for r in pd.read_csv(output_dir / "fcr_only_validation_summary.csv").itertuples():
        lines.append(f"| {r.fleet} | {r.nadir_sampled_hz_mean:.5f} | {r.frequency_error_hz2_s_mean:.6f} |")
    lines += ["",
        "## Single delivery shown in the figures", "",
        "| Fleet | FCR MW | Nadir Hz | Final Hz | FI EUR/event | FI cost | FI profit |",
        "|---|---:|---:|---:|---:|---:|---:|"]
    for r in summary.query("bank == 'delivery'").itertuples():
        lines.append(f"| {r.label.split(':')[0]} | {r.requirement_mw:g} | {r.nadir_sampled_hz_mean:.5f} | "
            f"{r.final_frequency_hz_mean:.5f} | {r.net_payment_eur_mean:.2f} | {r.activation_cost_eur_mean:.2f} | {r.fi_profit_eur_mean:.2f} |")
    lines += ["", "Each plotted event uses the same available resource draw. These trajectories are not expected trajectories. "
        "The dashed frequency reference, where available, is the preserved full-procurement gamma-zero delivery.", "",
        "## Market and physical interpretation", "",
        "Offers stay fixed because the implemented offer heuristic does not depend on procurement requirement or acceptance. "
        "Merit-order clearing changes awards and may lower the uniform price. A partially awarded provider stays FCR; "
        "an FCR-only provider with zero award remains FCR with zero modulation. Only existing fcr_or_fi or fi providers "
        "can enter FI. Rejecting an FCR bid does not turn an FCR-only machine into an FI provider. "
        "Online synchronous inertia and operating points are unchanged even when reserve awards vanish.", "",
        "Lower procurement reduces effective FCR bounds; gains are not retuned. Extra gamma cannot create resource "
        "when the FI target already reaches availability. Low-availability draws therefore matter even if one high-availability "
        "delivery looks satisfactory. See target-bound fractions and paired error differences.", "",
        "## Reproduction and files", "",
        "Use the existing runner with --tso-procurement --config configs/fcr_requirement.toml "
        "--output-dir results/fcr_requirement, then --tso-procurement-report results/fcr_requirement. "
        "The latter performs no integrations. See docs/FCR_REQUIREMENT_STUDY.md for protocol and docs/FLEET_STUDY.md "
        "for unchanged machine parameter tables and provenance.", "",
        "Figures: frequency.png; provider_responses.png (all machines); fi_response.png (targets, response and "
        "dashed availability); fi_finances.png; tso_spending.png (validation means and separate accounting units). "
        "CSV tables: comparison_summary, paired_validation, paired_sample_differences, economic_sensitivity, "
        "provider_delivery and fi_delivery. New case directories retain full replay inputs, samples, checks and timings.", "",
        f"{report['new_cases']} new optimizations, {report['referenced_cases']} referenced cases, "
        f"{report['new_optimizer_simulations']} new optimizer integrations including refinement checks and delivery, "
        f"plus {report['fcr_only_validation_simulations']} full-FCR gamma-zero validation integrations; "
        f"{report['baseline_files_unchanged']} reference files unchanged. New cumulative optimizer CPU: "
        f"{report['new_optimizer_cpu_s']/60:.2f} minutes. Best tested gamma only; no global optimality certificate. "
        "No changes to physics, source-inspired machine parameters, availability or economic calibration."]
    (output_dir / "ANALYSIS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
