import importlib.util
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


SERVER_PATH = Path(__file__).resolve().parents[1] / "mcp" / "server.py"
sys.path.insert(0, str(SERVER_PATH.parent))
SPEC = importlib.util.spec_from_file_location("colab_remote_server", SERVER_PATH)
server = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = server
SPEC.loader.exec_module(server)


def completed(stdout="", stderr="", returncode=0):
    return server.subprocess.CompletedProcess([], returncode, stdout, stderr)


class ServerTests(unittest.TestCase):
    def test_subprocesses_cannot_read_mcp_stdin(self):
        with patch.object(server.subprocess, "run", return_value=completed()) as run:
            server._run(["example"])
        self.assertEqual(run.call_args.kwargs["stdin"], server.subprocess.DEVNULL)
        self.assertNotIn("input", run.call_args.kwargs)
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")
        self.assertEqual(run.call_args.kwargs["errors"], "replace")

    def test_host_command_runs_directly_on_linux_and_macos(self):
        with (
            patch.object(server, "_uses_wsl", return_value=False),
            patch.object(server, "_run", return_value=completed("ok")) as run,
        ):
            result = server._wsl(["colab", "version"])
        self.assertEqual(result.stdout, "ok")
        self.assertEqual(run.call_args.args[0], ["colab", "version"])

    def test_host_command_uses_wsl_on_windows(self):
        with (
            patch.object(server, "_uses_wsl", return_value=True),
            patch.object(server, "_distro", return_value="Ubuntu"),
            patch.object(server, "_run", return_value=completed()) as run,
        ):
            server._wsl(["colab", "version"])
        self.assertEqual(
            run.call_args.args[0],
            ["wsl.exe", "-d", "Ubuntu", "--", "colab", "version"],
        )

    def test_local_cli_path_is_native_on_linux_and_macos(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(server, "_uses_wsl", return_value=False),
        ):
            path = Path(temporary) / "file.py"
            self.assertEqual(server._wsl_path(path), str(path.resolve()))

    def test_native_credential_mode_uses_portable_python_stat(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            token = home / ".config" / "colab-cli" / "token.json"
            token.parent.mkdir(parents=True)
            token.write_text("{}", encoding="utf-8")
            token.chmod(0o600)
            with (
                patch.object(server, "_uses_wsl", return_value=False),
                patch.object(server.Path, "home", return_value=home),
                patch.object(
                    server.Path,
                    "stat",
                    return_value=MagicMock(st_mode=server.stat.S_IFREG | 0o600),
                ),
            ):
                metadata = server._credential_metadata()
        self.assertTrue(metadata["oauth_token_present"])
        self.assertTrue(metadata["owner_only_mode"])
        self.assertEqual(metadata["mode"], "600")

    def test_insecure_wsl_token_permissions_are_repaired_automatically(self):
        insecure = {
            "oauth_token_present": True,
            "owner_only_mode": False,
            "mode": "644",
            "symlink": False,
        }
        secure = {**insecure, "owner_only_mode": True, "mode": "600"}
        with (
            patch.object(
                server, "_credential_metadata", side_effect=[insecure, secure]
            ),
            patch.object(server, "_linux_home", return_value="/home/test"),
            patch.object(server, "_uses_wsl", return_value=True),
            patch.object(server, "_wsl", return_value=completed()) as wsl,
        ):
            repaired = server._repair_credential_permissions()
        self.assertTrue(repaired)
        self.assertEqual(
            wsl.call_args.args[0],
            ["chmod", "600", "--", "/home/test/.config/colab-cli/token.json"],
        )

    def test_token_permission_repair_rejects_symlink(self):
        metadata = {
            "oauth_token_present": True,
            "owner_only_mode": False,
            "mode": "644",
            "symlink": True,
        }
        with (
            patch.object(server, "_credential_metadata", return_value=metadata),
            self.assertRaisesRegex(PermissionError, "symlink"),
        ):
            server._repair_credential_permissions()

    def test_colab_serializes_session_operations(self):
        lock = MagicMock()
        lock.__enter__.return_value = None
        with (
            patch.object(server, "_require_credentials"),
            patch.object(
                server, "_colab_path", return_value="/home/test/.local/bin/colab"
            ),
            patch.object(
                server, "_wsl", side_effect=[completed(), completed(stdout="ok")]
            ),
            patch.object(
                server, "_session_cli_lock", return_value=lock
            ) as session_lock,
        ):
            result = server._colab(
                ["exec", "-s", "test-session"], input_text="print(1)"
            )
        self.assertEqual(result.stdout, "ok")
        session_lock.assert_called_once_with("test-session", 300)

    def test_colab_parallel_operation_bypasses_session_lock(self):
        with (
            patch.object(server, "_require_credentials"),
            patch.object(
                server, "_colab_path", return_value="/home/test/.local/bin/colab"
            ),
            patch.object(
                server, "_wsl", side_effect=[completed(), completed(stdout="ok")]
            ),
            patch.object(server, "_session_cli_lock") as session_lock,
        ):
            result = server._colab(
                ["download", "-s", "test-session", "/content/a", "/tmp/a"],
                serialize_session=False,
            )
        self.assertEqual(result.stdout, "ok")
        session_lock.assert_not_called()

    def test_colab_retries_only_retry_safe_commands(self):
        config = {**server.DEFAULT_CONFIG, "retry_attempts": 2}
        with (
            patch.object(server, "_require_credentials"),
            patch.object(
                server, "_colab_path", return_value="/home/test/.local/bin/colab"
            ),
            patch.object(server, "_load_config", return_value=config),
            patch.object(
                server,
                "_wsl",
                side_effect=[
                    completed(),
                    RuntimeError("temporary"),
                    completed(stdout="ready"),
                ],
            ) as wsl,
            patch.object(server.time, "sleep"),
        ):
            result = server._colab(["sessions"])
        self.assertEqual(result.stdout, "ready")
        self.assertEqual(wsl.call_count, 3)

        with (
            patch.object(server, "_require_credentials"),
            patch.object(
                server, "_colab_path", return_value="/home/test/.local/bin/colab"
            ),
            patch.object(server, "_load_config", return_value=config),
            patch.object(
                server,
                "_wsl",
                side_effect=[completed(), RuntimeError("allocation failed")],
            ) as wsl,
            self.assertRaisesRegex(RuntimeError, "allocation failed"),
        ):
            server._colab(["new", "-s", "test-session"])
        self.assertEqual(wsl.call_count, 2)

    def test_colab_blocks_commands_while_drive_mount_waits(self):
        with (
            patch.object(server, "_require_credentials"),
            patch.object(
                server, "_colab_path", return_value="/home/test/.local/bin/colab"
            ),
            patch.object(server, "_wsl", return_value=completed()),
            patch.object(server, "_drive_mount_is_active", return_value=True),
            self.assertRaisesRegex(RuntimeError, "complete_google_drive_mount"),
        ):
            server._colab(["exec", "-s", "test-session"], input_text="print(1)")

    def test_expected_tools_are_registered(self):
        names = {tool.name for tool in server.mcp._tool_manager.list_tools()}
        self.assertFalse(any(name.startswith("ssh_") for name in names))
        self.assertEqual(
            names,
            {
                "add_notebook_cell",
                "authentication_instructions",
                "cancel_transfer",
                "complete_google_drive_mount",
                "create_notebook",
                "create_drive_folder",
                "create_session",
                "credential_status",
                "delete_notebook_cell",
                "delete_drive_path",
                "doctor",
                "disable_local_secrets",
                "download_file",
                "edit_notebook_cell",
                "enable_local_secrets",
                "execute_code",
                "execute_file",
                "export_session_notebook",
                "get_config",
                "get_logs",
                "import_notebook",
                "install_packages",
                "job_logs",
                "job_status",
                "list_files",
                "list_drive_files",
                "list_local_secrets",
                "list_sessions",
                "list_transfers",
                "load_notebook_from_drive",
                "mount_google_drive",
                "move_notebook_cell",
                "move_drive_path",
                "notification_history",
                "prepare_language",
                "prepare_local_secret",
                "read_notebook",
                "recover_session",
                "recovery_status",
                "restart_kernel",
                "restore_from_drive",
                "resume_transfer",
                "run_notebook_cells",
                "save_notebook_to_drive",
                "save_to_drive",
                "session_status",
                "session_url",
                "set_config",
                "set_session_lifetime",
                "start_download",
                "start_job",
                "start_upload",
                "stop_job",
                "stop_session",
                "terminal_exec",
                "test_notification",
                "transfer_status",
                "upload_file",
                "watch_job",
            },
        )
        readme = (SERVER_PATH.parents[3] / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"{len(names)} MCP tools", readme)
        for name in names:
            self.assertIn(f"`{name}`", readme)

    def test_redacts_oauth_material(self):
        text = 'code=4/example-secret access_token=ya29.secret "refresh_token":"refresh-me"'
        redacted = server._redact(text)
        self.assertNotIn("example-secret", redacted)
        self.assertNotIn("ya29.secret", redacted)
        self.assertNotIn("refresh-me", redacted)

    def test_redacts_colab_runtime_proxy_token_from_error_url(self):
        text = "https://runtime.example/api?authuser=0&colab-runtime-proxy-token=header.payload.signature"
        redacted = server._redact(text)
        self.assertNotIn("header.payload.signature", redacted)
        self.assertIn("colab-runtime-proxy-token=[REDACTED]", redacted)

    def test_json_marker_ignores_terminal_control_codes(self):
        output = (
            '\x1b[32mCODEX_JOB_STATUS={"exists":true,"status":"running"}\x1b[0m prompt>'
        )
        result = server._extract_json_marker(output, "CODEX_JOB_STATUS=")
        self.assertTrue(result["exists"])

    def test_remote_shell_uses_real_remote_exit_code(self):
        payload = 'CODEX_REMOTE_SHELL={"returncode":7,"stdout":"","stderr":"failed"}\n'
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.object(server, "STATE_ROOT", Path(temporary)),
                patch.object(server, "_wsl_path", side_effect=lambda path: str(path)),
                patch.object(server, "_colab", return_value=completed(payload)) as colab,
                self.assertRaisesRegex(RuntimeError, "failed"),
            ):
                server._remote_shell("test-session", "exit 7")
        console_calls = [
            call
            for call in colab.call_args_list
            if call.args[0][:3] == ["console", "-s", "test-session"]
        ]
        self.assertEqual(len(console_calls), 1)
        self.assertIn("python3", console_calls[0].kwargs["input_text"])

    def test_remote_shell_stages_large_payload_with_official_upload(self):
        payload = 'CODEX_REMOTE_SHELL={"returncode":0,"stdout":"ok","stderr":""}\n'
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.object(server, "STATE_ROOT", Path(temporary)),
                patch.object(server, "_wsl_path", side_effect=lambda path: str(path)),
                patch.object(server, "_colab", return_value=completed(payload)) as colab,
            ):
                result = server._remote_shell(
                    "test-session", "printf %s " + "x" * 12000
                )

            upload = colab.call_args_list[0]
            console = colab.call_args_list[1]
            cleanup = colab.call_args_list[2]
            self.assertEqual(upload.args[0][0], "upload")
            self.assertEqual(console.args[0][0], "console")
            self.assertLess(len(console.kwargs["input_text"]), 300)
            self.assertEqual(cleanup.args[0][0], "rm")
            self.assertFalse(list(Path(temporary).glob("remote-shell-*.py")))
        self.assertEqual(result.stdout, "ok")

    def test_memory_probe_retries_new_console_race(self):
        marker = 'CODEX_MEMORY={"bytes":13605830656,"gib":12.67}\n'
        with (
            patch.object(
                server,
                "_remote_shell",
                side_effect=[RuntimeError("not ready"), completed(marker)],
            ) as remote_shell,
            patch.object(server.time, "sleep") as sleep,
        ):
            memory = server._memory_status("test-session")
        self.assertEqual(memory["gib"], 12.67)
        self.assertEqual(remote_shell.call_count, 2)
        sleep.assert_called_once_with(2)

    def test_terminal_exec_uses_official_cli(self):
        with (
            patch.object(
                server, "_remote_shell", return_value=completed("linux-ok\n")
            ) as remote_shell,
            patch.object(
                server, "_load_config", return_value=dict(server.DEFAULT_CONFIG)
            ),
        ):
            result = server.terminal_exec(
                "test-session", "uname -a", "/content", timeout_seconds=45
            )
        self.assertEqual(result["transport"], "official-colab-cli")
        self.assertEqual(result["stdout"], "linux-ok\n")
        self.assertIn("uname -a", remote_shell.call_args.args[1])
        self.assertEqual(remote_shell.call_args.kwargs["timeout"], 45)

    def test_config_requires_confirmation_for_local_roots(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(server, "STATE_ROOT", Path(temporary) / "state"),
            patch.object(
                server, "CONFIG_PATH", Path(temporary) / "state" / "config.json"
            ),
            patch.object(
                server,
                "NOTIFICATIONS_PATH",
                Path(temporary) / "state" / "notifications.jsonl",
            ),
        ):
            with self.assertRaises(PermissionError):
                server.set_config(allowed_local_roots=[temporary])
            result = server.set_config(
                allowed_local_roots=[temporary], confirm_sensitive_change=True
            )
            self.assertEqual(
                result["allowed_local_roots"], [str(Path(temporary).resolve())]
            )

    def test_config_migrates_legacy_high_ram_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            state.mkdir()
            config_path = state / "config.json"
            config_path.write_text(
                '{"prefer_high_ram": true, "ssh_secret_name": "OLD_LABEL"}',
                encoding="utf-8",
            )
            with (
                patch.object(server, "STATE_ROOT", state),
                patch.object(server, "CONFIG_PATH", config_path),
                patch.object(server, "_secure_state_root"),
            ):
                result = server.get_config()
        self.assertTrue(result["default_high_ram"])
        self.assertNotIn("prefer_high_ram", result)
        self.assertNotIn("ssh_secret_name", result)

    def test_config_migrates_to_commented_jsonc(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            state.mkdir()
            legacy_path = state / "config.json"
            config_path = state / "config.jsonc"
            legacy_path.write_text(
                '{"notifications_enabled": true}', encoding="utf-8"
            )
            with (
                patch.object(server, "STATE_ROOT", state),
                patch.object(server, "CONFIG_PATH", config_path),
                patch.object(server, "_secure_state_root"),
            ):
                result = server.get_config()
            stored_text = config_path.read_text(encoding="utf-8")
            stored = server.config_io.loads(stored_text)
        self.assertEqual(result["notification_mode"], "all")
        self.assertNotIn("notifications_enabled", stored)
        self.assertFalse(legacy_path.exists())
        self.assertIn("// Desktop popup policy", stored_text)
        self.assertIn("// Allowed: \"off\", \"failures_only\", \"all\".", stored_text)
        self.assertNotIn("_documentation", stored)

    def test_config_updates_new_operational_defaults(self):
        rendered = ""
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(server, "STATE_ROOT", Path(temporary) / "state"),
            patch.object(
                server, "CONFIG_PATH", Path(temporary) / "state" / "config.jsonc"
            ),
            patch.object(
                server,
                "_secure_state_root",
                lambda: (Path(temporary) / "state").mkdir(parents=True, exist_ok=True),
            ),
        ):
            result = server.set_config(
                notification_mode="failures_only",
                max_concurrent_sessions=8,
                transfer_compression=True,
                transfer_parallelism=6,
                retry_attempts=5,
                default_drive_checkpoint_folder="training/checkpoints",
            )
            rendered = (Path(temporary) / "state" / "config.jsonc").read_text(
                encoding="utf-8"
            )
        self.assertEqual(result["notification_mode"], "failures_only")
        self.assertEqual(result["max_concurrent_sessions"], 8)
        self.assertTrue(result["transfer_compression"])
        self.assertEqual(result["transfer_parallelism"], 6)
        self.assertEqual(result["retry_attempts"], 5)
        self.assertEqual(
            result["default_drive_checkpoint_folder"], "training/checkpoints"
        )
        self.assertIn("// Total attempts for retry-safe", rendered)
        self.assertEqual(server.config_io.loads(rendered)["retry_attempts"], 5)

    def test_local_file_access_is_disabled_by_default(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(server, "STATE_ROOT", Path(temporary) / "state"),
            patch.object(
                server, "CONFIG_PATH", Path(temporary) / "state" / "config.json"
            ),
        ):
            with self.assertRaises(PermissionError):
                server._allowed_local_path(
                    str(Path(temporary) / "file.py"), must_exist=False
                )

    def test_official_cli_forces_oauth2_and_unsets_adc(self):
        calls = []

        def fake_wsl(args, **_kwargs):
            calls.append(args)
            return completed()

        with (
            patch.object(server, "_require_credentials"),
            patch.object(
                server, "_colab_path", return_value="/home/test/.local/bin/colab"
            ),
            patch.object(server, "_wsl", side_effect=fake_wsl),
        ):
            server._colab(["sessions"])
        command = calls[-1]
        self.assertIn("GOOGLE_APPLICATION_CREDENTIALS", command)
        self.assertIn("CLOUDSDK_CONFIG", command)
        self.assertIn("oauth2", command)
        self.assertNotIn("adc", command)

    def test_authentication_instructions_secure_new_token(self):
        with (
            patch.object(server, "_uses_wsl", return_value=True),
            patch.object(server, "_distro", return_value="Ubuntu"),
        ):
            result = server.authentication_instructions()
        self.assertIn("umask 077", result["command"])
        self.assertIn("chmod 600", result["command"])
        self.assertEqual(result["host_transport"], "wsl")

    def test_authentication_instructions_are_native_on_linux_and_macos(self):
        with patch.object(server, "_uses_wsl", return_value=False):
            result = server.authentication_instructions()
        self.assertNotIn("wsl", result["command"])
        self.assertIn("colab --auth oauth2 sessions", result["command"])
        self.assertEqual(result["host_transport"], "native")

    def test_create_requires_cost_acknowledgement(self):
        with patch.object(
            server, "_load_config", return_value=dict(server.DEFAULT_CONFIG)
        ):
            with self.assertRaises(PermissionError):
                server.create_session("test-session", accelerator="A100")

    def test_concurrent_session_limit_counts_server_assignments(self):
        listing = completed(
            "[first] endpoint-1 | Hardware: CPU | Variant: DEFAULT\n"
            "[?] endpoint-2 | Hardware: T4 | Variant: GPU\n"
        )
        config = {**server.DEFAULT_CONFIG, "max_concurrent_sessions": 2}
        self.assertEqual(server._active_session_count(listing.stdout), 2)
        with (
            patch.object(server, "_colab", return_value=listing),
            self.assertRaisesRegex(RuntimeError, "2/2"),
        ):
            server._enforce_session_limit(config)

    def test_standing_allocation_authorization_is_loaded_from_config_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            config_path = state / "config.json"
            config_path.write_text(
                '{"require_cost_acknowledgement": false}', encoding="utf-8"
            )
            with (
                patch.object(server, "STATE_ROOT", state),
                patch.object(server, "CONFIG_PATH", config_path),
                patch.object(server, "_secure_state_root"),
            ):
                self.assertFalse(server._load_config()["require_cost_acknowledgement"])

    def test_disabling_per_session_cost_approval_requires_confirmation(self):
        with (
            patch.object(server, "_load_config", return_value=dict(server.DEFAULT_CONFIG)),
            patch.object(server, "_save_config"),
        ):
            with self.assertRaises(PermissionError):
                server.set_config(require_cost_acknowledgement=False)

    def test_disabling_per_session_cost_approval_with_confirmation(self):
        with (
            patch.object(server, "_load_config", return_value=dict(server.DEFAULT_CONFIG)),
            patch.object(server, "_save_config") as save,
        ):
            result = server.set_config(
                require_cost_acknowledgement=False,
                confirm_sensitive_change=True,
            )
        self.assertFalse(result["require_cost_acknowledgement"])
        save.assert_called_once()

    def test_create_requests_high_ram(self):
        with (
            patch.object(
                server, "_load_config", return_value=dict(server.DEFAULT_CONFIG)
            ),
            patch.object(
                server,
                "_enforce_session_limit",
                return_value={"active_sessions": 0, "max_concurrent_sessions": 8},
            ),
            patch.object(server, "_colab", return_value=completed("ok")) as colab,
            patch.object(
                server,
                "_initialize_native_language",
                return_value={
                    "language": "python",
                    "kernel": "python3",
                    "native": True,
                },
            ),
            patch.object(
                server, "_memory_status", return_value={"bytes": 55, "gib": 51.0}
            ),
            patch.object(server, "_record_session"),
            patch.object(
                server, "_session_compute_metadata", return_value={"tracked": True}
            ),
        ):
            result = server.create_session(
                "test-session",
                accelerator="cpu",
                high_ram=True,
                acknowledge_cost=True,
            )
        self.assertTrue(result["high_ram_requested"])
        self.assertEqual(colab.call_args_list[0].kwargs["machine_shape"], "hm")

    def test_standing_authorization_creates_without_per_session_acknowledgement(self):
        config = {
            **server.DEFAULT_CONFIG,
            "require_cost_acknowledgement": False,
        }
        with (
            patch.object(server, "_load_config", return_value=config),
            patch.object(
                server,
                "_enforce_session_limit",
                return_value={"active_sessions": 0, "max_concurrent_sessions": 8},
            ),
            patch.object(server, "_colab", return_value=completed("ok")),
            patch.object(
                server,
                "_initialize_native_language",
                return_value={
                    "language": "python",
                    "kernel": "python3",
                    "native": True,
                },
            ),
            patch.object(
                server, "_memory_status", return_value={"bytes": 14, "gib": 13.0}
            ),
            patch.object(server, "_record_session"),
            patch.object(
                server, "_session_compute_metadata", return_value={"tracked": True}
            ),
        ):
            result = server.create_session("automatic-session")
        self.assertEqual(result["session_name"], "automatic-session")

    def test_create_returns_trusted_attached_colab_url(self):
        attached_url = (
            "https://colab.research.google.com/notebooks/empty.ipynb?"
            "dbu=%2Ftun%2Fm%2Ftest-endpoint#datalabBackendUrl="
            "https://colab.research.google.com/tun/m/test-endpoint"
        )
        with (
            patch.object(
                server,
                "_load_config",
                return_value=dict(server.DEFAULT_CONFIG),
            ),
            patch.object(
                server,
                "_enforce_session_limit",
                return_value={"active_sessions": 0, "max_concurrent_sessions": 8},
            ),
            patch.object(server, "_colab", return_value=completed("ok")),
            patch.object(
                server,
                "_initialize_native_language",
                return_value={
                    "language": "python",
                    "kernel": "python3",
                    "native": True,
                },
            ),
            patch.object(
                server,
                "_memory_status",
                return_value={"bytes": 14, "gib": 13.0},
            ),
            patch.object(server, "_record_session"),
            patch.object(
                server,
                "_session_compute_metadata",
                return_value={"tracked": True},
            ),
            patch.object(
                server, "_created_session_url", return_value=attached_url
            ) as session_url,
        ):
            result = server.create_session(
                "test-session",
                accelerator="cpu",
                high_ram=False,
                acknowledge_cost=True,
            )

        session_url.assert_called_once_with("test-session")
        self.assertEqual(result["session_url"], attached_url)
        self.assertEqual(result["session_url_presentation"], "copy_paste_only")
        self.assertIn("fenced code block", result["session_url_instructions"])
        self.assertIn("never as a Markdown link", result["session_url_instructions"])
        self.assertIn("browser address bar", result["session_url_instructions"])
        self.assertIn(
            "Do not click Colab's normal Connect",
            result["session_url_instructions"],
        )
        self.assertIn("Secrets sidebar", result["secrets_setup"])

    def test_created_session_url_preserves_raw_attachment_fragment(self):
        attached_url = (
            "https://colab.research.google.com/notebooks/empty.ipynb?"
            "dbu=%2Ftun%2Fm%2Ftest-endpoint#datalabBackendUrl="
            "https://colab.research.google.com/tun/m/test-endpoint"
        )
        with patch.object(server, "_colab", return_value=completed(attached_url)):
            self.assertEqual(
                server._created_session_url("test-session"),
                attached_url,
            )

    def test_created_session_url_rejects_encoded_attachment_fragment(self):
        encoded_url = (
            "https://colab.research.google.com/notebooks/empty.ipynb?"
            "dbu=%2Ftun%2Fm%2Ftest-endpoint#datalabBackendUrl="
            "https%3A%2F%2Fcolab.research.google.com%2Ftun%2Fm%2Ftest-endpoint"
        )
        with patch.object(server, "_colab", return_value=completed(encoded_url)):
            with self.assertRaisesRegex(
                RuntimeError,
                "valid raw Colab attachment URL",
            ):
                server._created_session_url("test-session")

    def test_created_session_url_rejects_mismatched_backend(self):
        mismatched_url = (
            "https://colab.research.google.com/notebooks/empty.ipynb?"
            "dbu=%2Ftun%2Fm%2Ffirst-endpoint#datalabBackendUrl="
            "https://colab.research.google.com/tun/m/second-endpoint"
        )
        with patch.object(server, "_colab", return_value=completed(mismatched_url)):
            with self.assertRaisesRegex(
                RuntimeError,
                "valid raw Colab attachment URL",
            ):
                server._created_session_url("test-session")

    def test_created_session_url_rejects_untrusted_host(self):
        with patch.object(
            server,
            "_colab",
            return_value=completed("https://evil.example/notebooks/empty.ipynb"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "valid raw Colab attachment URL",
            ):
                server._created_session_url("test-session")

    def test_session_url_returns_copy_paste_contract(self):
        attached_url = (
            "https://colab.research.google.com/notebooks/empty.ipynb?"
            "dbu=%2Ftun%2Fm%2Ftest-endpoint#datalabBackendUrl="
            "https://colab.research.google.com/tun/m/test-endpoint"
        )
        with patch.object(server, "_created_session_url", return_value=attached_url):
            result = server.session_url("test-session")

        self.assertEqual(result["session_url"], attached_url)
        self.assertEqual(result["session_url_presentation"], "copy_paste_only")
        self.assertIn("fenced code block", result["session_url_instructions"])

    def test_high_ram_only_accelerators_override_false(self):
        self.assertEqual(
            server.HIGH_RAM_REQUIRED_ACCELERATORS,
            {"l4", "g4", "h100", "v5e-1", "v6e-1"},
        )
        self.assertEqual(
            server.ACCELERATORS - server.HIGH_RAM_REQUIRED_ACCELERATORS,
            {"cpu", "t4", "a100"},
        )
        for accelerator in sorted(server.HIGH_RAM_REQUIRED_ACCELERATORS):
            with self.subTest(accelerator=accelerator):
                with (
                    patch.object(
                        server,
                        "_load_config",
                        return_value=dict(server.DEFAULT_CONFIG),
                    ),
                    patch.object(
                        server,
                        "_enforce_session_limit",
                        return_value={
                            "active_sessions": 0,
                            "max_concurrent_sessions": 8,
                        },
                    ),
                    patch.object(
                        server, "_colab", return_value=completed("ok")
                    ) as colab,
                    patch.object(
                        server,
                        "_initialize_native_language",
                        return_value={
                            "language": "python",
                            "kernel": "python3",
                            "native": True,
                        },
                    ),
                    patch.object(
                        server,
                        "_memory_status",
                        return_value={"bytes": 55, "gib": 51.0},
                    ),
                    patch.object(server, "_record_session"),
                    patch.object(
                        server,
                        "_session_compute_metadata",
                        return_value={"tracked": True},
                    ),
                ):
                    result = server.create_session(
                        f"test-{accelerator}",
                        accelerator=accelerator,
                        high_ram=False,
                        acknowledge_cost=True,
                    )

                self.assertTrue(result["high_ram_requested"])
                self.assertTrue(result["high_ram_forced_by_accelerator"])
                self.assertEqual(
                    colab.call_args_list[0].kwargs["machine_shape"], "hm"
                )
                self.assertTrue(
                    any("enabled automatically" in item for item in result["warnings"])
                )

    def test_create_can_disable_high_ram_and_prefer_latest_runtime(self):
        with (
            patch.object(
                server,
                "_load_config",
                return_value={**server.DEFAULT_CONFIG, "default_high_ram": True},
            ),
            patch.object(
                server,
                "_enforce_session_limit",
                return_value={"active_sessions": 0, "max_concurrent_sessions": 8},
            ),
            patch.object(server, "_colab", return_value=completed("ok")) as colab,
            patch.object(
                server,
                "_initialize_native_language",
                return_value={
                    "language": "python",
                    "kernel": "python3",
                    "native": True,
                },
            ),
            patch.object(
                server, "_memory_status", return_value={"bytes": 14, "gib": 12.7}
            ),
            patch.object(server, "_record_session"),
            patch.object(
                server, "_session_compute_metadata", return_value={"tracked": True}
            ),
        ):
            result = server.create_session(
                "test-session",
                accelerator="cpu",
                high_ram=False,
                runtime_version="latest",
                acknowledge_cost=True,
            )
        self.assertFalse(result["high_ram_requested"])
        self.assertFalse(result["high_ram_forced_by_accelerator"])
        self.assertEqual(result["runtime_version"], "latest")
        self.assertIsNone(colab.call_args_list[0].kwargs["machine_shape"])
        self.assertIsNone(colab.call_args_list[0].kwargs["runtime_version"])

    def test_runtime_version_defaults_to_latest_and_accepts_dates(self):
        self.assertEqual(server._normalize_runtime_version("recommended"), "latest")
        self.assertEqual(server._normalize_runtime_version("2026.04"), "2026.04")
        with self.assertRaises(ValueError):
            server._normalize_runtime_version("3.12")

    def test_colab_compatibility_wrapper_receives_shape_and_version(self):
        calls = []

        def fake_wsl(args, **_kwargs):
            calls.append(args)
            return completed()

        with (
            patch.object(server, "_require_credentials"),
            patch.object(
                server, "_colab_path", return_value="/home/test/.local/bin/colab"
            ),
            patch.object(server, "_linux_home", return_value="/home/test"),
            patch.object(
                server, "_wsl_path", return_value="/plugin/scripts/colab_compat.py"
            ),
            patch.object(server, "_wsl", side_effect=fake_wsl),
        ):
            server._colab(
                ["new", "-s", "test-session"],
                machine_shape="hm",
                runtime_version="2026.04",
                runtime_language="julia",
            )
        command = calls[-1]
        self.assertIn("COLAB_REMOTE_MACHINE_SHAPE=hm", command)
        self.assertIn("COLAB_REMOTE_RUNTIME_VERSION=2026.04", command)
        self.assertIn("COLAB_REMOTE_LANGUAGE=julia", command)
        self.assertIn("/plugin/scripts/colab_compat.py", command)

    def test_create_returns_session_when_post_create_probes_fail(self):
        with (
            patch.object(
                server, "_load_config", return_value=dict(server.DEFAULT_CONFIG)
            ),
            patch.object(
                server,
                "_enforce_session_limit",
                return_value={"active_sessions": 0, "max_concurrent_sessions": 8},
            ),
            patch.object(
                server,
                "_colab",
                side_effect=[completed("created"), RuntimeError("status unavailable")],
            ),
            patch.object(
                server,
                "_initialize_native_language",
                return_value={
                    "language": "python",
                    "kernel": "python3",
                    "native": True,
                },
            ),
            patch.object(
                server, "_memory_status", side_effect=RuntimeError("memory unavailable")
            ),
            patch.object(server, "_record_session"),
            patch.object(
                server, "_session_compute_metadata", return_value={"tracked": True}
            ),
        ):
            result = server.create_session("created-session", acknowledge_cost=True)
        self.assertEqual(result["session_name"], "created-session")
        self.assertFalse(result["status"]["available"])
        self.assertFalse(result["memory"]["available"])

    def test_session_compute_duration_warning(self):
        with (
            patch.object(
                server,
                "_load_session_ledger",
                return_value={"demo": {"started_at": 100, "accelerator": "A100"}},
            ),
            patch.object(
                server,
                "_load_config",
                return_value={**server.DEFAULT_CONFIG, "compute_warning_minutes": 5},
            ),
            patch.object(server.time, "time", return_value=401),
        ):
            result = server._session_compute_metadata("demo")
        self.assertEqual(result["elapsed_seconds"], 301)
        self.assertIn("consuming quota", result["warning"])
        self.assertFalse(result["exact_cost_available"])

    def test_native_languages_need_no_download_or_approval(self):
        native = {"language": "julia", "kernel": "julia", "native": True}
        with patch.object(
            server, "_initialize_native_language", return_value=native
        ) as initialize:
            prepared = server.prepare_language("test-session", "julia")
        initialize.assert_called_once_with("test-session", "julia")
        self.assertTrue(prepared["native"])
        self.assertFalse(prepared["external_download_required"])

    def test_r_and_julia_execution_use_native_kernels(self):
        with (
            patch.object(server, "_colab", return_value=completed("ok")) as colab,
            patch.object(
                server, "_load_config", return_value=dict(server.DEFAULT_CONFIG)
            ),
        ):
            r_result = server.execute_code("test-session", "print(1 + 1)", "r")
            julia_result = server.execute_code(
                "test-session", "println(1 + 1)", "julia"
            )
        self.assertEqual(r_result["exit_code"], 0)
        self.assertEqual(julia_result["exit_code"], 0)
        self.assertEqual(colab.call_args_list[0].kwargs["runtime_language"], "r")
        self.assertEqual(colab.call_args_list[1].kwargs["runtime_language"], "julia")

    def test_create_stops_allocation_when_native_kernel_fails(self):
        with (
            patch.object(
                server, "_load_config", return_value=dict(server.DEFAULT_CONFIG)
            ),
            patch.object(
                server,
                "_enforce_session_limit",
                return_value={"active_sessions": 0, "max_concurrent_sessions": 8},
            ),
            patch.object(
                server,
                "_colab",
                side_effect=[completed("created"), completed("stopped")],
            ) as colab,
            patch.object(
                server,
                "_initialize_native_language",
                side_effect=RuntimeError("kernel unavailable"),
            ),
            self.assertRaisesRegex(RuntimeError, "session was stopped"),
        ):
            server.create_session(
                "test-session", language="julia", acknowledge_cost=True
            )
        self.assertEqual(
            colab.call_args_list[1].args[0], ["stop", "-s", "test-session"]
        )

    def test_chunked_upload_and_download_verify_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.bin"
            source.write_bytes(b"abcdefgh")
            destination = root / "destination.bin"
            config = {**server.DEFAULT_CONFIG, "allowed_local_roots": [str(root)]}
            remote_hash = hashlib.sha256(source.read_bytes()).hexdigest()

            with (
                patch.object(server, "_load_config", return_value=config),
                patch.object(server, "TRANSFERS_ROOT", root / "state-transfers"),
                patch.object(server, "DIRECT_TRANSFER_LIMIT", 4),
                patch.object(server, "TRANSFER_CHUNK_SIZE", 3),
                patch.object(server, "_secure_state_root"),
                patch.object(server, "_wsl_path", side_effect=lambda path: str(path)),
                patch.object(server, "_colab", return_value=completed()) as colab,
                patch.object(server, "_remote_shell", return_value=completed()),
                patch.object(
                    server,
                    "_remote_file_metadata",
                    return_value={"bytes": 8, "sha256": remote_hash},
                ),
            ):
                uploaded = server.upload_file(
                    "test-session", str(source), "/content/source.bin"
                )
            self.assertEqual(uploaded["transfer_mode"], "chunked")
            self.assertEqual(uploaded["chunks"], 3)
            self.assertEqual(colab.call_count, 3)

            chunks = {
                "part-000000": b"abc",
                "part-000001": b"def",
                "part-000002": b"gh",
            }

            def fake_download(arguments, **_kwargs):
                Path(arguments[-1]).write_bytes(chunks[Path(arguments[3]).name])
                return completed()

            with (
                patch.object(server, "_load_config", return_value=config),
                patch.object(server, "TRANSFERS_ROOT", root / "state-transfers-2"),
                patch.object(server, "DIRECT_TRANSFER_LIMIT", 4),
                patch.object(server, "TRANSFER_CHUNK_SIZE", 3),
                patch.object(server, "_secure_state_root"),
                patch.object(server, "_wsl_path", side_effect=lambda path: str(path)),
                patch.object(server, "_colab", side_effect=fake_download),
                patch.object(
                    server,
                    "_remote_shell",
                    side_effect=[
                        completed("part-000000\npart-000001\npart-000002\n"),
                        completed(),
                    ],
                ),
                patch.object(
                    server,
                    "_remote_file_metadata",
                    return_value={"bytes": 8, "sha256": remote_hash},
                ),
            ):
                downloaded = server.download_file(
                    "test-session", "/content/source.bin", str(destination)
                )
            self.assertEqual(downloaded["transfer_mode"], "chunked")
            self.assertEqual(destination.read_bytes(), source.read_bytes())

    def test_managed_transfer_uses_configured_defaults(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.bin"
            source.write_bytes(b"data")
            config = {
                **server.DEFAULT_CONFIG,
                "allowed_local_roots": [str(root)],
                "transfer_compression": True,
                "transfer_parallelism": 7,
                "retry_attempts": 5,
            }
            with (
                patch.object(server, "_load_config", return_value=config),
                patch.object(
                    server.managed_transfer,
                    "spawn",
                    side_effect=lambda _api, state: state,
                ),
            ):
                upload = server.start_upload(
                    "test-session", str(source), "/content/source.bin"
                )
                download = server.start_download(
                    "test-session", "/content/result.bin", str(root / "result.bin")
                )
        for state in (upload, download):
            self.assertTrue(state["compress"])
            self.assertEqual(state["parallelism"], 7)
            self.assertEqual(state["retry_attempts"], 5)

    def test_transfer_chunk_retries_are_bounded(self):
        calls = []

        def flaky(name):
            calls.append(name)
            if len(calls) < 3:
                raise RuntimeError("temporary")

        state = {
            "transfer_id": "a" * 24,
            "parallelism": 1,
            "retry_attempts": 3,
        }
        with (
            patch.object(server.managed_transfer, "progress"),
            patch.object(server.managed_transfer, "cancelled", return_value=False),
            patch.object(server.managed_transfer.time, "sleep"),
        ):
            server.managed_transfer.parallel_parts(
                server, state, [("part-000000", 4)], flaky
            )
        self.assertEqual(calls, ["part-000000"] * 3)

    def test_default_drive_checkpoint_folder_is_used_when_path_is_omitted(self):
        config = {
            **server.DEFAULT_CONFIG,
            "default_drive_checkpoint_folder": "training/checkpoints",
        }
        with (
            patch.object(server, "_load_config", return_value=config),
            patch.object(server, "_ensure_drive_workspace"),
            patch.object(
                server,
                "_drive_operation",
                return_value={
                    "drive_path": "MyDrive/codex-colab/training/checkpoints/model.bin"
                },
            ) as operation,
        ):
            result = server.save_to_drive(
                "test-session", "/content/model.bin"
            )
        self.assertEqual(
            operation.call_args.args[1]["drive_path"],
            "training/checkpoints/model.bin",
        )
        self.assertIn("training/checkpoints/model.bin", result["drive_path"])

    def test_job_wrapper_tracks_progress_and_real_process(self):
        captured = {}

        def fake_shell(_session, script, timeout=300):
            captured["script"] = script
            return completed("CODEX_JOB_STARTED=train")

        with patch.object(server, "_remote_shell", side_effect=fake_shell):
            result = server.start_job(
                "test-session", "train", "python train.py", notify_on_completion=False
            )
        self.assertEqual(result["status"], "started")
        self.assertIn("CODEX_PROGRESS_FILE", captured["script"])
        self.assertIn("heartbeat", captured["script"])
        self.assertIn("exit_code", captured["script"])

    def test_job_completion_monitor_is_opt_in(self):
        with (
            patch.object(server, "_remote_shell", return_value=completed()),
            patch.object(server, "_start_monitor") as monitor,
        ):
            result = server.start_job("test-session", "train", "python train.py")
        self.assertNotIn("monitor", result)
        monitor.assert_not_called()

    def test_job_can_opt_into_recovery_and_automatic_session_stop(self):
        with (
            patch.object(server, "_remote_shell", return_value=completed()),
            patch.object(server, "_remember_recovery_job") as remember,
            patch.object(
                server,
                "_start_monitor",
                return_value={"watching": True, "watcher_pid": 1},
            ) as monitor,
        ):
            result = server.start_job(
                "test-session",
                "train",
                "python train.py",
                stop_session_on_finish=True,
                recover_on_runtime_loss=True,
            )
        self.assertTrue(result["stop_session_on_finish"])
        self.assertTrue(result["recover_on_runtime_loss"])
        remember.assert_called_once()
        monitor.assert_called_once_with("test-session", "train", 30, False, True, True)

    def test_expired_lifetime_stops_session(self):
        record = {"expires_at": 99}
        with (
            patch.object(
                server, "_load_session_ledger", return_value={"test-session": record}
            ),
            patch.object(server, "_save_lease_record"),
            patch.object(server.time, "time", return_value=100),
            patch.object(server, "stop_session") as stop,
            patch.object(server, "_write_notification"),
        ):
            server._lease_session("test-session")
        stop.assert_called_once_with("test-session", confirm=True)

    def test_manual_recovery_confirmation_is_forwarded(self):
        with (
            patch.object(
                server,
                "_load_session_ledger",
                return_value={"test-session": {"recovery_enabled": False}},
            ),
            patch.object(
                server,
                "_recover_session_impl",
                return_value={"recovered": True},
            ) as recover,
        ):
            result = server.recover_session("test-session", confirm_reallocate=True)
        self.assertTrue(result["recovered"])
        recover.assert_called_once_with("test-session", preauthorized=True)

    def test_notebook_create_edit_reorder_delete_and_import(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {**server.DEFAULT_CONFIG, "allowed_local_roots": [str(root)]}
            notebook = root / "work.ipynb"
            copied = root / "copy.ipynb"
            with patch.object(server, "_load_config", return_value=config):
                created = server.create_notebook(str(notebook), "r", "Analysis")
                server.add_notebook_cell(str(notebook), "markdown", "# Intro")
                server.add_notebook_cell(str(notebook), "code", "print(1)")
                server.edit_notebook_cell(str(notebook), 1, "print(2)")
                server.move_notebook_cell(str(notebook), 1, 0)
                current = server.read_notebook(str(notebook))
                server.delete_notebook_cell(str(notebook), 1)
                imported = server.import_notebook(str(notebook), str(copied))
            self.assertEqual(created["metadata"]["kernelspec"]["language"], "r")
            self.assertEqual(current["cells"][0]["source"], "print(2)")
            self.assertEqual(imported["cell_count"], 1)

    def test_run_selected_notebook_cell_saves_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {**server.DEFAULT_CONFIG, "allowed_local_roots": [str(root)]}
            notebook = root / "run.ipynb"
            with (
                patch.object(server, "_load_config", return_value=config),
                patch.object(
                    server,
                    "execute_code",
                    return_value={"stdout": "42\n", "stderr": "", "exit_code": 0},
                ) as execute,
            ):
                server.create_notebook(str(notebook))
                server.add_notebook_cell(str(notebook), "code", "print(42)")
                result = server.run_notebook_cells("test-session", str(notebook), [0])
                saved = server.read_notebook(str(notebook))
            self.assertTrue(result["results"][0]["success"])
            self.assertEqual(saved["cells"][0]["outputs"][0]["text"], "42\n")
            execute.assert_called_once()

    def test_drive_save_validates_notebook_and_cleans_remote_temporary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {**server.DEFAULT_CONFIG, "allowed_local_roots": [str(root)]}
            notebook = root / "drive.ipynb"
            with patch.object(server, "_load_config", return_value=config):
                server.create_notebook(str(notebook))
            with (
                patch.object(server, "_load_config", return_value=config),
                patch.object(server, "_ensure_drive_workspace") as ensure,
                patch.object(server, "upload_file", return_value={"exit_code": 0}),
                patch.object(
                    server,
                    "_drive_operation",
                    return_value={
                        "drive_path": "MyDrive/codex-colab/Projects/drive.ipynb"
                    },
                ) as drive,
                patch.object(
                    server, "_remote_shell", return_value=completed()
                ) as shell,
            ):
                result = server.save_notebook_to_drive(
                    "test-session", str(notebook), "Projects/drive.ipynb"
                )
            self.assertEqual(
                result["drive_path"],
                "MyDrive/codex-colab/Projects/drive.ipynb",
            )
            ensure.assert_called_once_with("test-session", mount_if_needed=True)
            self.assertEqual(drive.call_args.args[1]["action"], "save")
            self.assertEqual(
                drive.call_args.args[1]["drive_path"], "Projects/drive.ipynb"
            )
            self.assertIn("rm -f", shell.call_args.args[1])

    def test_general_drive_tools_pass_only_relative_workspace_paths(self):
        with (
            patch.object(server, "_ensure_drive_workspace") as ensure,
            patch.object(
                server,
                "_drive_operation",
                return_value={
                    "drive_path": "MyDrive/codex-colab/runs/checkpoint.bin"
                },
            ) as operation,
        ):
            result = server.save_to_drive(
                "test-session",
                "/content/checkpoint.bin",
                "runs/checkpoint.bin",
            )
        ensure.assert_called_once_with("test-session", mount_if_needed=True)
        self.assertEqual(
            operation.call_args.args[1],
            {
                "action": "save",
                "remote_path": "/content/checkpoint.bin",
                "drive_path": "runs/checkpoint.bin",
                "overwrite": False,
            },
        )
        self.assertEqual(
            result["drive_path"], "MyDrive/codex-colab/runs/checkpoint.bin"
        )

    def test_drive_operation_executes_helper_with_python(self):
        source = "print('drive helper')\n"
        remote_result = {
            "drive_path": "MyDrive/codex-colab/runs/checkpoint.bin"
        }
        stdout = server.drive_ops.RESULT_MARKER + server.json.dumps(remote_result)
        observed = {}

        def execute(arguments, **kwargs):
            observed["arguments"] = arguments
            helper = Path(arguments[arguments.index("-f") + 1])
            observed["source"] = helper.read_text(encoding="utf-8")
            return completed(stdout=stdout)

        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.object(server, "STATE_ROOT", Path(temporary)),
                patch.object(server, "_secure_state_root"),
                patch.object(server, "_wsl_path", side_effect=lambda path: str(path)),
                patch.object(server, "_load_session_ledger", return_value={}),
                patch.object(server.drive_ops, "remote_script", return_value=source),
                patch.object(server, "_colab", side_effect=execute),
            ):
                result = server._drive_operation(
                    "test-session", {"action": "list", "drive_path": "."}
                )
        self.assertEqual(observed["arguments"][:3], ["exec", "-s", "test-session"])
        self.assertEqual(observed["source"], source)
        self.assertEqual(result, remote_result)

    def test_drive_mount_worker_starts_official_cli_in_persistent_host(self):
        process = MagicMock(pid=1234)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                patch.object(server, "STATE_ROOT", root),
                patch.object(server, "DRIVE_MOUNTS_ROOT", root / "drive-mounts"),
                patch.object(server, "_secure_state_root"),
                patch.object(server, "_require_credentials"),
                patch.object(server, "_colab_path", return_value="/home/test/colab"),
                patch.object(server, "_linux_home", return_value="/home/test"),
                patch.object(server, "_uses_wsl", return_value=False),
                patch.object(server, "_wsl_path", side_effect=lambda path: str(path)),
                patch.object(server, "_wsl", return_value=completed()),
                patch.object(
                    server.process_utils, "background_popen", return_value=process
                ) as popen,
            ):
                result = server._start_drive_mount_worker("test-session")
        command = popen.call_args.args[0]
        self.assertIn("drive_mount_worker.py", " ".join(command))
        self.assertIn("/home/test/colab", command)
        self.assertNotIn("accounts.google.com", " ".join(command))
        self.assertFalse(
            popen.call_args.kwargs.get("windowless_python_entrypoint", False)
        )
        self.assertEqual(result["launcher_pid"], 1234)

    def test_drive_delete_needs_confirmation_before_mounting(self):
        with (
            patch.object(server, "_ensure_drive_workspace") as ensure,
            patch.object(server, "_drive_operation") as operation,
        ):
            with self.assertRaises(PermissionError):
                server.delete_drive_path("test-session", "runs/old", confirm=False)
        ensure.assert_not_called()
        operation.assert_not_called()

    def test_drive_paths_reject_traversal_before_remote_access(self):
        with (
            patch.object(server, "_ensure_drive_workspace") as ensure,
            patch.object(server, "_drive_operation") as operation,
        ):
            with self.assertRaises(ValueError):
                server.create_drive_folder("test-session", "../private")
        ensure.assert_not_called()
        operation.assert_not_called()

    def test_drive_mount_reuses_mount_and_bootstraps_workspace(self):
        workspace = {
            "mounted": True,
            "workspace_exists": True,
            "workspace_path": "/content/drive/MyDrive/codex-colab",
            "drive_path": "MyDrive/codex-colab",
        }
        with (
            patch.object(server, "_drive_mount_status", return_value={"event": "missing"}),
            patch.object(server, "_drive_operation", return_value=workspace),
            patch.object(server, "_start_drive_mount_worker") as start,
        ):
            result = server.mount_google_drive("test-session")
        start.assert_not_called()
        self.assertTrue(result["already_mounted"])
        self.assertFalse(result["authorization_required"])
        self.assertEqual(result["scope"], "MyDrive/codex-colab only")

    def test_drive_mount_keeps_worker_alive_and_opens_approval(self):
        authorization_url = "https://accounts.google.com/example"
        with (
            patch.object(
                server,
                "_drive_mount_status",
                return_value={"event": "missing"},
            ),
            patch.object(
                server, "_drive_operation", side_effect=RuntimeError("not mounted")
            ),
            patch.object(server, "_start_drive_mount_worker") as start,
            patch.object(
                server,
                "_wait_for_drive_mount",
                return_value={
                    "event": "authorization_required",
                    "worker_pid": 42,
                },
            ),
            patch.object(
                server,
                "_consume_drive_authorization_url",
                return_value=authorization_url,
            ),
            patch.object(server.webbrowser, "open", return_value=True) as browser,
        ):
            result = server.mount_google_drive("test-session")
        start.assert_called_once_with("test-session")
        browser.assert_called_once_with(authorization_url, new=2)
        self.assertTrue(result["authorization_required"])
        self.assertTrue(result["browser_opened"])
        self.assertTrue(result["mount_in_progress"])
        self.assertNotIn("authorization_url", result)

    def test_complete_drive_mount_resumes_same_worker_and_bootstraps(self):
        workspace = {
            "mounted": True,
            "workspace_exists": True,
            "workspace_path": "/content/drive/MyDrive/codex-colab",
            "drive_path": "MyDrive/codex-colab",
        }
        with (
            patch.object(
                server,
                "_drive_mount_status",
                return_value={"event": "authorization_required", "worker_pid": 42},
            ),
            patch.object(server, "_drive_mount_worker_alive", return_value=True),
            patch.object(server, "_request_drive_mount_resume") as resume,
            patch.object(
                server,
                "_wait_for_drive_mount",
                return_value={"event": "completed", "exit_code": 0},
            ),
            patch.object(server, "_drive_operation", return_value=workspace),
        ):
            result = server.complete_google_drive_mount("test-session", wait_seconds=30)
        resume.assert_called_once_with("test-session")
        self.assertFalse(result["authorization_required"])
        self.assertFalse(result["mount_in_progress"])
        self.assertEqual(result["workspace_path"], workspace["workspace_path"])

    def test_transfer_cancel_and_resume_preserve_state(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(server, "TRANSFERS_ROOT", Path(temporary)),
            patch.object(server, "_secure_state_root"),
        ):
            transfer_id = "a" * 24
            state = {
                "transfer_id": transfer_id,
                "status": "failed",
                "direction": "upload",
                "error": "interrupted",
            }
            server.managed_transfer.save_state(server, state)
            cancelled = server.cancel_transfer(transfer_id, confirm=True)
            cancelled["status"] = "cancelled"
            server.managed_transfer.save_state(server, cancelled)
            with patch.object(
                server.managed_transfer,
                "spawn",
                return_value={"transfer_id": transfer_id, "status": "starting"},
            ) as spawn:
                resumed = server.resume_transfer(transfer_id)
            self.assertTrue(
                (
                    Path(temporary) / "managed" / transfer_id / "cancel.requested"
                ).exists()
            )
            self.assertEqual(resumed["status"], "starting")
            self.assertNotIn("error", spawn.call_args.args[1])

    def test_transfer_archive_round_trip_and_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "folder"
            source.mkdir()
            (source / "data.txt").write_text("hello", encoding="utf-8")
            archive = root / "payload.tar.gz"
            destination = root / "restored"
            server.managed_transfer.create_archive(source, archive)
            server.managed_transfer.safe_extract(archive, destination, False)
            self.assertEqual(
                (destination / "data.txt").read_text(encoding="utf-8"), "hello"
            )

            unsafe = root / "unsafe.tar.gz"
            with tarfile.open(unsafe, "w:gz") as bundle:
                payload = b"bad"
                member = tarfile.TarInfo("../outside.txt")
                member.size = len(payload)
                bundle.addfile(member, io.BytesIO(payload))
            with self.assertRaisesRegex(ValueError, "unsafe"):
                server.managed_transfer.safe_extract(
                    unsafe, root / "unsafe-output", False
                )

    def test_monitor_is_backgrounded_and_rejects_missing_job(self):
        with (
            patch.object(server, "_job_status_impl", return_value={"exists": False}),
            self.assertRaises(ValueError),
        ):
            server._start_monitor("test-session", "missing", 30)

        process = type("Process", (), {"pid": 4321})()
        with (
            patch.object(server, "_job_status_impl", return_value={"exists": True}),
            patch.object(server, "_load_monitor_ledger", return_value={}),
            patch.object(server, "_save_monitor_record") as save,
            patch.object(
                server.process_utils, "background_popen", return_value=process
            ) as popen,
        ):
            result = server._start_monitor("test-session", "background", 30)
        self.assertEqual(result["watcher_pid"], 4321)
        self.assertFalse(result["already_running"])
        self.assertTrue(popen.call_args.kwargs["windowless_python_entrypoint"])
        save.assert_called_once()

    def test_session_lease_uses_windowless_background_python(self):
        process = MagicMock(pid=9876)
        with tempfile.TemporaryDirectory() as temporary:
            lease_path = Path(temporary) / "lease.json"
            with (
                patch.object(
                    server,
                    "_load_session_ledger",
                    return_value={"test-session": {"expires_at": 9999999999}},
                ),
                patch.object(server, "_lease_path", return_value=lease_path),
                patch.object(server, "_save_lease_record") as save,
                patch.object(
                    server.process_utils, "background_popen", return_value=process
                ) as popen,
            ):
                result = server._start_session_lease("test-session")
        self.assertEqual(result["watcher_pid"], 9876)
        self.assertTrue(popen.call_args.kwargs["windowless_python_entrypoint"])
        save.assert_called_once()

    def test_managed_transfer_uses_windowless_background_python(self):
        process = MagicMock(pid=2468)
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.object(server, "TRANSFERS_ROOT", Path(temporary)),
                patch.object(server, "_secure_state_root"),
                patch.object(
                    server.process_utils, "background_popen", return_value=process
                ) as popen,
            ):
                state = {"transfer_id": "a" * 24}
                result = server.managed_transfer.spawn(server, state)
        self.assertEqual(result["worker_pid"], 2468)
        self.assertTrue(popen.call_args.kwargs["windowless_python_entrypoint"])

    def test_monitor_records_use_collision_safe_files(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(server, "STATE_ROOT", Path(temporary)),
            patch.object(server, "MONITORS_ROOT", Path(temporary) / "monitors"),
            patch.object(server, "_secure_state_root"),
        ):
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [
                    pool.submit(
                        server._save_monitor_record,
                        "test-session",
                        "job",
                        {"session_name": "test-session", "job_name": "job", "n": n},
                    )
                    for n in range(8)
                ]
                for future in futures:
                    future.result()
            records = server._load_monitor_ledger()
        self.assertEqual(set(records), {"test-session/job"})

    def test_remote_workdir_rejects_traversal(self):
        with self.assertRaises(ValueError):
            server._validate_remote_workdir("/content/../root")
        with self.assertRaises(ValueError):
            server._validate_remote_path("--help")

    def test_notification_history_contains_no_credentials(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(server, "STATE_ROOT", Path(temporary) / "state"),
            patch.object(
                server, "CONFIG_PATH", Path(temporary) / "state" / "config.json"
            ),
            patch.object(
                server,
                "NOTIFICATIONS_PATH",
                Path(temporary) / "state" / "notifications.jsonl",
            ),
            patch.object(
                server,
                "_load_config",
                return_value={**server.DEFAULT_CONFIG, "notification_mode": "off"},
            ),
        ):
            server._write_notification("Done", "safe")
            rows = server.notification_history()
        self.assertEqual(rows[0]["message"], "safe")

    def test_desktop_notifications_are_disabled_by_default(self):
        self.assertEqual(server.DEFAULT_CONFIG["notification_mode"], "off")
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(server, "STATE_ROOT", Path(temporary) / "state"),
            patch.object(
                server,
                "NOTIFICATIONS_PATH",
                Path(temporary) / "state" / "notifications.jsonl",
            ),
            patch.object(server, "_load_config", return_value=server.DEFAULT_CONFIG),
            patch.object(server, "_run") as run,
        ):
            result = server._write_notification("Title", "Body")
        self.assertFalse(result["desktop_delivered"])
        run.assert_not_called()

    def test_failures_only_notification_mode_suppresses_success(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(server, "STATE_ROOT", Path(temporary) / "state"),
            patch.object(
                server,
                "NOTIFICATIONS_PATH",
                Path(temporary) / "state" / "notifications.jsonl",
            ),
            patch.object(
                server,
                "_load_config",
                return_value={
                    **server.DEFAULT_CONFIG,
                    "notification_mode": "failures_only",
                },
            ),
            patch.object(server.sys, "platform", "win32"),
            patch.object(server, "_run", return_value=completed()) as run,
        ):
            success = server._write_notification("Done", "ok", "success")
            failure = server._write_notification("Failed", "bad", "warning")
        self.assertFalse(success["desktop_delivered"])
        self.assertTrue(failure["desktop_delivered"])
        run.assert_called_once()

    def test_per_job_popup_opt_out_overrides_enabled_global_setting(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(server, "STATE_ROOT", Path(temporary) / "state"),
            patch.object(
                server,
                "NOTIFICATIONS_PATH",
                Path(temporary) / "state" / "notifications.jsonl",
            ),
            patch.object(
                server,
                "_load_config",
                return_value={**server.DEFAULT_CONFIG, "notification_mode": "all"},
            ),
            patch.object(server, "_run") as run,
        ):
            result = server._write_notification("Title", "Body", desktop_popup=False)
        self.assertFalse(result["desktop_delivered"])
        run.assert_not_called()

    def test_windows_notification_uses_one_toast_without_tray_balloon(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(server, "STATE_ROOT", Path(temporary) / "state"),
            patch.object(
                server,
                "NOTIFICATIONS_PATH",
                Path(temporary) / "state" / "notifications.jsonl",
            ),
            patch.object(
                server,
                "_load_config",
                return_value={**server.DEFAULT_CONFIG, "notification_mode": "all"},
            ),
            patch.object(server.sys, "platform", "win32"),
            patch.object(server, "_run", return_value=completed()) as run,
        ):
            result = server._write_notification("Title", "Body")
        self.assertTrue(result["desktop_delivered"])
        run.assert_called_once()
        command = run.call_args.args[0]
        self.assertEqual(command[0], "powershell.exe")
        script = command[-1]
        self.assertIn("ToastNotificationManager", script)
        self.assertNotIn("NotifyIcon", script)

    def test_macos_notification_uses_osascript_arguments(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(server, "STATE_ROOT", Path(temporary) / "state"),
            patch.object(
                server,
                "NOTIFICATIONS_PATH",
                Path(temporary) / "state" / "notifications.jsonl",
            ),
            patch.object(
                server,
                "_load_config",
                return_value={**server.DEFAULT_CONFIG, "notification_mode": "all"},
            ),
            patch.object(server.sys, "platform", "darwin"),
            patch.object(server, "_run", return_value=completed()) as run,
        ):
            result = server._write_notification("Title", "Body")
        self.assertTrue(result["desktop_delivered"])
        self.assertEqual(run.call_args.args[0][0], "osascript")
        self.assertEqual(run.call_args.args[0][-2:], ["Title", "Body"])

    def test_linux_notification_uses_notify_send_when_available(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(server, "STATE_ROOT", Path(temporary) / "state"),
            patch.object(
                server,
                "NOTIFICATIONS_PATH",
                Path(temporary) / "state" / "notifications.jsonl",
            ),
            patch.object(
                server,
                "_load_config",
                return_value={**server.DEFAULT_CONFIG, "notification_mode": "all"},
            ),
            patch.object(server.sys, "platform", "linux"),
            patch.object(server.shutil, "which", return_value="/usr/bin/notify-send"),
            patch.object(server, "_run", return_value=completed()) as run,
        ):
            result = server._write_notification("Title", "Body")
        self.assertTrue(result["desktop_delivered"])
        self.assertEqual(run.call_args.args[0][0], "notify-send")


if __name__ == "__main__":
    unittest.main()
