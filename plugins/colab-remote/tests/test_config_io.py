import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "mcp" / "config_io.py"
SPEC = importlib.util.spec_from_file_location("colab_remote_config_io", MODULE_PATH)
config_io = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(config_io)


class ConfigIoTests(unittest.TestCase):
    def test_comments_trailing_commas_and_comment_like_strings_parse_safely(self):
        value = config_io.loads(
            r'''{
              // line comment
              "url": "https://example.com/a//b",
              "pattern": "/* still text */",
              /* block
                 comment */
              "items": [1, 2,],
            }'''
        )
        self.assertEqual(value["url"], "https://example.com/a//b")
        self.assertEqual(value["pattern"], "/* still text */")
        self.assertEqual(value["items"], [1, 2])

    def test_unterminated_block_comment_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unterminated"):
            config_io.loads('{"value": 1 /*')

    def test_renderer_places_help_above_setting_and_round_trips(self):
        documentation = {
            "settings": {
                "mode": {
                    "description": "Select the mode.",
                    "type": "string",
                    "default": "off",
                    "allowed": ["off", "all"],
                }
            }
        }
        rendered = config_io.render({"mode": "all"}, documentation)
        self.assertIn("// Select the mode.\n", rendered)
        self.assertIn('"mode": "all"', rendered)
        self.assertEqual(config_io.loads(rendered), {"mode": "all"})


if __name__ == "__main__":
    unittest.main()
