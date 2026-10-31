"""Frequency, provider-response and scenario-comparison figures."""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .frequency import FrequencyTrajectory
from .simulation import AggregateFCRTrajectory


def plot_fpp_controller_comparison(output, source, players, gains):
    from .fpp_comparison import CASES, CONTROLLERS
    labels = ["Previous continuous droop", "Sampled unit gain", "Selected gains"]
    colors = ["#394a59", "#34827c", "#b34129"]
    styles = ["-", "--", "-"]
    names = ["Sustained deficit", "Deficit to surplus"]
    traces = {(case,c): pd.read_csv(output / case / c / "trajectory.csv")
              for case in CASES for c in CONTROLLERS}
    def save(fig, stem):
        for extension in ("png","pdf"):
            fig.savefig(output / f"{stem}.{extension}", dpi=170, bbox_inches="tight")
        plt.close(fig)
    with plt.rc_context({"font.size":9, "axes.spines.top":False, "axes.spines.right":False,
                         "axes.grid":True, "grid.alpha":.18}):
        fig, axes = plt.subplots(3,2,figsize=(11.4,8.5), constrained_layout=True)
        for j,case in enumerate(CASES):
            event = 1. if case == "deficit" else 120.05
            focus = (max(0,event-2),event+10)
            for c,color,label,ls in zip(CONTROLLERS, colors, labels, styles):
                trace = traces[case,c]
                for ax in axes[:2,j]:
                    ax.plot(trace.time_s, trace.delta_frequency_hz*1000, color=color, ls=ls, lw=1.55, label=label)
                ax = axes[2,j]
                ax.plot(trace.time_s, trace.rocof_hz_per_s, color=color, ls=ls, lw=1.5)
                for r in pd.read_csv(output/case/c/"disturbance_limits.csv").itertuples():
                    ax.plot([r.time_s]*2, [r.rocof_left_hz_per_s,r.rocof_right_hz_per_s], color=color, ls=ls)
            axes[0,j].set(title=names[j], xlim=(0,300), ylabel="Frequency deviation [mHz]")
            axes[1,j].set(xlim=focus, ylabel="Frequency deviation [mHz]")
            axes[2,j].set(xlim=focus, ylabel="RoCoF [Hz/s]")
            for ax in axes[:,j]:
                ax.axvline(event, color="#888888", ls=":", lw=.8)
                ax.set_xlabel("Time [s]")
        axes[0,0].legend(fontsize=8)
        fig.suptitle("Matched frequency trajectories and unfiltered model RoCoF")
        save(fig,"frequency")

        fig, axes = plt.subplots(2,2,figsize=(11.4,6.9), constrained_layout=True)
        for j,case in enumerate(CASES):
            event = 1. if case == "deficit" else 120.05
            for i,p in enumerate(players):
                ax = axes[i,j]
                for c,color,label,ls in zip(CONTROLLERS, colors, labels, styles):
                    trace = traces[case,c]
                    ax.plot(trace.time_s, trace[f"{p}_response_mw"], color=color, ls=ls, lw=1.7, label=label)
                    if c == "selected_gains":
                        ax.step(trace.time_s, trace[f"{p}_target_mw"], where="post", color=color, ls=":",
                                lw=1., label="Selected target")
                ax.axvline(event, color="#888888", ls=":", lw=.8)
                ax.set(xlim=(max(0.,event-2),event+12), xlabel="Time [s]", ylabel="Response [MW]",
                       title=f"{names[j]} | {p.replace('renewable_', 'RES ')}")
        axes[0,0].legend(fontsize=8)
        fig.suptitle("Follower response during the disturbance")
        save(fig,"responses")

        table = pd.read_csv(source / "design_gain_payoffs.csv")
        fig, axes = plt.subplots(1,2,figsize=(10,4.3), constrained_layout=True)
        for i,(ax,p) in enumerate(zip(axes,players)):
            matrix = table.pivot(index="theta_1",columns="theta_2",values=f"score_{i+1}_mw_h")
            values = matrix.to_numpy()
            im = ax.imshow(values, cmap="Blues",origin="lower",aspect="auto")
            fig.colorbar(im,ax=ax,label="Mean FPP coefficient [MW h]")
            for (a,b),v in np.ndenumerate(values):
                ax.text(b,a,f"{v:.3f}",ha="center",va="center",
                        color="white" if v > (values.min()+values.max())/2 else "#162d40", fontsize=8)
            x = matrix.columns.get_loc(gains[players[1]])
            y = matrix.index.get_loc(gains[players[0]])
            ax.add_patch(plt.Rectangle((x-.49,y-.49),.98,.98,fill=False,edgecolor="#ed6a20",lw=2.4))
            ax.set(xticks=range(len(matrix.columns)),xticklabels=[f"{v:g}" for v in matrix.columns],
                   yticks=range(len(matrix.index)),yticklabels=[f"{v:g}" for v in matrix.index],
                   xlabel=f"{players[1]} gain",ylabel=f"{players[0]} gain",title=p.replace("_"," "))
            ax.grid(False)
        fig.suptitle("Common design cases | outlined cell is the delivered gain pair")
        save(fig,"gain_tables")


def plot_fpp_comparison(output_dir: Path, metrics, summary, accounts, schedule="constant", *, gain_game=False):
    """Keep the independent delivery, assessment statistics and currencies separate."""
    policies = ("fcr_alone", "bilinear_fi", "fpp")
    labels = {"fcr_alone": "FCR alone", "bilinear_fi": "Bilinear FI, gamma=50", "fpp": "FPP fixed droop"}
    if gain_game:
        labels["fpp"] = "FPP selected gains"
    colors = {"fcr_alone": "#777777", "bilinear_fi": "#215caf", "fpp": "#b7352d"}
    traces = {p: pd.read_csv(output_dir / "delivery/sample_000" / schedule / p / "trajectory.csv") for p in policies}
    fpp_dir = output_dir / "delivery/sample_000" / schedule / "fpp"
    fm = pd.read_csv(fpp_dir / "fpp_samples.csv")
    event_path = output_dir / "disturbance_events.csv"
    events = pd.read_csv(event_path) if event_path.exists() else None
    reversal = events is not None and len(events) > 1
    change_time = float(events.time_s.iloc[1]) if reversal else None
    detail_window = (change_time - 5, change_time + 25) if reversal else (0, 20)
    style = {"font.family": "DejaVu Sans", "font.size": 10, "axes.spines.top": False,
             "axes.spines.right": False, "axes.grid": True, "grid.alpha": .2}
    def save(fig, name):
        fig.savefig(output_dir / f"{name}.png", dpi=180, bbox_inches="tight")
        fig.savefig(output_dir / f"{name}.pdf", bbox_inches="tight")
        plt.close(fig)
    def fit_visible_y(ax):
        """Keep an initial-event zoom independent of later reversal amplitudes."""
        left, right = ax.get_xlim()
        visible = []
        for line in ax.lines:
            x, y = np.asarray(line.get_xdata()), np.asarray(line.get_ydata())
            if len(x) > 2:
                visible.extend(y[(x >= left) & (x <= right)])
        if visible:
            low, high = min(visible), max(visible)
            margin = max((high-low)*.08, 1e-6)
            ax.set_ylim(low-margin, high+margin)
    with plt.rc_context(style):
        fig, all_axes = plt.subplots(3 if reversal else 2, 2,
                                    figsize=(11.5, 10 if reversal else 7), constrained_layout=True)
        axes = (np.array([[all_axes[0, 0], all_axes[1, 0]], [all_axes[2, 0], all_axes[2, 1]]])
                if reversal else all_axes)
        for p, frame in traces.items():
            for ax in axes[0]:
                ax.plot(frame.time_s, frame.frequency_hz, color=colors[p], label=labels[p], lw=1.5)
            axes[1, 1].plot(frame.time_s, frame.rocof_hz_per_s, color=colors[p], label=labels[p], lw=1.3)
            if reversal:
                all_axes[0, 1].plot(frame.time_s, frame.frequency_hz, color=colors[p], label=labels[p], lw=1.5)
                limits = pd.read_csv(output_dir / "delivery/sample_000" / schedule / p / "disturbance_limits.csv")
                for _, event in limits.iterrows():
                    axes[1, 1].plot([event.time_s]*2, [event.rocof_left_hz_per_s, event.rocof_right_hz_per_s],
                                    color=colors[p], lw=1.3)
        axes[0, 0].set(xlim=(0, 20), ylabel="Physical frequency [Hz]", title="Initial response")
        axes[0, 1].set(ylabel="Physical frequency [Hz]", title="Complete settlement horizon")
        axes[0, 1].legend(fontsize=9)
        axes[1, 0].plot(traces["fpp"].time_s, traces["fpp"].delta_frequency_hz,
                        color=colors["fpp"], label="Physical deviation, delta f")
        axes[1, 0].plot(fm.time_s, fm.fm_hz, color="#007894", marker=".", ms=3,
                        label="Frequency Measure, 4-second samples")
        axes[1, 0].set(ylabel="Frequency signal [Hz]", title="FPP: frequency and settlement signal")
        axes[1, 0].legend(fontsize=8)
        axes[1, 1].set(xlim=(0, 20), ylabel="RoCoF [Hz/s]", title="Unfiltered model RHS; no smoothing window")
        if reversal:
            fit_visible_y(axes[0, 0])
            all_axes[0, 1].set(xlim=detail_window, ylabel="Physical frequency [Hz]", title="Deficit-to-surplus reversal")
            all_axes[0, 1].axhline(60, color="#555555", ls=":", lw=.8)
            all_axes[0, 1].axvline(change_time, color="#555555", ls="--", lw=.8)
            horizon = float(traces["fpp"].time_s.iloc[-1])
            all_axes[1, 1].step(np.r_[0, events.time_s, horizon],
                np.r_[0, events.net_power_deficit_mw, events.net_power_deficit_mw.iloc[-1]], where="post", color="#555555")
            all_axes[1, 1].axhline(0, color="#888888", lw=.8)
            levels = " then ".join(f"{v:+g}" for v in events.net_power_deficit_mw)
            all_axes[1, 1].set(ylabel="Net power deficit [MW]", title=f"Prescribed disturbance: {levels} MW")
            excluded = fm[fm.performance_excluded]
            axes[1, 0].scatter(excluded.time_s, excluded.fm_hz, marker="x", s=65, color="#b7352d",
                               zorder=5, label="Excluded from performance")
            axes[1, 0].set(xlim=detail_window, title="Filter memory at reversal (FPP case)")
            axes[1, 0].axhline(0, color="#555555", ls=":", lw=.8)
            axes[1, 0].axvline(change_time, color="#555555", ls="--", lw=.8)
            axes[1, 0].legend(fontsize=8)
            axes[1, 1].set(xlim=(0, horizon), title="Model RoCoF, including exact event limits")
        for ax in all_axes.flat:
            ax.set_xlabel("Time [s]")
        fig.suptitle("Representative delivery with matched availability and disturbance" if gain_game
                     else "Independent representative delivery - fixed policies, identical availability")
        save(fig, "delivery_frequency")

        fig, all_axes = plt.subplots(3 if reversal else 2, 2,
                                    figsize=(11.5, 10 if reversal else 7), constrained_layout=True)
        axes = (np.array([all_axes[0], all_axes[2]]) if reversal else all_axes)
        # Sum the switched providers identified by the saved role assignment.
        import json
        assignments = json.loads((output_dir / "policy_assignments.json").read_text(encoding="utf-8"))
        switched = [i for i, mode in assignments["fpp"].items() if mode == "fpp"]
        for p, frame in traces.items():
            values = frame[[f"{i}_response_mw" for i in switched]].sum(axis=1)
            targets = frame[[f"{i}_target_mw" for i in switched]].sum(axis=1)
            axes[0, 0].plot(frame.time_s, values, color=colors[p], label=labels[p])
            axes[0, 0].plot(frame.time_s, targets, color=colors[p], ls=":", lw=.9)
            if reversal:
                all_axes[1, 0].plot(frame.time_s, values, color=colors[p], label=labels[p])
                all_axes[1, 0].plot(frame.time_s, targets, color=colors[p], ls=":", lw=.9)
        axes[0, 0].set(xlim=(0, 20), ylabel="Switched-provider response [MW]", title="Solid: delivery; dotted: requested target")
        axes[0, 0].legend(fontsize=8)
        fpp_trace = traces["fpp"]
        for identity in assignments["fpp"]:
            axes[0, 1].plot(fpp_trace.time_s, fpp_trace[f"{identity}_response_mw"], label=identity.replace("conventional_", "C").replace("renewable_", "RES "))
            if reversal:
                all_axes[1, 1].plot(fpp_trace.time_s, fpp_trace[f"{identity}_response_mw"],
                    label=identity.replace("conventional_", "C").replace("renewable_", "RES "))
        axes[0, 1].set(xlim=(0, 20), ylabel="Individual delivered response [MW]", title="One response per provider in FPP case")
        axes[0, 1].legend(fontsize=8, ncol=2)
        ramp_path = output_dir / "delivery/sample_000/balanced_ramp/fpp/trajectory.csv"
        # Saved historical ramps remain visible when reporting those studies.
        reference_trace = pd.read_csv(ramp_path) if ramp_path.exists() else fpp_trace
        for identity in ("conventional_1", "conventional_2"):
            reference = reference_trace[f"{identity}_reference_mw"]
            axes[1, 0].plot(reference_trace.time_s, reference - reference.iloc[0],
                            label=identity.replace("conventional_", "C"))
        axes[1, 0].set(ylabel="Reference change from solved P0 [MW]",
            title="Prescribed dispatch targets" if ramp_path.exists() else "Constant ANDES reference: ref_i = P_i,0")
        axes[1, 0].legend()
        identity = "conventional_1"
        axes[1, 1].plot(reference_trace.time_s,
            reference_trace[f"{identity}_measured_mw"] - reference_trace[f"{identity}_reference_mw"],
            label="C1: meter minus reference", color=colors["fpp"])
        axes[1, 1].plot(fpp_trace.time_s, fpp_trace[f"{identity}_response_mw"],
                        label="C1: physical response", ls="--", color="#007894")
        axes[1, 1].set(ylabel="Power [MW]", title="Response and settlement deviation")
        axes[1, 1].legend(fontsize=8)
        if reversal:
            fit_visible_y(axes[0, 0])
            fit_visible_y(axes[0, 1])
            for ax, title in zip(all_axes[1], ("Switched provider: response reversal", "FPP case: individual response reversal")):
                ax.set(xlim=detail_window, ylabel="Response [MW]", title=title)
                ax.axhline(0, color="#555555", ls=":", lw=.8)
                ax.axvline(change_time, color="#555555", ls="--", lw=.8)
                ax.legend(fontsize=8, ncol=2)
        for ax in all_axes.flat:
            ax.set_xlabel("Time [s]")
        fig.suptitle("Representative delivery: control and settlement reference")
        save(fig, "delivery_responses_schedule")

        fig, axes = plt.subplots(3 if reversal else 2, 3,
                                figsize=(12, 10 if reversal else 7), constrained_layout=True)
        selected = (("nadir_sampled_hz", "Nadir [Hz]"), ("terminal_error_hz", "Terminal error [Hz]"),
                    ("frequency_error_hz2_s", "Live integrated squared error [Hz² s]"),
                    ("rocof_max_abs_hz_per_s", "Maximum absolute RoCoF [Hz/s]"),
                    ("fi_net_settlement_eur", "Bilinear FI transfer [EUR / 300 s]"),
                    ("switched_activation_cost_eur", "Switched-provider activation cost [EUR]"))
        if reversal:
            selected = (("nadir_sampled_hz", "Nadir [Hz]"), ("zenith_sampled_hz", "Zenith [Hz]"),
                ("terminal_error_hz", "Terminal error [Hz]"),
                ("frequency_error_hz2_s", "Integrated squared error [Hz² s]"),
                ("rocof_max_abs_hz_per_s", "Maximum absolute RoCoF [Hz/s]"),
                ("fi_gross_rewards_eur", "Bilinear FI gross rewards [EUR / 300 s]"),
                ("fi_charge_magnitudes_eur", "Bilinear FI charges [EUR / 300 s]"),
                ("fi_net_settlement_eur", "Bilinear FI net transfer [EUR / 300 s]"),
                ("switched_activation_cost_eur", "Switched-provider activation cost [EUR]"))
        for ax, (metric, title) in zip(axes.flat, selected):
            for j, p in enumerate(policies):
                row = summary[(summary.policy == p) & (summary.metric == metric)].iloc[0]
                ax.errorbar(j, row["mean"], yerr=[[row["mean"]-row.ci95_low], [row.ci95_high-row["mean"]]],
                            fmt="o", color=colors[p], capsize=4)
            ax.set(xticks=range(3), xticklabels=["FCR", "FI 50", "FPP"], title=title, xlim=(-.4, 2.4))
            if metric == "fi_net_settlement_eur":
                ax.text(.04, .84, "FI contract transfers only", transform=ax.transAxes, ha="left", va="top", fontsize=9)
        n = int(summary.n.iloc[0])
        fig.suptitle(f"Assessment bank: {n} matched draws - means and pointwise 95% t intervals")
        save(fig, "assessment_comparison")

        fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), sharey=True, constrained_layout=True)
        delivery = accounts[(accounts.bank == "delivery") & (accounts.schedule == schedule)]
        for ax, direction in zip(axes, ("raise", "lower")):
            rows = delivery[delivery.direction == direction].groupby("provider_id", sort=False).coefficient_mw_h.sum()
            colors_bar = ["#215caf" if value >= 0 else "#b7352d" for value in rows]
            ax.bar(range(len(rows)), rows.values, color=colors_bar)
            ax.axhline(0, color="#555555", lw=.8)
            names = [i.replace("conventional_", "C").replace("renewable_reliable", "RES reliable")
                     .replace("renewable_uncertain", "RES uncertain").replace("residual", "Residual") for i in rows.index]
            ax.set(xticks=range(len(rows)), xticklabels=names, ylabel="CF × RCR × 300/3600 [MW h]",
                   title=f"{direction.title()} coefficients; including residual")
            ax.tick_params(axis="x", labelrotation=30)
            part = delivery[delivery.direction == direction]
            if part.performance_is_null.all():
                ax.text(.5, .7, "Unreliable direction\nPerformance and CF: NULL\nRCR and coefficients: zero",
                        transform=ax.transAxes, ha="center", va="center", fontsize=11)
        fig.suptitle("FPP representative settlement coefficients - independent of external prices")
        save(fig, "fpp_settlement_coefficients")


def plot_tso_procurement_comparison(cases: list[dict], summary: pd.DataFrame, output_dir: Path) -> None:
    """Requirement comparisons within fleets; one matched delivery, separate means."""
    fleets = list(dict.fromkeys(c["fleet"] for c in cases))
    levels = list(dict.fromkeys(c["requirement_mw"] for c in cases))
    colors = dict(zip(levels, plt.cm.tab10.colors))
    groups = [[c for c in cases if c["fleet"] == fleet] for fleet in fleets]
    names = {"conventional_1": "C1", "conventional_2": "C2", "conventional_3": "C3",
             "renewable_reliable": "PV1", "renewable_uncertain": "PV2"}

    def save(figure, name):
        for axis in figure.axes:
            axis.grid(alpha=.18)
        figure.savefig(output_dir / f"{name}.png", dpi=170)
        plt.close(figure)

    figure, axes = plt.subplots(1, len(fleets), figsize=(5*len(fleets), 4.5), constrained_layout=True, squeeze=False, sharey=True)
    for group, axis in zip(groups, axes[0]):
        for c in group:
            q, frame = c["requirement_mw"], c["trajectory"]
            axis.plot(frame.time_s, frame.frequency_hz, color=colors[q],
                      label=f"FCR {q:g} MW; γ={c['metadata']['gamma_selected']:.2f}")
        reference = next(c for c in group if c["reference_directory"])
        baseline_path = reference["directory"] / "baseline_delivery.csv"
        if baseline_path.exists():
            baseline = pd.read_csv(baseline_path)
            axis.plot(baseline.time_s, baseline.frequency_hz, "--", color=".5", linewidth=1.2,
                      label=f"FCR {reference['requirement_mw']:g} MW; no FI")
        axis.set(title=group[0]["label"], xlabel="Time [s]", ylabel="Frequency [Hz]")
        axis.legend(fontsize=8, loc="lower right")
    deficit = cases[0]["config"]["scenario"]["power_deficit_mw"]
    figure.suptitle(f"Matched single delivery: frequency after the {deficit:g} MW deficit")
    save(figure, "frequency")

    identities = [raw["provider_id"] for raw in cases[0]["config"]["providers"]]
    figure, axes = plt.subplots(len(identities), len(fleets), figsize=(5*len(fleets), 2.4*len(identities)),
                                sharex=True, sharey=True, constrained_layout=True, squeeze=False)
    for column, group in enumerate(groups):
        for row, identity in enumerate(identities):
            axis = axes[row, column]
            for c in group:
                q, frame = c["requirement_mw"], c["trajectory"]
                axis.plot(frame.time_s, frame[f"{identity}_response_mw"], color=colors[q], label=f"FCR {q:g} MW")
            axis.set(title=f"{group[0]['label'].split(':')[0]} — {names.get(identity, identity)}",
                     ylabel="Response [MW]")
            if row == 0:
                axis.legend(fontsize=8)
            if row == len(identities)-1:
                axis.set_xlabel("Time [s]")
    figure.suptitle("Every provider, same availability draw: reduced awards do not change online inertia")
    save(figure, "provider_responses")

    panels = [(group, identity) for group in groups
              for identity in dict.fromkeys(i for c in group for i, role in c["market"]["roles"].items() if role == "fi")]
    figure, axes = plt.subplots(len(panels), len(levels), figsize=(5*len(levels), 3*len(panels)),
                                sharex=True, sharey=True, constrained_layout=True, squeeze=False)
    for row, (group, identity) in enumerate(panels):
        for column, q in enumerate(levels):
            c = next(c for c in group if c["requirement_mw"] == q)
            axis = axes[row, column]
            frame, decisions = c["trajectory"], c["decisions"].query("provider_id == @identity")
            axis.plot(frame.time_s, frame[f"{identity}_response_mw"], color=colors[q], label="Delivered power")
            if not decisions.empty:
                axis.step(np.r_[decisions.start_time_s, decisions.end_time_s.iloc[-1]],
                          np.r_[decisions.target_mw, decisions.target_mw.iloc[-1]], where="post",
                          color=".55", linewidth=.9, label="Held target")
            axis.axhline(c["availability"][identity], color="black", linestyle="--", linewidth=1, label="Available resource A")
            if frame[f"{identity}_response_mw"].min() < -1e-8 or (not decisions.empty and decisions.target_mw.min() < -1e-8):
                axis.axhline(-c["availability"][identity], color="black", linestyle="--", linewidth=1)
            axis.set(title=f"{c['label'].split(':')[0]} — {names.get(identity, identity)}; FCR {q:g} MW", ylabel="Power [MW]")
            if row == 0:
                axis.legend(fontsize=8, loc="lower right")
            if row == len(panels)-1:
                axis.set_xlabel("Time [s]")
    figure.suptitle("FI response and target versus actual availability: matched single delivery")
    save(figure, "fi_response")

    figure, axes = plt.subplots(len(fleets), 3, figsize=(15, 3*len(fleets)), sharex=True, constrained_layout=True, squeeze=False)
    fields = [("net_settlement_eur", "FI net payment"), ("activation_cost_eur", "FI activation cost"), ("profit_eur", "FI profit")]
    for row, group in enumerate(groups):
        for column, (field, title) in enumerate(fields):
            axis = axes[row, column]
            for c in group:
                q, frame = c["requirement_mw"], c["finances"]
                axis.plot(frame.time_s, frame[f"total_{field}"], color=colors[q], label=f"FCR {q:g} MW")
            axis.set(title=f"{group[0]['label'].split(':')[0]} — {title}", ylabel="Cumulative EUR / event")
            if row == 0:
                axis.legend(fontsize=8)
            if row == len(fleets)-1:
                axis.set_xlabel("Time [s]")
    figure.suptitle("FI accounting for the matched single delivery; cost is not extra TSO spending")
    save(figure, "fi_finances")

    validation = summary.query("bank == 'validation'")
    figure, axes = plt.subplots(1, 3, figsize=(15, 4.6), constrained_layout=True)
    common = validation[validation.fleet == fleets[0]].set_index("requirement_mw").loc[levels]
    axes[0].bar(np.arange(len(levels)), common.fcr_capacity_payment_eur_per_block, color=[colors[q] for q in levels])
    for x, value in enumerate(common.fcr_capacity_payment_eur_per_block):
        axes[0].text(x, value+4, f"{value:g}", ha="center")
    axes[0].set_xticks(np.arange(len(levels)), [f"{q:g} MW" for q in levels])
    hours, horizon = validation.fcr_block_hours.iloc[0], validation.fi_horizon_s.iloc[0]
    axes[0].set(title="FCR payment: same across fleets", ylabel=f"EUR / {hours:g}-hour block", ylim=(0, common.fcr_capacity_payment_eur_per_block.max()*1.16))
    for axis, metric, title, unit in [(axes[1], "net_payment_eur", "Expected FI payment", f"EUR / {horizon:g}-second event"),
                                    (axes[2], "frequency_error_hz2_s", "Expected integrated frequency error", "Hz² s")]:
        reference_file = output_dir / "fcr_only_validation_summary.csv"
        include_reference = metric == "frequency_error_hz2_s" and reference_file.exists()
        bars = len(levels) + int(include_reference)
        width = .8/bars
        for index, q in enumerate(levels):
            frame = validation[validation.requirement_mw == q].set_index("fleet").loc[fleets]
            axis.bar(np.arange(len(fleets))+(index-(bars-1)/2)*width, frame[f"{metric}_mean"],
                     width=width, color=colors[q], yerr=frame[f"{metric}_ci95_halfwidth"], capsize=3, label=f"FCR {q:g} MW")
        if include_reference:
            reference = pd.read_csv(reference_file).set_index("fleet").loc[fleets]
            axis.bar(np.arange(len(fleets))+(len(levels)-(bars-1)/2)*width, reference[f"{metric}_mean"],
                     width=width, color=".65", yerr=reference[f"{metric}_ci95_halfwidth"], capsize=3,
                     label=f"FCR {levels[0]:g} MW; no FI")
        axis.set_xticks(np.arange(len(fleets)), [g[0]["label"].split(":")[0] for g in groups])
        axis.set(title=title, ylabel=unit)
        axis.legend(fontsize=8)
    figure.suptitle(f"Independent validation: {int(validation.samples.iloc[0])} common draws; pointwise 95% intervals for means")
    save(figure, "tso_spending")


def plot_tso_fleet_comparison(cases: list[dict], summary: pd.DataFrame, output_dir: Path) -> None:
    """Saved single-event traces, with expected expenditures shown separately."""
    colors = ["#215caf", "#007894", "#b7352d", "#8656a3"]
    colors.extend(plt.cm.tab20(i % 20) for i in range(max(0, len(cases) - len(colors))))
    names = {"conventional_1": "C1 (FCR)", "conventional_2": "C2 (FCR)",
             "conventional_3": "C3 (FCR)", "renewable_reliable": "PV1 (FCR)",
             "renewable_uncertain": "PV2 (FI)"}

    def save(figure, name):
        for axis in figure.axes:
            axis.grid(alpha=.18)
        figure.savefig(output_dir / f"{name}.png", dpi=180)
        plt.close(figure)

    figure, axes = plt.subplots(1, 2, figsize=(12, 4.5), constrained_layout=True)
    for case, color in zip(cases, colors):
        frame, baseline = case["trajectory"], case["baseline"]
        label = f"{case['label']} (γ={case['metadata']['gamma_selected']:.2f})"
        for axis in axes:
            axis.plot(frame.time_s, frame.frequency_hz, color=color, label=label)
            axis.plot(baseline.time_s, baseline.frequency_hz, color=color, linestyle="--", alpha=.55)
    for axis in axes:
        axis.set(xlabel="Time [s]", ylabel="Frequency [Hz]")
    axes[0].set_title("Illustrative event: solid FI, dashed γ=0")
    axes[1].set(title="Initial response, matched availability", xlim=(0, 10))
    axes[0].legend(fontsize=8, loc="lower right")
    save(figure, "frequency")

    figure, axes = plt.subplots(len(cases), 1, figsize=(11, 3 * len(cases)), sharex=True, constrained_layout=True, squeeze=False)
    provider_colors = ["#215caf", "#007894", "#627313", "#8656a3", "#b7352d"]
    for case, axis in zip(cases, axes[:, 0]):
        frame = case["trajectory"]
        for raw, color in zip(case["config"]["providers"], provider_colors):
            identity = raw["provider_id"]
            axis.plot(frame.time_s, frame[f"{identity}_response_mw"], label=names.get(identity, identity), color=color)
        axis.set(title=case["label"], ylabel="Response ΔP [MW]")
        axis.legend(ncol=5, fontsize=8, loc="lower right")
    axes[-1, 0].set_xlabel("Time [s]")
    save(figure, "provider_responses")

    fi_panels = [(case, identity) for case in cases for identity, role in case["market"]["roles"].items() if role == "fi"]
    figure, axes = plt.subplots(len(fi_panels), 1, figsize=(11, 3 * len(fi_panels)), sharex=True, constrained_layout=True, squeeze=False)
    for (case, identity), axis in zip(fi_panels, axes[:, 0]):
        frame = case["trajectory"]
        decisions = case["decisions"].query("provider_id == @identity")
        axis.plot(frame.time_s, frame[f"{identity}_response_mw"], color="#215caf", label="Delivered power z(t)")
        axis.step(np.r_[decisions.start_time_s, decisions.end_time_s.iloc[-1]],
                  np.r_[decisions.target_mw, decisions.target_mw.iloc[-1]], where="post", color="#b7352d", linewidth=1.1, label="Target v(t)")
        axis.axhline(case["availability"][identity], color=".35", linestyle="--", label="Available resource A (upper bound)")
        minimum = min(float(frame[f"{identity}_response_mw"].min()), float(decisions.target_mw.min()), 0.)
        if minimum < -1e-8:
            axis.axhline(-case["availability"][identity], color=".35", linestyle="--")
        limit = 1.08 * max(c["availability"][i] for c, i in fi_panels)
        axis.set(title=f"{case['label']} — {names.get(identity, identity)}", ylabel="Power [MW]",
                 ylim=(-limit if minimum < -1e-8 else 0, limit))
        axis.legend(ncol=3, fontsize=8, loc="lower right")
    axes[-1, 0].set_xlabel("Time [s]")
    save(figure, "fi_response")

    figure, axes = plt.subplots(1, 3, figsize=(13, 4.4), constrained_layout=True)
    for case, color in zip(cases, colors):
        frame = case["finances"]
        for axis, field in zip(axes, ("net_settlement_eur", "activation_cost_eur", "profit_eur")):
            axis.plot(frame.time_s, frame[f"total_{field}"], color=color, label=case["label"])
    for axis, title in zip(axes, ("Net payment to the FI provider", "FI activation cost", "FI profit")):
        axis.set(title=title, xlabel="Time [s]", ylabel="Cumulative EUR over the event")
    axes[0].legend(fontsize=8)
    save(figure, "fi_finances")

    figure, axes = plt.subplots(1, 2, figsize=(11, 4.5), constrained_layout=True)
    delivery = summary.query("bank == 'delivery'").set_index("case").loc[[c["name"] for c in cases]]
    validation = summary.query("bank == 'validation'").set_index("case").loc[[c["name"] for c in cases]]
    x = np.arange(len(cases))
    axes[0].bar(x, delivery.fcr_capacity_payment_eur_per_block, color=colors[:len(cases)], width=.55)
    axes[0].set(title=f"FCR: capacity procured per {delivery.fcr_block_hours.iloc[0]:g}-hour block", ylabel="EUR / block")
    axes[1].bar(x-.17, delivery.net_payment_eur_mean, width=.34, color="#215caf", label="Single event shown in the plots")
    axes[1].bar(x+.17, validation.net_payment_eur_mean, width=.34, color="#007894",
                yerr=validation.net_payment_eur_ci95_halfwidth.fillna(0), capsize=4,
                label=f"Mean over {int(validation.samples.iloc[0])} draws + 95% CI")
    axes[1].set(title=f"FI: net payment per {delivery.fi_horizon_s.iloc[0]:g} s event", ylabel="EUR / event")
    axes[1].legend(fontsize=8)
    for axis in axes:
        axis.set_xticks(x, [c["label"].split(":")[0] for c in cases])
    save(figure, "tso_spending")


def plot_tso_stability(selections: pd.DataFrame, assessment: pd.DataFrame, output_dir: Path) -> None:
    """Policy variation and independent paired objective differences, not regret."""
    figure, axes = plt.subplots(1, 2, figsize=(12, 4.5), constrained_layout=True)
    reference = float(assessment.reference_gamma.iloc[0])
    for (seed, frame), color in zip(selections.groupby("seed", sort=True),
                                    plt.cm.tab10.colors):
        frame = frame.sort_values("design_samples")
        axes[0].plot(frame.design_samples, frame.gamma_selected, "o-", color=color, label=f"Seed {seed}")
    axes[0].axhline(reference, color=".45", linestyle="--", label=f"Previous gamma = {reference:g}")
    sizes = sorted(selections.design_samples.unique())
    axes[0].set_xscale("log", base=2)
    axes[0].set_xticks(sizes, [str(n) for n in sizes])
    axes[0].set(title="Selected gamma across nested design banks", xlabel="Design samples N",
                ylabel=r"Common $\gamma$ [EUR/(Hz MW s)]")
    axes[0].legend(fontsize=8)
    frame = assessment.sort_values("gamma")
    axes[1].errorbar(frame.gamma, frame.delta_objective_eur_mean,
                    yerr=frame.delta_objective_eur_ci95_halfwidth,
                    fmt="o", color="#215caf", capsize=4, label="Paired mean difference + pointwise 95% CI")
    axes[1].axhline(0, color=".5", linestyle="--")
    axes[1].axvline(reference, color=".7", linestyle=":")
    axes[1].set(title=f"Independent assessment ({int(frame.samples.iloc[0])} common draws)",
                xlabel=r"Frozen candidate $\gamma$ [EUR/(Hz MW s)]",
                ylabel="Expected J difference vs previous gamma [EUR/event]")
    axes[1].legend(fontsize=8)
    for axis in axes:
        axis.grid(alpha=.2)
    for extension in ("png", "pdf"):
        figure.savefig(output_dir / f"stability.{extension}", dpi=180)
    plt.close(figure)


def plot_tso_pareto(summary, traces, metadata, plan, output_dir):
    """Metric-specific empirical frontiers; individual events remain separate."""
    colors = {"design": "#215caf", "validation": "#b7352d"}
    metrics = [("nadir_sampled_hz", "Frequency nadir", "Mean sampled nadir [Hz]"),
               ("final_absolute_error_hz", "Terminal frequency error", "Mean absolute terminal error [Hz]"),
               ("frequency_error_hz2_s", "Integrated frequency error", r"Mean integrated squared error [Hz$^2$ s]"),
               ("rocof_max_abs_hz_per_s", "Maximum absolute RoCoF", "Mean max absolute RoCoF [Hz/s]")]
    style = {"font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
             "axes.grid": True, "grid.alpha": .2, "axes.axisbelow": True,
             "lines.linewidth": 1.6, "pdf.fonttype": 42}

    def save(fig, name):
        for suffix in ("png", "pdf"):
            fig.savefig(output_dir / f"{name}.{suffix}", dpi=180, bbox_inches="tight")
        plt.close(fig)

    def constant_axis(ax, metric):
        values = summary[f"{metric}_mean"].to_numpy()
        if np.ptp(values) <= 1e-10:
            margin = max(.005, abs(float(values[0])) * .05)
            ax.set_ylim(float(values[0])-margin, float(values[0])+margin)

    with plt.rc_context(style):
        fig, axes = plt.subplots(2, 3, figsize=(14, 8), layout="constrained")
        panels = [("net_payment_eur", "Net FI expenditure", "Mean net FI payment [EUR/event]"),
                  *metrics, ("target_bound_fraction", "FI target saturation", "Mean post-event time fraction")]
        for ax, (metric, title, ylabel) in zip(axes.flat, panels):
            for bank, frame in summary.groupby("bank", sort=False):
                frame = frame.sort_values("gamma")
                ax.errorbar(frame.gamma, frame[f"{metric}_mean"],
                    yerr=frame[f"{metric}_ci95_halfwidth"], capsize=3,
                    fmt="o-" if bank == "design" else "s--", color=colors[bank],
                    label=f"{bank.capitalize()} (N={frame.samples.iloc[0]})", markersize=4)
            ax.set(title=title, xlabel=r"Common $\gamma$ [EUR/(Hz MW s)]", ylabel=ylabel)
            constant_axis(ax, metric)
        axes[0, 0].legend(fontsize=9)
        fig.suptitle("Controlled gamma sweep | pointwise 95% confidence intervals of means")
        save(fig, "metrics_vs_gamma")

        fig, axes = plt.subplots(2, 2, figsize=(12.5, 9), layout="constrained")
        for ax, (metric, title, ylabel) in zip(axes.flat, metrics):
            for bank, frame in summary.groupby("bank", sort=False):
                frame = frame.sort_values("gamma")
                ax.errorbar(frame.net_payment_eur_mean, frame[f"{metric}_mean"],
                    xerr=frame.net_payment_eur_ci95_halfwidth,
                    yerr=frame[f"{metric}_ci95_halfwidth"], capsize=2,
                    fmt="o:" if bank == "design" else "s--", color=colors[bank],
                    alpha=.7, markersize=4, label=bank.capitalize())
                frontier = frame[frame[f"empirical_pareto_{metric}"]]
                ax.scatter(frontier.net_payment_eur_mean, frontier[f"{metric}_mean"],
                           s=95, facecolors="none", edgecolors=colors[bank], linewidths=1.6)
                if bank == "validation":
                    for i, row in enumerate(frame.itertuples()):
                        offset = (-15 if i % 2 else 10) if metric == "rocof_max_abs_hz_per_s" else 6
                        ax.annotate(f"{row.gamma:g}", (row.net_payment_eur_mean, getattr(row, f"{metric}_mean")),
                                    xytext=(5, offset), textcoords="offset points", fontsize=8)
            ax.set(title=title, xlabel="Mean net FI payment [EUR/event]", ylabel=ylabel)
            constant_axis(ax, metric)
            ax.margins(x=.1, y=.16)
        axes[0, 0].legend(fontsize=9)
        fig.suptitle("Cost-performance curves | circles: empirical nondominated means | labels: gamma\n"
                     "Pointwise 95% intervals; frontier membership is not a population claim", fontsize=12)
        save(fig, "pareto_curves")

        validation = summary[summary.bank == "validation"].sort_values("gamma")
        fig, axes = plt.subplots(2, 2, figsize=(12, 8), layout="constrained")
        for metric, label, color in (("gross_rewards_eur", "Gross rewards", "#215caf"),
                                     ("gross_charges_eur", "Gross charges", "#b7352d"),
                                     ("net_payment_eur", "Net FI payment", "#007894"),
                                     ("activation_cost_eur", "Provider activation cost", "#8656a3")):
            axes[0, 0].plot(validation.gamma, validation[f"{metric}_mean"], "o-", label=label, color=color)
        axes[0, 0].set(title="FI accounting", ylabel="Mean amount [EUR/event]")
        for metric, label in (("any_target_bound", "Draws touching a target bound"),
                              ("target_bound_fraction", "Mean time at target bound"),
                              ("tail_stationarity_pass", "Draws passing tail screen")):
            axes[0, 1].plot(validation.gamma, 100*validation[f"{metric}_mean"], "o-", label=label)
        axes[0, 1].set(title="Saturation and terminal behavior", ylabel="Percentage [%]", ylim=(-3, 103))
        for ax, metric, threshold, title, ylabel in (
                (axes[1, 0], "tail_frequency_range_hz", plan["tail_diagnostics"]["range_tolerance_hz"],
                 "Frequency range in final window", "Mean frequency range [Hz]"),
                (axes[1, 1], "tail_rocof_max_abs_hz_per_s", plan["tail_diagnostics"]["rocof_tolerance_hz_per_s"],
                 "Maximum absolute RoCoF in final window", "Mean max absolute RoCoF [Hz/s]")):
            ax.plot(validation.gamma, validation[f"{metric}_mean"], "o-", color=colors["validation"])
            ax.axhline(threshold, color="#555555", ls="--", label="Per-event diagnostic threshold")
            ax.set(title=title, ylabel=ylabel, yscale="symlog")
            ax.set_yscale("symlog", linthresh=1e-8)
        for ax in axes.flat:
            ax.set_xlabel(r"Common $\gamma$ [EUR/(Hz MW s)]")
            ax.legend(fontsize=8, loc="upper right" if ax in axes[1] else "best")
        fig.suptitle("Independent-bank diagnostics | tail screen is not a steady-state or security certificate")
        save(fig, "diagnostics")

        selected = sorted({0., min(traces, key=lambda g: abs(g-50)),
                           min(traces, key=lambda g: abs(g-100)), max(traces)})
        palette = ["#777777", "#215caf", "#007894", "#b7352d"]
        fig, axes = plt.subplots(3, 2, figsize=(12.5, 11), layout="constrained")
        for gamma, color in zip(selected, palette):
            frame = traces[gamma]
            label = fr"$\gamma={gamma:g}$"
            for ax, field in ((axes[0, 0], "frequency_hz"),
                              (axes[1, 0], "total_fcr_response_mw"), (axes[1, 1], "total_fi_response_mw"),
                              (axes[2, 0], "net_payment_eur"), (axes[2, 1], "frequency_hz")):
                ax.plot(frame.time_s, frame[field], color=color, label=label)
            event = plan["simulation_options"]["start_time_s"]
            before, after = frame.time_s < event, frame.time_s > event
            axes[0, 1].plot(np.r_[frame.time_s[before], event, event, frame.time_s[after]],
                np.r_[frame.rocof_hz_per_s[before], 0., summary.initial_rocof_hz_per_s_mean.iloc[0],
                      frame.rocof_hz_per_s[after]], color=color, label=label)
            target = frame[[f"{i}_target_mw" for i in metadata["fi_provider_ids"]]].sum(axis=1)
            axes[1, 1].step(frame.time_s, target, where="post", color=color, ls="--", alpha=.7)
        capacity = frame[[f"{i}_capacity_mw" for i in metadata["fi_provider_ids"]]].iloc[0].sum()
        axes[1, 1].axhline(capacity, color="#333333", ls=":", label="Total FI availability")
        axes[2, 1].set_xlim(max(plan["simulation_options"]["start_time_s"],
                              frame.time_s.iloc[-1]-plan["tail_diagnostics"]["window_s"]), frame.time_s.iloc[-1])
        tail_values = np.concatenate([traces[g].loc[traces[g].time_s >= axes[2, 1].get_xlim()[0],
                                                    "frequency_hz"].to_numpy() for g in selected])
        margin = max(.0001, float(np.ptp(tail_values)) * .1)
        axes[2, 1].set_ylim(float(tail_values.min())-margin, float(tail_values.max())+margin)
        for ax, title, ylabel in zip(axes.flat,
                ("Frequency", "Model RoCoF", "Total FCR delivery", "FI delivery (solid) and held target (dashed)",
                 "Cumulative net FI payment", "Final-window frequency"),
                ("Frequency [Hz]", "RoCoF [Hz/s]", "Power [MW]", "Power [MW]", "Payment [EUR]", "Frequency [Hz]")):
            ax.set(title=title, ylabel=ylabel, xlabel="Time [s]")
        axes[0, 0].legend(fontsize=9)
        axes[1, 1].legend(fontsize=8, loc="lower right")
        fig.suptitle("One matched illustrative event | predeclared design sample 0 | not expected trajectories")
        save(fig, "illustrative_trajectories")


def plot_tso_tradeoff(summary: pd.DataFrame, output_path: str | Path) -> None:
    """One event-level figure; error bars are pointwise uncertainty in means."""
    figure, axes = plt.subplots(1, 2, figsize=(12, 4.9), constrained_layout=True)
    colors = {"design": "#21618c", "validation": "#c35b22"}
    for bank, frame in summary.groupby("bank", sort=False):
        frame = frame.sort_values("gamma")
        axes[0].errorbar(frame.net_payment_eur_mean, frame.frequency_error_hz2_s_mean,
                         xerr=frame.net_payment_eur_ci95_halfwidth,
                         yerr=frame.frequency_error_hz2_s_ci95_halfwidth,
                         fmt="o-" if bank == "design" else "s--", color=colors[bank],
                         capsize=3, linewidth=1.3, label=f"{bank.capitalize()} (N={frame.samples.iloc[0]})")
        axes[1].plot(frame.gamma, 100 * frame.target_bound_fraction_mean,
                     "o-" if bank == "design" else "s--", color=colors[bank], label=bank.capitalize())
        if bank == "design":
            for row in frame.itertuples():
                axes[0].annotate(f"{row.gamma:g}", (row.net_payment_eur_mean, row.frequency_error_hz2_s_mean),
                                 xytext=(5, 7), textcoords="offset points", fontsize=8)
    axes[0].set(title="Frequency quality vs FI expenditure", xlabel="Mean net FI payment [EUR/event]",
                ylabel=r"Mean integrated squared frequency error [Hz$^2$ s]")
    axes[1].set(title="Time at the FI target bound", xlabel=r"Common $\gamma$ [EUR/(Hz MW s)]",
                ylabel="Mean fraction of post-event provider-time [%]", ylim=(-2, 102))
    for axis in axes:
        axis.grid(alpha=.25)
        axis.legend(fontsize=8)
    figure.suptitle("Fixed disturbance | labels: gamma | error bars: pointwise 95% CI of means", fontsize=11)
    figure.savefig(output_path, dpi=180)
    plt.close(figure)


def plot_tso_weight_comparison(cases: list[dict], baseline: pd.DataFrame, output_dir: Path) -> None:
    """Week-5-style figures from matched saved delivery events; no simulations.

    Each case contains metadata, trajectory, FI series/decisions and common
    availability/roles. Export vector PDFs and PNG previews for presentation.
    These curves are single realizations, not expectations or uncertainty bands.
    """
    palette = ["#215caf", "#007894", "#b7352d", "#627313", "#8656a3"]
    end = float(baseline.time_s.iloc[-1])
    event = cases[0]["metadata"]["simulation_options"]["start_time_s"]
    fi_ids = [identity for identity, role in cases[0]["market"]["roles"].items() if role == "fi"]

    def label(case):
        meta = case["metadata"]
        return (fr'$w_f={meta["settings"]["frequency_weight_eur_per_hz2_s"]:g}$, '
                fr'$\gamma={meta["gamma_selected"]:.3f}$')

    def save(fig, name):
        for suffix in ("png", "pdf"):
            fig.savefig(output_dir / f"{name}.{suffix}", dpi=180, bbox_inches="tight")
        plt.close(fig)

    style = {"font.size": 11, "axes.spines.top": False, "axes.spines.right": False,
             "axes.grid": True, "grid.alpha": .2, "axes.axisbelow": True,
             "lines.linewidth": 2.1, "pdf.fonttype": 42}
    with plt.rc_context(style):
        fig, axes = plt.subplots(1, 2, figsize=(12, 3.8), layout="constrained")
        for ax in axes:
            ax.plot(baseline.time_s, baseline.frequency_hz, color="#777777", ls="--",
                    label=r"FCR only ($\gamma=0$)")
            for j, case in enumerate(cases):
                frame = case["trajectory"]
                ax.plot(frame.time_s, frame.frequency_hz, color=palette[j % len(palette)], label=label(case))
            ax.set(xlabel="Time [s]", ylabel="Frequency [Hz]")
        axes[0].set(xlim=(0, end), title="Complete event")
        axes[1].set(xlim=(event, min(end, event + 7)), title="Initial transient")
        axes[0].legend(fontsize=9, loc="lower right", frameon=False)
        fig.suptitle("Frequency | same availability draw, dispatch and FCR awards", fontsize=12)
        save(fig, "frequency")

        fig, axes = plt.subplots(len(fi_ids), 2, figsize=(12, 3.6 * len(fi_ids)),
                                 squeeze=False, layout="constrained")
        for row, identity in enumerate(fi_ids):
            capacity = cases[0]["availability"][identity]
            for ax in axes[row]:
                for j, case in enumerate(cases):
                    frame = case["trajectory"]
                    decisions = case["decisions"].query("provider_id == @identity")
                    color = palette[j % len(palette)]
                    ax.plot(frame.time_s, frame[f"{identity}_response_mw"], color=color, label=label(case))
                    ax.step([*decisions.start_time_s, decisions.end_time_s.iloc[-1]],
                            [*decisions.target_mw, decisions.target_mw.iloc[-1]], where="post",
                            color=color, ls=":", lw=1.2)
                ax.axhline(capacity, color="#777777", ls="--", lw=1.2, label=f"Availability {capacity:.3f} MW")
                ax.set(xlabel="Time [s]", ylabel="FI response [MW]")
            axes[row, 0].set(xlim=(0, end), title=identity.replace("_", " "))
            axes[row, 1].set(xlim=(event, min(end, event + 3)), title="First decisions")
            axes[row, 0].legend(fontsize=8.5, loc="upper center", bbox_to_anchor=(.5, -.22),
                                ncol=2, frameon=False)
        fig.suptitle("FI response | solid: delivery, dotted: held target, dashed: capacity", fontsize=12)
        save(fig, "fi_response")

        fig, axes = plt.subplots(1, len(cases), figsize=(4.4 * len(cases), 3.8),
                                 sharey=True, squeeze=False, layout="constrained")
        for ax, case in zip(axes[0], cases, strict=True):
            frame = case["finances"]
            for key, color, name in (("net_settlement_eur", palette[0], "Actual net payment"),
                                     ("activation_cost_eur", "#777777", "Activation cost"),
                                     ("profit_eur", "#627313", "Provider profit")):
                ax.plot(frame.time_s, frame[f"total_{key}"], color=color, label=name,
                        ls="--" if key == "activation_cost_eur" else "-")
            ax.set(xlim=(0, end), xlabel="Time [s]", title=label(case))
            ax.legend(fontsize=8.5, loc="upper left", frameon=False)
        axes[0, 0].set_ylabel("Cumulative FI amount [EUR]")
        fig.suptitle("FI finances | one matched delivery event, common monetary scale", fontsize=12)
        save(fig, "fi_finances")

        fig, axes = plt.subplots(1, len(cases), figsize=(4.4 * len(cases), 3.8),
                                 sharey=True, squeeze=False, layout="constrained")
        for ax, case in zip(axes[0], cases, strict=True):
            frame = case["trajectory"]
            for j, (identity, role) in enumerate(case["market"]["roles"].items()):
                short = identity.replace("conventional_", "C").replace("renewable_", "R ")
                ax.plot(frame.time_s, frame[f"{identity}_response_mw"],
                        color=palette[j % len(palette)], label=f"{short} ({role.upper()})")
            ax.set(xlim=(0, end), xlabel="Time [s]", title=label(case))
        axes[0, 0].set_ylabel("Delivered response [MW]")
        fig.legend(*axes[0, 0].get_legend_handles_labels(), loc="outside lower center",
                   ncol=5, fontsize=9, frameon=False)
        fig.suptitle("Individual providers | deviations from the solved operating point", fontsize=12)
        save(fig, "provider_responses")


def plot_frequency_comparison(
    controlled: AggregateFCRTrajectory,
    numerical_no_fcr: AggregateFCRTrajectory,
    analytical_no_fcr: FrequencyTrajectory,
    output_path: str | Path,
) -> None:
    """Plot controlled frequency and both no-FCR regression trajectories."""

    figure, axis = plt.subplots(figsize=(8.0, 4.6), constrained_layout=True)
    axis.plot(
        controlled.time_s,
        controlled.frequency_hz,
        label="Numerical: FCR + FI" if controlled.fi is not None else "Numerical: FCR",
        linewidth=2.0,
    )
    axis.plot(
        numerical_no_fcr.time_s,
        numerical_no_fcr.frequency_hz,
        label="Numerical: no controlled response",
        linestyle=":",
        linewidth=2.0,
    )
    axis.plot(
        analytical_no_fcr.time_s,
        analytical_no_fcr.frequency_hz,
        label="Analytical: no controlled response",
        linestyle="--",
        linewidth=1.5,
    )
    axis.set_xlabel("Time [s]")
    axis.set_ylabel("Frequency [Hz]")
    axis.grid(alpha=0.3)
    axis.legend()
    figure.savefig(output_path, dpi=180)
    plt.close(figure)


def plot_provider_responses(
    controlled: AggregateFCRTrajectory,
    output_path: str | Path,
    *,
    reserve_symbol: str = r"q^{\mathrm{eff}}",
    participation_modes: tuple[str, ...] | None = None,
) -> None:
    """Plot every provider response separately with its reserve bounds."""

    number_of_providers = len(controlled.provider_ids)
    if participation_modes is not None and len(participation_modes) != number_of_providers:
        raise ValueError("participation_modes must match provider identities")
    figure, axes = plt.subplots(
        number_of_providers,
        1,
        figsize=(8.0, 2.2 * number_of_providers),
        sharex=True,
        constrained_layout=True,
    )
    if number_of_providers == 1:
        axes = [axes]

    for provider_index, (axis, provider_id) in enumerate(
        zip(axes, controlled.provider_ids, strict=True)
    ):
        reserve_mw = controlled.provider_reserve_mw[provider_index]
        is_fi = controlled.fi is not None and provider_id in controlled.fi.provider_ids
        role = (participation_modes[provider_index].upper() if participation_modes is not None
                else ("FI" if is_fi else "FCR"))
        axis.plot(
            controlled.time_s,
            controlled.provider_response_mw[:, provider_index],
            label=f"{provider_id}: {role} delivered",
            linewidth=2.0,
        )
        if is_fi:
            # Decision records retain every command even with a coarse output grid.
            records = [r for r in controlled.fi.intervals if r["provider_id"] == provider_id]
            axis.step([r["start_time_s"] for r in records] + [records[-1]["end_time_s"]],
                      [r["target_mw"] for r in records] + [records[-1]["target_mw"]],
                      where="post", linestyle=":", color="tab:orange", label="FI target")
        axis.axhline(
            reserve_mw,
            color="tab:red",
            linestyle="--",
            linewidth=1.0,
            label="$" + ("A_i" if is_fi else reserve_symbol) + f"={reserve_mw:g}$ MW",
        )
        axis.axhline(-reserve_mw, color="tab:red", linestyle="--", linewidth=1.0)
        axis.set_ylabel("Response [MW]")
        axis.grid(alpha=0.3)
        axis.legend(loc="lower right")

    axes[-1].set_xlabel("Time [s]")
    figure.savefig(output_path, dpi=180)
    plt.close(figure)


def plot_fi_financials(controlled: AggregateFCRTrajectory, output_path: str | Path) -> None:
    """Show signed rates, settlement benchmarks and cash ledgers, including totals."""
    fi = controlled.fi
    if fi is None:
        figure, axis = plt.subplots(figsize=(8, 3), constrained_layout=True)
        axis.text(.5, .5, "No FI participants in this scenario", ha="center", va="center")
        axis.set_axis_off()
    else:
        figure, axes = plt.subplots(len(fi.provider_ids) + 1, 3, squeeze=False,
                                   figsize=(15, 3 * (len(fi.provider_ids) + 1)), constrained_layout=True)
        for row, identity in enumerate((*fi.provider_ids, "All FI providers")):
            def values(name):
                data = getattr(fi, name)
                return data[:, row] if row < len(fi.provider_ids) else np.sum(data, axis=1)
            axes[row, 0].plot(controlled.time_s, values("payment_rate_eur_per_s"), color="tab:blue")
            axes[row, 0].axhline(0, color="black", linewidth=.6)
            axes[row, 0].set(title=identity, ylabel="Signed payment rate [EUR/s]")
            for name, label, style in (
                ("target_benchmark_eur", "Instantaneous-target benchmark", ":"),
                ("net_settlement_eur", "Actual settlement", "-"),
                ("predicted_settlement_eur", "Predicted settlement", "--"),
            ):
                axes[row, 1].plot(controlled.time_s, values(name), style, label=label)
            axes[row, 1].set(ylabel="Cumulative settlement [EUR]")
            for name, label in (("gross_rewards_eur", "Gross rewards"), ("gross_penalties_eur", "Gross penalties"),
                                ("activation_cost_eur", "Activation cost"), ("profit_eur", "Net profit")):
                axes[row, 2].plot(controlled.time_s, values(name), label=label)
            axes[row, 2].set(ylabel="Cumulative amount [EUR]")
            for axis in axes[row]:
                axis.set_xlabel("Time [s]")
                axis.grid(alpha=.25)
            axes[row, 1].legend(fontsize=8)
            axes[row, 2].legend(fontsize=8)
    figure.savefig(output_path, dpi=180)
    plt.close(figure)


def plot_scenario_comparison(
    trajectories: dict[str, pd.DataFrame], offers: pd.DataFrame, output_dir: Path,
) -> None:
    """Compare arbitrary named scenarios; labels and providers come from data."""
    figure, axes = plt.subplots(1, 2, figsize=(12, 4.8), constrained_layout=True)
    for label, frame in trajectories.items():
        axes[0].plot(frame.time_s, frame.frequency_fcr_hz, label=label, linewidth=2)
    axes[0].set(xlabel="Time [s]", ylabel="Frequency [Hz]", title="Controlled frequency")
    axes[0].legend(fontsize=8)
    pivot = offers.pivot(index="provider_id", columns="case", values="offered_mw")
    pivot = pivot.reindex(columns=list(trajectories))
    pivot.plot.bar(ax=axes[1], rot=20)
    axes[1].set(xlabel="", ylabel="Offered reserve [MW]", title="Provider participation")
    axes[1].legend(fontsize=8)
    axes[1].margins(y=0.25)
    for axis in axes:
        axis.grid(axis="y", alpha=0.2)
    figure.savefig(output_dir / "scenario_comparison.png", dpi=180)
    plt.close(figure)
