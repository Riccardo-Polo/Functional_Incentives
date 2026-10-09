import tomllib
import unittest
from dataclasses import replace

import numpy as np

from functional_incentives.experiment import DEFAULT_CONFIG_PATH, load_operating_point
from functional_incentives.grid import FleetGenerator, solve_operating_point


class OperatingPointTests(unittest.TestCase):
    def test_configured_fleet_has_five_generators_and_only_three_inertial_machines(self):
        config = tomllib.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        point = load_operating_point(config)
        ratings = dict(zip(point.generator_ids, point.generator_ratings_mva))
        self.assertEqual(len(ratings), 5)
        self.assertEqual(ratings["renewable_uncertain"], 150)
        self.assertEqual(ratings["conventional_3"], 400)
        np.testing.assert_allclose(point.synchronous_ratings_mva, [300, 400, 400])
        self.assertAlmostEqual(point.equivalent_inertia_s, 4700/1100)
        self.assertAlmostEqual(point.total_generation_mw-point.total_load_mw, point.network_losses_mw)
        self.assertAlmostEqual(point.total_load_mw, 1000)
        self.assertAlmostEqual(point.nominal_frequency_hz, 60)

    def test_bundled_fleet_is_a_parameter_choice_in_the_same_adapter(self):
        point = solve_operating_point()
        self.assertEqual(point.generator_ids, (0, 2, 4, 3))
        np.testing.assert_allclose(point.generator_ratings_mva, [200, 300, 300, 200])
        self.assertAlmostEqual(point.equivalent_inertia_s, 2)

    def test_renewable_cannot_silently_add_inertia_or_become_slack(self):
        renewable = FleetGenerator("r", 1, "renewable", 150, 90, 0, 0, 120)
        for kwargs in ({"inertia_s": 4}, {"is_slack": True}, {"rating_mva": 0},
                       {"power_setpoint_mw": 150}):
            with self.assertRaises(ValueError):
                replace(renewable, **kwargs)
