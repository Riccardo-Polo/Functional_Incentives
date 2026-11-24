"""Single-provider L2 design curves and explicit curve-based TSO selection.

The plant remains simulation.simulate_fcr_power_step. This module owns only
the experiment protocol, persistence and reporting, not another physical model.
"""

from dataclasses import asdict, replace
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re
from time import perf_counter, process_time
import tomllib

import numpy as np
import pandas as pd
from scipy.stats import t as student_t, truncnorm

from .metrics import frequency_performance_metrics, empirical_pareto_flags
from .providers import FIProviderParameters
from .incentives import L2_CONTRACT, L2_LEGACY_CONTRACT, validate_l2_contract
from .simulation import realize_fcr_block, simulate_fcr_power_step, power_deficit_events
from .tso import make_availability_banks


def _json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)


def validate_l2_plan(plan):
    required = {"base_config", "base_scenario", "seed", "design_samples", "validation_samples",
                "final_time_s", "output_time_step_s", "maximum_solver_step_s", "relative_tolerance",
                "absolute_tolerance", "lambda2_values", "gamma_values", "profiles", "workers"}
    if set(plan) - {"selection", "provider_overrides", "reference_study", "l2_contract"} != required:
        raise ValueError("L2 manifest contains missing or unknown settings")
    validate_l2_contract(plan.get("l2_contract", L2_CONTRACT))
    if ("provider_overrides" in plan) != ("reference_study" in plan):
        raise ValueError("L2 variance sensitivity needs provider_overrides and reference_study together")
    if "provider_overrides" in plan:
        overrides = plan["provider_overrides"]
        if (not isinstance(plan["reference_study"], str) or not plan["reference_study"].strip()
                or not isinstance(overrides, dict) or not overrides):
            raise ValueError("L2 variance sensitivity needs a reference study and nonempty overrides")
        for identity, fields in overrides.items():
            if (not identity or not isinstance(fields, dict) or set(fields) != {"variance_mw2"}
                    or isinstance(fields["variance_mw2"], bool)
                    or not np.isfinite(fields["variance_mw2"]) or fields["variance_mw2"] <= 0):
                raise ValueError("L2 sensitivity permits only an explicit positive availability variance")
    for key in ("seed", "design_samples", "validation_samples"):
        if type(plan[key]) is not int or plan[key] < (0 if key == "seed" else 2):
            raise ValueError(f"invalid L2 {key}")
    if type(plan["workers"]) is not int or plan["workers"] < 1:
        raise ValueError("L2 workers must be a positive integer")
    for key in ("final_time_s", "output_time_step_s", "maximum_solver_step_s",
                "relative_tolerance", "absolute_tolerance"):
        if not np.isfinite(plan[key]) or plan[key] <= 0:
            raise ValueError(f"invalid L2 {key}")
    for key in ("lambda2_values", "gamma_values"):
        values = plan[key]
        if (len(values) < 2 or len(set(values)) != len(values) or 0 not in values
                or any(isinstance(x, bool) or not np.isfinite(x) or x < 0 for x in values)):
            raise ValueError(f"invalid L2 {key}")
    if not plan["profiles"]:
        raise ValueError("L2 needs at least one disturbance profile")
    names = []
    for profile in plan["profiles"]:
        if set(profile) != {"name", "label", "purpose", "start_time_s", "power_deficit_mw", "changes", "weight"}:
            raise ValueError("invalid L2 profile fields")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", profile["name"]):
            raise ValueError("invalid L2 profile name")
        names.append(profile["name"])
        if (not np.isfinite(profile["start_time_s"]) or not 0 <= profile["start_time_s"] < plan["final_time_s"]
                or not np.isfinite(profile["power_deficit_mw"]) or profile["power_deficit_mw"] < 0
                or not np.isfinite(profile["weight"]) or profile["weight"] <= 0):
            raise ValueError("invalid L2 profile values")
        power_deficit_events(profile["power_deficit_mw"], profile["start_time_s"],
                             plan["final_time_s"], profile["changes"])
    if len(set(names)) != len(names) or not np.isclose(sum(p["weight"] for p in plan["profiles"]), 1.):
        raise ValueError("profile names must be unique and weights must sum to one")
    if "selection" in plan:
        selection = plan["selection"]
        if (set(selection) != {"lambda2", "gamma", "reason"} or not str(selection["reason"]).strip()
                or selection["lambda2"] not in plan["lambda2_values"]
                or selection["gamma"] not in plan["gamma_values"]):
            raise ValueError("TSO selection needs tested lambda2/gamma and a reason")


def simulate_l2_candidate(block, plant, plan, profile, mechanism, coefficient, *, refined=False):
    responses = tuple(replace(p, gamma_eur_per_hz_mw_s=coefficient)
        if isinstance(p, FIProviderParameters) and mechanism == "bilinear" else p
        for p in block.response_parameters)
    return simulate_fcr_power_step(plant, responses,
        provider_operating_power_mw=block.operating_powers_mw,
        power_deficit_mw=profile["power_deficit_mw"], start_time_s=profile["start_time_s"],
        power_deficit_changes=profile["changes"], final_time_s=plan["final_time_s"],
        disturbance_rise_s=profile.get("disturbance_rise_s", 0.),
        output_time_step_s=plan["output_time_step_s"] / (2 if refined else 1),
        maximum_solver_step_s=plan["maximum_solver_step_s"] / (2 if refined else 1),
        relative_tolerance=plan["relative_tolerance"] / (10 if refined else 1),
        absolute_tolerance=plan["absolute_tolerance"] / (10 if refined else 1),
        fi_decision_interval_s=.1,
        measurement_times_s=power_deficit_events(profile["power_deficit_mw"], profile["start_time_s"],
            plan["final_time_s"], profile["changes"])[:, 0],
        l2_weight_eur_per_hz2=(coefficient if mechanism == "l2" else None),
        # Unversioned saved plans predate passive-cost compensation. New study
        # entry points resolve and persist the current contract explicitly.
        l2_contract=plan.get("l2_contract", L2_LEGACY_CONTRACT))


def summarize_l2_candidate(trace):
    accounting = trace.l2 if trace.l2 is not None else trace.fi
    records = pd.DataFrame(accounting.intervals)
    for values in (trace.provider_response_mw, trace.provider_requested_response_mw):
        if not np.all(np.isfinite(values)) or np.any(abs(values) > trace.provider_reserve_mw + 1e-8):
            raise RuntimeError("L2 comparison violates target or delivered limits")
    for field in ("gross_rewards_eur", "gross_penalties_eur", "activation_cost_eur"):
        ledger = getattr(accounting, field)
        if not np.all(np.isfinite(ledger)) or np.any(np.diff(ledger, axis=0) < -1e-9):
            raise RuntimeError("invalid L2 comparison ledger")
        if not np.isclose(ledger[-1].sum(), records[field].sum(), atol=1e-7, rtol=1e-9):
            raise RuntimeError("interval/cumulative ledger mismatch")
    if abs(records.activation_cost_eur - records.predicted_activation_cost_eur).max() > 1e-6:
        raise RuntimeError("delivered cost differs from exact first-order prediction")
    integral = trace.integrated_squared_frequency_error_hz2_s
    if not np.all(np.isfinite(integral)) or np.any(np.diff(integral) < -1e-10):
        raise RuntimeError("invalid frequency-error ledger")
    fi_index = trace.provider_ids.index(accounting.provider_ids[0])
    bound = trace.provider_reserve_mw[fi_index]
    duration = records.end_time_s - records.start_time_s
    active = bound > 0
    saturation = np.isclose(abs(records.target_mw), bound, rtol=0, atol=1e-9) & active
    delivery = abs(abs(trace.provider_response_mw[:, fi_index])-bound) <= 1e-6
    errors = records.net_settlement_eur - records.predicted_settlement_eur
    return {**frequency_performance_metrics(trace),
        "zenith_sampled_hz": float(trace.frequency_hz.max()),
        "terminal_error_hz": float(trace.delta_frequency_hz[-1]),
        "frequency_error_hz2_s": float(integral[-1]),
        "gross_rewards_eur": float(accounting.gross_rewards_eur[-1].sum()),
        "gross_charges_eur": float(accounting.gross_penalties_eur[-1].sum()),
        "net_payment_eur": float(accounting.net_settlement_eur[-1].sum()),
        "activation_cost_eur": float(accounting.activation_cost_eur[-1].sum()),
        "profit_eur": float(accounting.profit_eur[-1].sum()),
        "predicted_payment_eur": float(records.predicted_settlement_eur.sum()),
        "payment_prediction_bias_eur": float(errors.sum()),
        "payment_prediction_absolute_error_eur": float(abs(errors).sum()),
        "payment_prediction_max_interval_error_eur": float(abs(errors).max()),
        "target_bound_time_fraction": float(np.sum(duration * saturation) / trace.time_s[-1]),
        "delivered_bound_time_fraction": float(np.trapezoid(delivery.astype(float), trace.time_s)
                                                / trace.time_s[-1]) if active else 0.,
        "decision_wall_s": float(records.decision_wall_s.sum()),
        "replay_wall_s": float(records.replay_wall_s.sum())}


def _save_trace(trace, directory):
    directory.mkdir(parents=True, exist_ok=True)
    accounting = trace.l2 if trace.l2 is not None else trace.fi
    table = dict(time_s=trace.time_s, delta_frequency_hz=trace.delta_frequency_hz,
        frequency_hz=trace.frequency_hz, rocof_hz_per_s=trace.rocof_hz_per_s,
        power_deficit_mw=trace.power_deficit_mw,
        integrated_squared_frequency_error_hz2_s=trace.integrated_squared_frequency_error_hz2_s,
        net_payment_eur=accounting.net_settlement_eur.sum(axis=1),
        activation_cost_eur=accounting.activation_cost_eur.sum(axis=1))
    for j, identity in enumerate(trace.provider_ids):
        table[f"{identity}_response_mw"] = trace.provider_response_mw[:, j]
        table[f"{identity}_target_mw"] = trace.provider_requested_response_mw[:, j]
        table[f"{identity}_active_power_mw"] = trace.provider_active_power_mw[:, j]
    pd.DataFrame(table).to_csv(directory / "trajectory.csv", index=False)
    pd.DataFrame(accounting.intervals).to_csv(directory / "intervals.csv", index=False)
    pd.DataFrame(trace.disturbance_limits).to_csv(directory / "disturbance_limits.csv", index=False)


def _refinement(coarse, fine):
    a, b = summarize_l2_candidate(coarse), summarize_l2_candidate(fine)
    tolerances = {"nadir_sampled_hz": 1e-4, "zenith_sampled_hz": 1e-4,
        "terminal_error_hz": 1e-6, "rocof_max_abs_hz_per_s": 1e-4,
        "frequency_error_hz2_s": 1e-7, "net_payment_eur": 1e-5, "activation_cost_eur": 1e-5}
    result = {f"{key}_difference": abs(a[key]-b[key]) for key in tolerances}
    for key, tolerance in tolerances.items():
        if result[f"{key}_difference"] > tolerance:
            raise RuntimeError(f"L2 refinement failed: {key}")
    result["frequency_max_difference_hz"] = float(np.max(abs(coarse.frequency_hz
        - np.interp(coarse.time_s, fine.time_s, fine.frequency_hz))))
    if result["frequency_max_difference_hz"] > 1e-6:
        raise RuntimeError("L2 refinement trajectory mismatch")
    return result


def _design_job(block, plant, plan, profile, mechanism, coefficient, sample, output):
    """Independent deterministic integration; all random draws are already fixed."""
    wall, cpu = perf_counter(), process_time()
    trace = simulate_l2_candidate(block, plant, plan, profile, mechanism, coefficient)
    metrics = summarize_l2_candidate(trace)
    if sample == 0:
        _save_trace(trace, output / "design_trajectories" / profile["name"] / mechanism / f"{coefficient:g}")
    return dict(profile=profile["name"], mechanism=mechanism, coefficient=coefficient,
        sample=sample, **metrics, wall_s=perf_counter()-wall, cpu_s=process_time()-cpu)


def _csv(path, records):
    """Publish complete checkpoints so readers never observe a truncated CSV."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    pd.DataFrame(records).to_csv(temporary, index=False)
    temporary.replace(path)


def _tree_hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _paired_variance_banks(cleared, config, plan, reference, output):
    """Map saved draws through common bounded-law quantiles; preserve both banks.

    This sensitivity changes only declared variances. Clearing is recomputed
    and must reproduce the original voluntary zero offers and awards exactly.
    """
    frozen = json.loads((reference / "frozen_inputs.json").read_text())
    protocol = {k: v for k, v in plan.items() if k not in ("provider_overrides", "reference_study")}
    if protocol != frozen["plan"]:
        raise ValueError("variance comparison requires identical grids, profiles, banks, seed and numerics")
    expected = json.loads(json.dumps(frozen["effective_config"]))
    for raw in expected["providers"]:
        raw.update(plan["provider_overrides"].get(raw["provider_id"], {}))
    if config != expected:
        raise ValueError("variance sensitivity must change only the specified provider variances")
    if not json.loads((reference / "study_status.json").read_text()).get("design_complete"):
        raise ValueError("variance sensitivity requires a completed reference design")
    for name, digest in frozen["source_sha256"].items():
        if name != "lyapunov.py" and hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"reference scientific implementation differs: {name}")
    market = json.loads((reference / "market.json").read_text())
    if ([asdict(a) for a in cleared.market.awards] != market["awards"]
            or [asdict(d) for d in cleared.decisions] != market["offers"]
            or dict(zip([p.provider_id for p in cleared.providers], cleared.participation_modes)) != market["roles"]):
        raise ValueError("reduced variance changed offers, awards or roles; no fixed zero offer is imposed")
    original = pd.read_csv(reference / "availability_draws.csv")
    identities = [p.provider_id for p in cleared.providers]
    if set(original.columns) != {"bank", "sample", *identities}:
        raise ValueError("reference availability identities do not match")
    if set(original.bank) != {"design", "validation"}:
        raise ValueError("reference requires independent design and validation banks")
    for label in ("design", "validation"):
        if sorted(original[original.bank == label]["sample"].tolist()) != list(range(plan[label+"_samples"])):
            raise ValueError("reference sample bank is incomplete or duplicated")
    banks = original.copy()
    old_providers = {p["provider_id"]: p for p in frozen["effective_config"]["providers"]}
    new_providers = {p["provider_id"]: p for p in config["providers"]}
    if config["availability"]["boundary_mode"] != "truncated":
        raise ValueError("paired variance study currently requires continuous truncated availability")
    quantiles, laws = [], []
    for identity in plan["provider_overrides"]:
        old, new = old_providers[identity], new_providers[identity]
        if new["variance_mw2"] >= old["variance_mw2"]:
            raise ValueError("reduced-uncertainty sensitivity must lower the underlying variance")
        def distribution(raw):
            sigma = np.sqrt(raw["variance_mw2"])
            return truncnorm(-raw["mean_mw"]/sigma,
                (raw["reserve_capacity_mw"]-raw["mean_mw"])/sigma, loc=raw["mean_mw"], scale=sigma)
        before, after = distribution(old), distribution(new)
        q = before.cdf(original[identity].to_numpy())
        if np.any((q <= 0) | (q >= 1)):
            raise ValueError("reference draws must have interior quantiles")
        values = after.ppf(q)
        if not np.allclose(after.cdf(values), q, rtol=0, atol=1e-12):
            raise RuntimeError("bounded availability quantile pairing failed")
        banks[identity] = values
        for row, uniform, value in zip(original.to_dict("records"), q, values, strict=True):
            quantiles.append(dict(bank=row["bank"], sample=row["sample"], provider_id=identity,
                quantile=float(uniform), reference_availability_mw=row[identity], availability_mw=float(value)))
        laws.append(dict(provider_id=identity, mean_mw=new["mean_mw"], capacity_mw=new["reserve_capacity_mw"],
            reference_variance_mw2=old["variance_mw2"], variance_mw2=new["variance_mw2"],
            reference_bounded_mean_mw=float(before.mean()), bounded_mean_mw=float(after.mean()),
            reference_bounded_sd_mw=float(before.std()), bounded_sd_mw=float(after.std())))
    _csv(output / "availability_quantiles.csv", quantiles)
    provenance = dict(reference_directory=str(reference), reference_files_sha256=_tree_hashes(reference),
        pairing="F_new_inverse(F_reference(A_reference)) for each bank/sample/provider", laws=laws,
        offers_awards_roles_unchanged=True, sample_counts_unchanged=True)
    path = output / "sensitivity_provenance.json"
    if path.exists() and json.loads(path.read_text()) != provenance:
        raise ValueError("preserved reference study changed")
    _json(path, provenance)
    return banks


def run_l2_study(manifest, output):
    from .experiment import configured_scenarios, prepare_experiment
    started, cpu_started = perf_counter(), process_time()
    plan = tomllib.loads(manifest.read_text(encoding="utf-8"))
    validate_l2_plan(plan)
    plan.setdefault("l2_contract", L2_CONTRACT)
    if "reference_study" in plan:
        reference_path = (manifest.parent / plan["reference_study"]).resolve()
        target_path = output.resolve()
        if reference_path == target_path or reference_path in target_path.parents or target_path in reference_path.parents:
            raise ValueError("variance study and preserved reference need separate non-nested directories")
    selection = plan.pop("selection", None)
    selection_path = output / "tso_selection.json"
    if selection_path.exists():
        previous = json.loads(selection_path.read_text())
        if selection is None or any(previous[key] != selection[key] for key in ("lambda2", "gamma", "reason")):
            raise ValueError("TSO policy changed after selection; independent validation cannot be reused for selection")
    config = tomllib.loads((manifest.parent / plan["base_config"]).read_text(encoding="utf-8"))
    config = {name: value for name, _, value in configured_scenarios(config)}[plan["base_scenario"]]
    overrides = plan.get("provider_overrides", {})
    if set(overrides) - {p["provider_id"] for p in config["providers"]}:
        raise ValueError("unknown provider in L2 variance overrides")
    for raw in config["providers"]:
        raw.update(overrides.get(raw["provider_id"], {}))
    point, cleared, plant = prepare_experiment(config)
    if cleared.participation_modes.count("fi") != 1:
        raise ValueError("first L2 study requires exactly one resolved FI provider")
    files = [Path(__file__).with_name(name) for name in (
        "lyapunov.py", "simulation.py", "incentives.py", "decision.py", "providers.py",
        "frequency.py", "metrics.py", "experiment.py", "availability.py", "market.py", "grid.py", "tso.py")]
    provenance = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    output.mkdir(parents=True, exist_ok=True)
    frozen = {"plan": plan, "effective_config": config, "source_sha256": provenance}
    if (output / "frozen_inputs.json").exists():
        if json.loads((output / "frozen_inputs.json").read_text()) != frozen:
            raise ValueError("L2 study inputs or code changed; use a new output directory")
    else:
        _json(output / "frozen_inputs.json", frozen)
    _json(output / "plan.json", plan)
    _json(output / "frequency_parameters.json", asdict(plant))
    _json(output / "operating_point.json", asdict(point))
    _json(output / "market.json", {"awards": [asdict(a) for a in cleared.market.awards],
        "offers": [asdict(d) for d in cleared.decisions], "roles": dict(zip(
            [p.provider_id for p in cleared.providers], cleared.participation_modes, strict=True)),
        "block_payment_eur": sum(a.capacity_payment_eur for a in cleared.market.awards)})
    # Child 0: design; child 1: independent validation, never used to choose.
    banks = (_paired_variance_banks(cleared, config, plan,
        (manifest.parent / plan["reference_study"]).resolve(), output)
        if overrides else make_availability_banks(cleared, plan))
    banks_path = output / "availability_draws.csv"
    if banks_path.exists():
        pd.testing.assert_frame_equal(pd.read_csv(banks_path), banks, check_exact=False, rtol=1e-14, atol=1e-14)
    else:
        banks.to_csv(banks_path, index=False)
    status = {"status": "running_design", "selection_method": "explicit TSO choice from design Pareto curves"}
    _json(output / "study_status.json", status)
    sample_path = output / "design_samples.csv"
    records = pd.read_csv(sample_path).to_dict("records") if sample_path.exists() else []
    keys = [(r["profile"], r["mechanism"], r["coefficient"], r["sample"]) for r in records]
    expected = {(p["name"], m, c, s) for p in plan["profiles"]
        for m, grid in (("l2", plan["lambda2_values"]), ("bilinear", plan["gamma_values"]))
        for c in grid for s in range(plan["design_samples"])}
    if len(set(keys)) != len(keys) or not set(keys) <= expected:
        raise ValueError("invalid saved L2 candidate records")
    done = set(keys)
    identities = [p.provider_id for p in cleared.providers]
    blocks = {int(row["sample"]): realize_fcr_block(cleared, {p: row[p] for p in identities})
              for row in banks[banks.bank == "design"].to_dict("records")}
    with ProcessPoolExecutor(max_workers=plan["workers"]) as executor:
        for profile in plan["profiles"]:
            for mechanism, grid in (("l2", plan["lambda2_values"]), ("bilinear", plan["gamma_values"])):
                for coefficient in grid:
                    jobs = [executor.submit(_design_job, block, plant, plan, profile,
                        mechanism, coefficient, sample, output) for sample, block in blocks.items()
                        if (profile["name"], mechanism, coefficient, sample) not in done]
                    for job in as_completed(jobs):
                        row = job.result()
                        records.append(row)
                        _csv(sample_path, records)
                        done.add((row["profile"], row["mechanism"], row["coefficient"], row["sample"]))
                    print(f"L2 study: {profile['name']} {mechanism}={coefficient:g}; {len(done)}/{len(expected)} runs", flush=True)
    if done != expected:
        raise RuntimeError("incomplete L2 design bank")
    checks_path = output / "numerical_checks.csv"
    checks = pd.read_csv(checks_path).to_dict("records") if checks_path.exists() else []
    check_done = {(r["profile"], r["mechanism"], r["coefficient"], r["sample"]) for r in checks}
    fi_id = next(p.provider_id for p, role in zip(cleared.providers, cleared.participation_modes) if role == "fi")
    design = banks[banks.bank == "design"]
    extremes = {int(design.loc[design[fi_id].idxmin(), "sample"]),
                int(design.loc[design[fi_id].idxmax(), "sample"])}
    for profile in (plan["profiles"][0], plan["profiles"][-1]):
        for mechanism, grid in (("l2", plan["lambda2_values"]), ("bilinear", plan["gamma_values"])):
            for coefficient in (grid[len(grid)//2], max(grid)):
                for sample in extremes:
                    key = (profile["name"], mechanism, coefficient, sample)
                    if key in check_done:
                        continue
                    a = simulate_l2_candidate(blocks[sample], plant, plan, profile, mechanism, coefficient)
                    b = simulate_l2_candidate(blocks[sample], plant, plan, profile, mechanism, coefficient, refined=True)
                    checks.append(dict(profile=profile["name"], mechanism=mechanism, coefficient=coefficient,
                                       sample=sample, **_refinement(a, b)))
                    check_done.add(key)
                    _csv(checks_path, checks)
    _json(output / "study_status.json", {**status, "status": "awaiting_tso_selection", "design_complete": True,
        "numerical_checks_passed": True, "design_runs": len(records),
        "last_invocation_wall_s": perf_counter()-started, "last_invocation_cpu_s": process_time()-cpu_started})
    run_l2_report(output)
    if selection is not None:
        _evaluate_selection(output, plan, selection, cleared, plant, banks)
        run_l2_report(output)
    return json.loads((output / "study_status.json").read_text())


def _evaluate_selection(output, plan, selection, cleared, plant, banks):
    """Freeze a reviewed design choice before reading independent outcomes."""
    target = output / "tso_selection.json"
    frozen = {**selection, "method": "explicit TSO choice from design Pareto curves",
              "design_samples_sha256": hashlib.sha256((output / "design_samples.csv").read_bytes()).hexdigest()}
    if target.exists() and json.loads(target.read_text()) != frozen:
        raise ValueError("TSO policy changed after selection; independent validation cannot be reused for selection")
    _json(target, frozen)
    path = output / "validation_samples.csv"
    records = pd.read_csv(path).to_dict("records") if path.exists() else []
    done = {(r["profile"], r["mechanism"], r["coefficient"], r["sample"]) for r in records}
    for row in banks[banks.bank == "validation"].to_dict("records"):
        block = realize_fcr_block(cleared, {p.provider_id: row[p.provider_id] for p in cleared.providers})
        for profile in plan["profiles"]:
            for mechanism, coefficient in (("l2", selection["lambda2"]), ("bilinear", selection["gamma"]),
                                           ("bilinear", 0.)):
                key = (profile["name"], mechanism, coefficient, row["sample"])
                if key in done:
                    continue
                trace = simulate_l2_candidate(block, plant, plan, profile, mechanism, coefficient)
                records.append(dict(profile=profile["name"], mechanism=mechanism, coefficient=coefficient,
                    sample=row["sample"], **summarize_l2_candidate(trace)))
                _csv(path, records)
                done.add(key)
    status = json.loads((output / "study_status.json").read_text())
    _json(output / "study_status.json", {**status, "status": "selected_policies_validated",
        "validation_runs": len(records)})


def run_l2_report(output):
    """Regenerate design curves and summaries without calling a simulator."""
    plan = json.loads((output / "plan.json").read_text())
    samples = pd.read_csv(output / "design_samples.csv")
    keys = ["profile", "mechanism", "coefficient"]
    fields = [c for c in samples.select_dtypes(include=np.number) if c not in ("coefficient", "sample")]
    # Each scenario uses the same availability draw: aggregate per draw BEFORE
    # estimating mixture uncertainty so cross-profile covariance is retained.
    mix = samples.copy()
    mix[fields] = mix[fields].mul(mix.profile.map({p["name"]: p["weight"] for p in plan["profiles"]}), axis=0)
    mix = mix.groupby(["mechanism", "coefficient", "sample"], as_index=False)[fields].sum()
    mix["profile"] = "equal_weight_mixture"
    samples = pd.concat([samples, mix], ignore_index=True)
    rows = []
    for key, group in samples.groupby(keys):
        n = len(group)
        row = dict(zip(keys, key, strict=True), n=n)
        for field in fields:
            mean = float(group[field].mean())
            half = float(student_t.ppf(.975, n-1) * group[field].std(ddof=1)/np.sqrt(n)) if n > 1 else 0.
            row[field] = mean
            row[field+"_ci95_halfwidth"] = half
        rows.append(row)
    summary = pd.DataFrame(rows)
    performance = (("frequency_error_hz2_s", False), ("nadir_sampled_hz", True),
                   ("zenith_sampled_hz", False), ("final_absolute_error_hz", False),
                   ("rocof_max_abs_hz_per_s", False))
    for _, group in summary.groupby(["profile", "mechanism"]):
        for field, maximize in performance:
            summary.loc[group.index, "empirical_frontier_"+field] = empirical_pareto_flags(
                group.net_payment_eur, group[field], maximize=maximize)
    summary.to_csv(output / "design_summary.csv", index=False)
    if (output / "validation_samples.csv").exists():
        _validation_report(output)
    from .plotting import plot_l2_study
    plot_l2_study(output, plan, summary)
    status = json.loads((output / "study_status.json").read_text())
    cost = json.loads((output / "frozen_inputs.json").read_text())["effective_config"]
    roles = json.loads((output / "market.json").read_text())["roles"]
    provider = next(p for p in cost["providers"] if roles[p["provider_id"]] == "fi")
    lines = ["# Single-provider L2 incentive experiment", "", f"Status: {status['status']}.", "",
        "## Protocol", "", "L2 without effort weighting follows the Week 9 slides (main_content.tex, from the frame",
        "'First proposed simulation: L2 without effort weighting' through the experiment closing slide) and",
        "Matt, 17 July 2026, Section 3.1, equations (18)-(22), with the explicitly proposed dynamic adaptation.",
        "The provider uses a causal affine endpoint predictor; the full plant remains unchanged.",
        f"L2 contract: {plan.get('l2_contract', L2_LEGACY_CONTRACT)}.",
        "The performance component is lambda2*(passive_endpoint^2-actual_endpoint^2) in EUR.",
        ("Actual payment adds the exact zero-target decay cost rho*z_k^2*T*(1-exp(-2h/T))/4. "
         "Zero target has zero profit; predicted local optima have nonnegative profit. "
         "Prediction errors can still produce realized losses."
         if plan.get("l2_contract", L2_LEGACY_CONTRACT) == L2_CONTRACT else
         "Historical endpoint-only settlement excludes passive-cost compensation."),
        "The passive replay starts from the complete actual state with zero own target, the realized",
        "disturbance and responsive FCR. Inherited own response decays rather than resetting.",
        "Only settlement sees the realized interval disturbance; decisions use current frequency,",
        "current model-RHS RoCoF, own response, known mass/time constant and own availability.", "",
        "The TSO selects one lambda2 explicitly from design Pareto curves; bilinear gamma is also",
        "an explicit curve-based choice. No weighted TSO optimization or automatic knee selection is used.",
        "The accepted weighted optimizer remains available separately. A coefficient is frozen across",
        "the profile mixture, before independent evaluation; profiles are not announced to the provider.", "",
        f"Design bank: {plan['design_samples']} draws; independent validation bank: {plan['validation_samples']} draws.",
        "SeedSequence children 0/1 are independent. Awards/roles freeze before sampling; each bank",
        "is reused across coefficients, mechanisms and profiles. Four draws are exploratory, not a",
        "precision guarantee. Pointwise Student-t 95% intervals are descriptive, not simultaneous bounds.",
        "Mixture uncertainty is calculated after weighting profiles within each draw, preserving covariance.",
        "Empirical frontier flags do not establish population dominance or resolve numerical plateaus.", "",
        f"Decision clock: 0.1 s; horizon: {plan['final_time_s']:g} s; output step: {plan['output_time_step_s']:g} s.",
        "Exact event times supplement the output grid to retain frequency extrema at disturbance cusps.",
        f"Design integrations use {plan['workers']} independent process workers; their draws are frozen before dispatch.",
        "Lambda2 units: EUR/Hz^2 per decision interval. Gamma units: EUR/(Hz MW s). Their numbers",
        "are not comparable and lambda2 depends on the declared decision interval.",
        f"Both mechanisms retain base fi_endogenous rho={provider['fi_rho_eur_per_mw2_s']:g} EUR/(MW^2 s).",
        "This is the preserved pedagogical benchmark, not the separate D-040 FPP economic preset.", "",
        "## Synthetic disturbance profiles", "",
        "All levels are absolute net deficits in MW; negative levels mean surplus. They are explicit",
        "scenario assumptions on the 1000 MW teaching system, not calibrated event records.", ""]
    for p in plan["profiles"]:
        lines += [f"- {p['label']}: {p['power_deficit_mw']:g} MW at {p['start_time_s']:g} s; "
            f"later levels {p['changes']}. {p['purpose']}."]
    lines += ["", "The 40 s experiment resolves primary-control transients and recovery. It does not",
        "replace the historical 300 s reversal. No energy limits or secondary restoration are added.", "",
        "## Metrics and accounting", "",
        "Nadir/zenith are extrema on the numerical output grid including exact event instants. Terminal deviation is signed;",
        "integrated squared frequency deviation is a live ODE ledger in Hz^2 s.",
        "RoCoF is the unfiltered swing-equation RHS in Hz/s, including exact left/right event limits;",
        "the statistic is maximum absolute value over the full event horizon, with no averaging window.",
        "L2 gross transfers split each signed endpoint transfer; cumulative payment posts only at",
        "interval closure. Bilinear gross amounts split the continuous delivered-power payment rate.",
        "Activation cost is the live integral rho*z^2/2 and remains separate from TSO payments.",
        "Signed net, gross rewards/charges, profit, prediction errors and saturation are saved.",
        "Target saturation uses 1e-9 MW and exact decision-duration weights over the whole horizon;",
        "delivered saturation uses 1e-6 MW and trapezoidal time weights. Zero availability is not saturation.",
        "Prediction error is model mismatch, not a solver residual. Inherited response incurs cost",
        "even with zero target. The compensated contract reimburses that passive cost; the historical",
        "endpoint-only contract does not. Neither establishes a general stability theorem.", "",
        "## Verification and timings", "",
        "numerical_checks.csv compares halved solver/output steps and tenfold tighter tolerances",
        "at middle/upper coefficients and the extreme design FI availabilities for the first/last profiles.",
        "Tolerances: sampled extrema/RoCoF 1e-4, terminal/common-time frequency 1e-6 Hz,",
        "integrated error 1e-7 Hz^2 s and total transfers/cost 1e-5 EUR. Decision clock is unchanged.",
        "Per-run wall/CPU time and decision/replay time are saved. Bilinear replay time is zero",
        "because that contract does not perform counterfactual settlement.",
        "Representative traces are design sample 0 for every tested coefficient, not validation or",
        "selected-policy deliveries. Results do not certify coupled-system Lyapunov stability.", "",
        "## Exploratory design results", "",
        "The table averages the four synthetic profiles within each availability draw, then averages",
        "across draws. These are design outcomes, not independent validation or chosen policies.",
        "E_f is the live squared-frequency-error integral; S is the signed TSO event transfer,",
        "C is actual provider activation cost and profit is S-C. RoCoF is the mean of each",
        "trajectory's peak absolute unfiltered RHS, including exact event limits over [0,40] s.", "",
        "| Contract | Coefficient | E_f [Hz^2 s] | S [EUR/event] | C [EUR/event] | Profit [EUR/event] | Peak absolute RoCoF [Hz/s] |",
        "|---|---:|---:|---:|---:|---:|---:|"]
    mixture = summary[summary.profile == "equal_weight_mixture"].sort_values(["mechanism", "coefficient"])
    for row in mixture.itertuples():
        lines.append(f"| {row.mechanism} | {row.coefficient:g} | {row.frequency_error_hz2_s:.8f} | "
            f"{row.net_payment_eur:.6f} | {row.activation_cost_eur:.6f} | {row.profit_eur:.6f} | "
            f"{row.rocof_max_abs_hz_per_s:.8f} |")
    lines += ["", "Pointwise 95% intervals and per-profile statistics are in design_summary.csv.",
        "A negative mean profit is not evidence of voluntary participation: roles are assigned",
        "by the preserved sequential market/FI rule. Provider participation is a separate question.",
        "Higher coefficients can raise payments with little frequency improvement at saturation.",
        "L2 observes current model-RHS RoCoF and knows the frequency mass; the implemented bilinear",
        "decision uses sampled frequency and its own actuator model. This compares the proposed",
        "contract/controller packages, not an isolated change of payment formula at identical information.",
        "The curves do not establish equally optimized mechanisms or a population ranking.", "",
        "## Next action", "", "Inspect design_pareto.png and metric_pareto.png, then record tested lambda2, gamma and",
        "the explicit TSO rationale in the manifest's selection table. Only then run independent",
        "validation. Matched-performance FCR procurement needs predefined metric tolerances and",
        "follows selection/validation; no event-count assumption or procurement optimum is invented.", ""]
    (output / "ANALYSIS.md").write_text("\n".join(lines), encoding="utf-8")
    if "reference_study" in plan:
        _write_variance_comparison(output, plan, summary)
    return summary


def _write_variance_comparison(output, plan, summary):
    """Report the changed law and measured CI widths at the same sample count."""
    provenance = json.loads((output / "sensitivity_provenance.json").read_text())
    reference = Path(provenance["reference_directory"])
    if _tree_hashes(reference) != provenance["reference_files_sha256"]:
        raise ValueError("preserved reference study changed")
    old_summary = pd.read_csv(reference / "design_summary.csv")
    fields = ["frequency_error_hz2_s", "nadir_sampled_hz", "zenith_sampled_hz", "final_absolute_error_hz",
              "rocof_max_abs_hz_per_s", "net_payment_eur", "activation_cost_eur", "profit_eur"]
    keys = ["profile", "mechanism", "coefficient"]
    samples = []
    for directory in (reference, output):
        data = pd.read_csv(directory / "design_samples.csv")
        mix = data.copy()
        mix[fields] = mix[fields].mul(mix.profile.map({p["name"]: p["weight"] for p in plan["profiles"]}), axis=0)
        mix = mix.groupby(["mechanism", "coefficient", "sample"], as_index=False)[fields].sum()
        mix["profile"] = "equal_weight_mixture"
        samples.append(pd.concat([data, mix], ignore_index=True).set_index(keys + ["sample"]).sort_index())
    if not samples[0].index.equals(samples[1].index):
        raise ValueError("variance comparison requires the same complete design cases")
    old = old_summary.set_index(keys)
    rows = []
    for record in summary.to_dict("records"):
        key = tuple(record[k] for k in keys)
        previous = old.loc[key]
        for field in fields:
            delta = samples[1].loc[key, field] - samples[0].loc[key, field]
            n = len(delta)
            before = float(previous[field + "_ci95_halfwidth"])
            after = float(record[field + "_ci95_halfwidth"])
            rows.append(dict(zip(keys, key, strict=True), metric=field, n=n,
                reference_mean=float(previous[field]), mean=float(record[field]),
                reference_ci95_halfwidth=before, ci95_halfwidth=after,
                ci_halfwidth_ratio=after/before if before > 1e-14 else None,
                paired_mean_difference=float(delta.mean()),
                paired_ci95_halfwidth=float(student_t.ppf(.975, n-1)*delta.std(ddof=1)/np.sqrt(n))))
    _csv(output / "uncertainty_comparison.csv", rows)
    comparison = pd.DataFrame(rows)
    from .plotting import plot_l2_uncertainty_comparison
    plot_l2_uncertainty_comparison(output, summary, old_summary, provenance["laws"])
    lines = ["", "## Reduced-uncertainty sensitivity", "",
        f"Reference study (preserved in place): {reference.as_posix()}.",
        "Only the underlying Gaussian availability variance changes. The mean, support, costs,",
        "minimum bid, penalties, dynamics, awards, profiles, coefficient grids and sample counts stay fixed.",
        "Offers and clearing are recomputed and checked against the reference; the uncertain provider",
        "still offers zero voluntarily and receives zero FCR. No prescribed zero offer is introduced.", ""]
    for law in provenance["laws"]:
        lines += [f"Provider {law['provider_id']}: underlying variance {law['reference_variance_mw2']:g} -> "
            f"{law['variance_mw2']:g} MW^2; mean {law['mean_mw']:g} MW, support [0,{law['capacity_mw']:g}] MW.",
            f"Actual bounded-law standard deviation: {law['reference_bounded_sd_mw']:.6f} -> "
            f"{law['bounded_sd_mw']:.6f} MW; reduction "
            f"{100*(1-law['bounded_sd_mw']/law['reference_bounded_sd_mw']):.2f}%."]
    lines += ["", "Draws are paired by the same bounded-law quantiles in each independent bank.",
        "Other providers retain their exact saved draws. The availability mean after truncation is",
        "unchanged here by symmetry; finite-bank means can change when the same quantiles are remapped.",
        "The 95% Student-t formula and n=4 are unchanged. Narrower intervals, where observed, result",
        "from the altered law and response, not a different confidence level or hidden samples.",
        "The change is a declared pedagogical scenario sensitivity, not calibration or evidence that",
        "a real provider's forecast has improved. Intervals need not shrink uniformly after nonlinear control.", "",
        "### Equal-weight mixture: frequency-error confidence halfwidth", "",
        "| Contract | Coefficient | Reference CI halfwidth [Hz^2 s] | New CI halfwidth [Hz^2 s] | New/reference |",
        "|---|---:|---:|---:|---:|"]
    selected = comparison[(comparison.profile == "equal_weight_mixture") & (comparison.metric == "frequency_error_hz2_s")]
    for row in selected.itertuples():
        ratio = f"{row.ci_halfwidth_ratio:.4f}" if pd.notna(row.ci_halfwidth_ratio) else "undefined"
        lines.append(f"| {row.mechanism} | {row.coefficient:g} | {row.reference_ci95_halfwidth:.8f} | "
                     f"{row.ci95_halfwidth:.8f} | {ratio} |")
    lines += ["", "uncertainty_comparison.csv also reports metric-specific interval widths and paired mean",
        "differences for each profile, including peak absolute unfiltered model-RHS RoCoF over [0,40] s",
        "with exact event limits and no measurement averaging. These design differences do not select",
        "a TSO coefficient or establish population dominance. Independent policy validation is still pending.", ""]
    with (output / "ANALYSIS.md").open("a", encoding="utf-8") as stream:
        stream.write("\n".join(lines))


def _validation_report(output):
    data = pd.read_csv(output / "validation_samples.csv")
    rows = []
    for profile, group in data.groupby("profile"):
        reference = group[(group.mechanism == "bilinear") & (group.coefficient == 0)].set_index("sample")
        for (mechanism, coefficient), policy in group.groupby(["mechanism", "coefficient"]):
            policy = policy.set_index("sample")
            if set(policy.index) != set(reference.index):
                raise ValueError("validation comparisons must use paired complete draws")
            for field in ("frequency_error_hz2_s", "nadir_sampled_hz", "zenith_sampled_hz",
                          "terminal_error_hz", "rocof_max_abs_hz_per_s", "net_payment_eur", "profit_eur"):
                delta = policy[field] - reference[field]
                n = len(delta)
                rows.append(dict(profile=profile, mechanism=mechanism, coefficient=coefficient,
                    metric=field, n=n, paired_difference=float(delta.mean()),
                    ci95_halfwidth=float(student_t.ppf(.975,n-1)*delta.std(ddof=1)/np.sqrt(n))))
    pd.DataFrame(rows).to_csv(output / "validation_paired_differences.csv", index=False)
