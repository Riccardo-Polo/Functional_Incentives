"""Run the four-provider aggregate FCR teaching scenario.

The scenario assumptions live in ``configs/experiments/pjm5_fcr_teaching.toml``.
The script writes auditable time series and two figures under ``results/``.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import tomllib

import numpy as np
import pandas as pd

from functional_incentives.grid import (
    LinearFrequencyParameters,
    damping_from_load_sensitivity,
    simulate_power_step,
    solve_pjm5_operating_point,
)
from functional_incentives.plotting import (
    plot_frequency_comparison,
    plot_provider_responses,
)
from functional_incentives.providers import load_fcr_provider_parameters
from functional_incentives.simulation import simulate_fcr_power_step


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = (
    PROJECT_ROOT / "configs" / "experiments" / "pjm5_fcr_teaching.toml"
)
DEFAULT_OUTPUT_DIRECTORY = PROJECT_ROOT / "results" / "pjm5_fcr_teaching"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    arguments = parser.parse_args()

    with arguments.config.open("rb") as config_file:
        config = tomllib.load(config_file)
    scenario = config["scenario"]
    providers = load_fcr_provider_parameters(arguments.config)

    operating_point = solve_pjm5_operating_point()
    if len(providers) != len(operating_point.generator_active_power_mw):
        raise ValueError(
            "The teaching scenario needs one provider per PJM 5-bus generator"
        )

    load_damping_mw_per_hz = damping_from_load_sensitivity(
        load_mw=operating_point.total_load_mw,
        nominal_frequency_hz=operating_point.nominal_frequency_hz,
        per_unit_load_change_per_unit_frequency_change=float(
            scenario["load_frequency_sensitivity"]
        ),
    )
    frequency_parameters = LinearFrequencyParameters(
        nominal_frequency_hz=operating_point.nominal_frequency_hz,
        equivalent_inertia_s=operating_point.equivalent_inertia_s,
        synchronous_rating_mva=operating_point.total_synchronous_rating_mva,
        load_damping_mw_per_hz=load_damping_mw_per_hz,
    )

    common_simulation_arguments = {
        "power_deficit_mw": float(scenario["power_deficit_mw"]),
        "start_time_s": float(scenario["event_time_s"]),
        "final_time_s": float(scenario["final_time_s"]),
    }
    output_time_step_s = float(scenario["output_time_step_s"])

    controlled = simulate_fcr_power_step(
        frequency_parameters,
        providers,
        output_time_step_s=output_time_step_s,
        provider_operating_power_mw=operating_point.generator_active_power_mw,
        **common_simulation_arguments,
    )
    numerical_no_fcr = simulate_fcr_power_step(
        frequency_parameters,
        (),
        output_time_step_s=output_time_step_s,
        **common_simulation_arguments,
    )
    analytical_no_fcr = simulate_power_step(
        frequency_parameters,
        time_step_s=output_time_step_s,
        **common_simulation_arguments,
    )

    refined_controlled = simulate_fcr_power_step(
        frequency_parameters,
        providers,
        output_time_step_s=output_time_step_s,
        maximum_solver_step_s=output_time_step_s / 2.0,
        relative_tolerance=1e-10,
        absolute_tolerance=1e-12,
        **common_simulation_arguments,
    )

    for label, trajectory in (
        ("numerical no-FCR", numerical_no_fcr),
        ("analytical no-FCR", analytical_no_fcr),
        ("refined FCR", refined_controlled),
    ):
        if not np.array_equal(controlled.time_s, trajectory.time_s):
            raise RuntimeError(f"The {label} trajectory uses a different output time grid")

    maximum_absolute_response_mw = np.max(
        np.abs(controlled.provider_response_mw), axis=0
    )
    if np.any(maximum_absolute_response_mw > controlled.provider_reserve_mw + 1e-8):
        raise RuntimeError("At least one provider exceeded its FCR reserve")

    analytical_regression_error_hz = float(
        np.max(
            np.abs(
                numerical_no_fcr.delta_frequency_hz
                - analytical_no_fcr.delta_frequency_hz
            )
        )
    )
    convergence_error_hz = float(
        np.max(
            np.abs(
                controlled.delta_frequency_hz
                - refined_controlled.delta_frequency_hz
            )
        )
    )

    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    time_series = pd.DataFrame(
        {
            "time_s": controlled.time_s,
            "power_deficit_mw": controlled.power_deficit_mw,
            "frequency_fcr_hz": controlled.frequency_hz,
            "frequency_no_fcr_numerical_hz": numerical_no_fcr.frequency_hz,
            "frequency_no_fcr_analytical_hz": analytical_no_fcr.frequency_hz,
            "total_fcr_response_mw": controlled.total_response_mw,
        }
    )
    for provider_index, provider_id in enumerate(controlled.provider_ids):
        time_series[f"{provider_id}_requested_mw"] = (
            controlled.provider_requested_response_mw[:, provider_index]
        )
        time_series[f"{provider_id}_response_mw"] = (
            controlled.provider_response_mw[:, provider_index]
        )
        if controlled.provider_active_power_mw is not None:
            time_series[f"{provider_id}_active_power_mw"] = (
                controlled.provider_active_power_mw[:, provider_index]
            )
    time_series.to_csv(arguments.output_dir / "time_series.csv", index=False)

    plot_frequency_comparison(
        controlled,
        numerical_no_fcr,
        analytical_no_fcr,
        arguments.output_dir / "frequency_comparison.png",
    )
    plot_provider_responses(
        controlled,
        arguments.output_dir / "provider_responses.png",
    )

    final_time_label = np.format_float_positional(controlled.time_s[-1], trim="-")
    print("PJM 5-bus aggregate FCR teaching scenario")
    print(f"  Solver: {controlled.solver_message}")
    print(f"  Positive permanent deficit: {scenario['power_deficit_mw']:.1f} MW")
    print(
        f"  No-FCR frequency at {final_time_label} s: "
        f"{numerical_no_fcr.frequency_hz[-1]:.6f} Hz"
    )
    print(f"  FCR frequency nadir: {np.min(controlled.frequency_hz):.6f} Hz")
    print(
        f"  FCR frequency at {final_time_label} s: "
        f"{controlled.frequency_hz[-1]:.6f} Hz"
    )
    print(
        "  Numerical/analytical no-FCR max error: "
        f"{analytical_regression_error_hz:.3e} Hz"
    )
    print(f"  Refined-solver max difference: {convergence_error_hz:.3e} Hz")
    print("\nProvider checks (teaching assumptions)")
    for provider_index, provider in enumerate(providers):
        base_power_mw = operating_point.generator_active_power_mw[provider_index]
        print(
            f"  u_{provider_index + 1}: K={provider.gain_mw_per_hz:g} MW/Hz, "
            f"T={provider.time_constant_s:g} s, q*={provider.reserve_mw:g} MW, "
            f"max|u|={maximum_absolute_response_mw[provider_index]:.6f} MW, "
            f"u({final_time_label} s)="
            f"{controlled.provider_response_mw[-1, provider_index]:.6f} MW, "
            f"P({final_time_label} s)="
            f"{base_power_mw + controlled.provider_response_mw[-1, provider_index]:.6f} MW"
        )
    print(f"\nResults written to: {arguments.output_dir}")


if __name__ == "__main__":
    main()
