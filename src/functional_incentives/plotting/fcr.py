"""Figures for the aggregate FCR teaching experiment."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from functional_incentives.grid.aggregate_frequency import FrequencyTrajectory
from functional_incentives.simulation.aggregate_fcr import AggregateFCRTrajectory


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
        label="Numerical: four FCR providers",
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
            label=f"$q^\\star={reserve_mw:g}$ MW",
        )
        axis.axhline(-reserve_mw, color="tab:red", linestyle="--", linewidth=1.0)
        axis.set_ylabel("Response [MW]")
        axis.grid(alpha=0.3)
        axis.legend(loc="lower right")

    axes[-1].set_xlabel("Time [s]")
    figure.savefig(output_path, dpi=180)
    plt.close(figure)
