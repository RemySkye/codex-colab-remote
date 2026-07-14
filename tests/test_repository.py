import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepositoryTests(unittest.TestCase):
    def test_portable_validator_passes(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "validate_repo.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_mcp_uses_portable_launcher(self):
        config = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
        server = config["mcpServers"]["colab-ssh"]
        self.assertEqual(server["command"], "powershell")
        self.assertTrue(any("run_mcp.ps1" in value for value in server["args"]))

    def test_runtime_scripts_do_not_pin_usernames(self):
        paths = [
            ROOT / "scripts" / "colab.ps1",
            ROOT / "scripts" / "finish_colab_auth.ps1",
            ROOT / "scripts" / "runtime.ps1",
            ROOT / "skills" / "operate-colab-ssh" / "SKILL.md",
        ]
        combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        self.assertNotIn("/home/" + "administrator", combined)
        self.assertIn("Get-ColabRemoteDistro", combined)


if __name__ == "__main__":
    unittest.main()
