import unittest
from dataclasses import replace

import numpy as np

from functional_incentives.frequency import (
    LinearFrequencyParameters, analytical_no_fcr, damping_from_load_sensitivity,
    frequency_derivative_hz_per_s,
)
from functional_incentives.providers import FCRProviderParameters, droop_gain_mw_per_hz
from functional_incentives.simulation import simulate_fcr_power_step


class FrequencyDynamicsTests(unittest.TestCase):
    def setUp(self):
        self.physical = LinearFrequencyParameters(60, 4700/1100, 1100, 1000/60)
        self.providers = tuple(FCRProviderParameters(str(i), base/3, delay, reserve)
                               for i, (base, delay, reserve) in enumerate(zip(
                                   [300, 400, 400, 150, 150], [2, 3, 4, .25, .25], [4, 4, 3, 1, 0])))
        self.event = dict(power_deficit_mw=10, start_time_s=1, final_time_s=40)

    def test_units_signs_droop_and_provider_response(self):
        self.assertAlmostEqual(self.physical.frequency_mass_mw_s_per_hz, 2*4700/60)
        self.assertLess(frequency_derivative_hz_per_s(0, power_deficit_mw=10,
                        power_response_mw=0, parameters=self.physical), 0)
        self.assertAlmostEqual(damping_from_load_sensitivity(load_mw=1000, nominal_frequency_hz=60,
                               per_unit_load_change_per_unit_frequency_change=1), 1000/60)
        for p in self.providers:
            self.assertGreaterEqual(p.requested_response_mw(-.1), 0)
            self.assertLessEqual(p.requested_response_mw(.1), 0)
            self.assertEqual(p.requested_response_mw(-10), p.reserve_mw)
        p = FCRProviderParameters("a", 20, 2, 3)
        self.assertEqual(p.response_derivative_mw_per_s(1, -.1), .5)

    def test_no_fcr_matches_analytical_reference_and_exact_endpoints(self):
        for damping in (0, 1000/60):
            parameters = replace(self.physical, load_damping_mw_per_hz=damping)
            for final, dt, event_time in [(2, .3, 1), (.05, .1, .025), (1, .3, 1-1e-10)]:
                event = dict(power_deficit_mw=10, start_time_s=event_time, final_time_s=final)
                numeric = simulate_fcr_power_step(parameters, (), **event, output_time_step_s=dt)
                exact = analytical_no_fcr(parameters, **event, time_step_s=dt)
                self.assertEqual(numeric.time_s[-1], final)
                self.assertTrue(np.all(np.diff(numeric.time_s) > 0))
                np.testing.assert_array_equal(numeric.time_s, exact.time_s)
                np.testing.assert_allclose(numeric.frequency_hz, exact.frequency_hz, atol=1e-9, rtol=0)

    def test_individual_states_bounds_and_total_power(self):
        powers = np.array([260, 310, 250.9, 90, 90])
        result = simulate_fcr_power_step(self.physical, self.providers, **self.event,
                                        output_time_step_s=.05, provider_operating_power_mw=powers)
        self.assertEqual(result.provider_response_mw.shape, (801, 5))
        np.testing.assert_array_equal(result.provider_response_mw[result.time_s <= 1], 0)
        for values in (result.provider_response_mw, result.provider_requested_response_mw):
            self.assertTrue(np.all(values >= -1e-8))
            self.assertTrue(np.all(abs(values) <= result.provider_reserve_mw + 1e-8))
        np.testing.assert_allclose(result.provider_active_power_mw, powers + result.provider_response_mw)
        self.assertLess(result.frequency_hz[-1], 60)
        no_fcr = analytical_no_fcr(self.physical, **self.event, time_step_s=.05)
        self.assertGreater(result.frequency_hz[-1], no_fcr.frequency_hz[-1])

    def test_zero_event_gain_or_award_has_no_artificial_response(self):
        zero = simulate_fcr_power_step(self.physical, self.providers,
                                      **{**self.event, "power_deficit_mw": 0}, output_time_step_s=.1)
        np.testing.assert_array_equal(zero.provider_response_mw, 0)
        np.testing.assert_array_equal(zero.frequency_hz, 60)
        providers = [FCRProviderParameters("a", 0, 1, 4), FCRProviderParameters("b", 100, 1, 0)]
        result = simulate_fcr_power_step(self.physical, providers, **self.event, output_time_step_s=.1)
        np.testing.assert_array_equal(result.provider_response_mw, 0)

    def test_solver_refinement_preserves_frequency_and_responses(self):
        nominal = simulate_fcr_power_step(self.physical, self.providers, **self.event, output_time_step_s=.05)
        refined = simulate_fcr_power_step(self.physical, self.providers, **self.event,
                                         output_time_step_s=.05, maximum_solver_step_s=.025,
                                         relative_tolerance=1e-10, absolute_tolerance=1e-12)
        np.testing.assert_allclose(nominal.frequency_hz, refined.frequency_hz, atol=1e-8, rtol=0)
        np.testing.assert_allclose(nominal.provider_response_mw, refined.provider_response_mw, atol=1e-6, rtol=0)

    def test_invalid_parameters_and_solver_inputs_fail(self):
        for invalid in (float("nan"), float("inf"), -1):
            with self.assertRaises(ValueError):
                replace(self.physical, load_damping_mw_per_hz=invalid)
            with self.assertRaises(ValueError):
                FCRProviderParameters("a", invalid, 1, 1)
            with self.assertRaises(ValueError):
                simulate_fcr_power_step(self.physical, self.providers, **self.event,
                                        output_time_step_s=invalid)
            with self.assertRaises(ValueError):
                analytical_no_fcr(self.physical, **self.event, time_step_s=invalid)
        with self.assertRaises(ValueError):
            simulate_fcr_power_step(self.physical, self.providers, **self.event,
                                    output_time_step_s=.1, provider_operating_power_mw=[np.nan]*5)


class NormalizedDroopTests(unittest.TestCase):
    def test_case_ratings_give_equal_normalized_droop(self):
        bases = np.array([200, 300, 300, 200])
        gains = np.array([droop_gain_mw_per_hz(p, 60) for p in bases])
        np.testing.assert_allclose(gains, [200 / 3, 100, 100, 200 / 3])
        np.testing.assert_allclose(gains * 60 / bases, 20)
        np.testing.assert_allclose(gains * 0.06 / bases, 0.02)

    def test_frequency_base_and_invalid_inputs(self):
        self.assertEqual(droop_gain_mw_per_hz(100, 50), 40)
        for field in range(3):
            for invalid in (0, -1, float("nan"), float("inf")):
                values = [100, 60, 0.05]
                values[field] = invalid
                with self.assertRaises(ValueError):
                    droop_gain_mw_per_hz(*values)
