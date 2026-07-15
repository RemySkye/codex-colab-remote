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
        server = config["mcpServers"]["colab-remote"]
        self.assertEqual(server["command"], "powershell")
        self.assertTrue(any("run_mcp.ps1" in value for value in server["args"]))

    def test_no_legacy_auth_handoff_helpers(self):
        for relative in (
            "assets/bootstrap_colab.py.tmpl",
            "scripts/start_colab_auth.sh",
            "scripts/submit_colab_auth.sh",
            "scripts/finish_colab_auth.ps1",
        ):
            self.assertFalse((ROOT / relative).exists(), relative)

    def test_cli_wrapper_forces_oauth2_and_clears_adc(self):
        wrapper = (ROOT / "scripts" / "colab.ps1").read_text(encoding="utf-8")
        self.assertIn("'--auth', 'oauth2'", wrapper)
        self.assertIn("GOOGLE_APPLICATION_CREDENTIALS", wrapper)
        self.assertIn("CLOUDSDK_CONFIG", wrapper)

    def test_smoke_test_verifies_cleanup(self):
        installer = (REPOSITORY_ROOT / "install.ps1").read_text(encoding="utf-8")
        self.assertIn("$stopExitCode", installer)
        self.assertIn("$sessionListing", installer)
        self.assertIn("cleanup could not be verified", installer)

    def test_repository_is_a_native_codex_marketplace(self):
        marketplace = json.loads(
            (REPOSITORY_ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8")
        )
        entry = next(item for item in marketplace["plugins"] if item["name"] == "colab-remote")
        self.assertEqual(entry["source"]["path"], "./plugins/colab-remote")

    def test_bootstrap_uses_native_codex_install_commands(self):
        installer = (REPOSITORY_ROOT / "install.ps1").read_text(encoding="utf-8")
        self.assertIn("plugin marketplace add", installer)
        self.assertIn('plugin add "$Plugin@$Marketplace"', installer)
        self.assertIn("$SkipAuthentication", installer)
        self.assertIn("umask 077", installer)
        self.assertIn("chmod 600", installer)
        self.assertIn("$EnableSshTunnel", installer)


if __name__ == "__main__":
    unittest.main()
