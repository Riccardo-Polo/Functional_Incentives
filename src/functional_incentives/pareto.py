"""Metric-specific Pareto reports and diagnostic replay of a completed gamma sweep.

The source is immutable. Replays use its exact draws, grid and simulation options;
no policy is selected and no physical or economic parameter is changed.
"""

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from time import perf_counter, process_time

import numpy as np
import pandas as pd

from .metrics import summarize_tso_trajectory, summarize_tso_samples, tail_frequency_metrics
from .simulation import realize_fcr_block
from .tso import simulate_common_gamma, validate_sweep_settings, _realize_banks


DIAGNOSTICS = {"window_s": 5., "range_tolerance_hz": 1e-4,
               "rocof_tolerance_hz_per_s": 1e-4}
KEYS = ["bank", "sample", "gamma"]
PERFORMANCE = ("frequency_error_hz2_s", "nadir_sampled_hz",
               "final_absolute_error_hz", "rocof_max_abs_hz_per_s")


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _check_source(plan):
    if _hashes(Path(plan["source_directory"])) != plan["source_sha256"]:
        raise ValueError("preserved source sweep has changed")


def _check_artifacts(root, metadata):
    for name, digest in metadata["artifact_sha256"].items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"saved Pareto artifact has changed: {name}")


def _validate_samples(samples, banks, gammas):
    expected = {(r.bank, r.sample, g) for r in banks.itertuples() for g in gammas}
    actual = set(samples[KEYS].itertuples(index=False, name=None))
    if samples.duplicated(KEYS).any() or actual != expected:
        raise ValueError("outcomes must contain every configured bank/sample/gamma exactly once")


def _trajectory_table(trajectory):
    fi = trajectory.fi
    table = {"time_s": trajectory.time_s, "frequency_hz": trajectory.frequency_hz,
             "rocof_hz_per_s": trajectory.rocof_hz_per_s,
             "total_fcr_response_mw": trajectory.total_fcr_response_mw,
             "total_fi_response_mw": trajectory.total_fi_response_mw,
             "net_payment_eur": fi.net_settlement_eur.sum(axis=1),
             "activation_cost_eur": fi.activation_cost_eur.sum(axis=1)}
    for j, identity in enumerate(trajectory.provider_ids):
        table[f"{identity}_response_mw"] = trajectory.provider_response_mw[:, j]
        table[f"{identity}_target_mw"] = trajectory.provider_requested_response_mw[:, j]
        table[f"{identity}_capacity_mw"] = np.full(len(trajectory.time_s), trajectory.provider_reserve_mw[j])
    return pd.DataFrame(table)


def _metric_refinement(coarse, fine, event_time_s):
    """Check denser output independently of tighter integration; clocks stay fixed."""
    old, new = (summarize_tso_trajectory(t, event_time_s) for t in (coarse, fine))
    row = {}
    tolerances = {"nadir_sampled_hz": 1e-4, "rocof_max_abs_hz_per_s": 1e-4,
                  "final_absolute_error_hz": 1e-6, "frequency_error_hz2_s": 1e-7,
                  "net_payment_eur": 1e-5, "activation_cost_eur": 1e-5}
    for name, tolerance in tolerances.items():
        difference = abs(old[name] - new[name])
        row[f"{name}_absolute_difference"] = difference
        if difference > tolerance:
            raise RuntimeError(f"Pareto refinement failed for {name}: {difference} > {tolerance}")
    row["frequency_max_difference_hz"] = float(np.max(abs(
        coarse.frequency_hz - np.interp(coarse.time_s, fine.time_s, fine.frequency_hz))))
    if row["frequency_max_difference_hz"] > 1e-6:
        raise RuntimeError("Pareto trajectory refinement failed")
    return row


def _replay_candidate(cleared, parameters, draws, gamma, options, check_samples):
    started, cpu = perf_counter(), process_time()
    records, checks, illustrative = [], [], None
    for draw in draws.to_dict("records"):
        bank, sample = draw.pop("bank"), int(draw.pop("sample"))
        block = realize_fcr_block(cleared, draw)
        before, before_cpu = perf_counter(), process_time()
        trajectory = simulate_common_gamma(block, parameters, gamma, options)
        values = summarize_tso_trajectory(trajectory, options["start_time_s"])
        values.update(tail_frequency_metrics(trajectory, options["start_time_s"], **DIAGNOSTICS))
        records.append({"bank": bank, "sample": sample, "gamma": gamma, **values,
                        "final_fcr_response_mw": float(trajectory.total_fcr_response_mw[-1]),
                        "final_fi_response_mw": float(trajectory.total_fi_response_mw[-1]),
                        "simulation_wall_s": perf_counter() - before,
                        "simulation_cpu_s": process_time() - before_cpu})
        # Fixed before outcomes: sample zero, all gamma values, design bank only.
        if bank == "design" and sample == 0:
            illustrative = _trajectory_table(trajectory)
        if bank == "design" and sample in check_samples:
            fine = simulate_common_gamma(block, parameters, gamma,
                {**options, "output_time_step_s": options["output_time_step_s"] / 2}, refined=True)
            checks.append({"bank": bank, "sample": sample, "gamma": gamma,
                           **_metric_refinement(trajectory, fine, options["start_time_s"])})
    return {"rows": records, "checks": checks, "wall_s": perf_counter() - started,
            "cpu_s": process_time() - cpu}, illustrative


def _verify_replay(original, replay):
    """Compare every pre-existing scientific metric; timings are not observables."""
    left, right = (f.set_index(KEYS).sort_index() for f in (original, replay))
    if not left.index.equals(right.index):
        raise ValueError("replay keys differ from the preserved sweep")
    differences = {}
    for column in left:
        if column.startswith("simulation_"):
            continue
        if column not in right or not np.allclose(left[column], right[column], rtol=1e-7, atol=1e-8):
            raise ValueError(f"replayed metric differs from preserved sweep: {column}")
        differences[column] = float(np.max(abs(left[column] - right[column])))
    return differences


def run_pareto_study(source_dir: Path, output_dir: Path, *, workers: int = 3) -> dict:
    """Enrich historical outcomes by replay, checkpointing whole candidates safely."""
    from . import experiment

    started = perf_counter()
    source_dir, output_dir = source_dir.resolve(), output_dir.resolve()
    if (source_dir == output_dir or source_dir in output_dir.parents or output_dir in source_dir.parents):
        raise ValueError("Pareto output must be separate from the preserved sweep")
    if type(workers) is not int or workers < 1:
        raise ValueError("workers must be a positive integer")
    source_meta = _read(source_dir / "summary.json")
    config = _read(source_dir / "effective_config.json")
    if (_read(source_dir / "run_status.json")["status"] != "complete"
            or source_meta["status"] != "complete" or source_meta.get("gamma_selected") is not None
            or source_meta["settings"] != config["tso"]):
        raise ValueError("Pareto replay requires a complete, consistent gamma sweep")
    gammas = validate_sweep_settings(config["tso"])
    plan = {"schema_version": 1, "source_directory": str(source_dir),
            "source_sha256": _hashes(source_dir), "tail_diagnostics": DIAGNOSTICS,
            "illustrative_bank": "design", "illustrative_sample": 0,
            "gamma_values": list(gammas), "simulation_options": source_meta["simulation_options"],
            "rocof_method": "unfiltered swing-equation RHS at output times plus exact event right limit; Hz/s",
            "policy_selection": None}
    output_dir.mkdir(parents=True, exist_ok=True)
    plan_file = output_dir / "study_plan.json"
    if plan_file.exists():
        if _read(plan_file) != plan:
            raise ValueError("saved Pareto plan differs; use a new output directory")
    elif any(output_dir.iterdir()):
        raise ValueError("Pareto output directory must be empty or match a saved plan")
    _save(plan_file, plan)
    if (output_dir / "study_status.json").exists() and _read(output_dir / "study_status.json")["status"] == "complete":
        metadata = _read(output_dir / "summary.json")
        _check_artifacts(output_dir, metadata)
        run_pareto_report(output_dir)
        return metadata

    _save(output_dir / "study_status.json", {"status": "running"})
    try:
        _, cleared, parameters = experiment.prepare_experiment(config)
        saved_market = _read(source_dir / "market.json")
        market = json.loads(json.dumps(asdict(cleared.market)))
        roles = dict(zip((p.provider_id for p in cleared.providers), cleared.participation_modes))
        if (market != saved_market["market"] or roles != saved_market["roles"]
                or asdict(parameters) != saved_market["physical_parameters"]
                or json.loads(json.dumps([asdict(d) for d in cleared.decisions])) != saved_market["decisions"]):
            raise ValueError("reconstructed market/roles/parameters differ from saved source")
        banks = pd.read_csv(source_dir / "availability_samples.csv", float_precision="round_trip")
        original = pd.read_csv(source_dir / "sample_outcomes.csv", float_precision="round_trip")
        _realize_banks(cleared, banks, config["tso"])
        _validate_samples(original, banks, gammas)
        fi_ids = [i for i, role in roles.items() if role == "fi"]
        capacity = banks[banks.bank == "design"].set_index("sample")[fi_ids].sum(axis=1)
        check_samples = {int(capacity.idxmin()), int(capacity.idxmax())}
        cache_dir = output_dir / "batches"
        trace_dir = output_dir / "illustrative"
        cache_dir.mkdir(exist_ok=True)
        trace_dir.mkdir(exist_ok=True)
        batches, pending = [], []
        for bank in ("design", "validation"):
            for index, gamma in enumerate(gammas):
                name = f"{bank}_{index:03d}"
                path = cache_dir / f"{name}.json"
                if path.exists():
                    payload = _read(path)
                    if "trajectory_file" in payload:
                        trace = output_dir / payload["trajectory_file"]
                        if hashlib.sha256(trace.read_bytes()).hexdigest() != payload["trajectory_sha256"]:
                            raise ValueError("cached illustrative trajectory has changed")
                    batches.append(payload)
                else:
                    pending.append((name, banks[banks.bank == bank], gamma))

        def completed(name, result):
            payload, trace = result
            if trace is not None:
                path = trace_dir / f"{name}.csv"
                trace.to_csv(path, index=False)
                payload["trajectory_file"] = path.relative_to(output_dir).as_posix()
                payload["trajectory_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            _save(cache_dir / f"{name}.json", payload)
            batches.append(payload)
            print(f"Pareto replay {name}: {len(payload['rows'])} draws, {len(payload['checks'])} refinements, "
                  f"{payload['wall_s']:.2f} s", flush=True)

        if workers == 1:
            for name, draws, gamma in pending:
                completed(name, _replay_candidate(cleared, parameters, draws, gamma,
                                                  plan["simulation_options"], check_samples))
        elif pending:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                jobs = {pool.submit(_replay_candidate, cleared, parameters, draws, gamma,
                                    plan["simulation_options"], check_samples): name
                        for name, draws, gamma in pending}
                for future in as_completed(jobs):
                    completed(jobs[future], future.result())
        replay = pd.DataFrame([r for batch in batches for r in batch["rows"]]).sort_values(KEYS)
        _validate_samples(replay, banks, gammas)
        differences = _verify_replay(original, replay)
        # Keep the exact historical observables; add only missing diagnostics.
        additions = [c for c in replay if c not in original and not c.startswith("simulation_")]
        samples = original.merge(replay[KEYS + additions], on=KEYS, validate="one_to_one").sort_values(KEYS)
        checks = pd.DataFrame([r for batch in batches for r in batch["checks"]]).sort_values(KEYS)
        expected_checks = {("design", s, g) for s in check_samples for g in gammas}
        if set(checks[KEYS].itertuples(index=False, name=None)) != expected_checks or checks.duplicated(KEYS).any():
            raise ValueError("missing or duplicate numerical refinement checks")
        samples.to_csv(output_dir / "sample_outcomes.csv", index=False)
        checks.to_csv(output_dir / "numerical_checks.csv", index=False)
        _check_source(plan)
        _save(output_dir / "verification.json", {"source_files_unchanged": len(plan["source_sha256"]),
              "replay_max_absolute_differences": differences, "all_draws_matched": True,
              "numerical_checks_passed": len(checks)})
        metadata = {"status": "complete", "sample_count": len(samples), "refinement_count": len(checks),
                    "simulation_count": len(samples) + len(checks), "failed_simulations": 0,
                    "workers": workers, "wall_s": perf_counter() - started,
                    "candidate_cpu_s": sum(b["cpu_s"] for b in batches),
                    "timing_scope": "wall: this invocation including setup; CPU: all cached replay/refinement batches",
                    "fcr_payment_eur_per_block": cleared.market.total_payment_eur,
                    "nominal_frequency_hz": parameters.nominal_frequency_hz,
                    "fi_provider_ids": fi_ids, "gamma_selected": None}
        metadata["artifact_sha256"] = {k: v for k, v in _hashes(output_dir).items()
            if k in ("study_plan.json", "sample_outcomes.csv", "numerical_checks.csv", "verification.json")
            or k.startswith(("batches/", "illustrative/"))}
        _save(output_dir / "summary.json", metadata)
        _save(output_dir / "study_status.json", {"status": "complete"})
    except Exception as error:
        _save(output_dir / "study_status.json", {"status": "failed", "error": repr(error)})
        raise
    run_pareto_report(output_dir)
    return metadata


def run_pareto_report(output_dir: Path) -> dict:
    """Regenerate all tables/figures from verified saved data; zero integrations."""
    from .plotting import plot_tso_pareto

    started = perf_counter()
    output_dir = output_dir.resolve()
    plan, metadata = (_read(output_dir / name) for name in ("study_plan.json", "summary.json"))
    if _read(output_dir / "study_status.json")["status"] != "complete" or metadata["status"] != "complete":
        raise ValueError("Pareto reporting requires a complete study")
    _save(output_dir / "report_status.json", {"status": "running"})
    try:
        _check_source(plan)
        _check_artifacts(output_dir, metadata)
        samples = pd.read_csv(output_dir / "sample_outcomes.csv", float_precision="round_trip")
        summary = summarize_tso_samples(samples)
        summary.to_csv(output_dir / "gamma_summary.csv", index=False)
        frontiers = pd.concat([summary[["bank", "gamma", "net_payment_eur_mean", f"{metric}_mean",
            f"empirical_pareto_{metric}"]].rename(columns={f"{metric}_mean": "performance_mean",
            f"empirical_pareto_{metric}": "empirical_pareto"}).assign(metric=metric) for metric in PERFORMANCE])
        frontiers.to_csv(output_dir / "pareto_frontiers.csv", index=False)
        traces = {gamma: pd.read_csv(output_dir / "illustrative" / f"design_{i:03d}.csv")
                  for i, gamma in enumerate(plan["gamma_values"])}
        plot_tso_pareto(summary, traces, metadata, plan, output_dir)
        _write_analysis(output_dir, summary, metadata, plan)
        result = {"status": "complete", "simulation_count": 0, "wall_s": perf_counter() - started}
        _save(output_dir / "report_status.json", result)
        return result
    except Exception as error:
        _save(output_dir / "report_status.json", {"status": "failed", "error": repr(error)})
        raise


def _write_analysis(output_dir, summary, metadata, plan):
    validation = summary[summary.bank == "validation"].sort_values("gamma")
    diagnostics = plan["tail_diagnostics"]
    lines = ["# Gamma sweep: frequency performance and empirical Pareto curves", "",
        f"Source sweep: `{plan['source_directory']}` (preserved in place, SHA-256 checked).",
        "Only common gamma varies. Market, roles, dynamics, disturbance, horizon and availability draws are fixed.",
        "No weight, optimum, security threshold or policy is selected.", "",
        "## Metric definitions and uncertainty", "",
        "Metrics are computed per event before averaging. Separate design and independent validation banks use matched draws across gamma.",
        "Error bars are pointwise Student-t 95% intervals for means, not event ranges or simultaneous confidence bands.",
        "Empirical Pareto flags use estimated means: cost and error/RoCoF are minimized; nadir is maximized for this positive-deficit experiment.",
        "Membership is metric-specific and conditional on the tested grid; it is not a population dominance claim.",
        "Paired improvements against gamma zero are positive when beneficial and preserve within-draw covariance.", "",
        "RoCoF [Hz/s] is the unfiltered swing-equation RHS at output times, including the exact event right limit.",
        "No finite difference, filtering or measurement window is applied. Report initial signed, minimum, maximum and maximum absolute RoCoF.",
        "At the step, all responses start at zero: initial RoCoF = -deficit / frequency mass, independent of gamma and availability.",
        "Later extrema are sampled and checked with denser output; they are not certified continuous-time extrema.",
        "Nadir is likewise sampled; final absolute error is |f(H)-f_nominal|, not automatically a steady-state error.",
        f"Tail screen: last {diagnostics['window_s']:g} s, frequency range <= {diagnostics['range_tolerance_hz']:g} Hz and "
        f"maximum absolute RoCoF <= {diagnostics['rocof_tolerance_hz_per_s']:g} Hz/s, with a full window and >=3 samples.",
        "This is a finite-horizon diagnostic, not an asymptotic or security certificate. Short windows cannot pass.",
        "Integrated squared error is the existing live numerical ledger. FI transfers retain sampled-frequency settlement.", "",
        "## Independent-bank estimates", "",
        "| Gamma | Net FI [EUR/event] | Nadir [Hz] | Final absolute error [Hz] | E_f [Hz² s] | Max absolute RoCoF [Hz/s] | Tail screen passed |",
        "|---:|---:|---:|---:|---:|---:|---:|"]
    for r in validation.itertuples():
        lines.append(f"| {r.gamma:g} | {r.net_payment_eur_mean:.3f} | {r.nadir_sampled_hz_mean:.6f} | "
                     f"{r.final_absolute_error_hz_mean:.6f} | {r.frequency_error_hz2_s_mean:.6f} | "
                     f"{r.rocof_max_abs_hz_per_s_mean:.6f} | {100*r.tail_stationarity_pass_mean:.1f}% |")
    first, last = validation.iloc[0], validation.iloc[-1]
    lines += ["", "## Observations within this fixed configuration", "",
        f"From gamma {first.gamma:g} to {last.gamma:g}, mean net FI spending changes from "
        f"{first.net_payment_eur_mean:.3f} to {last.net_payment_eur_mean:.3f} EUR/event.",
        f"Mean nadir changes from {first.nadir_sampled_hz_mean:.6f} to {last.nadir_sampled_hz_mean:.6f} Hz; "
        f"mean terminal absolute error from {first.final_absolute_error_hz_mean:.6f} to {last.final_absolute_error_hz_mean:.6f} Hz."]
    if first.frequency_error_hz2_s_mean > 0:
        lines.append(f"The mean integrated-error reduction at the largest tested gamma is "
                     f"{100*(1-last.frequency_error_hz2_s_mean/first.frequency_error_hz2_s_mean):.2f}% relative to gamma zero.")
    rocof = validation.rocof_max_abs_hz_per_s_mean
    lines.append(f"Mean maximum absolute RoCoF spans [{rocof.min():.8f}, {rocof.max():.8f}] Hz/s across the grid.")
    if np.ptp(rocof.to_numpy()) <= 1e-12:
        lines.append("It is unchanged at the reported numerical precision: this incentive changes subsequent control, "
                     "but does not alter the instantaneous inertial response to the step. "
                     "The lowest-cost gamma therefore dominates in the cost-versus-peak-RoCoF projection alone.")
    previous = validation.iloc[-2]
    lines += [f"The last grid increment ({previous.gamma:g} to {last.gamma:g}) increases mean payment by "
        f"{last.incremental_payment_eur_mean:.3f} EUR/event and reduces mean integrated error by "
        f"{last.incremental_error_reduction_hz2_s_mean:.8f} Hz² s "
        f"(paired pointwise 95% halfwidth {last.incremental_error_reduction_hz2_s_ci95_halfwidth:.8f}).",
        "These are conditional sample estimates, not a recommended gamma range or a chosen policy."]
    lines += ["", "## Empirical frontiers on the independent bank", ""]
    for metric in PERFORMANCE:
        points = validation.loc[validation[f"empirical_pareto_{metric}"], "gamma"].tolist()
        lines.append(f"- {metric}: gamma = {', '.join(f'{g:g}' for g in points)}.")
    lines += ["", "These flags use raw sample means without rounding. Tiny differences may lie below numerical resolution.",
        f"For example, the last increment's paired mean nadir change is "
        f"{last.incremental_improvement_nadir_sampled_hz_mean:.4g} Hz; compare this with numerical_checks.csv.",
        "Do not interpret raw frontier membership near a plateau as a resolved nadir advantage or as a practical-equivalence test."]
    lines += ["", "## Mechanism and accounting", "",
        "The illustrative trajectories use predeclared design sample 0 for all gamma values. They are not expected trajectories.",
        "Inspect FCR response, FI targets versus delivery/capacity, and payments together with frequency and RoCoF.",
        "Target-bound time is an interval-duration measure; a clipped target does not imply delivered response already equals availability.",
        "The diagnostic plot separates the fraction of draws touching a target bound from mean time at that bound.",
        f"FCR capacity payment is constant at {metadata['fcr_payment_eur_per_block']:.6f} EUR/block.",
        "Keep it separate from FI EUR/event. Provider activation costs are not additional TSO cash payments.",
        "No event-count extrapolation, commercial profitability or universal saturation/payment identity is inferred.", "",
        "## Reproduction and verification", "",
        f"{metadata['sample_count']} matched replays + {metadata['refinement_count']} tighter/denser checks; zero failed simulations.",
        "Every historical scientific observable is checked against the preserved sweep; original values are retained in the augmented table.",
        "Refinement halves output spacing and maximum solver step, and tightens both solver tolerances tenfold, with the same FI clock/draws.",
        "Absolute acceptance tolerances: nadir 1e-4 Hz, max absolute RoCoF 1e-4 Hz/s, terminal error 1e-6 Hz,",
        "integrated error 1e-7 Hz² s, payment/cost 1e-5 EUR, matched trajectory frequency 1e-6 Hz.",
        f"Replay wall time: {metadata['wall_s']:.2f} s; cumulative batch CPU: {metadata['candidate_cpu_s']:.2f} s; workers: {metadata['workers']}.",
        "Timing scopes and per-candidate checkpoints are saved. Report-only regeneration performs zero integrations.",
        "Figures: metrics_vs_gamma, pareto_curves, diagnostics, illustrative_trajectories (PNG and vector PDF).",
        "All conclusions remain conditional on this pedagogical configuration and fixed disturbance."]
    (output_dir / "ANALYSIS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
