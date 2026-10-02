import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = PROJECT_ROOT / "scripts" / "run_pjm5_fcr_teaching.py"
RUNNER_SPEC = importlib.util.spec_from_file_location("fcr_teaching_runner", RUNNER_PATH)
assert RUNNER_SPEC is not None and RUNNER_SPEC.loader is not None
runner = importlib.util.module_from_spec(RUNNER_SPEC)
RUNNER_SPEC.loader.exec_module(runner)


class FCRTeachingRunnerTests(unittest.TestCase):
    def test_custom_horizon_labels_and_csv_use_actual_sample_times(self) -> None:
        config_text = runner.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
        config_text = config_text.replace("final_time_s = 40.0", "final_time_s = 2.0")
        config_text = config_text.replace(
            "output_time_step_s = 0.05", "output_time_step_s = 0.3"
        )
        # The adapter has its own integration test. Use a fixed operating point
        # here while exercising the real simulators, CSV writer and plotting.
        operating_point = SimpleNamespace(
            generator_active_power_mw=(210.0, 323.49, 466.51, 3.94),
            total_load_mw=1000.0,
            nominal_frequency_hz=60.0,
            equivalent_inertia_s=2.0,
            total_synchronous_rating_mva=1000.0,
        )
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "scenario.toml"
            output_dir = Path(directory) / "results"
            config_path.write_text(config_text, encoding="utf-8")
            stdout = io.StringIO()
            with (
                patch.object(runner, "solve_pjm5_operating_point", return_value=operating_point),
                patch(
                    "sys.argv",
                    [
                        str(RUNNER_PATH),
                        "--config",
                        str(config_path),
                        "--output-dir",
                        str(output_dir),
                    ],
                ),
                redirect_stdout(stdout),
            ):
                runner.main()

            output = stdout.getvalue()
            self.assertIn("No-FCR frequency at 2 s:", output)
            self.assertIn("FCR frequency at 2 s:", output)
            self.assertEqual(output.count("u(2 s)="), 4)
            self.assertEqual(output.count("P(2 s)="), 4)
            self.assertNotIn("40 s", output)

            table = pd.read_csv(output_dir / "time_series.csv")
            self.assertEqual(len(table), 8)
            self.assertEqual(table["time_s"].iloc[-1], 2.0)
            self.assertTrue(np.all(np.diff(table["time_s"]) > 0.0))
            self.assertTrue(np.all(table["time_s"] <= 2.0))
            # Evaluate the independent formula at the CSV timestamps. Merely
            # comparing columns would miss assigning both to the wrong times.
            elapsed_s = np.maximum(table["time_s"].to_numpy() - 1.0, 0.0)
            damping_mw_per_hz = 1000.0 / 60.0
            frequency_mass_mw_s_per_hz = 2.0 * 2.0 * 1000.0 / 60.0
            expected_no_fcr_hz = 60.0 - (10.0 / damping_mw_per_hz) * (
                -np.expm1(-damping_mw_per_hz * elapsed_s / frequency_mass_mw_s_per_hz)
            )
            for column in (
                "frequency_no_fcr_analytical_hz",
                "frequency_no_fcr_numerical_hz",
            ):
                with self.subTest(column=column):
                    np.testing.assert_allclose(
                        table[column], expected_no_fcr_hz, atol=1e-9, rtol=0.0
                    )
            for filename in ("frequency_comparison.png", "provider_responses.png"):
                self.assertGreater((output_dir / filename).stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
