import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import call, patch

from colab_remote import cli, configuration


class UserCliTests(unittest.TestCase):
    class _Response:
        def __init__(self, payload, url=cli.INSTALLER_URL):
            self.payload = payload
            self.url = url

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def geturl(self):
            return self.url

        def read(self, maximum):
            return self.payload[:maximum]

    def test_top_level_help_explains_every_user_command(self):
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
            cli.main(["--help"])
        self.assertEqual(stopped.exception.code, 0)
        rendered = output.getvalue()
        for command in ("doctor", "version", "update", "config", "secrets"):
            self.assertIn(command, rendered)
        self.assertIn("does not execute remote jobs itself", rendered)

    def test_list_routes_to_name_only_broker_command(self):
        with patch.object(cli.secret_broker, "main", return_value=0) as broker:
            self.assertEqual(cli.main(["secrets", "list"]), 0)
        broker.assert_called_once_with(["list"])

    def test_add_and_remove_route_without_accepting_values_as_arguments(self):
        with patch.object(cli.secret_broker, "main", return_value=0) as broker:
            self.assertEqual(cli.main(["secrets", "add", "HF_TOKEN"]), 0)
            self.assertEqual(cli.main(["secrets", "remove", "HF_TOKEN"]), 0)
        self.assertEqual(
            broker.call_args_list,
            [
                call(["set", "HF_TOKEN"]),
                call(["delete", "HF_TOKEN"]),
            ],
        )

    def test_doctor_reports_backend_name_without_credentials(self):
        details = {
            "backend": "example.SafeKeyring",
            "platform_note": "Secure provider available.",
        }
        output = io.StringIO()
        with (
            patch.object(cli.secret_broker, "backend_status", return_value=details),
            redirect_stdout(output),
        ):
            self.assertEqual(cli.main(["secrets", "doctor"]), 0)
        rendered = output.getvalue()
        self.assertIn("example.SafeKeyring", rendered)
        self.assertNotIn("HF_TOKEN", rendered)

    def test_config_set_show_get_and_reset_use_isolated_state(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"COLAB_REMOTE_STATE_DIR": directory},
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    cli.main(["config", "set", "default_accelerator", "a100"]),
                    0,
                )
                self.assertEqual(
                    cli.main(["config", "get", "default_accelerator"]),
                    0,
                )
            self.assertIn('"a100"', output.getvalue())
            saved = configuration.load()
            self.assertEqual(saved["default_accelerator"], "a100")
            self.assertEqual(cli.main(["config", "reset", "default_accelerator"]), 0)
            self.assertEqual(
                configuration.load()["default_accelerator"],
                configuration.DEFAULT_CONFIG["default_accelerator"],
            )
            rendered = Path(directory, "config.jsonc").read_text(encoding="utf-8")
            self.assertIn("//", rendered)
            self.assertIn('"require_secret_enable_approval"', rendered)

    def test_sensitive_config_change_requires_yes_when_noninteractive(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"COLAB_REMOTE_STATE_DIR": directory},
        ), patch.object(cli.sys.stdin, "isatty", return_value=False):
            error = io.StringIO()
            with redirect_stderr(error):
                result = cli.main(
                    [
                        "config",
                        "set",
                        "require_cost_acknowledgement",
                        "false",
                    ]
                )
            self.assertEqual(result, 2)
            self.assertIn("--yes", error.getvalue())
            self.assertTrue(configuration.load()["require_cost_acknowledgement"])
            self.assertEqual(
                cli.main(
                    [
                        "config",
                        "set",
                        "require_cost_acknowledgement",
                        "false",
                        "--yes",
                    ]
                ),
                0,
            )
            self.assertFalse(configuration.load()["require_cost_acknowledgement"])

    def test_default_secret_policy_allows_named_aliases_without_prompt(self):
        self.assertFalse(
            configuration.DEFAULT_CONFIG["require_secret_enable_approval"]
        )

    def test_allow_root_is_cross_platform_and_validated(self):
        with tempfile.TemporaryDirectory() as state, tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, {"COLAB_REMOTE_STATE_DIR": state}):
                self.assertEqual(cli.main(["config", "allow-root", root]), 0)
                self.assertEqual(
                    configuration.load()["allowed_local_roots"],
                    [str(Path(root).resolve())],
                )
                self.assertEqual(cli.main(["config", "remove-root", root]), 0)
                self.assertEqual(configuration.load()["allowed_local_roots"], [])

    def test_config_edit_validates_temporary_copy_before_replacing_working_file(self):
        def invalid_edit(arguments, **_):
            Path(arguments[-1]).write_text(
                '{"default_runtime_version": "not-a-runtime"}',
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(arguments, 0)

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"COLAB_REMOTE_STATE_DIR": directory},
        ), patch.object(
            cli,
            "_editor_command",
            side_effect=lambda path: ["editor", str(path)],
        ), patch.object(
            cli.subprocess,
            "run",
            side_effect=invalid_edit,
        ):
            original = configuration.save(configuration.DEFAULT_CONFIG)
            error = io.StringIO()
            with redirect_stderr(error):
                self.assertEqual(cli.main(["config", "edit"]), 1)
            self.assertEqual(configuration.load(), original)
            self.assertIn("default_runtime_version", error.getvalue())

    def test_update_downloads_to_temporary_file_and_runs_python_without_a_shell(self):
        completed = subprocess.CompletedProcess([], 0)

        def fake_download(path):
            path.write_text("print('installer')", encoding="utf-8")

        with (
            patch.object(cli, "_download_installer", side_effect=fake_download),
            patch.object(cli.subprocess, "run", return_value=completed) as run,
        ):
            self.assertEqual(cli.main(["update"]), 0)
        arguments = run.call_args.args[0]
        self.assertEqual(arguments[0], cli.sys.executable)
        self.assertEqual(arguments[-1], "--skip-authentication")
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_installer_download_rejects_redirect_to_another_host(self):
        payload = b"# RemySkye/codex-colab-remote\ndef main(): pass\n"
        response = self._Response(payload, "https://example.com/install.py")
        with tempfile.TemporaryDirectory() as directory, patch.object(
            cli,
            "urlopen",
            return_value=response,
        ):
            with self.assertRaisesRegex(ValueError, "unexpected installer host"):
                cli._download_installer(Path(directory) / "install.py")

    def test_full_doctor_is_read_only_and_never_lists_aliases(self):
        details = {"backend": "example.SafeKeyring", "platform_note": ""}
        output = io.StringIO()
        with (
            patch.object(cli.shutil, "which", side_effect=lambda name: f"/bin/{name}"),
            patch.object(cli.configuration, "load", return_value={}),
            patch.object(cli.secret_broker, "backend_status", return_value=details),
            patch.object(cli.secret_broker, "main") as broker,
            redirect_stdout(output),
        ):
            self.assertEqual(cli.main(["doctor"]), 0)
        broker.assert_not_called()
        self.assertIn("All local checks passed", output.getvalue())


if __name__ == "__main__":
    unittest.main()
