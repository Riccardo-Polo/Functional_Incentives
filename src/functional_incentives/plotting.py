"""Frequency, provider-response and scenario-comparison figures."""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
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
        label=f"Numerical: {len(controlled.provider_ids)} FCR providers",
        linewidth=2.0,
    )
    axis.plot(
        numerical_no_fcr.time_s,
        numerical_no_fcr.frequency_hz,
        label="Numerical: no FCR",
        linestyle=":",
        linewidth=2.0,
    )
    axis.plot(
        analytical_no_fcr.time_s,
        analytical_no_fcr.frequency_hz,
        label="Analytical reference: no FCR",
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
) -> None:
    """Plot every provider response separately with its reserve bounds."""

    number_of_providers = len(controlled.provider_ids)
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
        axis.plot(
            controlled.time_s,
            controlled.provider_response_mw[:, provider_index],
            label=f"$u_{{{provider_index + 1}}}$ ({provider_id})",
            linewidth=2.0,
        )
        axis.axhline(
            reserve_mw,
            color="tab:red",
            linestyle="--",
            linewidth=1.0,
            label="$" + reserve_symbol + f"={reserve_mw:g}$ MW",
        )
        axis.axhline(-reserve_mw, color="tab:red", linestyle="--", linewidth=1.0)
        axis.set_ylabel("Response [MW]")
        axis.grid(alpha=0.3)
        axis.legend(loc="lower right")

    axes[-1].set_xlabel("Time [s]")
    figure.savefig(output_path, dpi=180)
    plt.close(figure)


def plot_scenario_comparison(
    trajectories: dict[str, pd.DataFrame], offers: pd.DataFrame, output_dir: Path,
) -> None:
    """Compare arbitrary named scenarios; labels and providers come from data."""
    figure, axes = plt.subplots(1, 2, figsize=(12, 4.8), constrained_layout=True)
    for label, frame in trajectories.items():
        axes[0].plot(frame.time_s, frame.frequency_fcr_hz, label=label, linewidth=2)
    axes[0].set(xlabel="Time [s]", ylabel="Frequency [Hz]", title="Frequency with FCR")
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
