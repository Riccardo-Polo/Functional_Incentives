import unittest

import numpy as np

from functional_incentives.grid.andes_adapter import solve_pjm5_operating_point


class AndesAdapterTests(unittest.TestCase):
    def test_pjm5_operating_point_and_dynamic_parameters(self) -> None:
        operating_point = solve_pjm5_operating_point()

        self.assertTrue(np.isclose(operating_point.nominal_frequency_hz, 60.0))
        self.assertTrue(np.isclose(operating_point.total_load_mw, 1000.0))
        self.assertGreater(
            operating_point.total_generation_mw, operating_point.total_load_mw
        )
        self.assertTrue(
            np.isclose(
                operating_point.network_losses_mw,
                operating_point.total_generation_mw - operating_point.total_load_mw,
            )
        )
        self.assertTrue(
            np.isclose(operating_point.total_synchronous_rating_mva, 1000.0)
        )
        self.assertTrue(np.isclose(operating_point.equivalent_inertia_s, 2.0))


if __name__ == "__main__":
    unittest.main()
