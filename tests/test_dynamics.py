import unittest
from dataclasses import replace

import numpy as np
from scipy.integrate import quad

from functional_incentives.frequency import (
    LinearFrequencyParameters, analytical_no_fcr, damping_from_load_sensitivity,
    frequency_derivative_hz_per_s,
)
from functional_incentives.providers import FCRProviderParameters, FIProviderParameters, droop_gain_mw_per_hz
from functional_incentives.simulation import simulate_fcr_power_step


class FPPDynamicsTests(unittest.TestCase):
    def test_gain_controls_hold_their_target_between_ticks_and_cannot_see_future_events(self):
        from functional_incentives.providers import FPPProviderParameters
        plant = LinearFrequencyParameters(60, 5, 60, 1.)
        providers = (FPPProviderParameters("a", 100., .25, 2., 1., theta=0.5),
                     FPPProviderParameters("b", 100., .25, 3., 1., theta=1.))
        options = dict(power_deficit_mw=10., start_time_s=.05, final_time_s=.45,
                       output_time_step_s=.01, measurement_times_s=[.3, .35], maximum_solver_step_s=.01)
        trace = simulate_fcr_power_step(plant, providers, **options)
        changed = simulate_fcr_power_step(plant, providers, **options, power_deficit_changes=[(.315, -7.)])
        before = trace.time_s < .315
        np.testing.assert_allclose(trace.provider_response_mw[before], changed.provider_response_mw[before], atol=1e-11)
        np.testing.assert_array_equal(trace.provider_requested_response_mw[trace.time_s < .1], 0.)
        for left in (0., .1, .2):
            index = np.searchsorted(trace.time_s, left)
            mask = (trace.time_s >= left) & (trace.time_s < left + .1)
            expected = [p.requested_response_mw(trace.delta_frequency_hz[index]) for p in providers]
            np.testing.assert_allclose(trace.provider_requested_response_mw[mask],
                                       np.tile(expected, (int(mask.sum()), 1)), atol=1e-10)
        self.assertGreater(trace.provider_requested_response_mw.max(), 0.)
        self.assertLessEqual(np.max(abs(trace.provider_response_mw[:, 0])), 2.)
        self.assertLessEqual(np.max(abs(trace.provider_response_mw[:, 1])), 3.)
        silent = replace(providers[0], theta=0.)
        self.assertEqual(silent.requested_response_mw(-.1), 0.)

    def test_ideal_settlement_scores_a_single_small_sample_and_balances(self):
        from functional_incentives.fpp import DispatchSchedule, IDEAL_PROFILE, settle_fpp
        schedule = DispatchSchedule(("a","b"), [[100,50],[100,50]], [100,50], [0,0], [200,100], [4,4])
        times = np.arange(0., 301., 4.)
        errors = np.zeros_like(times)
        errors[-1] = -.005
        meter = schedule.reference_mw(times).copy()
        meter[-1] += [2., -1.]
        ideal = settle_fpp(times, errors, meter, schedule, profile=IDEAL_PROFILE)
        np.testing.assert_array_equal(ideal["samples"].fm_hz, -errors[1:])
        accounts = ideal["accounts"].query("direction == 'raise'").set_index("provider_id")
        np.testing.assert_allclose(accounts.loc[["a","b","residual"]].coefficient_mw_h, [2/12,-1/12,-1/12])
        self.assertTrue((ideal["accounts"].query("direction == 'lower'").coefficient_mw_h == 0.).all())
        self.assertTrue((settle_fpp(times, errors, meter, schedule)["accounts"].coefficient_mw_h == 0.).all())

    def test_signed_disturbance_superposition_and_exact_rocof_at_all_events(self):
        from functional_incentives.metrics import frequency_performance_metrics
        for damping in (0., 1.):
            physical = LinearFrequencyParameters(60, 5, 60, damping)
            options = dict(power_deficit_mw=10., start_time_s=.15, final_time_s=8.,
                output_time_step_s=.2, maximum_solver_step_s=.03,
                power_deficit_changes=((2.37, -7.), (5.11, 0.)),
                relative_tolerance=1e-11, absolute_tolerance=1e-13)
            trajectory = simulate_fcr_power_step(physical, (), **options)
            mass = physical.frequency_mass_mw_s_per_hz
            def exact(time):
                value = np.zeros_like(np.asarray(time, dtype=float))
                for event_time, increment in ((.15, 10.), (2.37, -17.), (5.11, 7.)):
                    elapsed = np.maximum(np.asarray(time) - event_time, 0.)
                    value += (-increment * elapsed / mass if damping == 0 else
                              increment / damping * np.expm1(-damping * elapsed / mass))
                return value
            np.testing.assert_allclose(trajectory.delta_frequency_hz, exact(trajectory.time_s), atol=2e-10)
            np.testing.assert_array_equal(trajectory.power_deficit_mw,
                np.select([trajectory.time_s < .15, trajectory.time_s < 2.37, trajectory.time_s < 5.11],
                          [0., 10., -7.], default=0.))
            self.assertEqual(len(trajectory.disturbance_limits), 3)
            for event in trajectory.disturbance_limits:
                self.assertAlmostEqual(event["delta_frequency_hz"], float(exact(event["time_s"])), places=9)
                jump = -(event["deficit_after_mw"] - event["deficit_before_mw"]) / mass
                self.assertAlmostEqual(event["rocof_right_hz_per_s"] - event["rocof_left_hz_per_s"], jump)
            peak = max(abs(e[k]) for e in trajectory.disturbance_limits
                       for k in ("rocof_left_hz_per_s", "rocof_right_hz_per_s"))
            self.assertAlmostEqual(frequency_performance_metrics(trajectory)["rocof_max_abs_hz_per_s"], peak)
            expected_integral = quad(lambda t: float(exact(t))**2, 0, 8, points=[.15, 2.37, 5.11])[0]
            self.assertAlmostEqual(trajectory.integrated_squared_frequency_error_hz2_s[-1], expected_integral, places=8)
        for changes in (((.1, -7.),), ((2., -7.), (2., 0.)), ((8., -7.),), ((2., np.nan),)):
            with self.assertRaises(ValueError):
                simulate_fcr_power_step(physical, (), **{**options, "power_deficit_changes": changes})

    def test_reversal_preserves_fi_clock_and_live_accounting(self):
        physical = LinearFrequencyParameters(60, 5, 60, 1.)
        provider = FIProviderParameters("fi", .25, 4., 50., 1.)
        trajectory = simulate_fcr_power_step(physical, (provider,), power_deficit_mw=10,
            start_time_s=.15, final_time_s=10, output_time_step_s=.05,
            maximum_solver_step_s=.02, fi_decision_interval_s=.2,
            power_deficit_changes=((3.07, -7.),), relative_tolerance=1e-11, absolute_tolerance=1e-13)
        self.assertEqual(len(trajectory.fi.intervals), 50)
        np.testing.assert_allclose([r["start_time_s"] for r in trajectory.fi.intervals], np.arange(50)*.2)
        for row in trajectory.fi.intervals:
            self.assertAlmostEqual(row["net_settlement_eur"], row["predicted_settlement_eur"], delta=1e-7)
            self.assertAlmostEqual(row["activation_cost_eur"], row["predicted_activation_cost_eur"], delta=1e-7)
        self.assertGreater(trajectory.provider_response_mw.max(), 0)
        self.assertLess(trajectory.provider_response_mw.min(), 0)

    def test_reversal_exercises_both_fpp_directions_and_filter_memory(self):
        from functional_incentives.providers import FPPProviderParameters
        from functional_incentives.fpp import DispatchSchedule, settle_fpp
        physical = LinearFrequencyParameters(60, 4700/1100, 1100, 1000/60)
        providers = (FCRProviderParameters("fcr", 200., 2., 8.), FPPProviderParameters("fpp", 50., .25, 4., 1.))
        schedule = DispatchSchedule(("fcr", "fpp"), [[100, 50], [100, 50]],
                                    [100, 50], [0, 0], [200, 100], [8, 4])
        trajectory = simulate_fcr_power_step(physical, providers, power_deficit_mw=10,
            start_time_s=1., final_time_s=300., output_time_step_s=.13, maximum_solver_step_s=.1,
            provider_operating_power_mw=[100, 50], dispatch_schedule=schedule,
            measurement_times_s=np.arange(4, 301, 4), power_deficit_changes=((120.05, -7.),))
        result = settle_fpp(trajectory.time_s, trajectory.delta_frequency_hz,
                            trajectory.provider_active_power_mw, schedule)
        self.assertTrue(result["directions"].reliable.all())
        self.assertTrue(result["samples"].performance_excluded.any())
        self.assertGreater(trajectory.delta_frequency_hz[-1], 0)
        np.testing.assert_allclose(result["accounts"].groupby("direction").coefficient_mw_h.sum(), 0, atol=1e-12)

    def test_causal_deadband_droop_preserves_existing_gain_and_lag(self):
        from functional_incentives.providers import FPPProviderParameters
        provider = FPPProviderParameters("test", 50., .25, 2., 1.)
        np.testing.assert_allclose(provider.requested_response_mw(np.array([-.1, -.025, -.015, 0, .015, .025, .1])),
                                   [2, .5, 0, 0, 0, -.5, -2], atol=1e-14)
        self.assertAlmostEqual(provider.response_derivative_mw_per_s(.1, -.025), 1.6)
        self.assertEqual(provider.gain_mw_per_hz, 50.)
        zero = replace(provider, reserve_mw=0)
        self.assertEqual(zero.requested_response_mw(-.1), 0)

    def test_shared_integrator_has_no_settlement_delay_or_bilinear_payment(self):
        from functional_incentives.providers import FPPProviderParameters
        from functional_incentives.fpp import DispatchSchedule, RegulationPrices, settle_fpp
        physical = LinearFrequencyParameters(60, 4700/1100, 1100, 1000/60)
        providers = (FCRProviderParameters("fcr", 300., 2., 8.),
                     FPPProviderParameters("fpp", 50., .25, 4., 1.))
        schedule = DispatchSchedule(("fcr", "fpp"), [[100, 50], [100, 50]],
                                    [100, 50], [0, 0], [200, 100], [8, 4])
        options = dict(power_deficit_mw=10, start_time_s=1.03, final_time_s=300,
            output_time_step_s=.3, maximum_solver_step_s=.1, relative_tolerance=1e-11,
            absolute_tolerance=1e-13, provider_operating_power_mw=[100, 50],
            dispatch_schedule=schedule, measurement_times_s=np.arange(4, 301, 4))
        trajectory = simulate_fcr_power_step(physical, providers, **options)
        np.testing.assert_allclose(trajectory.provider_active_power_mw
            - schedule.reference_mw(trajectory.time_s), trajectory.provider_response_mw, atol=1e-12)
        self.assertIsNone(trajectory.fi)
        self.assertGreater(trajectory.provider_response_mw[trajectory.time_s < 4, 1].max(), 0)
        np.testing.assert_allclose(trajectory.total_fcr_response_mw, trajectory.provider_response_mw[:, 0])
        np.testing.assert_allclose(trajectory.total_fpp_response_mw, trajectory.provider_response_mw[:, 1])
        self.assertLess(trajectory.initial_rocof_hz_per_s, 0)
        self.assertTrue(np.all(abs(trajectory.provider_response_mw) <= np.array([8, 4]) + 1e-8))
        # Independent sampled quadrature approximates the live cost ledger.
        sampled_cost = np.trapezoid(trajectory.provider_response_mw[:, 1]**2 / 2, trajectory.time_s)
        self.assertAlmostEqual(trajectory.fpp_activation_cost_eur[-1, 0], sampled_cost, delta=.005)
        prices = RegulationPrices(10, 20, "AUD", "Synthetic test", "2026-10-26", "test area", "illustrative", "synthetic")
        result = settle_fpp(trajectory.time_s, trajectory.delta_frequency_hz,
                            trajectory.provider_active_power_mw, schedule, prices)
        repriced = settle_fpp(trajectory.time_s, trajectory.delta_frequency_hz,
            trajectory.provider_active_power_mw, schedule, replace(prices, raise_per_mw_h=50))
        np.testing.assert_allclose(repriced["accounts"].query("direction == 'raise'").net_settlement,
                                   5 * result["accounts"].query("direction == 'raise'").net_settlement)
        replay = simulate_fcr_power_step(physical, providers, **options)
        np.testing.assert_array_equal(replay.frequency_hz, trajectory.frequency_hz)
        another_grid = simulate_fcr_power_step(physical, providers, **{**options, "output_time_step_s": .7})
        another_settlement = settle_fpp(another_grid.time_s, another_grid.delta_frequency_hz,
                                        another_grid.provider_active_power_mw, schedule, prices)
        np.testing.assert_array_equal(result["samples"].fm_hz, another_settlement["samples"].fm_hz)
        np.testing.assert_array_equal(result["accounts"].coefficient_mw_h,
                                      another_settlement["accounts"].coefficient_mw_h)
        ramp = replace(schedule, targets_mw=np.array([[100, 50], [101, 49]]))
        ramped = simulate_fcr_power_step(physical, providers, **{**options, "dispatch_schedule": ramp})
        np.testing.assert_array_equal(ramped.provider_response_mw, trajectory.provider_response_mw)
        np.testing.assert_array_equal(ramped.frequency_hz, trajectory.frequency_hz)
        np.testing.assert_array_equal(ramped.provider_active_power_mw, trajectory.provider_active_power_mw)
        np.testing.assert_allclose(ramped.provider_active_power_mw,
                                   np.array([100, 50]) + ramped.provider_response_mw)
        ramp_result = settle_fpp(ramped.time_s, ramped.delta_frequency_hz,
                                ramped.provider_active_power_mw, ramp, prices)
        # Changing DT changes scoring, never the measured output. With no
        # dispatch-tracking control, the endpoint differences are -1 and +1 MW.
        samples, baseline = ramp_result["samples"], result["samples"]
        for identity, offset in (("fcr", 1.), ("fpp", -1.)):
            np.testing.assert_allclose(samples[f"{identity}_deviation_mw"] - baseline[f"{identity}_deviation_mw"],
                                       -offset * samples.time_s / 300, atol=1e-12)
        self.assertFalse(np.allclose(ramp_result["accounts"].coefficient_mw_h,
                                     result["accounts"].coefficient_mw_h))


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

    def test_squared_error_ledger_matches_no_fcr_integral(self):
        for damping in (0., 1000/60):
            parameters = replace(self.physical, load_damping_mw_per_hz=damping)
            result = simulate_fcr_power_step(parameters, (), power_deficit_mw=10,
                start_time_s=.15, final_time_s=8.23, output_time_step_s=2, maximum_solver_step_s=.02)
            mass = parameters.frequency_mass_mw_s_per_hz
            def squared_error(t):
                error = -10*t/mass if damping == 0 else 10/damping*np.expm1(-damping*t/mass)
                return error**2
            expected = quad(squared_error, 0, 8.23-.15, epsabs=1e-12)[0]
            self.assertAlmostEqual(result.integrated_squared_frequency_error_hz2_s[-1], expected, places=10)

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


class FIDynamicsTests(unittest.TestCase):
    def test_rocof_uses_model_equation_and_exact_off_grid_event_limit(self):
        from functional_incentives.metrics import frequency_performance_metrics, tail_frequency_metrics
        parameters = LinearFrequencyParameters(60, 5, 60, 1)
        trajectory = simulate_fcr_power_step(parameters, (), power_deficit_mw=10,
            start_time_s=.15, final_time_s=8.23, output_time_step_s=.2, maximum_solver_step_s=.02)
        expected = np.where(trajectory.time_s >= .15,
            -10/parameters.frequency_mass_mw_s_per_hz * np.exp(
                -(trajectory.time_s-.15)/parameters.frequency_mass_mw_s_per_hz), 0.)
        np.testing.assert_allclose(trajectory.rocof_hz_per_s, expected, atol=1e-10)
        metrics = frequency_performance_metrics(trajectory)
        self.assertEqual(metrics["initial_rocof_hz_per_s"], -1.)
        self.assertEqual(metrics["rocof_max_abs_hz_per_s"], 1.)
        self.assertLess(np.max(abs(trajectory.rocof_hz_per_s)), 1.)
        self.assertEqual(tail_frequency_metrics(trajectory, .15)["tail_stationarity_pass"], 0.)
        # A small terminal error must not hide oscillations in the final window.
        oscillatory = replace(trajectory, frequency_hz=60 + .01*np.sin(trajectory.time_s*20),
                              rocof_hz_per_s=.2*np.cos(trajectory.time_s*20))
        self.assertGreater(tail_frequency_metrics(oscillatory, .15)["tail_frequency_range_hz"], .01)
        self.assertEqual(tail_frequency_metrics(oscillatory, .15)["tail_stationarity_pass"], 0.)
        zero = simulate_fcr_power_step(parameters, (), power_deficit_mw=0,
            start_time_s=1, final_time_s=8, output_time_step_s=.1)
        self.assertEqual(frequency_performance_metrics(zero)["rocof_max_abs_hz_per_s"], 0.)
        self.assertEqual(tail_frequency_metrics(zero, 1)["tail_stationarity_pass"], 1.)

    def setUp(self):
        self.physical = LinearFrequencyParameters(60, 5, 60, 1)
        self.fi = FIProviderParameters("fi", .5, 4, 1000, 1)
        self.event = dict(power_deficit_mw=1, start_time_s=.15, final_time_s=8.23)

    def simulate(self, providers=None, **kwargs):
        return simulate_fcr_power_step(self.physical, [self.fi] if providers is None else providers,
            **self.event, fi_decision_interval_s=1, maximum_solver_step_s=.02, **kwargs)

    def test_decision_clock_lag_and_actual_positive_negative_settlements(self):
        trajectory = self.simulate(output_time_step_s=.17, provider_operating_power_mw=[10])
        fi = trajectory.fi
        records = fi.intervals
        self.assertEqual([r["start_time_s"] for r in records], list(range(9)))
        self.assertEqual(records[-1]["end_time_s"], 8.23)
        self.assertEqual(records[0]["target_mw"], 0)  # event does not trigger a new decision
        self.assertGreater(records[1]["target_mw"], 0)
        self.assertLess(records[1]["final_response_mw"], records[1]["target_mw"])
        self.assertTrue(any(r["gross_penalties_eur"] > 0 for r in records))
        self.assertTrue(any(r["gross_rewards_eur"] > 0 for r in records))
        for record in records:
            duration = record["end_time_s"] - record["start_time_s"]
            def response(t):
                return (record["initial_response_mw"]*np.exp(-t/.5)
                        + record["target_mw"]*(-np.expm1(-t/.5)))
            def payment(t):
                return -1000*record["sampled_frequency_error_hz"]*response(t)
            reward = quad(lambda t: max(payment(t), 0), 0, duration, epsabs=1e-8)[0]
            charge = quad(lambda t: max(-payment(t), 0), 0, duration, epsabs=1e-8)[0]
            cost = quad(lambda t: .5*response(t)**2, 0, duration, epsabs=1e-10)[0]
            self.assertAlmostEqual(record["gross_rewards_eur"], reward, places=5)
            self.assertAlmostEqual(record["gross_penalties_eur"], charge, places=5)
            self.assertAlmostEqual(record["activation_cost_eur"], cost, places=7)
            self.assertAlmostEqual(record["net_settlement_eur"], record["predicted_settlement_eur"], places=6)
            self.assertAlmostEqual(record["profit_eur"], reward-charge-cost, places=5)
        np.testing.assert_allclose(fi.net_settlement_eur, fi.predicted_settlement_eur, atol=1e-6)
        np.testing.assert_allclose(fi.profit_eur, fi.net_settlement_eur-fi.activation_cost_eur)
        self.assertAlmostEqual(fi.net_settlement_eur[-1, 0], sum(r["net_settlement_eur"] for r in records))
        self.assertTrue(np.all(np.abs(trajectory.provider_response_mw) <= 4+1e-8))
        np.testing.assert_allclose(trajectory.provider_active_power_mw, 10+trajectory.provider_response_mw)

    def test_output_clock_is_independent_and_final_partial_interval_is_settled(self):
        fine = self.simulate(output_time_step_s=.05)
        coarse = self.simulate(output_time_step_s=2)
        self.assertEqual(fine.fi.intervals, coarse.fi.intervals)
        self.assertEqual(fine.fi.profit_eur[-1, 0], coarse.fi.profit_eur[-1, 0])
        self.assertEqual(fine.integrated_squared_frequency_error_hz2_s[-1],
                         coarse.integrated_squared_frequency_error_hz2_s[-1])
        np.testing.assert_array_equal(fine.provider_response_mw[-1], coarse.provider_response_mw[-1])
        self.assertEqual(len(coarse.fi.intervals), 9)
        self.assertNotEqual(coarse.fi.intervals[-1]["net_settlement_eur"], 0)

    def test_multiple_fi_providers_share_samples_and_coexist_with_fcr(self):
        providers = [self.fi, FCRProviderParameters("fcr", 20, .2, 2), replace(self.fi, provider_id="fi2")]
        trajectory = self.simulate(providers, output_time_step_s=.1)
        np.testing.assert_array_equal(trajectory.provider_response_mw[:, 0], trajectory.provider_response_mw[:, 2])
        np.testing.assert_allclose(trajectory.total_fi_response_mw, 2*trajectory.provider_response_mw[:, 0])
        np.testing.assert_allclose(trajectory.total_fcr_response_mw, trajectory.provider_response_mw[:, 1], atol=1e-14)
        np.testing.assert_array_equal(trajectory.fi.sampled_frequency_error_hz[:, 0], trajectory.fi.sampled_frequency_error_hz[:, 1])
        self.assertEqual(self.fi.gamma_eur_per_hz_mw_s, 1000)

    def test_zero_gamma_zero_capacity_and_zero_event_remain_quiescent(self):
        for p in (replace(self.fi, gamma_eur_per_hz_mw_s=0), replace(self.fi, reserve_mw=0)):
            result = self.simulate([p], output_time_step_s=.2)
            np.testing.assert_array_equal(result.provider_response_mw, 0)
            np.testing.assert_array_equal(result.fi.profit_eur, 0)
        self.event["power_deficit_mw"] = 0
        result = self.simulate(output_time_step_s=.2)
        np.testing.assert_array_equal(result.frequency_hz, 60)
        np.testing.assert_array_equal(result.fi.profit_eur, 0)

    def test_decision_interval_required_and_positive(self):
        for interval in (None, 0, -1, np.inf, np.nan):
            with self.assertRaisesRegex(ValueError, "fi_decision_interval_s"):
                simulate_fcr_power_step(self.physical, [self.fi], **self.event,
                    output_time_step_s=.1, fi_decision_interval_s=interval)


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
