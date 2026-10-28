"""Reproducible fixed-policy FCR/FI/FPP comparison through the existing model."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import platform
from time import perf_counter, process_time
import tomllib

import numpy as np
import pandas as pd

from .fpp import (DispatchSchedule, RegulationPrices, SOURCE_PROFILE, SAMPLE_SECONDS,
                  complete_interval_count, settle_fpp)
from .providers import FCRProviderParameters, FPPProviderParameters
from .simulation import realize_fcr_block, simulate_fcr_power_step, power_deficit_events
from .metrics import summarize_fpp_benchmark, summarize_fpp_assessment


POLICIES = ("fcr_alone", "bilinear_fi", "fpp")


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def load_fpp_overlay(path: Path):
    from .experiment import configured_scenarios
    overlay = tomllib.loads(path.read_text(encoding="utf-8"))
    unknown = set(overlay) - {"base_config", "scenario_name", "source_profile", "scenario", "numerics",
                              "assessment", "assessment_schedule", "schedules", "prices", "disturbance_changes"}
    if unknown:
        raise ValueError(f"unsupported FPP overlay fields: {sorted(unknown)}")
    if overlay["source_profile"] != SOURCE_PROFILE:
        raise ValueError("unsupported FPP source profile; constants are not tuning knobs")
    base_path = (path.parent / overlay["base_config"]).resolve()
    source = base_path.read_text(encoding="utf-8")
    base = json.loads(source) if base_path.suffix == ".json" else tomllib.loads(source)
    variants = {name: config for name, _, config in configured_scenarios(base)}
    if overlay["scenario_name"] not in variants:
        raise ValueError("FPP overlay names an unknown base scenario")
    config = variants[overlay["scenario_name"]]
    if set(overlay["scenario"]) - {"final_time_s", "output_time_step_s", "maximum_solver_step_s"}:
        raise ValueError("FPP overlay only changes horizon and numerical sampling, not physical/market inputs")
    config["scenario"].update(overlay["scenario"])
    complete_interval_count(config["scenario"]["final_time_s"])
    changes = overlay.get("disturbance_changes", [])
    if any(set(row) != {"time_s", "net_power_deficit_mw"} for row in changes):
        raise ValueError("disturbance changes require time_s and net_power_deficit_mw")
    power_deficit_events(config["scenario"]["power_deficit_mw"], config["scenario"]["event_time_s"],
        config["scenario"]["final_time_s"], [(r["time_s"], r["net_power_deficit_mw"]) for r in changes])
    numerics = overlay["numerics"]
    if set(numerics) != {"relative_tolerance", "absolute_tolerance"} or not all(
            np.isfinite(v) and v > 0 for v in numerics.values()):
        raise ValueError("declare positive relative and absolute numerical tolerances")
    assessment = overlay["assessment"]
    if (set(assessment) != {"samples", "seed"} or type(assessment["samples"]) is not int
            or assessment["samples"] < 2 or type(assessment["seed"]) is not int or assessment["seed"] < 0):
        raise ValueError("assessment requires at least two samples and a nonnegative integer seed")
    prices = RegulationPrices(**overlay["prices"]) if "prices" in overlay else None
    if overlay.get("assessment_schedule", "constant") not in [s["name"] for s in overlay["schedules"]]:
        raise ValueError("assessment_schedule must name a declared dispatch reference")
    return overlay, config, prices, base_path


def make_dispatch_schedules(cleared, raw_schedules, final_time_s):
    count = complete_interval_count(final_time_s)
    providers = cleared.providers
    ids = tuple(p.provider_id for p in providers)
    initial = np.array([p.operating_power_mw for p in providers])
    schedules = {}
    for raw in raw_schedules:
        if set(raw) - {"name", "target_offsets_mw"}:
            raise ValueError("unsupported schedule fields")
        name = raw["name"]
        if not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_" for c in name) or name in schedules:
            raise ValueError("schedule names must be unique simple lowercase names")
        targets = np.tile(initial, (count + 1, 1))
        for identity, offsets in raw.get("target_offsets_mw", {}).items():
            if identity not in ids or len(offsets) != count:
                raise ValueError("target offsets require a known provider and one value per interval")
            targets[1:, ids.index(identity)] += np.asarray(offsets, dtype=float)
        schedules[name] = DispatchSchedule(ids, targets, initial,
            np.array([p.minimum_power_mw for p in providers]),
            np.array([p.maximum_power_mw for p in providers]),
            np.array([p.availability.capacity_mw for p in providers]))
    if "constant" not in schedules or np.any(schedules["constant"].targets_mw != initial):
        raise ValueError("include a constant-target baseline named constant")
    return schedules


def policy_response_parameters(cleared, block, policy):
    """Only original FI assignments change; FCR parameters are returned intact."""
    if policy not in POLICIES:
        raise ValueError("unknown fixed comparison policy")
    responses = []
    for p, response, mode, gain, settlement in zip(cleared.providers, block.response_parameters,
            cleared.participation_modes, cleared.gains_mw_per_hz, block.settlements, strict=True):
        if mode != "fi" or policy == "bilinear_fi":
            responses.append(response)
        elif policy == "fcr_alone":
            responses.append(FCRProviderParameters(p.provider_id, gain, p.time_constant_s, 0.))
        else:
            responses.append(FPPProviderParameters(p.provider_id, gain, p.time_constant_s,
                                                   settlement.available_mw, p.fi_rho_eur_per_mw2_s))
    return tuple(responses)


def save_trajectory(trajectory, schedule, directory):
    from .experiment import save_fi_accounting
    data = {"time_s": trajectory.time_s, "frequency_hz": trajectory.frequency_hz,
            "power_deficit_mw": trajectory.power_deficit_mw,
            "delta_frequency_hz": trajectory.delta_frequency_hz,
            "rocof_hz_per_s": trajectory.rocof_hz_per_s,
            "integrated_squared_error_hz2_s": trajectory.integrated_squared_frequency_error_hz2_s}
    references = schedule.reference_mw(trajectory.time_s)
    for j, identity in enumerate(trajectory.provider_ids):
        data.update({f"{identity}_response_mw": trajectory.provider_response_mw[:, j],
                     f"{identity}_target_mw": trajectory.provider_requested_response_mw[:, j],
                     f"{identity}_measured_mw": trajectory.provider_active_power_mw[:, j],
                     f"{identity}_reference_mw": references[:, j],
                     f"{identity}_deviation_mw": trajectory.provider_active_power_mw[:, j] - references[:, j]})
    if trajectory.fpp_activation_cost_eur is not None:
        for j, identity in enumerate(trajectory.fpp_provider_ids):
            data[f"{identity}_activation_cost_eur"] = trajectory.fpp_activation_cost_eur[:, j]
    pd.DataFrame(data).to_csv(directory / "trajectory.csv", index=False)
    pd.DataFrame(trajectory.disturbance_limits).to_csv(directory / "disturbance_limits.csv", index=False)
    if trajectory.fi is not None:
        save_fi_accounting(trajectory, directory)


def run_fpp_study(overlay_path: Path, output_dir: Path):
    from .experiment import prepare_experiment
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if (output_dir / "study_status.json").exists():
        raise ValueError("study already exists; use --fpp-report or a new output directory")
    wall_start, cpu_start = perf_counter(), process_time()
    overlay, config, prices, base_path = load_fpp_overlay(overlay_path)
    write_json(output_dir / "study_status.json", {"status": "running"})
    try:
        point, cleared, parameters = prepare_experiment(config)
        original_modes = dict(zip((p.provider_id for p in cleared.providers), cleared.participation_modes))
        switched = [p for p in cleared.providers if original_modes[p.provider_id] == "fi"]
        if not switched or any(p.fi_gamma_eur_per_hz_mw_s != 50 for p in switched):
            raise ValueError("initial fixed comparison requires an FI assignment with explicit gamma=50")
        scenario = config["scenario"]
        changes = [(row["time_s"], row["net_power_deficit_mw"])
                   for row in overlay.get("disturbance_changes", [])]
        events = power_deficit_events(scenario["power_deficit_mw"], scenario["event_time_s"],
                                     scenario["final_time_s"], changes)
        pd.DataFrame(events, columns=["time_s", "net_power_deficit_mw"]).to_csv(
            output_dir / "disturbance_events.csv", index=False)
        schedules = make_dispatch_schedules(cleared, overlay["schedules"], scenario["final_time_s"])
        assessment_schedule = overlay.get("assessment_schedule", "constant")
        # Freeze all policy assignments and dispatch targets BEFORE drawing.
        assignments = {policy: {identity: ("fpp" if policy == "fpp" else "inactive")
                       if mode == "fi" and policy != "bilinear_fi" else mode
                       for identity, mode in original_modes.items()} for policy in POLICIES}
        write_json(output_dir / "policy_assignments.json", assignments)
        pd.DataFrame([{"provider_id": identity, "original_control_role": mode,
                       "fpp_case_control_role": assignments["fpp"][identity],
                       "fpp_settlement_eligible": True}
                      for identity, mode in original_modes.items()]).to_csv(
                          output_dir / "fpp_participants.csv", index=False)
        write_json(output_dir / "effective_config.json", config)
        write_json(output_dir / "overlay.json", overlay)
        write_json(output_dir / "operating_point.json", {**asdict(point), "case_path": str(point.case_path)})
        write_json(output_dir / "frequency_parameters.json", asdict(parameters))
        write_json(output_dir / "price_scenario.json", None if prices is None else asdict(prices))
        providers = pd.DataFrame([{**asdict(p), "resolved_gain_mw_per_hz": gain,
                                   "original_role": mode}
                for p, gain, mode in zip(cleared.providers, cleared.gains_mw_per_hz,
                                         cleared.participation_modes, strict=True)])
        providers.to_json(output_dir / "providers.json", orient="records", indent=2)
        pd.DataFrame([asdict(d.offer) for d in cleared.decisions]).to_csv(output_dir / "offers.csv", index=False)
        pd.DataFrame([asdict(a) for a in cleared.market.awards]).to_csv(output_dir / "awards.csv", index=False)
        for name, schedule in schedules.items():
            table = pd.DataFrame(schedule.targets_mw, columns=schedule.provider_ids)
            table.insert(0, "time_s", np.arange(len(table)) * 300)
            table.to_csv(output_dir / f"{name}_dispatch_targets.csv", index=False)
        seed = overlay["assessment"]["seed"]
        streams = np.random.SeedSequence(seed).spawn(2)
        rows = []
        for bank, size, stream in (("assessment", overlay["assessment"]["samples"], streams[0]),
                                    ("delivery", 1, streams[1])):
            rng = np.random.default_rng(stream)
            for sample in range(size):
                rows.append(dict(bank=bank, sample=sample, **{
                    p.provider_id: p.availability.sample_mw(rng) for p in cleared.providers}))
        banks = pd.DataFrame(rows)
        banks.to_csv(output_dir / "availability_draws.csv", index=False)
        write_json(output_dir / "provenance.json", {
            "seed": seed, "streams": {"assessment": [0], "delivery": [1]},
            "generator": "numpy.random.default_rng (PCG64)", "numpy_version": np.__version__,
            "python_version": platform.python_version(), "profile": SOURCE_PROFILE,
            "base_config": str(base_path), "base_config_sha256": hashlib.sha256(base_path.read_bytes()).hexdigest(),
            "overlay_path": str(overlay_path.resolve()),
            "disturbance_changes": overlay.get("disturbance_changes", []),
            "draws_sha256": hashlib.sha256((output_dir / "availability_draws.csv").read_bytes()).hexdigest(),
            "policies_selected_on_assessment": False, "initial_history": "nominal at -116,-112,...,0 seconds",
            "rocof": "unfiltered physical RHS, exact left/right limits of every event included; no smoothing window; maximum absolute on [0,H]",
            "metering_model": "solved_operating_power_plus_delivered_response",
            "dispatch_reference_role": "settlement_only; no tracking imposed on physical power",
            "assessment_schedule": assessment_schedule,
            "dispatch_targets_file": f"{assessment_schedule}_dispatch_targets.csv",
            "scope": "one aggregate area; exact synchronized model-output metering; FPP coefficients unless prices declared"})
        options = dict(power_deficit_mw=scenario["power_deficit_mw"], start_time_s=scenario["event_time_s"],
            power_deficit_changes=changes,
            final_time_s=scenario["final_time_s"], output_time_step_s=scenario["output_time_step_s"],
            maximum_solver_step_s=scenario["maximum_solver_step_s"],
            fi_decision_interval_s=config["fi"]["decision_interval_s"], **overlay["numerics"],
            provider_operating_power_mw=[p.operating_power_mw for p in cleared.providers],
            measurement_times_s=np.arange(4, scenario["final_time_s"] + 1, SAMPLE_SECONDS))
        records, financial_rows, timings, checks, saturation_rows, all_accounts, all_directions = [], [], [], [], [], [], []
        deliveries = {}
        for row in rows:
            draw = {p.provider_id: row[p.provider_id] for p in cleared.providers}
            block = realize_fcr_block(cleared, draw)
            for settlement in block.settlements:
                financial_rows.append({"bank": row["bank"], "sample": row["sample"], **asdict(settlement)})
            for name, schedule in schedules.items():
                if row["bank"] == "assessment" and name != assessment_schedule:
                    continue
                for policy in POLICIES:
                    start_wall, start_cpu = perf_counter(), process_time()
                    responses = policy_response_parameters(cleared, block, policy)
                    trajectory = simulate_fcr_power_step(parameters, responses, dispatch_schedule=schedule, **options)
                    metrics, saturation = summarize_fpp_benchmark(trajectory, schedule, scenario["event_time_s"])
                    tags = {"bank": row["bank"], "sample": row["sample"], "schedule": name, "policy": policy}
                    directory = output_dir / row["bank"] / f"sample_{row['sample']:03d}" / name / policy
                    directory.mkdir(parents=True, exist_ok=True)
                    # Every run retains total meter trajectories; settlement has no controller dependency.
                    save_trajectory(trajectory, schedule, directory)
                    if policy == "fpp":
                        settlement = settle_fpp(trajectory.time_s, trajectory.delta_frequency_hz,
                                                 trajectory.provider_active_power_mw, schedule, prices)
                        for table_name, table in settlement.items():
                            table.to_csv(directory / f"fpp_{table_name}.csv", index=False)
                        all_accounts.append(settlement["accounts"].assign(**tags))
                        all_directions.append(settlement["directions"].assign(**tags))
                        for _, group in settlement["accounts"].groupby(["interval", "direction"]):
                            if abs(group.coefficient_mw_h.sum()) > 1e-10:
                                raise RuntimeError("FPP coefficient ledger does not balance including residual")
                            if prices is not None and abs(group.net_settlement.sum()) > 1e-8:
                                raise RuntimeError("FPP monetary ledger does not balance including residual")
                    records.append({**tags, **metrics})
                    saturation_rows.extend({**tags, **r} for r in saturation)
                    timings.append({**tags, "kind": "comparison", "wall_s": perf_counter() - start_wall,
                                    "cpu_s": process_time() - start_cpu})
                    if row["bank"] == "delivery":
                        deliveries[name, policy] = (trajectory, responses)
                    print(f"FPP study: {row['bank']} {row['sample']} {name} {policy} complete", flush=True)
        # Tighter integration and denser output reuse the independent delivery draw.
        for policy in POLICIES:
            coarse, responses = deliveries[assessment_schedule, policy]
            fine_options = {**options, "maximum_solver_step_s": options["maximum_solver_step_s"] / 2,
                "output_time_step_s": options["output_time_step_s"] / 2,
                "relative_tolerance": options["relative_tolerance"] / 10,
                "absolute_tolerance": options["absolute_tolerance"] / 10}
            start_wall, start_cpu = perf_counter(), process_time()
            fine = simulate_fcr_power_step(parameters, responses, dispatch_schedule=schedules[assessment_schedule], **fine_options)
            fine_metrics, _ = summarize_fpp_benchmark(fine, schedules[assessment_schedule], scenario["event_time_s"])
            indices = np.searchsorted(fine.time_s, coarse.time_s)
            # Exact grids are nested for the supplied overlay; diagnose incompatible numerical grids.
            if not np.allclose(fine.time_s[indices], coarse.time_s, rtol=0, atol=1e-10):
                raise RuntimeError("refinement output grid is not nested")
            check = dict(policy=policy, frequency_max_diff_hz=float(np.max(abs(coarse.frequency_hz - fine.frequency_hz[indices]))),
                response_max_diff_mw=float(np.max(abs(coarse.provider_response_mw - fine.provider_response_mw[indices]))),
                integrated_error_diff_hz2_s=float(abs(coarse.integrated_squared_frequency_error_hz2_s[-1]
                                                      - fine.integrated_squared_frequency_error_hz2_s[-1])))
            coarse_metrics, _ = summarize_fpp_benchmark(coarse, schedules[assessment_schedule], scenario["event_time_s"])
            for metric in ("nadir_sampled_hz", "zenith_sampled_hz", "rocof_max_abs_hz_per_s", "switched_activation_cost_eur", "fi_net_settlement_eur"):
                check[metric + "_difference"] = abs(coarse_metrics[metric] - fine_metrics[metric])
            if policy == "fpp":
                a, b = [settle_fpp(t.time_s, t.delta_frequency_hz, t.provider_active_power_mw,
                                    schedules[assessment_schedule], prices) for t in (coarse, fine)]
                check["coefficient_max_diff_mw_h"] = float(np.max(abs(a["accounts"].coefficient_mw_h - b["accounts"].coefficient_mw_h)))
                check["rcr_max_diff_mw"] = float(np.max(abs(a["directions"].rcr_mw - b["directions"].rcr_mw)))
                if check["coefficient_max_diff_mw_h"] > 1e-6 or check["rcr_max_diff_mw"] > 1e-5:
                    raise RuntimeError("FPP settlement refinement exceeds tolerance")
            if (check["frequency_max_diff_hz"] > 1e-6 or check["response_max_diff_mw"] > 1e-5
                    or check["integrated_error_diff_hz2_s"] > 1e-7
                    or check["nadir_sampled_hz_difference"] > 1e-5
                    or check["zenith_sampled_hz_difference"] > 1e-5
                    or check["rocof_max_abs_hz_per_s_difference"] > 1e-6
                    or check["switched_activation_cost_eur_difference"] > 1e-4
                    or check["fi_net_settlement_eur_difference"] > 1e-4):
                raise RuntimeError("fixed-policy numerical refinement exceeds declared tolerances")
            checks.append(check)
            timings.append(dict(policy=policy, kind="refinement", wall_s=perf_counter() - start_wall,
                                cpu_s=process_time() - start_cpu))
        ramp_checks = []
        for name in schedules:
            if name == "constant":
                continue
            for policy in POLICIES:
                a, b = deliveries["constant", policy][0], deliveries[name, policy][0]
                difference = float(np.max(abs(a.delta_frequency_hz - b.delta_frequency_hz)))
                if difference != 0 or not np.array_equal(a.provider_response_mw, b.provider_response_mw):
                    raise RuntimeError("settlement reference unexpectedly changed physical response")
                meter_difference = float(np.max(abs(a.provider_active_power_mw - b.provider_active_power_mw)))
                if meter_difference != 0:
                    raise RuntimeError("settlement reference unexpectedly changed actual metered power")
                reference_change = schedules[name].reference_mw(b.time_s) - schedules["constant"].reference_mw(a.time_s)
                deviation_change = ((b.provider_active_power_mw - schedules[name].reference_mw(b.time_s))
                                    - (a.provider_active_power_mw - schedules["constant"].reference_mw(a.time_s)))
                deviation_error = float(np.max(abs(deviation_change + reference_change)))
                if deviation_error > 1e-10:
                    raise RuntimeError("FPP deviation does not equal actual meter minus reference")
                ramp_checks.append(dict(schedule=name, policy=policy, frequency_max_diff_hz=difference,
                    meter_max_diff_mw=meter_difference, deviation_change_identity_error_mw=deviation_error))
        for filename, table in (("metrics", pd.DataFrame(records)), ("saturation", pd.DataFrame(saturation_rows)),
                                ("fcr_block_accounting", pd.DataFrame(financial_rows)), ("timings", pd.DataFrame(timings)),
                                ("numerical_checks", pd.DataFrame(checks)), ("ramp_checks", pd.DataFrame(ramp_checks)),
                                ("fpp_accounts", pd.concat(all_accounts, ignore_index=True)),
                                ("fpp_directions", pd.concat(all_directions, ignore_index=True))):
            table.to_csv(output_dir / f"{filename}.csv", index=False)
        write_json(output_dir / "verification.json", {"solver_success": True, "response_bounds": True,
            "total_power_limits": True, "schedule_full_support_headroom": True, "balanced_ramps": True,
            "numerical_refinement": True, "residual_ledger_balance": True,
            "metering_model": "solved_operating_power_plus_delivered_response",
            "assessment_samples": len(banks[banks.bank == "assessment"]), "integrations": len(timings)})
        write_json(output_dir / "study_status.json", {"status": "complete", "integrations": len(timings),
            "simulation_and_export_wall_s": perf_counter() - wall_start,
            "simulation_and_export_cpu_s": process_time() - cpu_start})
        run_fpp_report(output_dir)
    except Exception as error:
        write_json(output_dir / "study_status.json", {"status": "failed", "error": str(error),
                                                      "wall_s": perf_counter() - wall_start})
        raise


def run_fpp_report(output_dir: Path):
    from .plotting import plot_fpp_comparison
    start = perf_counter()
    status = json.loads((output_dir / "study_status.json").read_text(encoding="utf-8"))
    if status["status"] != "complete":
        raise ValueError("FPP report requires a completed study")
    metrics = pd.read_csv(output_dir / "metrics.csv")
    overlay = json.loads((output_dir / "overlay.json").read_text(encoding="utf-8"))
    assessment_schedule = overlay.get("assessment_schedule", "constant")
    summary, paired = summarize_fpp_assessment(metrics, schedule=assessment_schedule)
    summary.to_csv(output_dir / "assessment_statistics.csv", index=False)
    paired.to_csv(output_dir / "paired_differences.csv", index=False)
    accounts = pd.read_csv(output_dir / "fpp_accounts.csv")
    directions = pd.read_csv(output_dir / "fpp_directions.csv")
    plot_fpp_comparison(output_dir, metrics, summary, accounts, schedule=assessment_schedule)
    save_reversal_diagnostics(output_dir, schedule=assessment_schedule)
    write_fpp_analysis(output_dir, metrics, summary, paired, accounts, directions, status)
    write_json(output_dir / "report_status.json", {"status": "complete", "integrations": 0,
                                                  "wall_s": perf_counter() - start})


def save_reversal_diagnostics(output_dir, schedule="constant"):
    """Saved-grid zero crossings and exact event limits, for independent delivery."""
    event_path = output_dir / "disturbance_events.csv"
    if not event_path.exists():
        return
    events = pd.read_csv(event_path)
    if len(events) < 2:
        return
    assignments = json.loads((output_dir / "policy_assignments.json").read_text(encoding="utf-8"))
    switched = [i for i, role in assignments["fpp"].items() if role == "fpp"]
    rows = []
    for event_index in range(1, len(events)):
        event_time = float(events.time_s.iloc[event_index])
        for policy in POLICIES:
            directory = output_dir / "delivery/sample_000" / schedule / policy
            trace = pd.read_csv(directory / "trajectory.csv")
            end = float(events.time_s.iloc[event_index+1]) if event_index+1 < len(events) else float(trace.time_s.iloc[-1])
            part = trace[(trace.time_s >= event_time) & (trace.time_s <= end)]
            times = part.time_s.to_numpy()
            def crossing(values):
                values = np.asarray(values)
                indices = np.flatnonzero(values[:-1]*values[1:] < 0)
                if not len(indices):
                    return None
                j = indices[0]
                return float(times[j] - values[j] * (times[j+1]-times[j]) / (values[j+1]-values[j]))
            limit = pd.read_csv(directory / "disturbance_limits.csv").iloc[event_index].to_dict()
            rows.append(dict(policy=policy, **limit,
                frequency_first_zero_crossing_s=crossing(part.delta_frequency_hz),
                switched_response_first_zero_crossing_s=crossing(part[[f"{i}_response_mw" for i in switched]].sum(axis=1)),
                post_event_zenith_hz=float(part.frequency_hz.max()),
                post_event_nadir_hz=float(part.frequency_hz.min())))
    pd.DataFrame(rows).to_csv(output_dir / "reversal_diagnostics.csv", index=False)


def write_fpp_analysis(output_dir, metrics, summary, paired, accounts, directions, status):
    """Write the interpretation alongside reproducible tables, without new simulations."""
    def mean(policy, metric):
        return summary[(summary.policy == policy) & (summary.metric == metric)]["mean"].iloc[0]
    labels = {"fcr_alone": "FCR alone", "bilinear_fi": "Bilinear FI (gamma=50)", "fpp": "FPP fixed droop"}
    config = json.loads((output_dir / "effective_config.json").read_text(encoding="utf-8"))
    overlay = json.loads((output_dir / "overlay.json").read_text(encoding="utf-8"))
    assessment_schedule = overlay.get("assessment_schedule", "constant")
    provenance = json.loads((output_dir / "provenance.json").read_text(encoding="utf-8"))
    actual_meter = provenance.get("metering_model") == "solved_operating_power_plus_delivered_response"
    changes = overlay.get("disturbance_changes", [])
    events = [(config["scenario"]["event_time_s"], config["scenario"]["power_deficit_mw"])]
    events += [(r["time_s"], r["net_power_deficit_mw"]) for r in changes]
    event_description = "; ".join(f"{d:+g} MW net deficit from {t:g} s" for t, d in events)
    meter_description = (
        "Meters read the actual aggregate-model output P_i,0 + u_i(t), with u_i the numerically "
        "integrated delivered response (z_i for FI/FPP). They are not constructed from DT or the "
        "dispatch reference. No dispatch-tracking dynamics or artificial tracking error are added. "
        "Settlement computes d_i = P_i^meter - ref_i, including any failure to follow the prescribed "
        "reference. Changing DT alone cannot change frequency, RoCoF or actual output."
        if actual_meter else
        "Historical run: metered power was constructed as reference plus response, imposing ideal "
        "schedule tracking. These saved results retain that older convention; report regeneration "
        "does not convert them to actual-output metering.")
    lines = ["# AEMO-inspired FPP fixed-policy benchmark", "",
        "The same solved five-provider fi_endogenous fleet, prescribed disturbance and FCR clearing are used "
        "for all policies. Every event is recomputed over [0,300] s. No optimizer or policy selection "
        "uses these assessment draws. This compares complete fixed control/settlement policies; it "
        "does not isolate the behavioural effect of a payment rule.", "",
        f"Disturbance: zero before the first event; {event_description}. Negative net deficit means "
        "surplus. Each later value replaces the preceding level. States, financial ledgers, fixed "
        "availability and decision clocks carry through every change. Event times and magnitudes "
        "are declared scenario assumptions, selected before the runs.", "",
        "## Metering and dispatch reference", "", meter_description, "",
        f"The predeclared assessment and representative-result reference is `{assessment_schedule}`. "
        "DT values are scenario inputs, not a dispatch optimization. How to select future DT values "
        "remains open. The initial row is the solved ANDES operating point. The 300 s endpoint "
        "is a target, not a claim that physical output reaches it.", "",
        "| Provider | Initial DT [MW] | DT at 300 s [MW] |", "|---|---:|---:|",
        *[f"| {identity} | {targets.iloc[0][identity]:.9f} | {targets.iloc[1][identity]:.9f} |"
          for targets in [pd.read_csv(output_dir / f"{assessment_schedule}_dispatch_targets.csv")]
          for identity in targets.columns if identity != "time_s"], "",
        "## Assessment bank", "",
        f"{int(summary.n.iloc[0])} matched draws; seed and streams are in provenance.json. "
        "These exploratory means and pointwise Student-t intervals do not establish population "
        "optimality or practical equivalence. Paired differences are saved separately.", "",
        "| Policy | Nadir [Hz] | Zenith [Hz] | Terminal error [Hz] | Integrated error [Hz² s] | Max abs RoCoF [Hz/s] |",
        "|---|---:|---:|---:|---:|---:|"]
    for policy in POLICIES:
        values = [mean(policy, m) for m in ("nadir_sampled_hz", "zenith_sampled_hz", "terminal_error_hz",
                                           "frequency_error_hz2_s", "rocof_max_abs_hz_per_s")]
        lines.append(f"| {labels[policy]} | " + " | ".join(f"{v:.8g}" for v in values) + " |")
    fi_reduction = 100 * (1 - mean("bilinear_fi", "frequency_error_hz2_s") / mean("fcr_alone", "frequency_error_hz2_s"))
    fpp_reduction = 100 * (1 - mean("fpp", "frequency_error_hz2_s") / mean("fcr_alone", "frequency_error_hz2_s"))
    lines += ["", f"In this fixed-policy assessment, bilinear FI reduces mean integrated error by {fi_reduction:.2f}% "
        f"and FPP fixed droop by {fpp_reduction:.2f}% relative to FCR alone. This is conditional "
        "on the declared gains, deadband, gamma, disturbance and small assessment bank, not a "
        "general ranking of incentive mechanisms."]
    lines += ["", "RoCoF is df/dt from the unfiltered physical model RHS, including the exact left and right "
        "limits of every disturbance. No smoothing or measurement window is applied. The reported summary is the "
        "maximum absolute value on [0,300] s over saved RHS samples and those limits. Nadir and "
        "zenith are sampled extrema; integrated squared error is a live ODE ledger. The initial "
        "RoCoF is common because inertia and the disturbance are identical and lagged response "
        "is initially zero. No frequency-security threshold is introduced.", "",
        "| Paired difference (first minus reference) | Metric | Mean | Pointwise 95% CI |",
        "|---|---|---:|---|"]
    for _, row in paired[paired.metric.isin(["nadir_sampled_hz", "zenith_sampled_hz", "frequency_error_hz2_s", "rocof_max_abs_hz_per_s"])].iterrows():
        lines.append(f"| {labels[row.policy]} minus {labels[row.reference]} | {row.metric} | "
                     f"{row['mean']:.7g} | [{row.ci95_low:.7g}, {row.ci95_high:.7g}] |")
    if changes:
        lines += ["", "## Reversal diagnostics: independent representative delivery", "",
            "The surplus arrives while providers still deliver upward power from the preceding deficit. "
            "Frequency and responses are continuous; the disturbance step changes RoCoF immediately. "
            "At near equilibrium, the 10 to -7 MW level change gives a RoCoF jump of 17/M, not 7/M. "
            "Instantaneous-frequency controls respond before the four-second FM changes direction. "
            "The deadband may subsequently reduce the FPP target to zero before a new small "
            "steady response develops. These are dynamics of the prescribed policies, not price effects.", "",
            "| Policy | Event [s] | RoCoF left [Hz/s] | RoCoF right [Hz/s] | First frequency zero crossing [s] | Switched response zero crossing [s] |",
            "|---|---:|---:|---:|---:|---:|"]
        for _, row in pd.read_csv(output_dir / "reversal_diagnostics.csv").iterrows():
            values = [row.time_s, row.rocof_left_hz_per_s, row.rocof_right_hz_per_s,
                      row.frequency_first_zero_crossing_s, row.switched_response_first_zero_crossing_s]
            lines.append(f"| {labels[row.policy]} | " + " | ".join("not applicable" if pd.isna(v) else f"{v:.8g}" for v in values) + " |")
        lines += ["", "Zero-crossing times are linearly interpolated on the saved 0.05 s output grid; "
            "RoCoF limits use the live solver state exactly at each event, without smoothing. "
            "The following FPP rows show raw sampled frequency and FM around each reversal; "
            "an exclusion affects performance only, not control, reliability or RCR.", "",
            "| Time [s] | Frequency deviation [Hz] | FM [Hz] | Performance excluded |", "|---|---:|---:|---|"]
        samples = pd.read_csv(output_dir / "delivery/sample_000" / assessment_schedule / "fpp/fpp_samples.csv")
        mask = np.zeros(len(samples), dtype=bool)
        for change in changes:
            mask |= (samples.time_s >= change["time_s"]-5) & (samples.time_s <= change["time_s"]+16)
        for _, row in samples[mask].iterrows():
            lines.append(f"| {row.time_s:g} | {row.delta_frequency_hz:.8g} | {row.fm_hz:.8g} | {row.performance_excluded} |")
    lines += ["", "## Physical checks and saturation", "",
        "FCR awards, effective reserves and controllers are retained. Only the original FI provider "
        "is inactive, bilinear-controlled or FPP-droop-controlled across the three policies. The FPP "
        "controller uses the existing resolved K and T, actual availability, instantaneous frequency "
        "and a 0.015 Hz deadband. Its response and activation cost have continuous dynamics; "
        "the four-second settlement clock adds no control delay.", "",
        "The complete scheduled path preserves both active-power margins for the full unchanged "
        "availability support. Total delivered power and response bounds are checked on every saved "
        "sample. Bounded first-order targets preserve the continuous response invariant. Numerical "
        "refinement uses the same delivery draws, half solver/output steps and tenfold tighter "
        "tolerances. See numerical_checks.csv and verification.json.", "",
        "Saturation in saturation.csv separates requested targets from delivered response. Fractions "
        "are trapezoidal estimates over post-event output samples: target distance <=1e-9 MW, "
        "delivered distance <=1e-6 MW from its bound. Zero availability is reported separately.", "",
        "| Policy | Switched provider: mean target-bound fraction | Mean delivered-bound fraction |",
        "|---|---:|---:|"]
    saturation = pd.read_csv(output_dir / "saturation.csv")
    assignments = json.loads((output_dir / "policy_assignments.json").read_text(encoding="utf-8"))
    switched = [i for i, role in assignments["fpp"].items() if role == "fpp"]
    for policy in POLICIES:
        part = saturation[(saturation.bank == "assessment") & (saturation.policy == policy)
                          & saturation.provider_id.isin(switched)]
        lines.append(f"| {labels[policy]} | {part.target_bound_fraction.mean():.6g} | {part.delivered_bound_fraction.mean():.6g} |")
    lines += ["", "## FPP settlement and separate economic quantities", "",
        "All five metered units participate in FPP settlement, plus the accounting residual. "
        "The residual is minus their summed deviations, not another physical response or the "
        "disturbance. Performance includes helpful and harmful contributions; same-sign "
        "normalization includes the residual. Misalignment excludes performance samples only. "
        "Reliability checks count raw directional FM samples and test FM amplitude. Unreliable "
        "directions retain NULL performance/CF and have RCR=0 and zero transfer coefficients.", ""]
    price_scenario = json.loads((output_dir / "price_scenario.json").read_text(encoding="utf-8"))
    if price_scenario is None:
        lines += ["No justified external monetary scenario is supplied for this aggregate EUR-based fleet. "
            "FPP monetary fields are absent, not zero revenue. Directional CF*RCR/12 coefficients "
            "are exported in MW h for repricing without rerunning dynamics. No arbitrary multiplier "
            "or FCR block-price substitution is used. Synthetic monetary tests validate units and signs; "
            "they are not calibrated revenue. No FI/FPP revenue or profitability ranking is claimed.", ""]
    else:
        lines += [f"FPP prices: {price_scenario}. FPP amounts retain this currency. "
                  "No AUD/EUR conversion is applied; regional proxies remain labelled proxies.", ""]
    lines += ["| Policy | FI gross rewards [EUR] | FI charges [EUR] | FI net settlement [EUR] | Switched-provider activation cost [EUR] |",
              "|---|---:|---:|---:|---:|"]
    for policy in POLICIES:
        values = [mean(policy, m) for m in ("fi_gross_rewards_eur", "fi_charge_magnitudes_eur",
                                           "fi_net_settlement_eur", "switched_activation_cost_eur")]
        lines.append(f"| {labels[policy]} | " + " | ".join(f"{v:.7g}" for v in values) + " |")
    block_accounts = pd.read_csv(output_dir / "fcr_block_accounting.csv")
    block_payment = block_accounts.groupby(["bank", "sample"]).capacity_payment_eur.sum()
    lines += ["", f"FCR capacity payments are {block_payment.iloc[0]:.6g} EUR per whole block in every policy. "
        "These are 300-second FI transfers and known switched-provider activation costs. "
        "No activation costs are invented for FCR providers. FCR accounting in fcr_block_accounting.csv "
        "retains EUR per whole block and is shared by the policies. A balanced FPP transfer ledger "
        "including residual does not imply zero resource cost.", "",
        "| FPP assessment direction | Reliable intervals | Mean RCR [MW] |",
        "|---|---:|---:|"]
    for direction in ("raise", "lower"):
        part = directions[(directions.bank == "assessment") & (directions.direction == direction)]
        lines.append(f"| {direction} | {int(part.reliable.sum())}/{len(part)} | {part.rcr_mw.mean():.8g} |")
    lines += ["", "| Direction | Entity | Mean performance [MW Hz] | Mean CF | Mean coefficient [MW h] |",
              "|---|---|---:|---:|---:|"]
    for (direction, identity), part in accounts[accounts.bank == "assessment"].groupby(["direction", "provider_id"], sort=False):
        values = [part.performance_mw_hz.mean(), part.contribution_factor.mean(), part.coefficient_mw_h.mean()]
        lines.append(f"| {direction} | {identity} | " + " | ".join("NULL" if pd.isna(v) else f"{v:.8g}" for v in values) + " |")
    lines += ["", "All directional rows, including NULL performance where unreliable, are in fpp_accounts.csv and "
        "fpp_directions.csv. Per-run fpp_samples.csv retains reference, meter, deviation, FM, exclusion "
        "flags, signed deviation sums and residual. fpp_filter_history.csv preserves every reset and "
        "120-second initialization window. Coefficient sums, and monetary sums when priced, balance "
        "with the residual. Residual allocation to real participants and Regulation FCAS procurement "
        "or cost recovery are outside this implementation.", "",
        "## Representative delivery and reference check", "",
        "The figures use an independent delivery stream where labelled; they are separate from "
        "assessment statistics. The C1 +1 MW / C2 -1 MW reference ramp preserves scheduled total "
        "generation and both headroom margins. See ramp_checks.csv for comparisons against the "
        "constant reference on the same draw. " + meter_description, "",
        "- delivery_frequency.png: physical frequency, FM and model RoCoF.",
        "- delivery_responses_schedule.png: control, individual delivery and balanced schedule.",
        "- assessment_comparison.png: assessment means and pointwise uncertainty.",
        "- fpp_settlement_coefficients.png: signed representative coefficients with residual.", "",
        "## Reproduction, timings and scope", "", "```powershell",
        f".\\.venv\\Scripts\\python.exe scripts\\run_fcr.py --fpp-benchmark --config {Path(provenance['overlay_path']).as_posix()} --output-dir results/{output_dir.name}_replay",
        f".\\.venv\\Scripts\\python.exe scripts\\run_fcr.py --fpp-report results/{output_dir.name}", "```", "",
        f"Completed {status['integrations']} integrations, including three tighter checks. "
        f"Simulation/export wall time {status['simulation_and_export_wall_s']:.3f} s; "
        f"process CPU {status['simulation_and_export_cpu_s']:.3f} s. Per-run timings are in timings.csv; "
        "report-only timing is separate and performs zero integrations.", "",
        "Sources, exact equations, version caveats, parameter classification and exclusions are in "
        "../../docs/AEMO_FPP.md. ANDES supplies the initial operating point; the existing aggregate "
        "RK45 model integrates dynamics. This is an adapted benchmark, not an Australian market "
        "reproduction or a profit-maximizing FPP follower. Prices change settlement only. Existing "
        "bilinear FI, FCR controllers, nominal 60 Hz, availability laws and accepted TSO objective "
        "are retained. Gamma optimization remains specific to bilinear FI.", ""]
    test_path = output_dir / "test_results.json"
    if test_path.exists():
        tests = json.loads(test_path.read_text(encoding="utf-8"))
        suite = tests["full_suite"]
        lines += [f"Recorded verification: {suite['tests']} tests {suite['status']} in "
                  f"{suite['elapsed_s']:.3f} s using `{suite['command']}`. "
                  "See test_results.json for subsequent focused checks and review notes.", ""]
    (output_dir / "ANALYSIS.md").write_text("\n".join(lines), encoding="utf-8")
