import unittest

import numpy as np

from functional_incentives.grid.aggregate_frequency import (
    LinearFrequencyParameters,
    damping_from_load_sensitivity,
    frequency_derivative_hz_per_s,
    simulate_power_step,
)


class AggregateFrequencyTests(unittest.TestCase):
    def test_frequency_mass_and_initial_rocof(self) -> None:
        parameters = LinearFrequencyParameters(
            nominal_frequency_hz=60.0,
            equivalent_inertia_s=2.0,
            synchronous_rating_mva=1000.0,
            load_damping_mw_per_hz=20.0,
        )

        self.assertTrue(
            np.isclose(parameters.frequency_mass_mw_s_per_hz, 200.0 / 3.0)
        )
        self.assertTrue(
            np.isclose(
                frequency_derivative_hz_per_s(
                    0.0,
                    power_deficit_mw=10.0,
                    power_response_mw=0.0,
                    parameters=parameters,
                ),
                -0.15,
            )
        )

    def test_damped_step_converges_to_expected_steady_state(self) -> None:
        parameters = LinearFrequencyParameters(
            nominal_frequency_hz=60.0,
            equivalent_inertia_s=2.0,
            synchronous_rating_mva=1000.0,
            load_damping_mw_per_hz=20.0,
        )

        trajectory = simulate_power_step(
            parameters,
            power_deficit_mw=10.0,
            start_time_s=1.0,
            final_time_s=100.0,
            time_step_s=0.1,
        )

        self.assertTrue(
            np.all(trajectory.delta_frequency_hz[trajectory.time_s < 1.0] == 0.0)
        )
        self.assertTrue(
            np.isclose(trajectory.delta_frequency_hz[-1], -0.5, atol=1e-10)
        )
        self.assertTrue(np.isclose(trajectory.frequency_hz[-1], 59.5, atol=1e-10))

    def test_zero_damping_gives_a_frequency_ramp(self) -> None:
        parameters = LinearFrequencyParameters(
            nominal_frequency_hz=60.0,
            equivalent_inertia_s=2.0,
            synchronous_rating_mva=1000.0,
            load_damping_mw_per_hz=0.0,
        )

        trajectory = simulate_power_step(
            parameters,
            power_deficit_mw=10.0,
            start_time_s=1.0,
            final_time_s=3.0,
            time_step_s=0.1,
        )

        self.assertTrue(np.isclose(trajectory.delta_frequency_hz[-1], -0.3))

    def test_load_sensitivity_conversion(self) -> None:
        damping = damping_from_load_sensitivity(
            load_mw=1000.0,
            nominal_frequency_hz=60.0,
            per_unit_load_change_per_unit_frequency_change=1.0,
        )

        self.assertTrue(np.isclose(damping, 1000.0 / 60.0))


if __name__ == "__main__":
    unittest.main()
