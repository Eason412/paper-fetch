import json
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402


class ConfigTests(unittest.TestCase):
    def test_defaults_use_desktop_papers_and_safe_institutional_limits(self):
        resolved = config.resolve_config({}, {})

        self.assertEqual(
            resolved["output_dir"], Path.home() / "Desktop" / "Papers"
        )
        self.assertEqual(resolved["oa_delay"], 1.0)
        self.assertFalse(resolved["institutional"])
        self.assertEqual(resolved["inst_delay"], 4.0)
        self.assertEqual(resolved["inst_jitter"], 3.0)
        self.assertEqual(resolved["max_institutional"], 30)

    def test_cli_values_override_file_values_one_key_at_a_time(self):
        file_values = {
            "output_dir": "/tmp/from-config",
            "oa_delay": 5,
            "institutional": True,
        }
        resolved = config.resolve_config(
            file_values,
            {"output_dir": Path("/tmp/from-cli"), "oa_delay": 2},
        )

        self.assertEqual(resolved["output_dir"], Path("/tmp/from-cli"))
        self.assertEqual(resolved["oa_delay"], 2.0)
        self.assertTrue(resolved["institutional"])

    def test_unknown_and_sensitive_keys_are_ignored_without_echoing_values(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "output_dir": "/tmp/papers",
                        "password": "must-never-be-echoed",
                    }
                ),
                encoding="utf-8",
            )
            warnings = []

            loaded = config.load_config(path, warn=warnings.append)

        self.assertEqual(loaded["output_dir"], "/tmp/papers")
        self.assertNotIn("password", loaded)
        self.assertTrue(any("password" in warning for warning in warnings))
        self.assertFalse(any("must-never-be-echoed" in warning for warning in warnings))

    def test_invalid_institutional_floor_is_rejected_from_file(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps({"version": 1, "inst_delay": 1}),
                encoding="utf-8",
            )

            with self.assertRaises(config.ConfigError) as raised:
                config.load_config(path)

        self.assertIn("inst_delay", str(raised.exception))

    def test_save_is_atomic_mode_0600_and_serializes_only_whitelisted_keys(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "config.json"
            config.save_config(
                path,
                {
                    "output_dir": Path(tmp) / "papers",
                    "institutional": True,
                    "password": "do-not-write",
                },
            )
            payload = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
            self.assertEqual(payload["version"], 1)
            self.assertTrue(payload["institutional"])
            self.assertNotIn("password", payload)
            self.assertFalse(list(path.parent.glob("*.part-*")))

    def test_cli_can_save_standing_preferences_without_a_paper_input(self):
        with TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            output_dir = Path(tmp) / "papers"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "oa_fetch.py"),
                    "--config",
                    str(config_path),
                    "--out",
                    str(output_dir),
                    "--oa-delay",
                    "2",
                    "--institutional",
                    "--inst-delay",
                    "6",
                    "--inst-jitter",
                    "0",
                    "--save-config",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["output_dir"], str(output_dir))
        self.assertEqual(payload["oa_delay"], 2.0)
        self.assertTrue(payload["institutional"])
        self.assertEqual(payload["inst_delay"], 6.0)
        self.assertEqual(payload["inst_jitter"], 0.0)


if __name__ == "__main__":
    unittest.main()
