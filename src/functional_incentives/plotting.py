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
