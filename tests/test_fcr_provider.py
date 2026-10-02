import tempfile
import unittest
from pathlib import Path

import numpy as np

from functional_incentives.providers.fcr import (
    FCRProviderParameters,
    load_fcr_provider_parameters,
)


class FCRProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = FCRProviderParameters(
            provider_id="provider_1",
            gain_mw_per_hz=20.0,
            time_constant_s=2.0,
            reserve_mw=3.0,
        )

    def test_droop_sign_and_symmetric_saturation(self) -> None:
        self.assertTrue(np.isclose(self.provider.requested_response_mw(-0.1), 2.0))
        self.assertTrue(np.isclose(self.provider.requested_response_mw(0.1), -2.0))
        self.assertTrue(np.isclose(self.provider.requested_response_mw(-1.0), 3.0))
        self.assertTrue(np.isclose(self.provider.requested_response_mw(1.0), -3.0))

    def test_first_order_derivative_moves_toward_requested_response(self) -> None:
        derivative = self.provider.response_derivative_mw_per_s(
            response_mw=1.0,
            delta_frequency_hz=-0.1,
        )
        self.assertTrue(np.isclose(derivative, 0.5))

    def test_invalid_parameters_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            FCRProviderParameters("provider", 1.0, 0.0, 1.0)
        with self.assertRaises(ValueError):
            FCRProviderParameters("provider", -1.0, 1.0, 1.0)
        with self.assertRaises(ValueError):
            FCRProviderParameters("provider", 1.0, 1.0, -1.0)

    def test_non_finite_parameters_are_rejected(self) -> None:
        valid_parameters = {
            "gain_mw_per_hz": 20.0,
            "time_constant_s": 2.0,
            "reserve_mw": 3.0,
        }
        for field in valid_parameters:
            for value in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(field=field, value=value):
                    parameters = {**valid_parameters, field: value}
                    with self.assertRaises(ValueError):
                        FCRProviderParameters("provider", **parameters)

    def test_toml_loader_rejects_non_finite_parameters(self) -> None:
        valid_values = {
            "gain_mw_per_hz": "20.0",
            "time_constant_s": "2.0",
            "reserve_mw": "3.0",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "providers.toml"
            for field in valid_values:
                for value in ("nan", "inf", "-inf"):
                    with self.subTest(field=field, value=value):
                        values = {**valid_values, field: value}
                        toml_text = '\n[[providers]]\nprovider_id = "provider"\n'
                        toml_text += "\n".join(
                            f"{key} = {item}" for key, item in values.items()
                        )
                        path.write_text(toml_text, encoding="utf-8")
                        with self.assertRaises(ValueError):
                            load_fcr_provider_parameters(path)

    def test_toml_loader_rejects_duplicate_provider_ids(self) -> None:
        toml_text = b"""
[[providers]]
provider_id = "same"
gain_mw_per_hz = 1.0
time_constant_s = 1.0
reserve_mw = 1.0
[[providers]]
provider_id = "same"
gain_mw_per_hz = 2.0
time_constant_s = 2.0
reserve_mw = 2.0
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "providers.toml"
            path.write_bytes(toml_text)
            with self.assertRaises(ValueError):
                load_fcr_provider_parameters(path)

    def test_teaching_configuration_defines_four_providers(self) -> None:
        config_path = (
            Path(__file__).resolve().parents[1]
            / "configs"
            / "experiments"
            / "pjm5_fcr_teaching.toml"
        )
        providers = load_fcr_provider_parameters(config_path)

        self.assertEqual(len(providers), 4)
        np.testing.assert_allclose(
            [provider.gain_mw_per_hz for provider in providers],
            [10.0, 15.0, 20.0, 25.0],
        )
        np.testing.assert_allclose(
            [provider.time_constant_s for provider in providers],
            [0.4, 0.8, 1.5, 3.0],
        )
        np.testing.assert_allclose(
            [provider.reserve_mw for provider in providers],
            [1.0, 2.0, 3.0, 4.0],
        )


if __name__ == "__main__":
    unittest.main()
