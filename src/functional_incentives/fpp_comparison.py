"""Paired delivery comparison under one ideal-frequency settlement rule."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from time import perf_counter, process_time

import numpy as np
import pandas as pd

from .experiment import prepare_experiment
from .fpp import IDEAL_PROFILE, SAMPLE_SECONDS, settle_fpp
from .fpp_study import make_dispatch_schedules, policy_response_parameters, save_trajectory, write_json
from .metrics import summarize_fpp_benchmark
from .simulation import realize_fcr_block, simulate_fcr_power_step


CONTROLLERS = ("continuous_droop", "sampled_unit_gain", "selected_gains")
CASES = {"deficit": (), "reversal": ((120.05, -7.),)}


def run_controller_comparison(source: Path, output: Path):
    """Replay one independent draw and its already frozen gains in two specified tests."""
    source, output = source.resolve(), output.resolve()
    if (output == source or source in output.parents or output in source.parents
            or (output.exists() and any(output.iterdir()))):
        raise ValueError("use a new, separate controller-comparison output directory")
    def read(name):
        return json.loads((source / name).read_text(encoding="utf-8"))
    overlay, policy = read("overlay.json"), read("gain_equilibrium.json")
    if read("study_status.json")["status"] != "complete" or overlay["source_profile"] != IDEAL_PROFILE:
        raise ValueError("controller comparison requires a completed ideal-frequency gain-game study")
    players = policy["players"]
    config = read("effective_config.json")
    horizon = config["scenario"]["final_time_s"]
    if horizon != 300 or overlay["schedules"] != [{"name": "constant"}]:
        raise ValueError("paired cases require a 300 s constant ANDES reference")
    _, cleared, plant = prepare_experiment(config)
    schedule = make_dispatch_schedules(cleared, overlay["schedules"], horizon)["constant"]
    delivery = pd.read_csv(source / "availability_draws.csv").query("bank == 'delivery'")
    if len(delivery) != 1:
        raise ValueError("expected one representative delivery draw")
    row = delivery.iloc[0]
    draw = {p.provider_id: float(row[p.provider_id]) for p in cleared.providers}
    gains = {p: float(row[f"theta_{i+1}"]) for i,p in enumerate(players)}
    for p in players:
        index = policy["catalogue"].index(gains[p])
        if policy["probabilities"][p][index] <= 0:
            raise ValueError("saved delivery gain has zero probability under the frozen policy")
    saved_p0 = pd.read_csv(source / "constant_dispatch_targets.csv").loc[0, list(schedule.provider_ids)]
    saved_awards = pd.read_csv(source / "awards.csv").set_index("provider_id")
    if (not np.allclose(schedule.initial_operating_power_mw, saved_p0, rtol=0., atol=1e-9) or
            any(a.quantity_mw != saved_awards.loc[a.provider_id, "quantity_mw"] for a in cleared.market.awards)):
        raise RuntimeError("reconstructed ANDES powers or FCR awards differ from the gain study")
    block = realize_fcr_block(cleared, draw)
    frozen = policy_response_parameters(cleared, block, "fpp", gains=gains)
    policies = {
        "continuous_droop": policy_response_parameters(cleared, block, "fpp"),
        "sampled_unit_gain": policy_response_parameters(cleared, block, "fpp", gains=dict.fromkeys(players, 1.)),
        "selected_gains": frozen,
    }
    options = dict(power_deficit_mw=10., start_time_s=1., final_time_s=horizon,
                   output_time_step_s=config["scenario"]["output_time_step_s"],
                   maximum_solver_step_s=config["scenario"]["maximum_solver_step_s"],
                   provider_operating_power_mw=schedule.initial_operating_power_mw, dispatch_schedule=schedule,
                   measurement_times_s=np.arange(SAMPLE_SECONDS, horizon+1, SAMPLE_SECONDS), **overlay["numerics"])
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "comparison_inputs.json", dict(source_study=str(source), settlement_profile=IDEAL_PROFILE,
        reference="solved constant ANDES P_i,0", operating_power_mw=dict(zip(schedule.provider_ids, schedule.initial_operating_power_mw)),
        availability_mw=draw, players=players, episode_gains=gains, source_gain_distribution=policy["probabilities"],
        awards=[asdict(a) for a in cleared.market.awards], profile_cases=CASES, initial_event_s=1.,
        initial_deficit_mw=10., horizon_s=horizon, decision_interval_s=.1, run_order=list(policies)))
    records, accounts, directions, refinements, saturation = [], [], [], [], []
    initial_traces = {}
    start, cpu = perf_counter(), process_time()
    write_json(output / "study_status.json", {"status":"running"})
    try:
        for case, changes in CASES.items():
            for name, responses in policies.items():
                print(f"Paired FPP comparison: {case}, {name}", flush=True)
                case_options = {**options, "power_deficit_changes":changes}
                trace = simulate_fcr_power_step(plant, responses, **case_options)
                table = settle_fpp(trace.time_s, trace.delta_frequency_hz, trace.provider_active_power_mw,
                                   schedule, profile=IDEAL_PROFILE)
                samples = table["samples"]
                if (samples.performance_excluded.any() or not np.array_equal(samples.fm_hz, -samples.delta_frequency_hz)
                        or not table["directions"].reliable.all()):
                    raise RuntimeError("inconsistent ideal-frequency settlement")
                metric, limits = summarize_fpp_benchmark(trace, schedule, 1.)
                tag = dict(case=case, controller=name)
                records.append({**tag, **metric})
                saturation.extend({**tag, **v} for v in limits)
                accounts.append(table["accounts"].assign(**tag))
                directions.append(table["directions"].assign(**tag))
                directory = output / case / name
                directory.mkdir(parents=True)
                save_trajectory(trace, schedule, directory)
                for key in ("samples", "accounts", "directions"):
                    table[key].to_csv(directory / f"fpp_{key}.csv", index=False)
                prefix_difference = 0.
                if case == "deficit":
                    initial_traces[name] = trace.delta_frequency_hz.copy()
                else:
                    mask = trace.time_s < changes[0][0]
                    prefix_difference = float(np.max(abs(trace.delta_frequency_hz[mask] - initial_traces[name][mask])))
                reference_error = float(np.max(abs(trace.provider_active_power_mw -
                    schedule.initial_operating_power_mw - trace.provider_response_mw)))
                balance = float(table["accounts"].groupby(["interval", "direction"]).coefficient_mw_h.sum().abs().max())
                fine_options = {**case_options, "output_time_step_s":options["output_time_step_s"]/2,
                    "maximum_solver_step_s":options["maximum_solver_step_s"]/2,
                    "relative_tolerance":options["relative_tolerance"]/10, "absolute_tolerance":options["absolute_tolerance"]/10}
                fine = simulate_fcr_power_step(plant, responses, **fine_options)
                idx = np.searchsorted(fine.time_s, trace.time_s)
                if not np.allclose(fine.time_s[idx], trace.time_s, rtol=0., atol=1e-10):
                    raise RuntimeError("refinement grids are not nested")
                fine_metric, _ = summarize_fpp_benchmark(fine, schedule, 1.)
                fine_table = settle_fpp(fine.time_s, fine.delta_frequency_hz, fine.provider_active_power_mw,
                                        schedule, profile=IDEAL_PROFILE)
                checks = dict(frequency_diff_hz=float(np.max(abs(trace.delta_frequency_hz-fine.delta_frequency_hz[idx]))),
                    response_diff_mw=float(np.max(abs(trace.provider_response_mw-fine.provider_response_mw[idx]))),
                    rocof_peak_diff_hz_per_s=abs(metric["rocof_max_abs_hz_per_s"]-fine_metric["rocof_max_abs_hz_per_s"]),
                    integrated_error_diff_hz2_s=abs(metric["frequency_error_hz2_s"]-fine_metric["frequency_error_hz2_s"]),
                    coefficient_diff_mw_h=float(np.max(abs(table["accounts"].coefficient_mw_h -
                                                           fine_table["accounts"].coefficient_mw_h))),
                    reference_identity_error_mw=reference_error, balance_error_mw_h=balance,
                    common_prefix_diff_hz=prefix_difference)
                tolerance = dict(frequency_diff_hz=1e-6, response_diff_mw=1e-5, rocof_peak_diff_hz_per_s=1e-6,
                    integrated_error_diff_hz2_s=1e-7, coefficient_diff_mw_h=1e-6,
                    reference_identity_error_mw=1e-10, balance_error_mw_h=1e-10, common_prefix_diff_hz=1e-6)
                if any(not np.isfinite(v) or v > tolerance[k] for k,v in checks.items()):
                    raise RuntimeError(f"paired controller verification failed: {tag}, {checks}")
                refinements.append({**tag, **checks})
        for filename, data in (("metrics", pd.DataFrame(records)), ("accounts", pd.concat(accounts)),
                              ("directions", pd.concat(directions)), ("numerical_checks", pd.DataFrame(refinements)),
                              ("saturation", pd.DataFrame(saturation))):
            data.to_csv(output / f"{filename}.csv", index=False)
        from .plotting import plot_fpp_controller_comparison
        plot_fpp_controller_comparison(output, source, players, gains)
        write_controller_analysis(output, source, players, gains)
        write_json(output / "study_status.json", dict(status="complete", coarse_integrations=6, refinements=6,
                   wall_s=perf_counter()-start, cpu_s=process_time()-cpu))
    except Exception as error:
        write_json(output / "study_status.json", dict(status="failed", error=str(error)))
        raise


def write_controller_analysis(output, source, players, gains):
    metrics = pd.read_csv(output / "metrics.csv").set_index(["case","controller"])
    accounts = pd.read_csv(output / "accounts.csv")
    inputs = json.loads((output / "comparison_inputs.json").read_text(encoding="utf-8"))
    checks = pd.read_csv(output / "numerical_checks.csv")
    labels = dict(continuous_droop="Previous continuous droop", sampled_unit_gain="Sampled unit gain",
                  selected_gains="Selected gains")
    lines = ["# Paired FPP controller comparison", "",
        f"The selected episode gains are {', '.join(f'{p} = {gains[p]:g}' for p in players)}. "
        "Every controller settles its own unfiltered frequency and metered response at the same 75 "
        "four-second timestamps against constant ANDES references. "
        "All physical generators and the accounting residual enter the same two directional ledgers.", "",
        "## Delivery conditions", "",
        "Both cases begin with a 10 MW deficit at 1 s. The reversal replaces it with a 7 MW surplus "
        "at 120.05 s. The initial 10 MW step continuing for the full horizon is a prescribed stress "
        "case outside the four complete design paths. The same independent availability vector and "
        "episode gains apply to both cases. The controllers observe frequency during delivery.", "",
        "| Provider | Available MW | ANDES reference [MW] |", "|---|---:|---:|"]
    for p,a in inputs["availability_mw"].items():
        lines.append(f"| {p} | {a:.6f} | {inputs['operating_power_mw'][p]:.6f} |")
    lines += ["", "## Frequency and activation", "",
        "| Case | Controller | Nadir [Hz] | Zenith [Hz] | Terminal error [Hz] | Integrated error [Hz² s] | Max abs RoCoF [Hz/s] | Follower activation cost [EUR] |",
        "|---|---|---:|---:|---:|---:|---:|---:|"]
    for (case, controller), row in metrics.iterrows():
        values = row[["nadir_sampled_hz","zenith_sampled_hz","terminal_error_hz","frequency_error_hz2_s",
                      "rocof_max_abs_hz_per_s","switched_activation_cost_eur"]]
        lines.append(f"| {case} | {labels[controller]} | " + " | ".join(f"{v:.8f}" for v in values) + " |")
    lines += ["", "RoCoF is the unfiltered model derivative in Hz/s. The maximum absolute value covers "
        "the entire 300 s horizon and includes the exact event limits with no averaging window.", "",
        "## Paired changes", ""]
    for case in CASES:
        fixed, unit, selected = [metrics.loc[case,c] for c in CONTROLLERS]
        key = "frequency_error_hz2_s"
        lines.append(f"- {case.capitalize()}. Relative to continuous droop the selected gains change integrated "
                     f"squared error by {100*(selected[key]/fixed[key]-1):+.2f}%. Sampling alone changes it by "
                     f"{100*(unit[key]/fixed[key]-1):+.2f}%. The paired nadir difference for selected versus "
                     f"continuous is {1000*(selected.nadir_sampled_hz-fixed.nadir_sampled_hz):+.3f} mHz. "
                     f"Their peak absolute RoCoF difference is "
                     f"{selected.rocof_max_abs_hz_per_s-fixed.rocof_max_abs_hz_per_s:+.8f} Hz/s.")
    scores = accounts.groupby(["case","controller","provider_id"]).coefficient_mw_h.sum()
    saturation = pd.read_csv(output/"saturation.csv").set_index(["case","controller","provider_id"])
    lines += ["", "## Signed FPP scores", "",
        "| Case | Controller | " + " | ".join([*players,"residual"]) + " |", "|---|---|---:|---:|---:|"]
    for case,controller in metrics.index:
        values = [scores.loc[case,controller,p] for p in [*players,"residual"]]
        lines.append(f"| {case} | {labels[controller]} | " + " | ".join(f"{v:+.8f}" for v in values) + " |")
    lines += ["", "Scores are raise plus lower coefficients in MW h. The conventional providers also "
        "settle and appear in accounts.csv. The activation cost is a separate ledger. These are two "
        "matched delivery cases using one availability vector. The finite design table and independent "
        f"holdout are in [{source.name}](../{source.name}/ANALYSIS.md).", "",
        "## Delivered power", "",
        "| Case | Provider | Available [MW] | Previous peak absolute response [MW] | Selected peak absolute response [MW] | Selected target at bound [% of post-event time] |",
        "|---|---|---:|---:|---:|---:|"]
    for case in CASES:
        for p in players:
            fixed = saturation.loc[(case,"continuous_droop",p)]
            chosen = saturation.loc[(case,"selected_gains",p)]
            lines.append(f"| {case} | {p} | {inputs['availability_mw'][p]:.5f} | {fixed.peak_delivered_mw:.5f} | "
                         f"{chosen.peak_delivered_mw:.5f} | {100*chosen.target_bound_fraction:.3f} |")
    steady = {c: pd.read_csv(output/"deficit"/c/"trajectory.csv").iloc[-1] for c in CONTROLLERS}
    lines += ["", "At the end of the sustained deficit, the delivered power of each follower was", "",
        "| Provider | Continuous droop [MW] | Selected gains [MW] |", "|---|---:|---:|"]
    for p in players:
        col = f"{p}_response_mw"
        lines.append(f"| {p} | {steady['continuous_droop'][col]:.6f} | {steady['selected_gains'][col]:.6f} |")
    negative = accounts[(accounts.controller == "selected_gains") & (accounts.coefficient_mw_h < 0)]
    lines += ["", "Negative directional coefficients in these selected deliveries belong to " +
              ", ".join(sorted(negative.provider_id.unique())) + ". The residual is a passive accounting entity."]
    decision = json.loads((source/"gain_equilibrium.json").read_text(encoding="utf-8"))
    payoffs = pd.read_csv(source/"design_gain_payoffs.csv")
    holdout = pd.read_csv(source/"holdout_deviations.csv").set_index(["provider_id","deviating_theta"])
    first = payoffs.pivot(index="theta_1",columns="theta_2",values="score_1_mw_h").to_numpy()
    second = payoffs.pivot(index="theta_1",columns="theta_2",values="score_2_mw_h").to_numpy()
    own_scores = [first @ decision["probabilities"][players[1]], decision["probabilities"][players[0]] @ second]
    lines += ["", "## Changing one gain with the opponent policy held fixed", "",
        "| Own gain | Player 1 design score [MW h] | Player 2 design score [MW h] | Player 1 holdout change [MW h] | Player 2 holdout change [MW h] |",
        "|---:|---:|---:|---:|---:|"]
    for i, gain in enumerate(decision["catalogue"]):
        deltas = [holdout.loc[(p,gain)].paired_gain_mw_h for p in players]
        lines.append(f"| {gain:g} | {own_scores[0][i]:.8f} | {own_scores[1][i]:.8f} | "
                     f"{deltas[0]:+.8f} | {deltas[1]:+.8f} |")
    lines += ["", "The holdout change is the paired difference from following the frozen profile. "
        "The catalogue determines which unilateral changes were tested. holdout_deviations.csv in "
        "the source study gives uncertainty limits for the small independent bank.", "",
        f"All six controller/case pairs passed numerical refinement. The largest coarse/fine frequency "
        f"difference was {checks.frequency_diff_hz.max():.3g} Hz. The maximum transfer balance residual "
        f"was {checks.balance_error_mw_h.max():.3g} MW h. The two case prefixes agreed within "
        f"{checks.common_prefix_diff_hz.max():.3g} Hz before the reversal.", "",
        "frequency.png shows frequency and RoCoF. responses.png shows delivered power and held targets "
        "for the two followers. gain_tables.png shows each provider's private mean design score."]
    (output / "ANALYSIS.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
