import unittest
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
