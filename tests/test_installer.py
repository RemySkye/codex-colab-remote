import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "colab_remote_installer", ROOT / "install.py"
)
installer = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(installer)


def completed(returncode=0, stdout="", stderr=""):
    return installer.subprocess.CompletedProcess([], returncode, stdout, stderr)


class InstallerTests(unittest.TestCase):
    def test_desktop_notifications_are_opt_in(self):
        disabled = installer.Installer(
            installer.parser().parse_args([]), platform="linux"
        )
        enabled = installer.Installer(
            installer.parser().parse_args(["--enable-notifications"]),
            platform="linux",
        )
        self.assertEqual(disabled.default_config()["notification_mode"], "off")
        self.assertEqual(enabled.default_config()["notification_mode"], "all")

    def test_default_config_and_documentation_cover_operational_defaults(self):
        subject = installer.Installer(
            installer.parser().parse_args([]), platform="linux"
        )
        config = subject.default_config()
        self.assertEqual(config["max_concurrent_sessions"], 8)
        self.assertFalse(config["transfer_compression"])
        self.assertEqual(config["transfer_parallelism"], 4)
        self.assertEqual(config["retry_attempts"], 3)
        self.assertEqual(config["default_drive_checkpoint_folder"], "checkpoints")
        self.assertNotIn("_documentation", config)
        self.assertIn(
            "max_concurrent_sessions",
            subject.configuration_documentation()["settings"],
        )

    def test_windows_and_posix_flags_share_one_parser(self):
        windows = installer.parser().parse_args(
            [
                "-Distro",
                "Ubuntu",
                "-DefaultAccelerator",
                "a100",
                "-DefaultLanguage",
                "r",
                "-PreferHighRam",
            ]
        )
        posix = installer.parser().parse_args(
            [
                "--distro",
                "Ubuntu",
                "--default-accelerator",
                "a100",
                "--default-language",
                "r",
                "--high-ram",
            ]
        )
        self.assertEqual(vars(windows), vars(posix))

    def test_config_is_owner_only_on_posix(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            options = installer.parser().parse_args(
                ["--state-root", str(state), "--high-ram", "--enable-ssh"]
            )
            subject = installer.Installer(options, platform="linux")
            subject.write_config()
            config_path = state / "config.jsonc"
            text = config_path.read_text(encoding="utf-8")
            config = installer.parse_jsonc(text)
            self.assertTrue(config["default_high_ram"])
            self.assertTrue(config["ssh_tunnel_enabled"])
            self.assertIn("// Default native Colab kernel language.", text)
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)

    def test_colab_transport_changes_but_arguments_do_not(self):
        options = installer.parser().parse_args([])
        posix = installer.Installer(options, platform="linux")
        posix.colab_bin = "/home/test/.local/bin/colab"
        windows = installer.Installer(options, platform="win32")
        windows.colab_bin = "/home/test/.local/bin/colab"
        arguments = ["sessions"]
        self.assertEqual(
            posix.colab_command(arguments)[-3:], ["--auth", "oauth2", "sessions"]
        )
        self.assertEqual(
            windows.colab_command(arguments)[-3:], ["--auth", "oauth2", "sessions"]
        )
        self.assertEqual(
            windows.colab_command(arguments)[:5],
            ["wsl.exe", "-d", "Ubuntu", "--", "env"],
        )

    def test_windows_config_is_restricted_with_icacls(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            options = installer.parser().parse_args(["-StateRoot", str(state)])
            subject = installer.Installer(options, platform="win32")
            subject.run = MagicMock()
            with patch.dict(
                installer.os.environ,
                {"USERNAME": "tester", "USERDOMAIN": "DOMAIN"},
                clear=False,
            ):
                subject.write_config()
        command = subject.run.call_args.args[0]
        self.assertEqual(command[0], "icacls.exe")
        self.assertIn("DOMAIN\\tester:(OI)(CI)F", command)

    def test_plugin_install_refreshes_existing_marketplace(self):
        options = installer.parser().parse_args([])
        subject = installer.Installer(options, platform="linux")
        subject.marketplace_is_local = False
        subject.marketplace_source = installer.REPOSITORY
        subject.run = MagicMock(
            side_effect=[
                completed(1, stderr="marketplace already exists"),
                completed(0, stdout="Updated marketplace"),
                completed(0),
                completed(
                    0,
                    stdout=(
                        "colab-remote@colab-remote  installed, enabled  0.6.2  /plugin\n"
                    ),
                ),
            ]
        )
        subject.install_plugin()
        commands = [call.args[0] for call in subject.run.call_args_list]
        self.assertEqual(
            commands,
            [
                ["codex", "plugin", "marketplace", "add", installer.REPOSITORY],
                ["codex", "plugin", "marketplace", "upgrade", installer.MARKETPLACE],
                [
                    "codex",
                    "plugin",
                    "add",
                    f"{installer.PLUGIN}@{installer.MARKETPLACE}",
                ],
                ["codex", "plugin", "list"],
            ],
        )

    def test_fresh_git_install_does_not_run_upgrade(self):
        options = installer.parser().parse_args([])
        subject = installer.Installer(options, platform="linux")
        subject.marketplace_is_local = False
        subject.marketplace_source = installer.REPOSITORY
        subject.run = MagicMock(
            side_effect=[
                completed(0, stdout="Added marketplace"),
                completed(0),
                completed(
                    0,
                    stdout="colab-remote@colab-remote  installed, enabled  0.6.2  /plugin\n",
                ),
            ]
        )
        subject.install_plugin()
        commands = [call.args[0] for call in subject.run.call_args_list]
        self.assertNotIn(
            ["codex", "plugin", "marketplace", "upgrade", installer.MARKETPLACE],
            commands,
        )
        self.assertFalse(any("remove" in command for command in commands))

    def test_local_marketplace_is_reused_without_git_upgrade(self):
        options = installer.parser().parse_args([])
        subject = installer.Installer(options, platform="linux")
        subject.marketplace_is_local = True
        subject.marketplace_source = str(ROOT)
        subject.run = MagicMock(
            side_effect=[
                completed(0, stdout="Marketplace is already added"),
                completed(
                    0,
                    stdout=f"MARKETPLACE ROOT\ncolab-remote  {ROOT}\n",
                ),
                completed(0),
                completed(
                    0,
                    stdout="colab-remote@colab-remote  installed, enabled  0.6.2  /plugin\n",
                ),
            ]
        )
        subject.install_plugin()
        commands = [call.args[0] for call in subject.run.call_args_list]
        self.assertFalse(any("upgrade" in command for command in commands))
        self.assertFalse(any("remove" in command for command in commands))

    def test_stale_local_marketplace_is_repaired_for_git_install(self):
        options = installer.parser().parse_args([])
        subject = installer.Installer(options, platform="linux")
        subject.marketplace_is_local = False
        subject.marketplace_source = installer.REPOSITORY
        subject.run = MagicMock(
            side_effect=[
                completed(1, stderr="marketplace already exists"),
                completed(
                    1,
                    stderr="marketplace `colab-remote` is not configured as a Git marketplace",
                ),
                completed(
                    0,
                    stdout="MARKETPLACE ROOT\ncolab-remote  /missing/marketplace\n",
                ),
                completed(0),
                completed(0),
                completed(0),
                completed(
                    0,
                    stdout="colab-remote@colab-remote  installed, enabled  0.6.2  /plugin\n",
                ),
            ]
        )
        subject.install_plugin()
        commands = [call.args[0] for call in subject.run.call_args_list]
        self.assertIn(
            ["codex", "plugin", "marketplace", "remove", installer.MARKETPLACE],
            commands,
        )
        self.assertLess(
            commands.index(
                ["codex", "plugin", "marketplace", "remove", installer.MARKETPLACE]
            ),
            commands.index(
                [
                    "codex",
                    "plugin",
                    "add",
                    f"{installer.PLUGIN}@{installer.MARKETPLACE}",
                ]
            ),
        )

    def test_existing_config_is_preserved_except_explicit_options(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            state.mkdir()
            legacy_path = state / "config.json"
            config_path = state / "config.jsonc"
            original = {
                "distro": "ExistingLinux",
                "default_accelerator": "t4",
                "default_language": "julia",
                "allowed_local_roots": ["/keep/me"],
                "custom_future_setting": "preserved",
                "require_cost_acknowledgement": False,
            }
            legacy_path.write_text(json.dumps(original), encoding="utf-8")
            options = installer.parser().parse_args(
                ["--state-root", str(state), "--default-accelerator", "a100"]
            )
            options.explicit_config_options = {"default_accelerator"}
            subject = installer.Installer(options, platform="linux")
            subject.write_config()
            updated_text = config_path.read_text(encoding="utf-8")
            updated = installer.parse_jsonc(updated_text)
        self.assertEqual(updated["default_accelerator"], "a100")
        self.assertEqual(updated["default_language"], "julia")
        self.assertEqual(updated["allowed_local_roots"], ["/keep/me"])
        self.assertEqual(updated["custom_future_setting"], "preserved")
        self.assertTrue(updated["require_cost_acknowledgement"])
        self.assertFalse(legacy_path.exists())
        self.assertIn("// Preserved additional setting", updated_text)

    def test_windows_update_reuses_configured_wsl_distro(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            state.mkdir()
            (state / "config.json").write_text(
                json.dumps({"distro": "Existing Ubuntu"}), encoding="utf-8"
            )
            options = installer.parser().parse_args(["-StateRoot", str(state)])
            options.explicit_config_options = set()
            subject = installer.Installer(options, platform="win32")
        self.assertEqual(subject.options.distro, "Existing Ubuntu")

    def test_explicit_wsl_distro_overrides_existing_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            state.mkdir()
            (state / "config.json").write_text(
                json.dumps({"distro": "Existing Ubuntu"}), encoding="utf-8"
            )
            options = installer.parser().parse_args(
                ["-StateRoot", str(state), "-Distro", "New Ubuntu"]
            )
            options.explicit_config_options = {"distro"}
            subject = installer.Installer(options, platform="win32")
        self.assertEqual(subject.options.distro, "New Ubuntu")

    def test_update_preserves_authentication(self):
        options = installer.parser().parse_args([])
        subject = installer.Installer(options, platform="linux")
        subject.check_host = MagicMock()
        subject.plugin_is_installed = MagicMock(return_value=True)
        subject.install_uv = MagicMock(return_value=Path("/uv"))
        subject.install_colab_cli = MagicMock()
        subject.install_plugin = MagicMock()
        subject.write_config = MagicMock()
        subject.authenticate = MagicMock()
        subject.smoke_test = MagicMock()
        subject.execute()
        subject.authenticate.assert_not_called()
        subject.install_plugin.assert_called_once_with()

    def test_shared_installer_rejects_old_python(self):
        original = installer.sys.version_info
        try:
            installer.sys.version_info = (3, 10, 0)
            self.assertEqual(installer.main(["--help"]), 2)
        finally:
            installer.sys.version_info = original


if __name__ == "__main__":
    unittest.main()
