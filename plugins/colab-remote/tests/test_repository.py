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
        self.assertEqual(
            server,
            {
                "command": "uv",
                "args": ["run", "--project", ".", "python", "./mcp/server.py"],
                "cwd": ".",
            },
        )
        self.assertNotIn("${", json.dumps(server))
        self.assertTrue((ROOT / "mcp" / "server.py").is_file())

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

    def test_native_colab_kernels_are_used_for_all_languages(self):
        compatibility = (ROOT / "scripts" / "colab_compat.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"python": "python3"', compatibility)
        self.assertIn('"r": "ir"', compatibility)
        self.assertIn('"julia": "julia"', compatibility)
        runtime_sources = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in (
                ROOT / "mcp" / "server.py",
                ROOT / "scripts" / "colab_compat.py",
                ROOT / "skills" / "operate-colab-remote" / "SKILL.md",
            )
        )
        self.assertNotIn("install.julialang.org", runtime_sources)
        self.assertNotIn("Juliaup", runtime_sources)

    def test_skill_probes_deferred_tools_before_reporting_registration_failure(self):
        skill = (ROOT / "skills" / "operate-colab-remote" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("defer MCP tool definitions", skill)
        self.assertIn("Do not claim that Colab tools are unregistered", skill)
        self.assertIn("list_sessions", skill)

    def test_smoke_test_verifies_cleanup(self):
        installer = (REPOSITORY_ROOT / "install.py").read_text(encoding="utf-8")
        self.assertIn('self.colab_command(["stop", "-s", session])', installer)
        self.assertIn('self.colab_command(["sessions"])', installer)
        self.assertIn("smoke-test cleanup could not be verified", installer)

    def test_repository_is_a_native_codex_marketplace(self):
        marketplace = json.loads(
            (REPOSITORY_ROOT / ".agents" / "plugins" / "marketplace.json").read_text(
                encoding="utf-8"
            )
        )
        entry = next(
            item for item in marketplace["plugins"] if item["name"] == "colab-remote"
        )
        self.assertEqual(entry["source"]["path"], "./plugins/colab-remote")
        self.assertEqual(
            (REPOSITORY_ROOT / entry["source"]["path"]).resolve(), ROOT.resolve()
        )

    def test_manifest_companion_paths_resolve_inside_plugin(self):
        manifest = json.loads(
            (ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        for field in ("skills", "mcpServers"):
            reference = manifest[field]
            self.assertTrue(reference.startswith("./"), reference)
            target = (ROOT / reference).resolve()
            self.assertTrue(target.is_relative_to(ROOT.resolve()), target)
            self.assertTrue(target.exists(), target)

    def test_bootstrap_uses_safe_codex_install_and_update_commands(self):
        installer = (REPOSITORY_ROOT / "install.py").read_text(encoding="utf-8")
        self.assertIn(
            '["codex", "plugin", "marketplace", "add", self.marketplace_source]',
            installer,
        )
        self.assertIn(
            '["codex", "plugin", "marketplace", "upgrade", MARKETPLACE]',
            installer,
        )
        self.assertIn(
            '["codex", "plugin", "add", f"{PLUGIN}@{MARKETPLACE}"]', installer
        )
        self.assertIn("plugin_is_installed", installer)
        self.assertIn("Preserving existing Google Colab authentication", installer)
        self.assertIn("skip_authentication", installer)
        self.assertIn("chmod", installer)
        self.assertIn("enable_ssh", installer)

    def test_posix_bootstrap_is_pinned_and_secure(self):
        shared = (REPOSITORY_ROOT / "install.py").read_text(encoding="utf-8")
        launcher = (REPOSITORY_ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('UV_VERSION = "0.11.28"', shared)
        self.assertIn('COLAB_CLI_VERSION = "0.6.0"', shared)
        self.assertIn("sha256", shared)
        self.assertIn('"GOOGLE_APPLICATION_CREDENTIALS"', shared)
        self.assertIn('python3 "$installer" "$@"', launcher)
        self.assertLess(len(launcher.splitlines()), 50)

    def test_windows_launcher_delegates_to_shared_installer(self):
        launcher = (REPOSITORY_ROOT / "install.ps1").read_text(encoding="utf-8")
        self.assertIn("install.py", launcher)
        self.assertIn("@args", launcher)
        self.assertLess(len(launcher.splitlines()), 60)

    def test_cross_platform_documentation_exists(self):
        for relative in (
            "docs/installation.md",
            "docs/architecture.md",
            "docs/configuration.md",
            "docs/tools.md",
            "docs/troubleshooting.md",
            "docs/development.md",
            "docs/roadmap.md",
            "wiki/Home.md",
        ):
            self.assertTrue((REPOSITORY_ROOT / relative).is_file(), relative)

    def test_posix_one_liner_is_short_and_interactive(self):
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        command = (
            'bash -c "$(curl -fsSL '
            'https://raw.githubusercontent.com/RemySkye/codex-colab-remote/main/install.sh)"'
        )
        self.assertEqual(readme.count(command), 2)
        self.assertNotIn('tmp="$(mktemp)"', readme)
        self.assertNotIn(
            "curl -fsSL https://raw.githubusercontent.com/RemySkye/codex-colab-remote/main/install.sh | bash",
            readme,
        )


if __name__ == "__main__":
    unittest.main()
