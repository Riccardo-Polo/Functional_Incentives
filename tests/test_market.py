import unittest

import numpy as np

from functional_incentives.fpp import (
    DispatchSchedule, RegulationPrices, frequency_measure, directional_reliability,
    same_sign_contribution_factors, settle_fpp,
)


class FPPSettlementTests(unittest.TestCase):
    def schedule(self, targets=None):
        return DispatchSchedule(("a", "b"), [[50, 50], [50, 50]] if targets is None else targets,
                                [50, 50], [0, 0], [100, 100], [10, 10])

    def prices(self, raise_price=120, lower_price=60):
        return RegulationPrices(raise_price, lower_price, "AUD", "Synthetic hand calculation",
                                "2026-10-26", "Single test area", "illustrative", "synthetic")

    def settle(self, error=-.02, deviation=(2., -1.), schedule=None, prices=None):
        schedule = self.schedule() if schedule is None else schedule
        times = np.arange(0, schedule.final_time_s + 1, 4)
        errors = np.full(len(times), error) if np.isscalar(error) else np.asarray(error)
        errors[0] = 0
        measured = schedule.reference_mw(times) + np.asarray(deviation)
        return settle_fpp(times, errors, measured, schedule, prices)

    def test_reference_endpoints_balanced_ramp_and_synthetic_deviations(self):
        schedule = self.schedule([[50, 50], [51, 49], [49, 51]])
        values = schedule.reference_mw([0, 4, 150, 300, 304, 600])
        np.testing.assert_allclose(values[:, 0], [50, 50+1/75, 50.5, 51, 51-2/75, 49])
        np.testing.assert_allclose(values.sum(axis=1), 100)
        result = self.settle(schedule=schedule)
        np.testing.assert_allclose(result["samples"].a_deviation_mw, 2)
        np.testing.assert_allclose(result["samples"].b_deviation_mw, -1)
        samples = result["samples"]
        self.assertEqual(len(samples), 150)
        self.assertEqual(samples[samples.interval == 1].time_s.iloc[-1], 300)
        self.assertEqual(samples[samples.interval == 2].time_s.iloc[0], 304)
        self.assertNotIn(0, samples.time_s.tolist())

    def test_schedule_rejects_unbalance_headroom_unsolved_initial_and_categories(self):
        for targets, message in (([[50, 50], [51, 50]], "unbalanced"),
                                 ([[50, 50], [95, 5]], "headroom"),
                                 ([[51, 49], [51, 49]], "solved")):
            with self.assertRaisesRegex(ValueError, message):
                self.schedule(targets)
        with self.assertRaisesRegex(ValueError, "category"):
            DispatchSchedule(("a",), [[50], [50]], [50], [0], [100], [10], "non_scheduled")
        with self.assertRaisesRegex(ValueError, "limits"):
            self.schedule().check_total_power([[101, 50]])

    def test_independent_hand_calculated_raise_and_lower_payments(self):
        # Raise: P = W*(2,-1,-1), CF=(1,-1/2,-1/2), RCR=2 MW.
        # At synthetic 120 AUD/(MW h), 300 s gives (20,-10,-10) AUD.
        result = self.settle(prices=self.prices())
        raised = result["accounts"].query("direction == 'raise'")
        np.testing.assert_allclose(raised.contribution_factor, [1, -.5, -.5])
        np.testing.assert_allclose(raised.rcr_mw, 2)
        np.testing.assert_allclose(raised.coefficient_mw_h, [1/6, -1/12, -1/12])
        np.testing.assert_allclose(raised.net_settlement, [20, -10, -10])
        self.assertAlmostEqual(raised.gross_reward.sum(), raised.charge_magnitude.sum())
        self.assertAlmostEqual(raised.performance_mw_hz.sum(), 0)
        # Lower: P = |W|*(-1,3,-2), CF=(-1/3,1,-2/3), RCR=3 MW.
        lower = self.settle(error=.02, deviation=(1., -3.), prices=self.prices())["accounts"].query("direction == 'lower'")
        np.testing.assert_allclose(lower.contribution_factor, [-1/3, 1, -2/3])
        np.testing.assert_allclose(lower.net_settlement, [-5, 15, -10])
        self.assertAlmostEqual(lower.net_settlement.sum(), 0)

    def test_filter_official_table15_initialization_and_sign_memory(self):
        # Calculation Guide 9 Dec 2024, Appendix A1.1 Table 15, pp.44--45.
        # First history sample (12:03:04) is assigned FM=0, despite FD=0.008.
        fd = [.008, .005, .0415, .038, .02075, .01525, .01925, .02225,
              .03025, .03375, .03375, .03425, .02375, .008, .004, -.004,
              .01075, .01475, -.00275, -.01875, -.02075, -.0075, .00025,
              -.007, -.005, -.00425, .00775, .009, .0035, -.00675, -.01475]
        fm = frequency_measure(np.pad(fd, (0, 105-len(fd))))
        self.assertEqual(fm[0], 0)
        np.testing.assert_allclose(fm[[1, 2, 19, 20, 29, 30]],
            [-.0011111, -.0100864, -.0034308, .001942683, .000297088, .003508846], atol=5e-8)
        self.assertLess(fm[19], 0)  # actual frequency is already below nominal
        self.assertGreater(fm[20], 0)

    def test_actual_preceding_history_is_recomputed_for_second_interval(self):
        schedule = self.schedule([[50, 50], [50, 50], [50, 50]])
        fd = np.r_[0., np.full(75, -.04), np.full(75, .02)]
        result = self.settle(error=fd, schedule=schedule)
        history = result["filter_history"].query("interval == 2")
        self.assertEqual(history.time_s.iloc[0], 184)
        self.assertEqual(history.fm_hz.iloc[0], 0)
        self.assertTrue((history.delta_frequency_hz.iloc[:30] == -.04).all())
        expected = .04 * (1 - (7/9)**29) * 7/9 - .02 * 2/9
        self.assertAlmostEqual(result["samples"].query("interval == 2").fm_hz.iloc[0], expected)

    def test_reliability_uses_fm_strict_boundaries_and_counts_before_exclusion(self):
        for direction, sign in (("raise", 1), ("lower", -1)):
            fm = np.r_[np.full(6, sign * .02), np.zeros(69)]
            self.assertFalse(directional_reliability(fm, direction)[0])
            fm[6] = sign * .01
            self.assertTrue(directional_reliability(fm, direction)[0])
            fm[:7] = sign * .01
            self.assertFalse(directional_reliability(fm, direction)[0])
            fm[0] = sign * np.nextafter(.01, 1.)
            self.assertTrue(directional_reliability(fm, direction)[0])
        # Large instantaneous errors alone do not make a direction reliable.
        result = self.settle(error=np.r_[0., -.02, np.zeros(74)])
        self.assertFalse(result["directions"].query("direction == 'raise'").reliable.iloc[0])

    def test_misalignment_masks_performance_but_not_rcr_or_reliability(self):
        errors = np.r_[0., np.full(75, -.04)]
        errors[20:23] = [.015, .0150001, .02]
        deviations = np.tile([2., -1.], (76, 1))
        deviations[22] = [9., -1.]
        result = self.settle(error=errors, deviation=deviations)
        samples = result["samples"]
        self.assertFalse(samples.performance_excluded.iloc[19])
        self.assertTrue(samples.performance_excluded.iloc[20])
        self.assertTrue(samples.performance_excluded.iloc[21])
        raised = result["accounts"].query("direction == 'raise'")
        self.assertEqual(raised.rcr_mw.iloc[0], 9)
        expected = np.sum(np.where(samples.performance_excluded, 0, np.maximum(samples.fm_hz, 0))
                          * samples.a_deviation_mw)
        self.assertAlmostEqual(raised.performance_mw_hz.iloc[0], expected)

    def test_null_zero_denominators_and_residual_are_explicit(self):
        invalid = self.settle(error=0., prices=self.prices())["accounts"]
        self.assertTrue(invalid.performance_is_null.all())
        self.assertTrue(invalid.performance_mw_hz.isna().all())
        self.assertTrue(invalid.contribution_factor.isna().all())
        self.assertTrue((invalid.rcr_mw == 0).all())
        self.assertTrue((invalid.net_settlement == 0).all())
        zero = self.settle(deviation=(0., 0.))["accounts"].query("direction == 'raise'")
        self.assertFalse(zero.performance_is_null.any())
        self.assertTrue((zero.contribution_factor == 0).all())
        self.assertTrue(zero.net_settlement.isna().all())  # absent prices are not zero prices
        np.testing.assert_array_equal(same_sign_contribution_factors([0., 0.]), [0., 0.])
        with self.assertRaisesRegex(ValueError, "historical"):
            same_sign_contribution_factors([np.nan, 1.])
        residual = self.settle()["samples"].residual_deviation_mw
        np.testing.assert_array_equal(residual, -1.)  # accounting residual, not disturbance

    def test_missing_data_incomplete_intervals_and_misaligned_clocks_rejected(self):
        schedule = self.schedule()
        for times, error, power, message in (
            (np.arange(0, 301, 4), np.r_[0., np.nan, np.zeros(74)], np.full((76, 2), 50), "frequency"),
            (np.arange(0, 301, 4), np.zeros(76), np.full((76, 2), np.nan), "metered"),
            (np.arange(0, 297, 4), np.zeros(75), np.full((75, 2), 50), "complete"),
            (np.arange(0, 301, 3), np.zeros(101), np.full((101, 2), 50), "synchronized")):
            with self.assertRaisesRegex(ValueError, message):
                settle_fpp(times, error, power, schedule)

    def test_price_units_provenance_and_repricing(self):
        from dataclasses import replace
        price = self.prices()
        for fields in ({"units": "EUR/MW/block"}, {"source": ""}, {"date": "unknown"},
                       {"price_basis": "regional_market_proxy", "evidence": "observed"}):
            with self.assertRaises(ValueError):
                replace(price, **fields)
        a = self.settle(prices=price)
        b = self.settle(prices=replace(price, raise_per_mw_h=240))
        np.testing.assert_array_equal(a["samples"].fm_hz, b["samples"].fm_hz)
        np.testing.assert_array_equal(a["accounts"].coefficient_mw_h, b["accounts"].coefficient_mw_h)
        np.testing.assert_allclose(2*a["accounts"].net_settlement, b["accounts"].net_settlement)
import numpy as np
from scipy.optimize import linprog

from functional_incentives.market import (
    FCRCapacityOffer, FCRCapacityAward, clear_fcr_market,
    fixed_capacity_allocation, settle_reserve_block,
)
from functional_incentives.providers import QuadraticCommitmentCost


class FCRMarketTests(unittest.TestCase):
    def test_worked_example_partial_award_and_payments(self):
        offers = tuple(
            FCRCapacityOffer(str(i), price, quantity)
            for i, (price, quantity) in enumerate(zip([10, 15, 20, 30], [1, 2, 3, 4]))
        )
        result = clear_fcr_market(offers, 5)
        self.assertTrue(result.feasible)
        self.assertEqual(result.clearing_price_eur_per_mw_block, 20)
        self.assertEqual([a.quantity_mw for a in result.awards], [1, 2, 2, 0])
        self.assertEqual([a.capacity_payment_eur for a in result.awards], [20, 40, 40, 0])
        self.assertEqual(result.total_payment_eur, 100)

    def test_ties_are_deterministic_and_zero_prices_do_not_overprocure(self):
        offers = [FCRCapacityOffer("b", 0, 3), FCRCapacityOffer("a", 0, 3)]
        for ordering in (offers, offers[::-1]):
            result = clear_fcr_market(ordering, 4)
            self.assertEqual({a.provider_id: a.quantity_mw for a in result.awards},
                             {"a": 3, "b": 1})
            self.assertEqual(result.total_payment_eur, 0)

    def test_zero_exact_capacity_and_short_supply(self):
        offers = [FCRCapacityOffer("a", 2, 1), FCRCapacityOffer("b", 3, 2)]
        self.assertEqual(clear_fcr_market([], 0).clearing_price_eur_per_mw_block, 0)
        zero = clear_fcr_market(offers, 0)
        self.assertEqual([a.quantity_mw for a in zero.awards], [0, 0])
        exact = clear_fcr_market(offers, 3)
        self.assertEqual([a.quantity_mw for a in exact.awards], [1, 2])
        self.assertEqual(exact.clearing_price_eur_per_mw_block, 3)
        short = clear_fcr_market(offers, 4)
        self.assertFalse(short.feasible)
        self.assertIsNone(short.clearing_price_eur_per_mw_block)
        self.assertEqual(short.shortage_mw, 1)
        self.assertEqual(sum(a.quantity_mw for a in short.awards), 0)
        # A roundoff residual must not accept a costly next offer and set price.
        boundary = clear_fcr_market([
            FCRCapacityOffer("a", 1, 0.2), FCRCapacityOffer("b", 2, 0.4),
            FCRCapacityOffer("c", 100, 1),
        ], 0.2 + 0.4)
        self.assertEqual(boundary.awards[-1].quantity_mw, 0)
        self.assertEqual(boundary.clearing_price_eur_per_mw_block, 2)

    def test_merit_order_matches_independent_linear_program(self):
        rng = np.random.default_rng(4)
        for _ in range(20):
            prices = rng.uniform(0, 100, 6)
            quantities = rng.uniform(0.1, 10, 6)
            requirement = float(rng.uniform(0, quantities.sum()))
            offers = [FCRCapacityOffer(str(i), c, q)
                      for i, (c, q) in enumerate(zip(prices, quantities))]
            result = clear_fcr_market(offers, requirement)
            lp = linprog(prices, A_ub=[-np.ones(6)], b_ub=[-requirement],
                         bounds=list(zip(np.zeros(6), quantities)), method="highs")
            self.assertTrue(lp.success)
            awards = np.array([a.quantity_mw for a in result.awards])
            self.assertAlmostEqual(float(prices @ awards), lp.fun, places=8)
            self.assertAlmostEqual(float(awards.sum()), requirement, places=10)
            self.assertTrue(np.all(awards <= quantities))

    def test_invalid_inputs(self):
        for value in (-1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                clear_fcr_market([], value)
            with self.assertRaises(ValueError):
                FCRCapacityOffer("a", value, 1)
            with self.assertRaises(ValueError):
                FCRCapacityOffer("a", 1, value)
        with self.assertRaises(ValueError):
            clear_fcr_market([FCRCapacityOffer("a", 1, 1)] * 2, 1)


class AllocationAndSettlementTests(unittest.TestCase):
    def test_fixed_awards_are_explicit_and_bounded(self):
        offers = [FCRCapacityOffer("a", 10, 2), FCRCapacityOffer("b", 20, 2)]
        result = fixed_capacity_allocation(offers, 3, {"a": 1, "b": 2}, 12)
        self.assertEqual([a.quantity_mw for a in result.awards], [1, 2])
        self.assertEqual(result.total_payment_eur, 36)
        for awards, price in [({"a": 3, "b": 0}, 12), ({"a": 1}, 12),
                              ({"a": 1, "b": 1}, 12), ({"a": 1, "b": 2}, -1)]:
            with self.assertRaises(ValueError):
                fixed_capacity_allocation(offers, 3, awards, price)

    def test_settlement_uses_award_and_available_capacity(self):
        cost = QuadraticCommitmentCost(2, 1)
        result = settle_reserve_block(FCRCapacityAward("a", 2, 40), available_mw=1,
                                     cost=cost, penalty_eur_per_missing_mw_block=5)
        self.assertEqual((result.effective_reserve_mw, result.capacity_shortfall_mw), (1, 1))
        self.assertEqual((result.commitment_cost_eur, result.penalty_eur, result.profit_eur), (6, 5, 29))
        rejected = settle_reserve_block(FCRCapacityAward("a", 0, 0), available_mw=3,
                                       cost=cost, penalty_eur_per_missing_mw_block=5)
        self.assertEqual(rejected.profit_eur, 0)
        with self.assertRaises(ValueError):
            settle_reserve_block(FCRCapacityAward("a", 2, 40), available_mw=-1,
                                 cost=cost, penalty_eur_per_missing_mw_block=5)
