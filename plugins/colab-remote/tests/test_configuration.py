import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from colab_remote import config_io, configuration


class ConfigurationTests(unittest.TestCase):
    def test_packaged_schema_matches_plugin_schema(self):
        plugin_schema = Path(__file__).resolve().parents[1] / "config_schema.json"
        self.assertEqual(
            json.loads(plugin_schema.read_text(encoding="utf-8")),
            configuration.DOCUMENTATION,
        )

    def test_every_documented_default_normalizes(self):
        self.assertEqual(
            configuration.normalize(configuration.DEFAULT_CONFIG),
            configuration.DEFAULT_CONFIG,
        )

    def test_invalid_enum_boolean_integer_runtime_and_drive_path_fail_closed(self):
        cases = (
            ("default_accelerator", "v100"),
            ("default_language", "ruby"),
            ("notification_mode", "sometimes"),
            ("default_high_ram", "true"),
            ("retry_attempts", 0),
            ("default_runtime_version", "old"),
            ("default_drive_checkpoint_folder", "../outside"),
        )
        for name, value in cases:
            with self.subTest(name=name), self.assertRaises(ValueError):
                configuration.normalize(
                    {**configuration.DEFAULT_CONFIG, name: value}
                )

    def test_jsonc_round_trip_and_atomic_save(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"COLAB_REMOTE_STATE_DIR": directory},
        ):
            saved = configuration.save(
                {
                    **configuration.DEFAULT_CONFIG,
                    "notification_mode": "failures_only",
                }
            )
            path = Path(directory) / "config.jsonc"
            self.assertTrue(path.is_file())
            self.assertFalse(path.with_suffix(".tmp").exists())
            self.assertEqual(
                config_io.loads(path.read_text(encoding="utf-8")),
                saved,
            )
            self.assertEqual(configuration.load(), saved)

    def test_allowed_roots_must_be_existing_absolute_directories(self):
        with self.assertRaises(ValueError):
            configuration.normalize(
                {
                    **configuration.DEFAULT_CONFIG,
                    "allowed_local_roots": ["relative"],
                }
            )

    def test_value_parser_never_interprets_strings_as_code(self):
        self.assertEqual(
            configuration.parse_value("default_accelerator", "__import__('os')"),
            "__import__('os')",
        )
        with self.assertRaises(ValueError):
            configuration.parse_value("allowed_local_roots", "__import__('os')")


if __name__ == "__main__":
    unittest.main()
