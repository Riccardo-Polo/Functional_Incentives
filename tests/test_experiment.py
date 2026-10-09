import io
import json
import tempfile
import tomllib
import unittest
from contextlib import redirect_stdout
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

from functional_incentives import experiment
from functional_incentives.availability import GaussianReserve
from functional_incentives.providers import BlockProvider, QuadraticCommitmentCost
from functional_incentives.market import FCRCapacityOffer
from functional_incentives.simulation import prepare_fcr_block


def provider(identity, price):
    return BlockProvider(
        provider_id=identity, generator_id=identity,
        operating_power_mw=100, minimum_power_mw=90, maximum_power_mw=110,
        power_base_mw=200, time_constant_s=1,
        availability=GaussianReserve(3, 1, 4, "truncated"),
        cost=QuadraticCommitmentCost(1, 0),
        penalty_eur_per_missing_mw_block=1,
        minimum_bid_price_eur_per_mw_block=price,
        maximum_bid_price_eur_per_mw_block=price,
    )


class FCRBlockTests(unittest.TestCase):
    def test_awards_and_draws_keep_identity_and_decisions_precede_randomness(self):
        providers = (provider("b", 20), provider("a", 10))
        args = dict(requirement_mw=5, nominal_frequency_hz=60)
        first = prepare_fcr_block(providers, rng=np.random.default_rng(21), **args)
        reordered = prepare_fcr_block(providers[::-1], rng=np.random.default_rng(21), **args)
        other_seed = prepare_fcr_block(providers, rng=np.random.default_rng(22), **args)
        self.assertEqual(first, reordered)
        self.assertEqual(first.decisions, other_seed.decisions)
        self.assertEqual(first.market, other_seed.market)
        self.assertNotEqual(first.settlements, other_seed.settlements)
        self.assertEqual([s.awarded_mw for s in first.settlements], [4, 1])
        self.assertEqual(first.market.clearing_price_eur_per_mw_block, 20)
        manual_rng = np.random.default_rng(21)
        for p, s, response in zip(first.providers, first.settlements, first.response_parameters):
            self.assertEqual(s.available_mw, p.availability.sample_mw(manual_rng))
            self.assertEqual(response.reserve_mw, min(s.awarded_mw, s.available_mw))
            self.assertAlmostEqual(response.gain_mw_per_hz, 200 / 3)

    def test_infeasible_market_does_not_draw_or_start_delivery(self):
        rng = np.random.default_rng(22)
        state = rng.bit_generator.state
        with self.assertRaisesRegex(ValueError, "Insufficient offered reserve"):
            prepare_fcr_block((provider("a", 10),), requirement_mw=5,
                              nominal_frequency_hz=60, rng=rng)
        self.assertEqual(state, rng.bit_generator.state)

    def test_zero_requirement_and_full_or_zero_availability(self):
        for available in (0, 4):
            p = replace(provider("a", 10), availability=GaussianReserve(available, 0, 4, "truncated"))
            for requirement in (0, 4):
                block = prepare_fcr_block((p,), requirement_mw=requirement,
                                          nominal_frequency_hz=60, rng=np.random.default_rng(1))
                self.assertEqual(block.response_parameters[0].reserve_mw, min(requirement, available))
                self.assertEqual(block.settlements[0].capacity_shortfall_mw,
                                 max(0, requirement - available))

    def test_inconsistent_capability_and_duplicate_identities_fail(self):
        p = provider("a", 10)
        with self.assertRaisesRegex(ValueError, "headroom"):
            replace(p, minimum_power_mw=99)
        with self.assertRaisesRegex(ValueError, "unique"):
            prepare_fcr_block((p, p), requirement_mw=1,
                              nominal_frequency_hz=60, rng=np.random.default_rng(1))

    def test_safety_margin_changes_bids_without_changing_draws_or_gains(self):
        p = provider("a", 10)
        args = dict(requirement_mw=3, nominal_frequency_hz=60)
        base = prepare_fcr_block((p,), rng=np.random.default_rng(1), **args)
        limited = replace(p, reserve_safety_margin_fraction=.1)
        block = prepare_fcr_block((limited,), rng=np.random.default_rng(1), **args)
        self.assertEqual(limited.maximum_offer_mw, 3.6)
        self.assertEqual(block.decisions[0].offer.quantity_mw, 3.6)
        self.assertEqual(base.settlements, block.settlements)
        self.assertEqual(base.response_parameters, block.response_parameters)
        for margin in (1, .8):
            closed = prepare_fcr_block((replace(p, reserve_safety_margin_fraction=margin),),
                                       requirement_mw=0, nominal_frequency_hz=60,
                                       rng=np.random.default_rng(1))
            self.assertEqual(closed.decisions[0].offer.quantity_mw, 0)
        for margin in (-.1, 1.1, np.nan, np.inf):
            with self.assertRaises(ValueError):
                replace(p, reserve_safety_margin_fraction=margin)
        with self.assertRaisesRegex(ValueError, "headroom"):
            replace(p, maximum_power_mw=102, reserve_safety_margin_fraction=.75)

    def test_fixed_offers_obey_minimum_and_margin_but_awards_can_be_smaller(self):
        p = replace(provider("a", 10), reserve_safety_margin_fraction=.1)
        args = dict(requirement_mw=.5, nominal_frequency_hz=60)
        for quantity in (.5, 3.7):
            rng = np.random.default_rng(1)
            state = deepcopy(rng.bit_generator.state)
            with self.assertRaisesRegex(ValueError, "offer must be zero"):
                prepare_fcr_block((p,), rng=rng,
                                  fixed_offers=[FCRCapacityOffer("a", 2, quantity)], **args)
            self.assertEqual(state, rng.bit_generator.state)
        block = prepare_fcr_block((p,), rng=np.random.default_rng(1),
                                  fixed_offers=[FCRCapacityOffer("a", 2, 1)], **args)
        self.assertEqual(block.market.awards[0].quantity_mw, .5)


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.config = tomllib.loads(experiment.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        self.point = SimpleNamespace(
            generator_ids=("conventional_1", "conventional_2", "renewable_reliable",
                           "renewable_uncertain", "conventional_3"),
            generator_active_power_mw=(260, 310, 90, 90, 250.9060955862529),
            generator_ratings_mva=(300, 400, 150, 150, 400),
            nominal_frequency_hz=60, total_load_mw=1000,
            equivalent_inertia_s=4700/1100, total_synchronous_rating_mva=1100,
        )

    def prepare(self, config):
        return prepare_fcr_block(
            experiment.load_block_providers(config, self.point),
            requirement_mw=config["market"]["requirement_mw"],
            minimum_bid_mw=config["market"].get("minimum_bid_mw", 1),
            nominal_frequency_hz=60, rng=np.random.default_rng(config["scenario"]["seed"]))

    def test_current_participation_outcome_and_parameter_comparisons(self):
        base = self.prepare(self.config)
        quantities = [d.offer.quantity_mw for d in base.decisions]
        self.assertTrue(all(3.9 < q < 4 for q in quantities[:3]))
        self.assertTrue(1 < quantities[3] < 2)
        self.assertEqual(quantities[-1], 0)
        self.assertAlmostEqual(sum(a.quantity_mw for a in base.market.awards), 12)
        self.assertEqual(base.market.clearing_price_eur_per_mw_block, 24)
        for name, _, changed in experiment.configured_scenarios(self.config)[1:]:
            block = self.prepare(changed)
            if name == "no_penalty":
                self.assertTrue(all(p.penalty_eur_per_missing_mw_block == 0 for p in block.providers))
                self.assertEqual([d.offer.quantity_mw for d in block.decisions], [4]*5)
                self.assertEqual([a.quantity_mw for a in block.market.awards], [4, 4, 0, 4, 0])
                self.assertEqual(block.market.clearing_price_eur_per_mw_block, 20)
                self.assertEqual([s.available_mw for s in block.settlements],
                                 [s.available_mw for s in base.settlements])
                for original, altered in zip(self.config["providers"], changed["providers"]):
                    self.assertEqual(altered, {**original, "penalty_eur_per_missing_mw_block": 0})
            else:
                self.assertAlmostEqual(block.decisions[-1].offer.quantity_mw, quantities[3])
        relaxed = deepcopy(self.config)
        relaxed["market"]["minimum_bid_mw"] = .1
        self.assertTrue(0 < self.prepare(relaxed).decisions[-1].offer.quantity_mw < 1)

    def test_fixed_offers_awards_and_explicit_gains_reuse_the_same_block(self):
        providers = tuple(replace(p, gain_mw_per_hz=20)
                          for p in experiment.load_block_providers(self.config, self.point))
        offers = [FCRCapacityOffer(p.provider_id, 10, 3) for p in providers]
        awards = {p.provider_id: 2 for p in providers}
        block = prepare_fcr_block(providers, requirement_mw=10, nominal_frequency_hz=60,
                                  rng=np.random.default_rng(1), fixed_offers=offers,
                                  fixed_awards_mw=awards, fixed_price_eur_per_mw_block=12)
        self.assertTrue(all(p.gain_mw_per_hz == 20 for p in block.response_parameters))
        self.assertEqual(block.market.total_payment_eur, 120)
        self.assertTrue(all(a.quantity_mw == 2 for a in block.market.awards))
        with self.assertRaises(ValueError):
            prepare_fcr_block(providers, requirement_mw=10, nominal_frequency_hz=60,
                              rng=np.random.default_rng(1), fixed_offers=offers[:-1])

    def test_runner_writes_latest_outputs_and_replayable_effective_inputs(self):
        self.config["scenario"].update(final_time_s=2, output_time_step_s=.3)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with patch.object(experiment, "load_operating_point", return_value=self.point), redirect_stdout(io.StringIO()):
                summary = experiment.run_experiment(self.config, path, seed_override=123)
            self.assertEqual(summary["seed"], 123)
            self.assertEqual(summary["clearing_price_eur_per_mw_block"], 24)
            market = pd.read_csv(path / "market_outcomes.csv")
            trajectory = pd.read_csv(path / "time_series.csv")
            self.assertEqual(len(market), 5)
            self.assertEqual(trajectory.time_s.iloc[-1], 2)
            self.assertEqual(len(trajectory), 8)
            np.testing.assert_allclose(market.gain_mw_per_hz, [100, 400/3, 400/3, 50, 50])
            self.assertTrue((abs(trajectory.renewable_uncertain_response_mw) == 0).all())
            self.assertEqual(json.loads((path/"effective_config.json").read_text())["scenario"]["seed"], 123)
            for filename in ["frequency_comparison.png", "provider_responses.png",
                             "offer_rewards.csv", "operating_point.json"]:
                self.assertGreater((path/filename).stat().st_size, 0)

    def test_cli_and_fixed_modes_are_configurable(self):
        config = deepcopy(self.config)
        config["scenario"].update(final_time_s=2, output_time_step_s=.2)
        config["decision"]["mode"] = "fixed"
        config["market"].update(mode="fixed", requirement_mw=5, fixed_price_eur_per_mw_block=7)
        for raw in config["providers"]:
            raw.update(fixed_bid_eur_per_mw_block=1, fixed_offer_mw=2, fixed_award_mw=1,
                       variance_mw2=0, mean_mw=2, gain_mw_per_hz=10)
            del raw["minimum_bid_price_eur_per_mw_block"]
            del raw["maximum_bid_price_eur_per_mw_block"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            source = path/"input.json"
            source.write_text(json.dumps(config), encoding="utf-8")
            output = path/"output"
            with (patch.object(experiment, "load_operating_point", return_value=self.point),
                  patch("sys.argv", ["run_fcr", "--config", str(source), "--output-dir", str(output)]),
                  redirect_stdout(io.StringIO())):
                experiment.main()
            market = pd.read_csv(output/"market_outcomes.csv")
            np.testing.assert_array_equal(market.awarded_mw, 1)
            np.testing.assert_array_equal(market.gain_mw_per_hz, 10)
            np.testing.assert_array_equal(market.capacity_payment_eur, 7)

    def test_comparisons_use_the_same_runner_and_reject_unsafe_names(self):
        config = deepcopy(self.config)
        config["comparisons"][0]["name"] = "../outside"
        with self.assertRaises(ValueError):
            experiment.run_comparisons(config, Path("unused"))
        # Provider joins are independent of configuration order.
        self.config["providers"].reverse()
        loaded = experiment.load_block_providers(self.config, self.point)
        self.assertEqual(loaded[0].power_base_mw, 150)
        self.config["providers"][0]["generator_id"] = "missing"
        with self.assertRaises(ValueError):
            experiment.load_block_providers(self.config, self.point)

    def test_scenario_overrides_are_independent_and_specific_fields_win(self):
        original = deepcopy(self.config)
        config = deepcopy(self.config)
        config["comparisons"].append({
            "name": "custom", "market": {"minimum_bid_mw": .5, "requirement_mw": 8},
            "scenario": {"power_deficit_mw": 5}, "availability": {"boundary_mode": "clipped"},
            "decision": {"quantity_tolerance_mw": 1e-7},
            "all_providers": {"penalty_eur_per_missing_mw_block": 10,
                              "reserve_safety_margin_fraction": .1},
            "providers": {"renewable_uncertain": {"variance_mw2": 9,
                          "penalty_eur_per_missing_mw_block": 30, "gain_mw_per_hz": 40}},
        })
        variants = experiment.configured_scenarios(config)
        changed = variants[-1][2]
        self.assertEqual(changed["market"]["minimum_bid_mw"], .5)
        self.assertEqual(changed["scenario"]["power_deficit_mw"], 5)
        self.assertEqual(changed["decision"]["quantity_tolerance_mw"], 1e-7)
        providers = experiment.load_block_providers(changed, self.point)
        self.assertTrue(all(p.maximum_offer_mw == 3.6 for p in providers))
        self.assertTrue(all(p.availability.boundary_mode == "clipped" for p in providers))
        self.assertEqual([p.penalty_eur_per_missing_mw_block for p in providers], [10]*4 + [30])
        self.assertEqual(providers[-1].gain_mw_per_hz, 40)
        self.assertEqual(providers[-1].availability.variance_mw2, 9)
        self.assertEqual(config["providers"], original["providers"])
        self.assertEqual(variants[0][2]["providers"], original["providers"])
        self.assertEqual(variants[1][2]["providers"][-1]["variance_mw2"], 4)

    def test_scenario_validation_rejects_typos_and_unknown_provider_ids(self):
        for extra in ({"unexpected": {}}, {"market": {"min_bid_mw": 2}},
                      {"all_providers": {"variance_mw": 9}},
                      {"providers": {"missing": {"variance_mw2": 9}}},
                      {"all_providers": {"provider_id": "same"}},
                      {"name": "base"}, {"label": "Base"}):
            config = deepcopy(self.config)
            config["comparisons"] = [{"name": "custom", **extra}]
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                experiment.configured_scenarios(config)

    def test_cli_selects_one_named_scenario(self):
        with (patch("sys.argv", ["run_fcr", "--scenario", "no_penalty"]),
              patch.object(experiment, "run_experiment") as run):
            experiment.main()
        self.assertEqual(run.call_count, 1)
        changed = run.call_args.args[0]
        self.assertTrue(all(p["penalty_eur_per_missing_mw_block"] == 0
                            for p in changed["providers"]))
        self.assertEqual(changed["market"], self.config["market"])

    def test_obsolete_grids_require_explicit_config_migration(self):
        self.config["providers"][0]["quantity_candidates_mw"] = [0, 1]
        with self.assertRaisesRegex(ValueError, "candidate grids are obsolete"):
            experiment.load_block_providers(self.config, self.point)
