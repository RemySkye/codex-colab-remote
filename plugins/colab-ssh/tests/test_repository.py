import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parents[1]


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

    def test_auth_handoff_uses_private_permissions(self):
        start = (ROOT / "scripts" / "start_colab_auth.sh").read_text(encoding="utf-8")
        submit = (ROOT / "scripts" / "submit_colab_auth.sh").read_text(encoding="utf-8")
        self.assertIn("umask 077", start)
        self.assertIn("chmod 700", start)
        self.assertIn("chmod 600", start)
        self.assertIn("chmod 600", submit)
        self.assertNotIn("/tmp/colab-auth", start + submit)

    def test_smoke_test_verifies_cleanup(self):
        installer = (REPOSITORY_ROOT / "install.ps1").read_text(encoding="utf-8")
        self.assertIn("$stopExitCode", installer)
        self.assertIn("$sessionListing", installer)
        self.assertIn("cleanup could not be verified", installer)

    def test_repository_is_a_native_codex_marketplace(self):
        path = REPOSITORY_ROOT / ".agents" / "plugins" / "marketplace.json"
        marketplace = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(marketplace["name"], "colab-remote")
        entry = next(item for item in marketplace["plugins"] if item["name"] == "colab-ssh")
        self.assertEqual(entry["source"]["path"], "./plugins/colab-ssh")

    def test_bootstrap_uses_native_codex_install_commands(self):
        installer = (REPOSITORY_ROOT / "install.ps1").read_text(encoding="utf-8")
        self.assertIn("plugin marketplace add", installer)
        self.assertIn('plugin add "$Plugin@$Marketplace"', installer)
        self.assertIn("$SkipAuthentication", installer)


if __name__ == "__main__":
    unittest.main()
