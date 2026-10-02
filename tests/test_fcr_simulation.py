import unittest

import numpy as np

from functional_incentives.grid.aggregate_frequency import (
    LinearFrequencyParameters,
    simulate_power_step,
)
from functional_incentives.providers.fcr import FCRProviderParameters
from functional_incentives.simulation.aggregate_fcr import simulate_fcr_power_step


class AggregateFCRSimulationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parameters = LinearFrequencyParameters(
            nominal_frequency_hz=60.0,
            equivalent_inertia_s=2.0,
            synchronous_rating_mva=1000.0,
            load_damping_mw_per_hz=1000.0 / 60.0,
        )
        self.providers = (
            FCRProviderParameters("provider_1", 10.0, 0.4, 1.0),
            FCRProviderParameters("provider_2", 15.0, 0.8, 2.0),
            FCRProviderParameters("provider_3", 20.0, 1.5, 3.0),
            FCRProviderParameters("provider_4", 25.0, 3.0, 4.0),
        )
        self.common_arguments = {
            "power_deficit_mw": 10.0,
            "start_time_s": 1.0,
            "final_time_s": 40.0,
            "output_time_step_s": 0.05,
        }

    def test_provider_states_are_separate_and_reserve_bounded(self) -> None:
        operating_power_mw = (210.0, 323.49, 466.51, 3.94)
        result = simulate_fcr_power_step(
            self.parameters,
            self.providers,
            provider_operating_power_mw=operating_power_mw,
            **self.common_arguments,
        )

        self.assertEqual(result.provider_response_mw.shape, (801, 4))
        self.assertEqual(result.provider_requested_response_mw.shape, (801, 4))
        self.assertTrue(np.all(result.provider_response_mw[result.time_s < 1.0] == 0.0))
        self.assertTrue(np.all(result.provider_response_mw >= -1e-10))
        self.assertTrue(
            np.all(
                np.abs(result.provider_response_mw)
                <= result.provider_reserve_mw[np.newaxis, :] + 1e-8
            )
        )
        self.assertIsNotNone(result.provider_active_power_mw)
        np.testing.assert_allclose(
            result.provider_active_power_mw,
            np.asarray(operating_power_mw)[np.newaxis, :]
            + result.provider_response_mw,
        )

    def test_numerical_no_fcr_matches_analytical_reference(self) -> None:
        numerical = simulate_fcr_power_step(
            self.parameters,
            (),
            **self.common_arguments,
        )
        analytical = simulate_power_step(
            self.parameters,
            power_deficit_mw=10.0,
            start_time_s=1.0,
            final_time_s=40.0,
            time_step_s=0.05,
        )
        np.testing.assert_allclose(
            numerical.delta_frequency_hz,
            analytical.delta_frequency_hz,
            atol=1e-9,
            rtol=0.0,
        )

    def test_non_divisible_output_grids_match_and_include_exact_endpoint(self) -> None:
        # These cases previously produced different lengths (40 s) or silently
        # compared the numerical value at 2 s with the analytical value at 2.1 s.
        for final_time_s in (40.0, 2.0):
            with self.subTest(final_time_s=final_time_s):
                numerical = simulate_fcr_power_step(
                    self.parameters,
                    (),
                    power_deficit_mw=10.0,
                    start_time_s=1.0,
                    final_time_s=final_time_s,
                    output_time_step_s=0.3,
                )
                analytical = simulate_power_step(
                    self.parameters,
                    power_deficit_mw=10.0,
                    start_time_s=1.0,
                    final_time_s=final_time_s,
                    time_step_s=0.3,
                )
                np.testing.assert_array_equal(numerical.time_s, analytical.time_s)
                self.assertEqual(numerical.time_s[0], 0.0)
                self.assertEqual(numerical.time_s[-1], final_time_s)
                self.assertTrue(np.all(numerical.time_s <= final_time_s))
                self.assertTrue(np.all(np.diff(numerical.time_s) > 0.0))
                np.testing.assert_allclose(
                    numerical.delta_frequency_hz,
                    analytical.delta_frequency_hz,
                    atol=1e-9,
                    rtol=0.0,
                )

    def test_event_just_before_final_time_is_not_omitted(self) -> None:
        start_time_s = 10.00001
        final_time_s = 10.00005
        numerical = simulate_fcr_power_step(
            self.parameters,
            (),
            power_deficit_mw=10.0,
            start_time_s=start_time_s,
            final_time_s=final_time_s,
            output_time_step_s=1.0,
        )
        analytical = simulate_power_step(
            self.parameters,
            power_deficit_mw=10.0,
            start_time_s=start_time_s,
            final_time_s=final_time_s,
            time_step_s=1.0,
        )
        np.testing.assert_array_equal(numerical.time_s, analytical.time_s)
        self.assertEqual(numerical.time_s[-1], final_time_s)
        self.assertTrue(np.all(np.diff(numerical.time_s) > 0.0))
        np.testing.assert_array_equal(numerical.delta_frequency_hz[:-1], 0.0)
        np.testing.assert_array_equal(numerical.power_deficit_mw[:-1], 0.0)
        self.assertLess(numerical.delta_frequency_hz[-1], 0.0)
        self.assertEqual(numerical.power_deficit_mw[-1], 10.0)
        np.testing.assert_allclose(
            numerical.delta_frequency_hz,
            analytical.delta_frequency_hz,
            atol=1e-12,
            rtol=0.0,
        )

    def test_numerical_simulation_rejects_non_finite_inputs(self) -> None:
        valid_inputs = {
            **self.common_arguments,
            "maximum_solver_step_s": 0.05,
            "relative_tolerance": 1e-9,
            "absolute_tolerance": 1e-11,
        }
        for field in valid_inputs:
            for value in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        simulate_fcr_power_step(
                            self.parameters,
                            self.providers,
                            **{**valid_inputs, field: value},
                        )

    def test_non_finite_operating_powers_are_rejected(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    simulate_fcr_power_step(
                        self.parameters,
                        self.providers,
                        provider_operating_power_mw=(210.0, value, 466.51, 3.94),
                        **self.common_arguments,
                    )

    def test_zero_deficit_preserves_equilibrium_with_active_providers(self) -> None:
        result = simulate_fcr_power_step(
            self.parameters,
            self.providers,
            **{**self.common_arguments, "power_deficit_mw": 0.0},
        )
        np.testing.assert_array_equal(result.delta_frequency_hz, 0.0)
        np.testing.assert_array_equal(result.frequency_hz, 60.0)
        np.testing.assert_array_equal(result.provider_response_mw, 0.0)
        np.testing.assert_array_equal(result.provider_requested_response_mw, 0.0)
        np.testing.assert_array_equal(result.total_response_mw, 0.0)

    def test_zero_gain_or_reserve_providers_remain_inactive(self) -> None:
        inactive_providers = (
            FCRProviderParameters("zero_gain", 0.0, 0.4, 1.0),
            FCRProviderParameters("zero_reserve", 15.0, 0.8, 0.0),
        )
        result = simulate_fcr_power_step(
            self.parameters,
            inactive_providers,
            **self.common_arguments,
        )
        no_fcr = simulate_power_step(
            self.parameters,
            power_deficit_mw=10.0,
            start_time_s=1.0,
            final_time_s=40.0,
            time_step_s=0.05,
        )
        np.testing.assert_array_equal(result.provider_response_mw, 0.0)
        np.testing.assert_array_equal(result.provider_requested_response_mw, 0.0)
        np.testing.assert_array_equal(result.total_response_mw, 0.0)
        np.testing.assert_allclose(
            result.delta_frequency_hz,
            no_fcr.delta_frequency_hz,
            atol=1e-9,
            rtol=0.0,
        )

    def test_fcr_contains_frequency_but_does_not_restore_nominal(self) -> None:
        controlled = simulate_fcr_power_step(
            self.parameters,
            self.providers,
            **self.common_arguments,
        )
        no_fcr = simulate_fcr_power_step(
            self.parameters,
            (),
            **self.common_arguments,
        )

        self.assertGreater(controlled.frequency_hz[-1], no_fcr.frequency_hz[-1])
        self.assertLess(controlled.frequency_hz[-1], self.parameters.nominal_frequency_hz)
        self.assertGreater(controlled.total_response_mw[-1], 0.0)

        # At equilibrium provider 1 is saturated at 1 MW. Providers 2--4
        # remain on their linear droop branches, whose total gain is 60 MW/Hz.
        expected_delta_frequency_hz = -9.0 / (
            self.parameters.load_damping_mw_per_hz + 60.0
        )
        self.assertTrue(
            np.isclose(
                controlled.delta_frequency_hz[-1],
                expected_delta_frequency_hz,
                atol=1e-6,
            )
        )

    def test_solution_converges_with_smaller_maximum_step(self) -> None:
        nominal = simulate_fcr_power_step(
            self.parameters,
            self.providers,
            **self.common_arguments,
        )
        refined = simulate_fcr_power_step(
            self.parameters,
            self.providers,
            maximum_solver_step_s=0.025,
            relative_tolerance=1e-10,
            absolute_tolerance=1e-12,
            **self.common_arguments,
        )
        np.testing.assert_allclose(
            nominal.delta_frequency_hz,
            refined.delta_frequency_hz,
            atol=1e-8,
            rtol=0.0,
        )
        np.testing.assert_allclose(
            nominal.provider_response_mw,
            refined.provider_response_mw,
            # Require every provider trajectory to agree within 1 W, including
            # where the saturated request changes between its linear branches.
            atol=1e-6,
            rtol=0.0,
        )


if __name__ == "__main__":
    unittest.main()
