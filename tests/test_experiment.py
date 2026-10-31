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
from functional_incentives.providers import BlockProvider, QuadraticCommitmentCost, FIProviderParameters
from functional_incentives.market import FCRCapacityOffer
from functional_incentives.frequency import LinearFrequencyParameters
from functional_incentives.simulation import (prepare_fcr_block, simulate_fcr_power_step,
                                              clear_fcr_block, realize_fcr_block)
from functional_incentives.tso import (make_availability_banks, evaluate_gamma_curve, validate_sweep_settings,
                                      validate_optimizer_settings, search_common_gamma,
                                      optimize_common_gamma, simulate_common_gamma, optimize_nested_gamma_banks)
from functional_incentives.metrics import (summarize_tso_samples, summarize_tso_trajectory,
                                         summarize_weighted_tso_samples, summarize_paired_tso_objectives)


class FPPExperimentTests(unittest.TestCase):
    def test_zero_disturbance_counts_every_gain_pair_without_network_integration(self):
        from functional_incentives.fpp_study import prepare_gain_study
        players = ["a", "b"]
        providers = tuple(replace(provider(p, 5), participation="fi", fi_gamma_eur_per_hz_mw_s=50.,
                                  fi_rho_eur_per_mw2_s=1.) for p in players)
        cleared = clear_fcr_block(providers, requirement_mw=0., nominal_frequency_hz=60.)
        overlay = dict(game=dict(players=players,gains=[0.,1.],design_samples=2,
                                 paths=[dict(name="no_event", start_time_s=0., power_deficit_mw=0.)]),
                       assessment=dict(seed=7, samples=2))
        with tempfile.TemporaryDirectory(dir=experiment.PROJECT_ROOT) as temp:
            directory = Path(temp)
            with patch("functional_incentives.fpp_study.simulate_fcr_power_step") as solver:
                _, _, count = prepare_gain_study(directory, overlay, cleared, None, None, {})
            self.assertEqual(count, 0)
            solver.assert_not_called()
            scores = pd.read_csv(directory / "design_gain_scores.csv")
            self.assertEqual(len(scores), 8)
            self.assertTrue((scores[["score_1_mw_h","score_2_mw_h"]] == 0.).all().all())
            self.assertEqual(json.loads((directory/"gain_equilibrium.json").read_text())["design_timing"]["exact_zero_scores"], 8)

    def test_gain_study_freezes_design_policy_before_holdout_and_delivery(self):
        from functional_incentives.fpp_study import load_fpp_overlay, make_dispatch_schedules, prepare_gain_study
        from functional_incentives.providers import FPPProviderParameters
        overlay, config, _, _ = load_fpp_overlay(experiment.PROJECT_ROOT / "configs/aemo_fpp.toml")
        with redirect_stdout(io.StringIO()):
            _, cleared, plant = experiment.prepare_experiment(config)
        overlay["game"].update(gains=[0., 1.], design_samples=2)
        overlay["game"]["paths"] = [p for p in overlay["game"]["paths"] if p["power_deficit_mw"] > 0]
        overlay["assessment"]["samples"] = 2
        schedule = make_dispatch_schedules(cleared, overlay["schedules"], 300)["constant"]
        calls = []
        def synthetic_meters(_plant, providers, *, dispatch_schedule, **options):
            # Favor delivery in design, penalize it in holdout. Re-selection would flip both gains.
            sign = 1 if len(calls) < 8 else -1
            times = np.arange(0., 301., 4.)
            errors = np.full_like(times, -.05)
            errors[0] = 0.
            meters = dispatch_schedule.reference_mw(times).copy()
            for j, p in enumerate(providers):
                if isinstance(p, FPPProviderParameters):
                    meters[1:, j] += sign * p.theta * p.reserve_mw
            calls.append((tuple(p.reserve_mw for p in providers), options["start_time_s"],
                          options["power_deficit_mw"], options["power_deficit_changes"]))
            return SimpleNamespace(time_s=times, delta_frequency_hz=errors, provider_active_power_mw=meters)
        with tempfile.TemporaryDirectory(dir=experiment.PROJECT_ROOT) as temp:
            directory = Path(temp)
            with patch("functional_incentives.fpp_study.simulate_fcr_power_step", side_effect=synthetic_meters):
                episodes, _, integrations = prepare_gain_study(directory, overlay, cleared, plant, schedule, {})
            decision = json.loads((directory / "gain_equilibrium.json").read_text())
            audit = pd.read_csv(directory / "holdout_deviations.csv")
        self.assertEqual(integrations, 16)
        self.assertEqual(decision["pure_profiles"], [[1.,1.]])
        self.assertTrue(all((r["theta_1"], r["theta_2"]) == (1.,1.) for r in episodes))
        self.assertEqual([r["bank"] for r in episodes], ["assessment", "assessment", "delivery"])
        self.assertTrue((audit.query("deviating_theta == 0").paired_gain_mw_h > 0.).all())
        for offset in range(0, len(calls), 4):
            self.assertEqual(len(set(calls[offset:offset+4])), 1)
        self.assertNotEqual(calls[0][0], calls[8][0])

    def test_gain_search_uses_paired_inputs_and_separate_private_best_responses(self):
        from functional_incentives.fpp_game import evaluate_gain_bank, gain_equilibrium, deviation_gains
        bank = [dict(bank="design", sample=i, path=str(i), a=a, b=b)
                for i, (a,b) in enumerate(((.5,3.), (3.,1.)))]
        gains = [.5, 1.]
        # The second provider prefers a different column depending on the first player's row.
        scores = np.array([[[2,1],[3,3]], [[4,3],[5,2]]], dtype=float)
        cases = []
        def score(case, pair):
            cases.append((pair, case["a"], case["b"], case["path"]))
            return scores[gains.index(pair[0]), gains.index(pair[1])] + [case["a"], case["b"]]
        values, records = evaluate_gain_bank(bank, gains, score)
        for pair in ((.5,.5),(.5,1.),(1.,.5),(1.,1.)):
            self.assertEqual([row[1:] for row in cases if row[0] == pair], [(.5,3.,"0"),(3.,1.,"1")])
        self.assertEqual(len(records), 8)
        x, y, pure = gain_equilibrium(values.mean(axis=0))
        self.assertEqual(pure, [[1,0]])
        np.testing.assert_array_equal(x, [0,1])
        np.testing.assert_array_equal(y, [1,0])
        self.assertTrue(all(v.max() == 0. for v in deviation_gains(values, x, y)))

    def test_gain_solver_handles_a_mixed_equilibrium_and_deterministic_ties(self):
        from functional_incentives.fpp_game import gain_equilibrium, deviation_gains
        game = np.array([[[3,0],[0,2]], [[0,1],[1,0]]])
        x, y, pure = gain_equilibrium(game)
        self.assertFalse(pure)
        np.testing.assert_allclose([x,y], [[1/3,2/3],[.25,.75]])
        self.assertTrue(all(v.max() < 1e-10 for v in deviation_gains(game, x, y)))
        x, y, pure = gain_equilibrium(np.zeros((2,2,2)))
        self.assertEqual(len(pure), 4)
        np.testing.assert_array_equal([x,y], [[1,0],[1,0]])

    def test_current_references_use_solved_powers_for_every_provider_and_interval(self):
        from functional_incentives.fpp_study import load_fpp_overlay, make_dispatch_schedules
        path = experiment.PROJECT_ROOT / "configs/aemo_fpp_metered.toml"
        overlay, config, prices, _ = load_fpp_overlay(path)
        self.assertEqual(overlay["assessment_schedule"], "constant")
        self.assertIsNone(prices)
        with redirect_stdout(io.StringIO()):
            _, cleared, _ = experiment.prepare_experiment(config)
        solved = {p.provider_id: p.operating_power_mw for p in cleared.providers}
        self.assertEqual([p.provider_id for p, m in zip(cleared.providers, cleared.participation_modes) if m == "fi"],
                         overlay["game"]["players"])
        self.assertEqual([a.quantity_mw for a in cleared.market.awards], [4.,4.,4.,0.,0.])
        # Use the actual solved slack power as well as prescribed non-slack powers.
        self.assertAlmostEqual(solved["conventional_3"], 250.906095586, places=8)
        reordered = replace(cleared, providers=tuple(reversed(cleared.providers)))
        for name in ("aemo_fpp.toml", "aemo_fpp_metered.toml", "aemo_fpp_reversal.toml"):
            with self.subTest(config=name):
                resolved, _, _, _ = load_fpp_overlay(path.parent / name)
                self.assertEqual(resolved["assessment_schedule"], "constant")
                self.assertEqual(resolved["schedules"], [{"name": "constant"}])
                schedules = make_dispatch_schedules(reordered, resolved["schedules"], 600)
                self.assertEqual(set(schedules), {"constant"})
                schedule = schedules["constant"]
                expected = [solved[i] for i in schedule.provider_ids]
                np.testing.assert_array_equal(schedule.targets_mw, np.tile(expected, (3, 1)))
                np.testing.assert_array_equal(schedule.reference_mw([0, 4, 150, 300, 304, 600]),
                                              np.tile(expected, (6, 1)))

    def test_historical_assessment_schedule_stays_separate_from_delivery(self):
        from functional_incentives.metrics import summarize_fpp_assessment
        rows = [{"bank": "assessment", "sample": s, "schedule": "balanced_ramp", "policy": p,
                 "metric": s + j} for s in range(2) for j, p in enumerate(("fcr_alone", "bilinear_fi", "fpp"))]
        rows.append(dict(bank="delivery", sample=0, schedule="constant", policy="fpp", metric=999))
        summary, _ = summarize_fpp_assessment(pd.DataFrame(rows), schedule="balanced_ramp")
        self.assertEqual(summary.query("policy == 'fpp'")["mean"].iloc[0], 2.5)

    def test_reversal_overlay_changes_only_the_prescribed_disturbance(self):
        from functional_incentives.fpp_study import load_fpp_overlay
        root = experiment.PROJECT_ROOT / "configs"
        old, original, _, _ = load_fpp_overlay(root / "aemo_fpp.toml")
        new, reversal, prices, _ = load_fpp_overlay(root / "aemo_fpp_reversal.toml")
        self.assertEqual(original, reversal)
        self.assertEqual({k: v for k, v in new.items() if k != "disturbance_changes"}, old)
        self.assertEqual(new["disturbance_changes"], [{"time_s": 120.05, "net_power_deficit_mw": -7.}])
        self.assertIsNone(prices)

    def test_fixed_policy_mapping_reuses_awards_gains_and_actual_availability(self):
        from functional_incentives.fpp_study import policy_response_parameters
        from functional_incentives.providers import FPPProviderParameters
        a = provider("a", 5)
        b = replace(provider("b", 20), participation="fcr_or_fi", gain_mw_per_hz=17.,
                    fi_gamma_eur_per_hz_mw_s=50, fi_rho_eur_per_mw2_s=2)
        cleared = clear_fcr_block((a, b), requirement_mw=2, nominal_frequency_hz=60,
                                 fixed_offers=(FCRCapacityOffer("a", 5, 2), FCRCapacityOffer("b", 20, 2)))
        self.assertEqual(cleared.participation_modes, ("fcr", "fi"))
        block = realize_fcr_block(cleared, {"a": 1.5, "b": 3.})
        for policy in ("fcr_alone", "bilinear_fi", "fpp"):
            responses = policy_response_parameters(cleared, block, policy)
            self.assertIs(responses[0], block.response_parameters[0])
            self.assertEqual(responses[0].reserve_mw, 1.5)
            if policy == "fpp":
                self.assertIsInstance(responses[1], FPPProviderParameters)
                self.assertEqual(responses[1].gain_mw_per_hz, 17)
                self.assertEqual(responses[1].reserve_mw, 3)
                self.assertEqual(responses[1].rho_eur_per_mw2_s, 2)
            elif policy == "fcr_alone":
                self.assertEqual(responses[1].reserve_mw, 0)
            else:
                self.assertIs(responses[1], block.response_parameters[1])
        self.assertEqual(block.market, cleared.market)

    def test_overlay_preserves_machine_market_inputs_and_reports_only_assessment(self):
        from functional_incentives.fpp_study import load_fpp_overlay
        from functional_incentives.metrics import summarize_fpp_assessment
        overlay, config, prices, _ = load_fpp_overlay(experiment.PROJECT_ROOT / "configs/aemo_fpp.toml")
        base = tomllib.loads(experiment.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        original = dict((n, c) for n, _, c in experiment.configured_scenarios(base))["fi_endogenous"]
        for key in ("market", "availability", "fi"):
            self.assertEqual(config[key], original[key])
        self.assertEqual(config["decision"]["mode"], "fixed")
        for current, previous in zip(config["providers"], original["providers"], strict=True):
            for key in ("mean_mw", "variance_mw2", "reserve_capacity_mw", "time_constant_s", "inertia_s"):
                self.assertEqual(current[key], previous[key])
        self.assertEqual(config["scenario"]["power_deficit_mw"], 10)
        self.assertEqual(config["scenario"]["final_time_s"], 300)
        self.assertIsNone(prices)
        rows = [{"bank": "assessment", "sample": s, "schedule": "constant", "policy": p,
                 "metric": s + j} for s in range(2) for j, p in enumerate(("fcr_alone", "bilinear_fi", "fpp"))]
        rows.append(dict(bank="delivery", sample=0, schedule="constant", policy="fpp", metric=999))
        summary, paired = summarize_fpp_assessment(pd.DataFrame(rows))
        self.assertEqual(summary.query("policy == 'fpp'")["mean"].iloc[0], 2.5)
        difference = paired.query("policy == 'fpp' and reference == 'bilinear_fi'").iloc[0]
        self.assertEqual(difference["mean"], 1.)
        self.assertEqual(difference.ci95_low, 1.)

    def test_fpp_cli_rejects_optimizer_and_ordinary_comparison_overrides(self):
        for flags in (["--fpp-benchmark", "--tso-optimize"], ["--fpp-benchmark", "--compare"],
                      ["--fpp-benchmark", "--frequency-weight", "10000"],
                      ["--fpp-benchmark", "--scenario", "fi_endogenous"], ["--fpp-benchmark"],
                      ["--fpp-compare-controllers", "unused"], ["--fpp-compare-controllers", "unused", "--tso-optimize"]):
            with patch("sys.argv", ["run_fcr", *flags]), self.assertRaises(SystemExit), redirect_stdout(io.StringIO()):
                experiment.main()


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
    def test_clearing_and_explicit_draws_reproduce_block_without_reclearing(self):
        participants = [provider("b", 5), provider("a", 4)]
        arguments = dict(requirement_mw=2, nominal_frequency_hz=60)
        cleared = clear_fcr_block(participants, **arguments)
        rng = np.random.default_rng(24)
        draws = {p.provider_id: p.availability.sample_mw(rng) for p in cleared.providers}
        replayed = realize_fcr_block(cleared, draws)
        self.assertEqual(replayed, prepare_fcr_block(participants, rng=np.random.default_rng(24), **arguments))
        for invalid in ({"a": 1}, {"a": 1, "b": 5}, {"a": np.nan, "b": 1}):
            with self.assertRaises(ValueError):
                realize_fcr_block(cleared, invalid)

    def test_fi_assignment_optout_rejection_and_inactive_are_distinct(self):
        def candidate(identity, price, mode):
            return replace(provider(identity, price), participation=mode,
                           fi_gamma_eur_per_hz_mw_s=10, fi_rho_eur_per_mw2_s=1)
        participants = (candidate("assigned", 10, "fi"), candidate("cheap", 5, "fcr_or_fi"),
                        candidate("optout", 0, "fcr_or_fi"), candidate("rejected", 20, "fcr_or_fi"),
                        candidate("fcr_only", 30, "fcr"),
                        candidate("inactive", 10, "inactive"))
        block = prepare_fcr_block(participants, requirement_mw=2, nominal_frequency_hz=60,
                                  rng=np.random.default_rng(1))
        modes = dict(zip((p.provider_id for p in block.providers), block.participation_modes))
        self.assertEqual(modes, dict(assigned="fi", cheap="fcr", optout="fi", rejected="fi",
                                    fcr_only="fcr", inactive="inactive"))
        self.assertEqual(sum(a.quantity_mw for a in block.market.awards), 2)
        cheap_offer = next(d.offer for d in block.decisions if d.offer.provider_id == "cheap")
        self.assertGreater(cheap_offer.quantity_mw, 2)  # partial award still excludes FI
        for p, s, response in zip(block.providers, block.settlements, block.response_parameters):
            if modes[p.provider_id] == "fi":
                self.assertIsInstance(response, FIProviderParameters)
                self.assertEqual(s.awarded_mw, 0)
                self.assertEqual(s.profit_eur, 0)
                self.assertEqual(response.reserve_mw, s.available_mw)
            elif p.provider_id in ("inactive", "fcr_only"):
                self.assertEqual(response.reserve_mw, 0)
        rejected = next(d for d in block.decisions if d.offer.provider_id == "rejected")
        self.assertGreater(rejected.offer.quantity_mw, 0)
        self.assertEqual(next(d.offer.quantity_mw for d in block.decisions
                              if d.offer.provider_id == "optout"), 0)
        trajectory = simulate_fcr_power_step(
            LinearFrequencyParameters(60, 5, 60, 1), block.response_parameters,
            power_deficit_mw=1, start_time_s=.15, final_time_s=1.1,
            output_time_step_s=.1, fi_decision_interval_s=.1,
        )
        self.assertEqual(trajectory.fi.provider_ids, ("assigned", "optout", "rejected"))
        self.assertGreater(trajectory.provider_response_mw[-1, trajectory.provider_ids.index("rejected")], 0)
        self.assertGreater(trajectory.fi.net_settlement_eur[-1, 2], 0)
        rng = np.random.default_rng(1)
        rng_state = deepcopy(rng.bit_generator.state)
        with self.assertRaisesRegex(ValueError, "Insufficient offered reserve"):
            prepare_fcr_block([participants[0]], requirement_mw=1, nominal_frequency_hz=60, rng=rng)
        self.assertEqual(rng.bit_generator.state, rng_state)

    def test_fixed_modes_cannot_hide_fi_overlap(self):
        p = replace(provider("a", 10), participation="fi", fi_gamma_eur_per_hz_mw_s=10, fi_rho_eur_per_mw2_s=1)
        for mode in ("fi", "inactive"):
            with self.assertRaises(ValueError):
                prepare_fcr_block([replace(p, participation=mode)], requirement_mw=0, nominal_frequency_hz=60,
                                  rng=np.random.default_rng(1), fixed_offers=[FCRCapacityOffer("a", 10, 2)])
        with self.assertRaises(ValueError):
            prepare_fcr_block([p], requirement_mw=1, nominal_frequency_hz=60, rng=np.random.default_rng(1),
                              fixed_awards_mw={"a": 1}, fixed_price_eur_per_mw_block=1)
        with self.assertRaisesRegex(ValueError, "required"):
            replace(provider("a", 10), participation="fi")
        with self.assertRaisesRegex(ValueError, "participation"):
            replace(p, participation="both")

    def test_fi_fallback_uses_award_in_fixed_and_optimized_modes(self):
        p = replace(provider("a", 10), participation="fcr_or_fi",
                    fi_gamma_eur_per_hz_mw_s=10, fi_rho_eur_per_mw2_s=1,
                    availability=GaussianReserve(0, 0, 4, "truncated"))
        for fixed_offer in (False, True):
            for fixed_award in (False, True):
                for quantity in (0, .5):
                    with self.subTest(fixed_offer=fixed_offer, fixed_award=fixed_award, award=quantity):
                        block = prepare_fcr_block(
                            [p], requirement_mw=quantity, nominal_frequency_hz=60,
                            rng=np.random.default_rng(1),
                            fixed_offers=[FCRCapacityOffer("a", 10, 2)] if fixed_offer else None,
                            fixed_awards_mw={"a": quantity} if fixed_award else None,
                            fixed_price_eur_per_mw_block=(10 if quantity else 0) if fixed_award else None,
                        )
                        self.assertGreater(block.decisions[0].offer.quantity_mw, 0)
                        self.assertEqual(block.settlements[0].awarded_mw, quantity)
                        self.assertEqual(block.participation_modes, ("fi" if quantity == 0 else "fcr",))
                        # Zero delivered reserve does not turn a positive award into FI.
                        self.assertEqual(block.response_parameters[0].reserve_mw, 0)
                        self.assertEqual(isinstance(block.response_parameters[0], FIProviderParameters), quantity == 0)

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

    def test_procurement_preserves_inputs_references_and_resumes(self):
        from functional_incentives import procurement
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline"
            config = next(c for n, _, c in experiment.configured_scenarios(self.config) if n == "fi_endogenous")
            config.pop("comparisons", None)
            config["scenario"]["final_time_s"] = 1.43
            config["tso_optimizer"].update(frequency_weight_eur_per_hz2_s=10000, design_samples=2,
                validation_samples=2, grid_points=3, gamma_tolerance=100., max_refinement_rounds=1)
            with patch.object(experiment, "load_operating_point", return_value=self.point), redirect_stdout(io.StringIO()):
                experiment.run_tso_optimization(config, baseline / "first")
                effective = procurement._read(baseline / "first/effective_config.json")
                procurement._save(baseline / "study_plan.json", {"cases": [
                    {"name": "first", "label": "First fleet", "config": effective}]})
                manifest = root / "manifest.toml"
                manifest.write_text('baseline_study="baseline"\nrequirements_mw=[10,8]\nworkers=1\n')
                hashes = procurement._hash_tree(baseline)
                plan = procurement.load_procurement_plan(manifest)
                for item in plan["cases"]:
                    changed = deepcopy(item["config"])
                    changed["market"]["requirement_mw"] = 12.
                    self.assertEqual(changed, effective)
                output = root / "study"
                procurement.run_procurement_study(manifest, output)
                with (patch.object(experiment, "run_tso_optimization") as optimize,
                      patch.object(procurement, "simulate_common_gamma") as simulate):
                    procurement.run_procurement_study(manifest, output)
                    optimize.assert_not_called()
                    report = procurement.run_procurement_report(output)
                    optimize.assert_not_called()
                    simulate.assert_not_called()
                self.assertEqual(report["report_simulations"], 0)
                self.assertEqual(report["fcr_only_validation_simulations"], 2)
                self.assertEqual(len(list(output.glob("*.png"))), 5)
                estimates = pd.read_csv(output / "comparison_summary.csv")
                self.assertEqual(set(estimates.query("bank == 'validation'").samples), {2})
                self.assertEqual(set(estimates.fcr_capacity_payment_eur_per_block), {288, 240, 160})
                economic = pd.read_csv(output / "economic_sensitivity.csv")
                np.testing.assert_allclose(economic.saving_if_one_event_vs_reference_fi_eur,
                    economic.fcr_savings_eur_per_block - economic.extra_fi_eur_per_event)
                self.assertEqual(hashes, procurement._hash_tree(baseline))
                self.assertFalse((output / "first_q12").exists())
                for q, price in [(10, 24), (8, 20)]:
                    market = procurement._read(output / f"first_q{q}/market.json")
                    self.assertEqual(market["market"]["clearing_price_eur_per_mw_block"], price)
                    self.assertEqual(market["roles"]["conventional_3"], "fcr")
                    self.assertEqual(market["roles"]["renewable_uncertain"], "fi")
                    self.assertEqual(market["market"]["offers"], procurement._read(baseline / "first/market.json")["market"]["offers"])
                    pd.testing.assert_frame_equal(pd.read_csv(output / f"first_q{q}/availability_samples.csv"),
                                                  pd.read_csv(baseline / "first/availability_samples.csv"))
                changed = output / "first_q8/delivery_availability.json"
                draws = procurement._read(changed)
                draws["renewable_uncertain"] *= .5
                procurement._save(changed, draws)
                with self.assertRaisesRegex(ValueError, "identical availability"):
                    procurement.run_procurement_report(output)
                self.assertEqual(procurement._read(output / "report_status.json")["status"], "failed")
                with self.assertRaisesRegex(ValueError, "separate"):
                    procurement.run_procurement_study(manifest, baseline / "nested")
                manifest.write_text('baseline_study="baseline"\nrequirements_mw=[10,10]\nworkers=1\n')
                with self.assertRaisesRegex(ValueError, "distinct"):
                    procurement.load_procurement_plan(manifest)
                manifest.write_text('baseline_study="baseline"\nrequirements_mw=[10]\nworkers=1\nfrequency_weight=4\n')
                with self.assertRaisesRegex(ValueError, "only"):
                    procurement.load_procurement_plan(manifest)

    def test_procurement_paired_metrics_match_by_sample_and_preserve_covariance(self):
        from functional_incentives.procurement import paired_procurement_differences
        reference = pd.DataFrame({"sample": [0, 1, 2], "frequency_error_hz2_s": [1., 2., 3.],
            "net_payment_eur": [30., 20., 10.], "activation_cost_eur": [10., 10., 10.],
            "fi_profit_eur": [20., 10., 0.], "nadir_sampled_hz": [59., 58., 57.],
            "final_frequency_hz": [59.9, 59.8, 59.7], "objective_eur": [40., 40., 40.]})
        candidate = reference.copy()
        candidate.frequency_error_hz2_s += [.1, .2, .3]
        candidate.net_payment_eur -= [1., 2., 3.]
        candidate.fi_profit_eur = candidate.net_payment_eur - candidate.activation_cost_eur
        candidate.objective_eur = 10*candidate.frequency_error_hz2_s + candidate.net_payment_eur
        row, raw = paired_procurement_differences(candidate.iloc[::-1], reference)
        self.assertAlmostEqual(row["delta_net_payment_eur_mean"], -2.)
        self.assertAlmostEqual(row["delta_objective_eur_mean"], 0.)
        self.assertAlmostEqual(row["delta_objective_eur_ci95_halfwidth"], 0.)
        self.assertEqual(raw["sample"].tolist(), [0, 1, 2])
        with self.assertRaisesRegex(ValueError, "identical unique"):
            paired_procurement_differences(candidate.iloc[:2], reference)

    def test_cli_procurement_modes_require_explicit_inputs(self):
        from functional_incentives import procurement
        with (patch("sys.argv", ["run_fcr", "--tso-procurement", "--config", "manifest.toml", "--output-dir", "study"]),
              patch.object(procurement, "run_procurement_study") as run):
            experiment.main()
        self.assertEqual(run.call_args.args, (Path("manifest.toml"), Path("study")))
        for args in (["--tso-procurement"], ["--tso-procurement", "--compare"],
                     ["--tso-procurement-report", "study", "--config", "manifest.toml"],
                     ["--tso-procurement", "--frequency-weight", "1"]):
            with patch("sys.argv", ["run_fcr", *args]), patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                experiment.main()

    def test_fleet_manifest_preserves_economics_and_rejects_confounding(self):
        from functional_incentives.fleet import load_fleet_plan, PHYSICAL_FIELDS
        path = experiment.PROJECT_ROOT / "configs/fleet_scenarios.toml"
        plan = load_fleet_plan(path)
        self.assertEqual(len(plan["cases"]), 3)
        common = []
        for case in plan["cases"]:
            config = case["config"]
            self.assertEqual(config["tso_optimizer"]["design_samples"], 32)
            self.assertEqual(config["tso_optimizer"]["validation_samples"], 32)
            common.append([{k: v for k, v in raw.items() if k not in PHYSICAL_FIELDS} for raw in config["providers"]])
        self.assertEqual(common[0], common[1])
        self.assertEqual(common[0], common[2])
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "manifest.toml"
            source = path.read_text(encoding="utf-8").replace('"fcr.toml"', '"' + experiment.DEFAULT_CONFIG_PATH.as_posix() + '"')
            invalid.write_text(source.replace("time_constant_s = 3.4", "fi_rho_eur_per_mw2_s = 3.4"), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "only allow physical"):
                load_fleet_plan(invalid)

    def test_fleet_report_accounts_by_identity_and_reuses_saved_runs(self):
        from functional_incentives import fleet
        # Reverse input order: clearing sorts providers, report must join by ID.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # Write a TOML baseline by editing the actual source; reverse resolved
            # providers below, since JSON is intentionally not a manifest format.
            source = experiment.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
            source = source.replace("final_time_s = 40.0", "final_time_s = 1.43")
            (root / "base.toml").write_text(source, encoding="utf-8")
            manifest = root / "manifest.toml"
            manifest.write_text('base_config="base.toml"\nbase_scenario="fi_endogenous"\nworkers=1\n'
                '[optimizer]\nfrequency_weight_eur_per_hz2_s=10000\ndesign_samples=2\nvalidation_samples=2\n'
                'grid_points=3\ngamma_tolerance=100.0\nmax_refinement_rounds=1\n'
                '[[cases]]\nname="first"\nlabel="First"\n'
                '[[cases]]\nname="second"\nlabel="Second"\n'
                '[cases.providers]\nconventional_1={time_constant_s=4.0}\n', encoding="utf-8")
            plan = fleet.load_fleet_plan(manifest)
            for case in plan["cases"]:
                case["config"]["providers"].reverse()
            output = root / "study"
            with (patch.object(experiment, "load_operating_point", return_value=self.point),
                  patch.object(fleet, "load_fleet_plan", return_value=plan), redirect_stdout(io.StringIO())):
                result = fleet.run_fleet_study(manifest, output)
                with patch.object(experiment, "run_tso_optimization") as optimize, patch.object(fleet, "simulate_common_gamma") as simulate:
                    resumed = fleet.run_fleet_study(manifest, output)
                    optimize.assert_not_called()
                    simulate.assert_not_called()
            self.assertEqual(result["new_baseline_simulations"], 2)
            self.assertEqual(resumed["new_baseline_simulations"], 0)
            self.assertEqual(len(list(output.glob("*.png"))), 5)
            summaries = pd.read_csv(output / "comparison_summary.csv")
            self.assertEqual(summaries.groupby("bank").samples.first().to_dict(), {"delivery": 1, "validation": 2})
            self.assertTrue((summaries.fcr_capacity_payment_eur_per_block == 288).all())
            self.assertTrue((summaries.fcr_block_hours == 4).all())
            self.assertTrue((summaries.fi_horizon_s == 1.43).all())
            np.testing.assert_allclose(summaries.fi_profit_eur_mean,
                                       summaries.net_payment_eur_mean - summaries.activation_cost_eur_mean)
            providers = pd.read_csv(output / "provider_delivery.csv")
            fi = providers.query("provider_id == 'renewable_uncertain'")
            self.assertTrue((fi.role == "fi").all())
            self.assertTrue((fi.fcr_awarded_mw == 0).all())
            point = pd.read_csv(output / "machine_parameters.csv")
            self.assertTrue((point.query("provider_id == 'conventional_2'").solved_power_mw == 310).all())
            changed = output / "second/delivery_availability.json"
            draws = json.loads(changed.read_text())
            draws["renewable_uncertain"] *= .5
            changed.write_text(json.dumps(draws))
            with self.assertRaisesRegex(ValueError, "identical draws"):
                fleet.run_fleet_report(output)
            self.assertEqual(json.loads((output / "report_status.json").read_text())["status"], "failed")

    def test_cli_fleet_modes_require_explicit_inputs(self):
        from functional_incentives import fleet
        with (patch("sys.argv", ["run_fcr", "--tso-fleet", "--config", "manifest.toml", "--output-dir", "study"]),
              patch.object(fleet, "run_fleet_study") as run):
            experiment.main()
        self.assertEqual(run.call_args.args, (Path("manifest.toml"), Path("study")))
        for args in (["--tso-fleet"], ["--tso-fleet", "--compare"],
                     ["--tso-fleet-report", "study", "--config", "manifest.toml"],
                     ["--tso-fleet", "--frequency-weight", "1"]):
            with patch("sys.argv", ["run_fcr", *args]), patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                experiment.main()

    def test_tso_curve_banks_accounting_and_replay(self):
        config = next(c for n, _, c in experiment.configured_scenarios(self.config) if n == "fi_endogenous")
        config["scenario"].update(final_time_s=1.43, output_time_step_s=.1)
        config["tso"].update(gamma_values=[0, 50, 200], design_samples=3, validation_samples=3)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with patch.object(experiment, "load_operating_point", return_value=self.point), redirect_stdout(io.StringIO()):
                result = experiment.run_tso_experiment(config, path)
                _, cleared, parameters = experiment.prepare_experiment(config)
            draws = pd.read_csv(path / "availability_samples.csv")
            pd.testing.assert_frame_equal(draws, make_availability_banks(cleared, config["tso"]))
            self.assertFalse(np.array_equal(draws.iloc[:3, 2:].values, draws.iloc[3:, 2:].values))
            outcomes = pd.read_csv(path / "sample_outcomes.csv")
            self.assertEqual(len(outcomes), 18)
            self.assertTrue((outcomes[outcomes.gamma == 0].net_payment_eur == 0).all())
            self.assertTrue((outcomes[outcomes.gamma == 0].activation_cost_eur == 0).all())
            self.assertIsNone(result["gamma_selected"])
            self.assertEqual(result["failed_simulations"], 0)
            self.assertGreater(result["simulation_count"], 18)
            self.assertEqual(json.loads((path / "run_status.json").read_text())["status"], "complete")
            self.assertTrue((path / "tradeoff.png").is_file())
            row = draws.iloc[0]
            block = realize_fcr_block(cleared, {p.provider_id: row[p.provider_id] for p in cleared.providers})
            direct = simulate_fcr_power_step(parameters, block.response_parameters, **result["simulation_options"])
            saved = outcomes[(outcomes.bank == "design") & (outcomes["sample"] == 0) & (outcomes.gamma == 50)].iloc[0]
            self.assertAlmostEqual(saved.net_payment_eur, direct.fi.net_settlement_eur[-1].sum(), places=10)
            self.assertAlmostEqual(saved.frequency_error_hz2_s, direct.integrated_squared_frequency_error_hz2_s[-1], places=10)
            with patch("functional_incentives.tso.simulate_fcr_power_step", side_effect=RuntimeError("solver failed")):
                with self.assertRaisesRegex(RuntimeError, "bank=design, gamma=0.0, sample=0"):
                    evaluate_gamma_curve(cleared, parameters, banks=draws, settings=config["tso"],
                                         simulation_options=result["simulation_options"])

    def test_pareto_replay_preserves_source_and_report_requires_no_simulations(self):
        from functional_incentives import pareto
        config = next(c for n, _, c in experiment.configured_scenarios(self.config) if n == "fi_endogenous")
        config["scenario"].update(final_time_s=1.43, output_time_step_s=.1)
        config["tso"].update(gamma_values=[0, 50, 200], design_samples=2, validation_samples=2)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "source", root / "pareto"
            with (patch.object(experiment, "load_operating_point", return_value=self.point),
                  patch("functional_incentives.plotting.plot_tso_pareto"), redirect_stdout(io.StringIO())):
                experiment.run_tso_experiment(config, source)
                # Emulate the historical sweep, which did not store RoCoF or terminal absolute error.
                old = pd.read_csv(source / "sample_outcomes.csv")
                old = old.drop(columns=[c for c in old if "rocof" in c or c == "final_absolute_error_hz"])
                old.to_csv(source / "sample_outcomes.csv", index=False)
                before = pareto._hashes(source)
                result = pareto.run_pareto_study(source, target, workers=1)
                self.assertEqual(result["sample_count"], 12)
                self.assertEqual(result["refinement_count"], 6)
                self.assertEqual(before, pareto._hashes(source))
                augmented = pd.read_csv(target / "sample_outcomes.csv")
                pd.testing.assert_frame_equal(old.sort_values(pareto.KEYS).reset_index(drop=True),
                    augmented[old.columns].sort_values(pareto.KEYS).reset_index(drop=True))
                self.assertEqual(augmented.rocof_max_abs_hz_per_s.nunique(), 1)
                self.assertTrue((augmented.tail_stationarity_pass == 0).all())
                frontier = pd.read_csv(target / "pareto_frontiers.csv")
                rocof = frontier[(frontier.metric == "rocof_max_abs_hz_per_s") & frontier.empirical_pareto]
                self.assertEqual(set(rocof.gamma), {0})
                with patch.object(pareto, "simulate_common_gamma", side_effect=AssertionError("unexpected integration")):
                    self.assertEqual(pareto.run_pareto_report(target)["simulation_count"], 0)
                    pareto.run_pareto_study(source, target, workers=1)
                with self.assertRaises(ValueError):
                    pareto.run_pareto_study(source, source / "nested", workers=1)
                # A changed completed artifact must not silently become a new report.
                with (target / "sample_outcomes.csv").open("a") as stream:
                    stream.write("\n")
                with self.assertRaisesRegex(ValueError, "artifact has changed"):
                    pareto.run_pareto_report(target)

    def test_pareto_metric_specific_dominance_and_paired_uncertainty(self):
        from functional_incentives.metrics import empirical_pareto_flags
        np.testing.assert_array_equal(empirical_pareto_flags([0, 1, 2, 3], [4, 3, 5, 2]),
                                      [True, True, False, True])
        np.testing.assert_array_equal(empirical_pareto_flags([0, 1, 2], [59, 60, 58], maximize=True),
                                      [True, True, False])
        np.testing.assert_array_equal(empirical_pareto_flags([0, 1, 1], [1, 1, 1]), [True, False, False])
        np.testing.assert_array_equal(empirical_pareto_flags([1, 1], [2, 2]), [True, True])
        with self.assertRaises(ValueError):
            empirical_pareto_flags([0, 1], [1, float("nan")])
        rows = [{"bank": bank, "sample": s, "gamma": g, "net_payment_eur": g,
                 "frequency_error_hz2_s": 3-g+s, "nadir_sampled_hz": 59+.1*g+.01*s,
                 "final_absolute_error_hz": .2-.05*g+.01*s, "rocof_max_abs_hz_per_s": .1}
                for bank in ("design", "validation") for g in (0, 1, 2) for s in range(3)]
        summary = summarize_tso_samples(pd.DataFrame(rows))
        self.assertTrue((summary.paired_improvement_nadir_sampled_hz_ci95_halfwidth < 1e-12).all())
        self.assertTrue((summary.paired_improvement_final_absolute_error_hz_mean >= 0).all())
        self.assertEqual(set(summary.loc[summary.empirical_pareto_rocof_max_abs_hz_per_s, "gamma"]), {0})
        self.assertTrue(summary.empirical_pareto_nadir_sampled_hz.all())

    def test_cli_pareto_uses_frozen_inputs(self):
        with (patch("sys.argv", ["run_fcr", "--tso-pareto", "source", "--output-dir", "target"]),
              patch("functional_incentives.pareto.run_pareto_study") as run):
            experiment.main()
            run.assert_called_once_with(Path("source"), Path("target"))
        with (patch("sys.argv", ["run_fcr", "--tso-pareto-report", "target"]),
              patch("functional_incentives.pareto.run_pareto_report") as report):
            experiment.main()
            report.assert_called_once_with(Path("target"))
        for args in (["--tso-pareto", "source"],
                     ["--tso-pareto", "source", "--scenario", "fi_endogenous"],
                     ["--tso-pareto", "source", "--seed", "1"],
                     ["--tso-pareto", "source", "--frequency-weight", "10000"],
                     ["--tso-pareto-report", "target", "--output-dir", "elsewhere"],
                     ["--tso-pareto", "source", "--tso-sweep"]):
            with patch("sys.argv", ["run_fcr", *args]), patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                experiment.main()

    def test_tso_optimizer_settings_require_explicit_valid_weight(self):
        settings = {**self.config["tso_optimizer"], "frequency_weight_eur_per_hz2_s": 10000.}
        validate_optimizer_settings(settings)
        invalid = [{"frequency_weight_eur_per_hz2_s": x} for x in (0, -1, np.nan, np.inf, True)]
        invalid += [{"gamma_max": 0}, {"gamma_tolerance": 300}, {"grid_points": 2},
                    {"max_refinement_rounds": 1.5}, {"seed": -1}, {"validation_samples": 1},
                    {"typo": 1}]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_optimizer_settings({**settings, **changes})
        with self.assertRaisesRegex(ValueError, "explicit frequency weight"):
            experiment.run_tso_optimization(self.config, Path("unused"))

    def test_tso_search_multiple_minima_endpoints_ties_and_resolution(self):
        settings = {**self.config["tso_optimizer"], "frequency_weight_eur_per_hz2_s": 1.,
                    "gamma_max": 8., "grid_points": 9, "gamma_tolerance": .01}
        evaluated = []

        def two_wells(gamma):
            evaluated.append(gamma)
            return min((gamma - 1.3)**2 + .1, (gamma - 6.7)**2)

        result = search_common_gamma(two_wells, settings)
        self.assertAlmostEqual(result["gamma_selected"], 6.7, delta=.01)
        self.assertEqual(len(evaluated), len(set(evaluated)))
        self.assertTrue(any(abs(g - 1.3) < .01 for g in evaluated))
        self.assertTrue(result["local_resolution_reached"])
        self.assertIn(0, evaluated)
        self.assertIn(8, evaluated)
        self.assertTrue(search_common_gamma(lambda g: -g, settings)["at_upper_bound"])
        self.assertTrue(search_common_gamma(lambda g: g, settings)["at_lower_bound"])
        self.assertEqual(search_common_gamma(lambda g: 1., settings)["gamma_selected"], 0)
        limited = search_common_gamma(two_wells, {**settings, "max_refinement_rounds": 1})
        self.assertFalse(limited["local_resolution_reached"])
        with self.assertRaisesRegex(RuntimeError, "non-finite TSO objective"):
            search_common_gamma(lambda g: np.nan, settings)

    def test_weighted_uncertainty_keeps_error_payment_covariance(self):
        # Perfect anticorrelation cancels in J; adding marginal variances would
        # incorrectly report a positive uncertainty for this constant objective.
        rows = [{"bank": "validation", "gamma": 5, "sample": j,
                 "frequency_error_hz2_s": j + 1, "net_payment_eur": 10 - 2 * (j + 1)}
                for j in range(3)]
        rows.append({**rows[0], "bank": "delivery"})
        summary = summarize_weighted_tso_samples(pd.DataFrame(rows), 2).set_index("bank")
        self.assertEqual(summary.loc["validation", "objective_eur_mean"], 10)
        self.assertEqual(summary.loc["validation", "objective_eur_se"], 0)
        self.assertGreater(summary.loc["validation", "frequency_error_hz2_s_se"], 0)
        self.assertTrue(pd.isna(summary.loc["delivery", "objective_eur_se"]))

    def test_tso_optimizer_delivery_replay_and_validation_isolation(self):
        config = next(c for n, _, c in experiment.configured_scenarios(self.config) if n == "fi_endogenous")
        config["scenario"].update(final_time_s=1.43, output_time_step_s=.1)
        config["tso_optimizer"].update(grid_points=3, gamma_tolerance=25., max_refinement_rounds=2,
                                        design_samples=2, validation_samples=3)
        # Optimization accepts rho/participation without a redundant manual gamma.
        del config["providers"][-1]["fi_gamma_eur_per_hz_mw_s"]
        original = deepcopy(config)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with patch.object(experiment, "load_operating_point", return_value=self.point) as prepare, redirect_stdout(io.StringIO()):
                result = experiment.run_tso_optimization(config, path, seed_override=721, frequency_weight=10000.)
            self.assertEqual(prepare.call_count, 1)
            self.assertEqual(config, original)
            gamma = result["gamma_selected"]
            samples = pd.read_csv(path / "sample_outcomes.csv")
            banks = pd.read_csv(path / "availability_samples.csv")
            for bank, count in (("validation", 3), ("delivery", 1)):
                subset = samples[samples.bank == bank]
                self.assertEqual(len(subset), count)
                self.assertEqual(subset.gamma.unique().tolist(), [gamma])
            by_candidate = samples[samples.bank == "design"].groupby("gamma").objective_eur.mean()
            self.assertEqual(gamma, float(by_candidate.idxmin()))
            np.testing.assert_allclose(samples.objective_eur,
                                       10000 * samples.frequency_error_hz2_s + samples.net_payment_eur)
            self.assertEqual(result["simulation_count"], len(samples) + len(pd.read_csv(path / "numerical_checks.csv")))
            self.assertGreater(result["timing"]["total_cpu_s"], 0)
            self.assertEqual(json.loads((path / "run_status.json").read_text())["status"], "complete")
            self.assertFalse(list(path.glob("*.png")))
            selected = json.loads((path / "selected_config.json").read_text())
            self.assertEqual(selected["providers"][-1]["fi_gamma_eur_per_hz_mw_s"], gamma)
            with patch.object(experiment, "load_operating_point", return_value=self.point):
                _, cleared, parameters = experiment.prepare_experiment(selected)
            settings, options = result["settings"], result["simulation_options"]
            expected_banks = make_availability_banks(cleared, settings)
            pd.testing.assert_frame_equal(banks, expected_banks, check_exact=False, atol=1e-14, rtol=1e-14)
            draws = json.loads((path / "delivery_availability.json").read_text())
            rng = np.random.default_rng(np.random.SeedSequence(721).spawn(3)[2])
            self.assertEqual(draws, {p.provider_id: p.availability.sample_mw(rng) for p in cleared.providers})
            block = realize_fcr_block(cleared, draws)
            replay = simulate_common_gamma(block, parameters, gamma, options)
            stored = pd.read_csv(path / "time_series.csv")
            np.testing.assert_allclose(stored.frequency_hz, replay.frequency_hz, atol=1e-12, rtol=0)
            self.assertAlmostEqual(result["delivery_fi"]["net_settlement_eur"],
                                   replay.fi.net_settlement_eur[-1].sum(), places=10)
            # Alter validation only. It must change its outcomes, never design
            # candidates or selection. The later delivery stream is unchanged.
            banks.loc[banks.bank == "validation", "renewable_uncertain"] = 0.
            changed = optimize_common_gamma(cleared, parameters, banks=banks, settings=settings,
                                            simulation_options=options)
            self.assertEqual(changed.search["gamma_selected"], gamma)
            pd.testing.assert_frame_equal(
                samples[samples.bank == "design"][["gamma", "objective_eur"]].reset_index(drop=True),
                changed.samples[changed.samples.bank == "design"][["gamma", "objective_eur"]].reset_index(drop=True))
            self.assertTrue((changed.samples[changed.samples.bank == "validation"].net_payment_eur == 0).all())
            self.assertEqual(changed.delivery_availability, draws)
            with (patch.object(experiment, "load_operating_point", return_value=self.point),
                  patch("functional_incentives.tso.simulate_fcr_power_step", side_effect=RuntimeError("solver failed")),
                  redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, "bank=design")):
                experiment.run_tso_optimization(config, path, frequency_weight=10000.)
            self.assertEqual(json.loads((path / "run_status.json").read_text())["status"], "failed")

    def test_common_gamma_applies_to_every_fi_provider_only(self):
        providers = tuple(replace(provider(i, 20), participation="fi",
                                  fi_gamma_eur_per_hz_mw_s=old, fi_rho_eur_per_mw2_s=1)
                          for i, old in (("a", 10), ("b", 90)))
        block = prepare_fcr_block(providers, requirement_mw=0, nominal_frequency_hz=60,
                                  rng=np.random.default_rng(1))
        with patch("functional_incentives.tso.simulate_fcr_power_step") as simulator:
            simulate_common_gamma(block, None, 37., {})
        responses = simulator.call_args.args[1]
        self.assertEqual([p.gamma_eur_per_hz_mw_s for p in responses], [37., 37.])
        self.assertEqual([p.gamma_eur_per_hz_mw_s for p in block.response_parameters], [10, 90])
        self.assertEqual([p.reserve_mw for p in responses], [p.reserve_mw for p in block.response_parameters])

    def test_cli_tso_optimization_weight_and_conflicting_modes(self):
        with (patch("sys.argv", ["run_fcr", "--scenario", "fi_endogenous", "--tso-optimize",
                                "--frequency-weight", "1234"]),
              patch.object(experiment, "run_tso_optimization") as optimize):
            experiment.main()
        self.assertEqual(optimize.call_args.args[-1], 1234.)
        for flags in (("--tso-optimize", "--compare"), ("--tso-optimize", "--tso-sweep"),
                      ("--frequency-weight", "1234")):
            with patch("sys.argv", ["run_fcr", *flags]), patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                experiment.main()

    def test_nested_banks_change_the_design_optimum_without_resimulating_prefix(self):
        p = replace(provider("fi", 20), participation="fi", fi_gamma_eur_per_hz_mw_s=0., fi_rho_eur_per_mw2_s=1.)
        cleared = clear_fcr_block((p,), requirement_mw=0., nominal_frequency_hz=60.)
        design = pd.DataFrame({"sample": [0, 1, 2], "fi": [.5, 1.5, 4.]})
        settings = {**self.config["tso_optimizer"], "frequency_weight_eur_per_hz2_s": 1., "gamma_max": 100.}

        def synthetic_trajectory(block, parameters, gamma, options, **kwargs):
            # The quadratic's exact minimum moves from 20 (first two draws)
            # to 40 (all three): this tests the sampling/cache layer only.
            return {"frequency_error_hz2_s": (gamma - 20 * block.settlements[0].available_mw) ** 2,
                    "net_payment_eur": 0.}

        with (patch("functional_incentives.tso.simulate_common_gamma", side_effect=synthetic_trajectory) as simulate,
              patch("functional_incentives.tso.summarize_tso_trajectory", side_effect=lambda value, event: value),
              patch("functional_incentives.tso._check_refinement", return_value={})):
            selections, outcomes, checks = optimize_nested_gamma_banks(
                cleared, None, design=design, sample_sizes=[2, 3], settings=settings,
                simulation_options={"start_time_s": 1.})
        self.assertLess(abs(selections[0]["gamma_selected"] - 20.), 1.)
        self.assertLess(abs(selections[1]["gamma_selected"] - 40.), 1.)
        self.assertEqual(simulate.call_count, len(outcomes) + 2 * len(checks))
        self.assertFalse(outcomes.duplicated(["gamma", "sample"]).any())

    def test_stability_paired_objective_preserves_covariance_and_matching(self):
        rows = []
        for gamma in (50., 60.):
            for sample in range(4):
                rows.append(dict(bank="holdout", sample=sample, gamma=gamma,
                                 frequency_error_hz2_s=sample + gamma / 10,
                                 net_payment_eur=100 * sample - 2 * gamma / 10 + (gamma == 60)))
        data = pd.DataFrame(rows)
        summary = summarize_paired_tso_objectives(data, 2., 50.).set_index("gamma")
        self.assertEqual(summary.loc[60., "delta_objective_eur_mean"], 1.)
        self.assertEqual(summary.loc[60., "delta_objective_eur_se"], 0.)
        self.assertGreater(summary.loc[60., "objective_eur_se"], 0.)
        with self.assertRaisesRegex(ValueError, "identical sample"):
            summarize_paired_tso_objectives(data.iloc[:-1], 2., 50.)

    def test_stability_nested_search_holdout_isolation_and_parallel_execution(self):
        config = next(c for n, _, c in experiment.configured_scenarios(self.config) if n == "fi_endogenous")
        config["scenario"].update(final_time_s=1.23, output_time_step_s=.1)
        config["tso_optimizer"].update(grid_points=3, gamma_tolerance=100., max_refinement_rounds=1)
        config["tso_stability"].update(sample_sizes=[2, 3], seeds=[81, 82], holdout_samples=3,
                                      holdout_seed=83, workers=2)
        original = deepcopy(config)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(experiment, "load_operating_point", return_value=self.point), redirect_stdout(io.StringIO()):
                result = experiment.run_tso_stability(config, root / "parallel", 10000.)
            self.assertEqual(config, original)
            path = root / "parallel"
            selections = result["selections"]
            self.assertEqual(len(selections), 4)
            self.assertEqual(result["parallel_workers"], 2)
            self.assertEqual(json.loads((path / "run_status.json").read_text())["status"], "complete")
            self.assertTrue((path / "stability.png").is_file())
            self.assertTrue((path / "stability.pdf").is_file())
            bank = pd.read_csv(path / "design_availability.csv")
            with patch.object(experiment, "load_operating_point", return_value=self.point):
                _, cleared, _ = experiment.prepare_experiment(config)
            for seed in (81, 82):
                expected = make_availability_banks(cleared, {**config["tso_optimizer"], "seed": seed,
                                                            "design_samples": 2, "validation_samples": 2})
                actual = bank[(bank.seed == seed) & (bank["sample"] < 2)].drop(columns="seed").reset_index(drop=True)
                pd.testing.assert_frame_equal(actual, expected[expected.bank == "design"].drop(columns="bank"),
                                              check_exact=False, atol=1e-14, rtol=1e-14)
                outcomes = pd.read_csv(path / f"seed_{seed}" / "design_outcomes.csv")
                self.assertFalse(outcomes.duplicated(["gamma", "sample"]).any())
                seed_selections = [r for r in selections if r["seed"] == seed]
                self.assertLess(len(outcomes), sum(r["candidate_count"] * r["design_samples"] for r in seed_selections))
                for row in seed_selections:
                    prefix = outcomes[outcomes["sample"] < row["design_samples"]]
                    # Replay the unchanged search on each prefix; cached larger
                    # banks must never replace a smaller-N objective average.
                    replay = search_common_gamma(lambda g: prefix[prefix.gamma == g].objective_eur.mean(),
                        {**config["tso_optimizer"], "frequency_weight_eur_per_hz2_s": 10000.})
                    self.assertEqual(replay["gamma_selected"], row["gamma_selected"])
                    self.assertAlmostEqual(replay["design_objective_eur"], row["design_objective_eur"])
            changed = deepcopy(config)
            changed["tso_stability"].update(holdout_seed=84, workers=1)
            with patch.object(experiment, "load_operating_point", return_value=self.point), redirect_stdout(io.StringIO()):
                serial = experiment.run_tso_stability(changed, root / "serial", 10000.)
            self.assertEqual([r["gamma_selected"] for r in selections],
                             [r["gamma_selected"] for r in serial["selections"]])
            self.assertEqual([r["design_objective_eur"] for r in selections],
                             [r["design_objective_eur"] for r in serial["selections"]])
            self.assertFalse(pd.read_csv(path / "holdout_availability.csv").equals(
                pd.read_csv(root / "serial" / "holdout_availability.csv")))
            with self.assertRaisesRegex(ValueError, "must be empty"):
                experiment.run_tso_stability(config, path, 10000.)
            with (patch.object(experiment, "load_operating_point", return_value=self.point),
                  patch("functional_incentives.tso.simulate_common_gamma", side_effect=RuntimeError("failed solver")),
                  self.assertRaisesRegex(RuntimeError, "design failed")):
                experiment.run_tso_stability(changed, root / "failed", 10000.)
            self.assertEqual(json.loads((root / "failed" / "run_status.json").read_text())["status"], "failed")

    def test_cli_tso_stability_and_invalid_seed_settings(self):
        with (patch("sys.argv", ["run_fcr", "--scenario", "fi_endogenous", "--tso-stability",
                                "--frequency-weight", "10000"]),
              patch.object(experiment, "run_tso_stability") as study):
            experiment.main()
        self.assertEqual(study.call_args.args[2], 10000.)
        for override in (("--compare",), ("--seed", "1"), ("--tso-optimize",)):
            with patch("sys.argv", ["run_fcr", "--tso-stability", *override]), patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                experiment.main()
        for invalid in ({"sample_sizes": [8, 8]}, {"seeds": [1, 1]}, {"holdout_seed": 20261015},
                        {"holdout_samples": 1}, {"workers": 0}, {"reference_gamma": 301}):
            config = deepcopy(self.config)
            config["tso_stability"].update(invalid)
            with self.assertRaises(ValueError):
                experiment.run_tso_stability(config, Path("unused"), 10000.)

    def test_tso_report_matches_draws_and_exports_delivery_and_validation_separately(self):
        config = next(c for n, _, c in experiment.configured_scenarios(self.config) if n == "fi_endogenous")
        config["scenario"].update(final_time_s=1.43, output_time_step_s=.1)
        config["tso_optimizer"].update(design_samples=2, validation_samples=2, grid_points=3,
                                        gamma_tolerance=100., max_refinement_rounds=1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = [root / "w5000", root / "w20000"]
            with patch.object(experiment, "load_operating_point", return_value=self.point), redirect_stdout(io.StringIO()):
                for path, weight in zip(runs, (5000., 20000.), strict=True):
                    experiment.run_tso_optimization(config, path, frequency_weight=weight)
                report = experiment.run_tso_report(runs[::-1], root / "report")
            self.assertEqual(report["weights"], [5000., 20000.])
            self.assertEqual(report["reference_evaluation"]["simulations"], 3)
            self.assertEqual(len(list((root / "report").glob("*.png"))), 4)
            self.assertEqual(len(list((root / "report").glob("*.pdf"))), 4)
            estimates = pd.read_csv(root / "report" / "comparison_summary.csv")
            self.assertEqual(len(estimates), 4)
            self.assertEqual(estimates.groupby("bank").samples.first().to_dict(), {"delivery": 1, "validation": 2})
            baseline = pd.read_csv(root / "report" / "baseline_samples.csv")
            self.assertTrue((baseline.net_payment_eur == 0).all())
            paired = pd.read_csv(root / "report" / "paired_baseline_differences.csv")
            for path, weight in zip(runs, (5000., 20000.), strict=True):
                summary = json.loads((path / "summary.json").read_text())
                outcome = next(r for r in summary["selected_estimates"] if r["bank"] == "delivery")
                base = baseline[baseline.bank == "delivery"].iloc[0]
                row = paired[(paired.bank == "delivery") & (paired.frequency_weight_eur_per_hz2_s == weight)].iloc[0]
                self.assertAlmostEqual(row.objective_reduction_eur,
                    weight * (base.frequency_error_hz2_s - outcome["frequency_error_hz2_s_mean"])
                    - outcome["net_payment_eur_mean"], places=10)
            # Never silently overlay runs that changed draws or the market.
            draws = runs[1] / "delivery_availability.json"
            original = draws.read_text()
            changed = json.loads(original)
            changed["renewable_uncertain"] /= 2
            draws.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, "identical availability"):
                experiment.run_tso_report(runs, root / "report")
            self.assertEqual(json.loads((root / "report" / "report_status.json").read_text())["status"], "failed")
            draws.write_text(original)
            (runs[1] / "run_status.json").write_text('{"status": "failed"}')
            with self.assertRaisesRegex(ValueError, "incomplete optimizer run"):
                experiment._load_tso_report_cases(runs)
            with self.assertRaisesRegex(ValueError, "outside individual"):
                experiment.run_tso_report(runs, runs[0])

    def test_cli_tso_report_uses_saved_inputs_and_rejects_overrides(self):
        with (patch("sys.argv", ["run_fcr", "--tso-report", "first", "second", "--output-dir", "report"]),
              patch.object(experiment, "run_tso_report") as report,
              patch.object(experiment, "run_experiment") as single):
            experiment.main()
        self.assertEqual(report.call_args.args, ([Path("first"), Path("second")], Path("report")))
        single.assert_not_called()
        for override in (("--scenario", "fi_endogenous"), ("--seed", "7"), ("--compare",),
                         ("--frequency-weight", "10000"), ("--tso-optimize",)):
            with patch("sys.argv", ["run_fcr", "--tso-report", "first", "second", *override]), patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                experiment.main()

    def test_tso_paired_statistics_frontier_and_settings(self):
        samples = pd.DataFrame([
            {"bank": "design", "sample": j, "gamma": gamma,
             "frequency_error_hz2_s": error + 2*j, "net_payment_eur": payment + (2*j if gamma else 0)}
            for gamma, error, payment in [(0, 5, 0), (1, 4, 2), (2, 4, 4)] for j in range(2)])
        summary = summarize_tso_samples(samples)
        self.assertEqual(summary.empirical_pareto.tolist(), [True, True, False])
        self.assertEqual(summary.paired_error_reduction_hz2_s_mean.tolist(), [0, 1, 1])
        self.assertTrue((summary.paired_error_reduction_hz2_s_se == 0).all())
        for changes in ({"gamma_values": [1, 2]}, {"gamma_values": [0, 0]},
                        {"gamma_values": [0, np.inf]}, {"design_samples": 1}, {"seed": -1},
                        {"validation_samples": 2.5}):
            with self.assertRaises(ValueError):
                validate_sweep_settings({**self.config["tso"], **changes})
        with self.assertRaisesRegex(ValueError, "same samples"):
            summarize_tso_samples(samples.iloc[:-1])

    def test_tso_precision_resolves_fcr_near_saturation(self):
        # Regression: the first sweep found a 1.9e-8 MW dense-output overshoot
        # at 5.55 s for the reliable FCR converter, with rtol=1e-9/atol=1e-11.
        config = next(c for n, _, c in experiment.configured_scenarios(self.config) if n == "fi_endogenous")
        with patch.object(experiment, "load_operating_point", return_value=self.point):
            _, cleared, parameters = experiment.prepare_experiment(config)
        draws = {"conventional_1": 3.9611613307769673, "conventional_2": 3.9679409050732937,
                 "conventional_3": 3.990874541049296, "renewable_reliable": 1.7575360723670346,
                 "renewable_uncertain": 2.0820261771717643}
        block = realize_fcr_block(cleared, draws)
        result = simulate_fcr_power_step(parameters, block.response_parameters,
            power_deficit_mw=10, start_time_s=1, final_time_s=8, output_time_step_s=.05,
            maximum_solver_step_s=.05, fi_decision_interval_s=.1,
            relative_tolerance=1e-11, absolute_tolerance=1e-13)
        self.assertTrue(np.all(abs(result.provider_response_mw) <= result.provider_reserve_mw + 1e-8))
        self.assertGreater(summarize_tso_trajectory(result, 1)["frequency_error_hz2_s"], 0)

    def test_cli_selects_tso_curve_without_single_event_reporting(self):
        with (patch("sys.argv", ["run_fcr", "--scenario", "fi_endogenous", "--tso-sweep"]),
              patch.object(experiment, "run_tso_experiment") as sweep,
              patch.object(experiment, "run_experiment") as single):
            experiment.main()
        self.assertEqual(sweep.call_count, 1)
        self.assertEqual(single.call_count, 0)

    def test_current_participation_outcome_and_parameter_comparisons(self):
        base = self.prepare(self.config)
        quantities = [d.offer.quantity_mw for d in base.decisions]
        self.assertTrue(all(3.9 < q < 4 for q in quantities[:3]))
        self.assertTrue(1 < quantities[3] < 2)
        self.assertEqual(quantities[-1], 0)
        self.assertAlmostEqual(sum(a.quantity_mw for a in base.market.awards), 12)
        self.assertEqual(base.market.clearing_price_eur_per_mw_block, 24)
        scenarios = experiment.configured_scenarios(self.config)
        self.assertEqual([name for name, _, _ in scenarios],
                         ["base", "lower_uncertainty", "fi_exogenous", "fi_endogenous", "fi_strong"])
        for name, _, changed in scenarios[1:]:
            block = self.prepare(changed)
            if name == "lower_uncertainty":
                self.assertAlmostEqual(block.decisions[-1].offer.quantity_mw, quantities[3])
            else:
                self.assertEqual(block.participation_modes[-1], "fi")
                self.assertEqual([a.quantity_mw for a in block.market.awards],
                                 [a.quantity_mw for a in base.market.awards])
                self.assertIsInstance(block.response_parameters[-1], FIProviderParameters)
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

    def test_fi_runner_saves_separate_finances_and_replayable_fixed_gamma(self):
        config = next(c for name, _, c in experiment.configured_scenarios(self.config) if name == "fi_endogenous")
        config["scenario"].update(final_time_s=2.03, output_time_step_s=.3, maximum_solver_step_s=.04)
        config["fi"]["decision_interval_s"] = .17
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with patch.object(experiment, "load_operating_point", return_value=self.point), redirect_stdout(io.StringIO()):
                summary = experiment.run_experiment(config, path)
            outcomes = pd.read_csv(path/"fi_outcomes.csv")
            intervals = pd.read_csv(path/"fi_decisions.csv")
            series = pd.read_csv(path/"fi_time_series.csv")
            market = pd.read_csv(path/"market_outcomes.csv")
            self.assertEqual(outcomes.provider_id.tolist(), ["renewable_uncertain"])
            self.assertEqual(market.participation.tolist(), ["fcr"]*4 + ["fi"])
            self.assertEqual(market.profit_eur.iloc[-1], 0)
            self.assertGreater(outcomes.profit_eur.iloc[0], 0)
            self.assertEqual(summary["fi"]["provider_count"], 1)
            self.assertAlmostEqual(summary["fi"]["profit_eur"], outcomes.profit_eur.sum())
            self.assertAlmostEqual(outcomes.profit_eur.sum(), intervals.profit_eur.sum())
            self.assertAlmostEqual(series.total_profit_eur.iloc[-1], outcomes.profit_eur.sum())
            self.assertEqual(intervals.end_time_s.iloc[-1], 2.03)
            effective = json.loads((path/"effective_config.json").read_text())
            self.assertEqual(effective["providers"][-1]["fi_gamma_eur_per_hz_mw_s"], 50)
            self.assertGreater((path/"fi_financials.png").stat().st_size, 0)

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
        by_name = {name: variant for name, _, variant in variants}
        self.assertEqual(by_name["lower_uncertainty"]["providers"][-1]["variance_mw2"], .04)
        self.assertEqual(by_name["fi_exogenous"]["providers"][-1]["variance_mw2"], 4)

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
        with (patch("sys.argv", ["run_fcr", "--scenario", "lower_uncertainty"]),
              patch.object(experiment, "run_experiment") as run):
            experiment.main()
        self.assertEqual(run.call_count, 1)
        changed = run.call_args.args[0]
        expected = deepcopy(self.config["providers"])
        expected[-1]["variance_mw2"] = .04
        self.assertEqual(changed["providers"], expected)
        self.assertEqual(changed["market"], self.config["market"])

    def test_obsolete_grids_require_explicit_config_migration(self):
        self.config["providers"][0]["quantity_candidates_mw"] = [0, 1]
        with self.assertRaisesRegex(ValueError, "candidate grids are obsolete"):
            experiment.load_block_providers(self.config, self.point)
