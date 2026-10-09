import unittest
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm, truncnorm

from functional_incentives.availability import GaussianReserve
from functional_incentives.decision import choose_capacity_offer
from functional_incentives.providers import QuadraticCommitmentCost
from functional_incentives.providers import symmetric_reserve_capacity_mw


class GaussianReserveTests(unittest.TestCase):
    def test_expected_penalty_matches_partial_normal_moments(self):
        mu, sigma, capacity = 2.0, 1.5, 5.0
        a, b = -mu / sigma, (capacity - mu) / sigma
        for mode in ("truncated", "clipped"):
            law = GaussianReserve(mu, sigma**2, capacity, mode)
            for q in (0, 0.1, 1, 2, 4.9, 5):
                z = (q - mu) / sigma
                if mode == "truncated":
                    reference = ((q - mu) * (norm.cdf(z) - norm.cdf(a))
                                 + sigma * (norm.pdf(z) - norm.pdf(a)))
                    reference /= norm.cdf(b) - norm.cdf(a)
                else:
                    def loss(x):
                        v = (x - mu) / sigma
                        return (x - mu) * norm.cdf(v) + sigma * norm.pdf(v)
                    reference = loss(q) - loss(0)
                self.assertAlmostEqual(law.expected_shortfall_mw(q), reference, places=10)

    def test_sampling_is_reproducible_and_has_the_expected_shortfall(self):
        for mode in ("truncated", "clipped"):
            law = GaussianReserve(1, 4, 4, mode)
            r1, r2 = np.random.default_rng(87), np.random.default_rng(87)
            self.assertEqual([law.sample_mw(r1) for _ in range(10)],
                             [law.sample_mw(r2) for _ in range(10)])
            samples = np.array([law.sample_mw(r1) for _ in range(4000)])
            self.assertTrue(np.all((samples >= 0) & (samples <= 4)))
            losses = np.maximum(0, 3 - samples)
            self.assertLess(abs(losses.mean() - law.expected_shortfall_mw(3)),
                            5 * losses.std() / np.sqrt(len(losses)))
            if mode == "truncated":
                self.assertTrue(np.all((samples > 0) & (samples < 4)))
            else:
                self.assertGreater(np.count_nonzero(samples == 0), 0)

    def test_zero_variance_and_zero_capacity(self):
        for mode in ("truncated", "clipped"):
            law = GaussianReserve(2, 0, 4, mode)
            self.assertEqual(law.expected_shortfall_mw(1), 0)
            self.assertEqual(law.expected_shortfall_mw(3), 1)
            self.assertEqual(law.sample_mw(np.random.default_rng(1)), 2)
            zero = GaussianReserve(0, 0, 0, mode)
            self.assertEqual(zero.expected_shortfall_mw(0), 0)
        with self.assertRaises(ValueError):
            GaussianReserve(-1, 0, 4, "truncated")
        self.assertEqual(GaussianReserve(-1, 0, 4, "clipped").expected_shortfall_mw(2), 2)

    def test_symmetric_distribution_and_narrow_or_tail_distributions(self):
        self.assertAlmostEqual(
            GaussianReserve(5, 9, 10, "truncated").expected_shortfall_mw(10), 5,
            places=10,
        )
        self.assertAlmostEqual(
            GaussianReserve(3, 1e-12, 10, "truncated").expected_shortfall_mw(5), 2,
            places=8,
        )
        for mu in (-10, 20):
            law = GaussianReserve(mu, 0.04, 5, "truncated")
            self.assertTrue(0 <= law.expected_shortfall_mw(2) <= 2)
            self.assertTrue(0 <= law.sample_mw(np.random.default_rng(1)) <= 5)

    def test_invalid_parameters_and_commitments(self):
        for args in [(0, -1, 5, "truncated"), (0, 1, -1, "clipped"),
                     (0, 1, 5, "raw"), (np.nan, 1, 5, "truncated"),
                     (0, np.inf, 5, "truncated")]:
            with self.assertRaises(ValueError):
                GaussianReserve(*args)
        law = GaussianReserve(1, 1, 5, "truncated")
        for q in (-1, 6, np.nan):
            with self.assertRaises(ValueError):
                law.expected_shortfall_mw(q)


class OfferDecisionTests(unittest.TestCase):
    def decision(self, law, cost=QuadraticCommitmentCost(5, 2), **kwargs):
        parameters = dict(
            availability=law, cost=cost, penalty_eur_per_missing_mw_block=20,
            minimum_bid_price_eur_per_mw_block=5,
            maximum_bid_price_eur_per_mw_block=10,
            maximum_offer_mw=law.capacity_mw,
        )
        return choose_capacity_offer("a", **{**parameters, **kwargs})

    def test_hand_computed_optimum_and_price_cap_consequence(self):
        decision = self.decision(GaussianReserve(2, 0, 5, "truncated"))
        self.assertEqual(decision.offer.quantity_mw, 2)
        self.assertEqual(decision.offer.price_eur_per_mw_block, 10)
        self.assertEqual(decision.expected_reward_eur, 6)
        self.assertEqual(decision.expected_penalty_eur, 0)

    def test_gaussian_expected_reward_matches_pdf_integration(self):
        for mode in ("truncated", "clipped"):
            law = GaussianReserve(2, 1, 5, mode)
            decision = self.decision(law, minimum_bid_mw=0.1)
            distribution = truncnorm(-2, 3, loc=2, scale=1) if mode == "truncated" else norm(2, 1)
            # Independent first-order condition for the continuous concave reward.
            optimum = brentq(lambda q: 5 - 2*q - 20*distribution.cdf(q), .1, 5)
            penalty = 20 * distribution.expect(
                lambda a: max(optimum - np.clip(a, 0, 5), 0))
            self.assertAlmostEqual(decision.offer.quantity_mw, optimum, places=6)
            self.assertAlmostEqual(decision.expected_reward_eur,
                                   5*optimum - optimum**2 - penalty, places=6)

    def test_nonparticipation_and_ties(self):
        decision = self.decision(GaussianReserve(5, 0, 5, "truncated"),
                                 cost=QuadraticCommitmentCost(10, 0))
        self.assertEqual(decision.offer.quantity_mw, 0)
        self.assertEqual(decision.offer.price_eur_per_mw_block, 5)
        self.assertEqual(decision.expected_reward_eur, 0)
        loss = self.decision(GaussianReserve(5, 0, 5, "truncated"),
                             cost=QuadraticCommitmentCost(11, 0))
        self.assertEqual(loss.offer.quantity_mw, 0)

    def test_continuous_interior_and_exact_endpoints(self):
        law = GaussianReserve(5, 0, 5, "truncated")
        interior = self.decision(law, penalty_eur_per_missing_mw_block=0)
        self.assertAlmostEqual(interior.offer.quantity_mw, 2.5, places=6)
        self.assertAlmostEqual(interior.expected_reward_eur, 6.25, places=10)
        capped = self.decision(law, maximum_offer_mw=2.3)
        self.assertEqual(capped.offer.quantity_mw, 2.3)
        lower = self.decision(law, minimum_bid_mw=3)
        self.assertEqual(lower.offer.quantity_mw, 3)
        single_price = self.decision(law, minimum_bid_price_eur_per_mw_block=10)
        self.assertEqual(single_price.offer.price_eur_per_mw_block, 10)

    def test_minimum_bid_singleton_empty_interval_and_zero_capacity(self):
        law = GaussianReserve(2, 0, 4, "truncated")
        for cap, expected in ((1, 1), (.99, 0), (0, 0)):
            decision = self.decision(law, maximum_offer_mw=cap)
            self.assertEqual(decision.offer.quantity_mw, expected)
        zero = self.decision(GaussianReserve(0, 0, 0, "truncated"))
        self.assertEqual(zero.offer.quantity_mw, 0)

    def test_uncertain_provider_opt_out_depends_on_minimum_bid(self):
        args = dict(cost=QuadraticCommitmentCost(5, 1),
                    penalty_eur_per_missing_mw_block=200,
                    maximum_bid_price_eur_per_mw_block=20)
        law = GaussianReserve(2, 4, 4, "truncated")
        self.assertEqual(self.decision(law, **args).offer.quantity_mw, 0)
        smaller = self.decision(law, minimum_bid_mw=.1, **args)
        self.assertTrue(.1 < smaller.offer.quantity_mw < 1)
        self.assertGreater(smaller.expected_reward_eur, 0)
        # Clipping can add an atom at zero: even arbitrarily small bids can lose.
        clipped = self.decision(GaussianReserve(-5, 1, 4, "clipped"),
                                minimum_bid_mw=.001, **args)
        self.assertEqual(clipped.offer.quantity_mw, 0)

    def test_invalid_intervals_and_solver_failure_are_reported(self):
        law = GaussianReserve(2, 1, 5, "truncated")
        for override in ({"minimum_bid_price_eur_per_mw_block": 11},
                         {"minimum_bid_price_eur_per_mw_block": -1},
                         {"maximum_bid_price_eur_per_mw_block": np.inf},
                         {"maximum_offer_mw": 6}, {"maximum_offer_mw": np.nan},
                         {"maximum_offer_mw": -1}, {"minimum_bid_mw": 0},
                         {"minimum_bid_mw": np.nan}, {"quantity_tolerance_mw": 0},
                         {"penalty_eur_per_missing_mw_block": -1}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                self.decision(law, **override)
        with patch("functional_incentives.decision.minimize_scalar",
                   return_value=SimpleNamespace(success=False)):
            with self.assertRaisesRegex(RuntimeError, "did not converge"):
                self.decision(law)

    def test_capability_uses_both_declared_margins(self):
        self.assertEqual(symmetric_reserve_capacity_mw(210, 205, 220), 5)
        with self.assertRaises(ValueError):
            symmetric_reserve_capacity_mw(210, 0, 200)


if __name__ == "__main__":
    unittest.main()
