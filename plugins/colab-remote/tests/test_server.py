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

    def test_expected_tools_are_registered(self):
        names = {tool.name for tool in server.mcp._tool_manager.list_tools()}
        self.assertEqual(
            names,
            {
                "add_notebook_cell",
                "authentication_instructions",
                "cancel_transfer",
                "create_notebook",
                "create_session",
                "credential_status",
                "delete_notebook_cell",
                "doctor",
                "disable_ssh",
                "download_file",
                "edit_notebook_cell",
                "execute_code",
                "execute_file",
                "enable_ssh",
                "export_session_notebook",
                "get_config",
                "get_logs",
                "import_notebook",
                "install_packages",
                "job_logs",
                "job_status",
                "list_files",
                "list_sessions",
                "list_transfers",
                "load_notebook_from_drive",
                "mount_google_drive",
                "move_notebook_cell",
                "notification_history",
                "prepare_language",
                "prepare_ssh_browser",
                "read_notebook",
                "recover_session",
                "recovery_status",
                "register_ssh_manifest",
                "restart_kernel",
                "resume_transfer",
                "run_notebook_cells",
                "save_notebook_to_drive",
                "session_status",
                "session_url",
                "set_config",
                "set_session_lifetime",
                "ssh_download",
                "ssh_exec",
                "ssh_requirements",
                "ssh_status",
                "ssh_upload",
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
        with (
            patch.object(server, "_colab", return_value=completed(payload)) as colab,
            self.assertRaisesRegex(RuntimeError, "failed"),
        ):
            server._remote_shell("test-session", "exit 7")
        self.assertEqual(colab.call_args.args[0][:3], ["console", "-s", "test-session"])
        self.assertIn("python3", colab.call_args.kwargs["input_text"])

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

    def test_terminal_exec_uses_official_cli_without_ssh(self):
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
        self.assertFalse(result["ssh_required"])
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

    def test_config_requires_confirmation_to_enable_ssh(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(server, "STATE_ROOT", Path(temporary) / "state"),
            patch.object(
                server, "CONFIG_PATH", Path(temporary) / "state" / "config.json"
            ),
            patch.object(
                server,
                "_secure_state_root",
                lambda: (Path(temporary) / "state").mkdir(parents=True, exist_ok=True),
            ),
        ):
            with self.assertRaises(PermissionError):
                server.set_config(ssh_tunnel_enabled=True)
            result = server.set_config(
                ssh_tunnel_enabled=True, confirm_sensitive_change=True
            )
        self.assertTrue(result["ssh_tunnel_enabled"])

    def test_ssh_requires_config_and_two_acknowledgements(self):
        disabled = {**server.DEFAULT_CONFIG, "ssh_tunnel_enabled": False}
        enabled = {**server.DEFAULT_CONFIG, "ssh_tunnel_enabled": True}
        with (
            patch.object(server, "_load_config", return_value=disabled),
            self.assertRaises(PermissionError),
        ):
            server.enable_ssh("test-session", True, True)
        with (
            patch.object(server, "_load_config", return_value=enabled),
            self.assertRaises(PermissionError),
        ):
            server.enable_ssh("test-session")
        with (
            patch.object(server, "_load_config", return_value=enabled),
            self.assertRaises(PermissionError),
        ):
            server.enable_ssh("test-session", acknowledge_colab_policy=True)

    def test_ssh_bootstrap_and_client_are_key_only_and_pinned(self):
        template = (
            SERVER_PATH.parents[1] / "assets" / "bootstrap_ssh.py.tmpl"
        ).read_text(encoding="utf-8")
        self.assertIn("PasswordAuthentication no", template)
        self.assertIn("AuthenticationMethods publickey", template)
        self.assertIn('["usermod", "-p", "*", "codex"]', template)
        self.assertIn("PermitRootLogin no", template)
        self.assertIn("AllowTcpForwarding no", template)
        self.assertNotIn("NOPASSWD", template)
        self.assertNotIn('"config", "add-authtoken"', template)
        self.assertIn("json.dumps(token)", template)
        state = {
            "private_key": "key",
            "port": 1234,
            "known_hosts": "known",
            "host": "example.test",
        }
        arguments = server._ssh_base(state)
        self.assertIn("StrictHostKeyChecking=yes", arguments)
        self.assertIn("BatchMode=yes", arguments)

    def test_redacts_labeled_ngrok_tokens(self):
        self.assertEqual(
            server._redact("NGROK_AUTHTOKEN=not-a-real-token"),
            "NGROK_AUTHTOKEN=[REDACTED]",
        )
        self.assertEqual(
            server._redact("authtoken: secret-value"), "authtoken: [REDACTED]"
        )

    def test_disable_ssh_retains_retry_state_when_remote_revoke_fails(self):
        with (
            patch.object(
                server, "_remote_shell", side_effect=RuntimeError("runtime unavailable")
            ),
            patch.object(server, "_delete_local_ssh_state") as delete_state,
        ):
            result = server.disable_ssh("test-session", confirm=True)
        self.assertFalse(result["closed"])
        self.assertTrue(result["retry_required"])
        self.assertFalse(result["private_key_deleted"])
        delete_state.assert_not_called()

    def test_stop_session_deletes_retry_state_after_verified_vm_stop(self):
        failed_cleanup = {
            "closed": False,
            "remote_revoked": False,
            "private_key_deleted": False,
            "retry_required": True,
        }
        with (
            patch.object(server, "_ssh_state_path") as state_path,
            patch.object(server, "disable_ssh", return_value=failed_cleanup),
            patch.object(server, "_colab", side_effect=[completed(), completed()]),
            patch.object(server, "_delete_local_ssh_state") as delete_state,
            patch.object(server, "_load_session_ledger", return_value={}),
            patch.object(server, "_save_session_ledger"),
        ):
            state_path.return_value.exists.return_value = True
            result = server.stop_session("test-session", confirm=True)
        delete_state.assert_called_once_with("test-session")
        self.assertTrue(result["ssh_cleanup"]["closed"])
        self.assertTrue(result["ssh_cleanup"]["terminated_by_session_stop"])

    def test_enable_ssh_registers_verified_manifest(self):
        host_key = "ssh-ed25519 " + server.base64.b64encode(b"0" * 32).decode()
        manifest = {
            "session_name": "test-session",
            "nonce": "fixed-nonce",
            "endpoint": "tcp://example.test:12345",
            "host_key": host_key,
            "host_fingerprint": "SHA256:test",
            "runtime": {"actual_gpu": None},
        }

        def fake_run(arguments, **_kwargs):
            if arguments[0] == "ssh-keygen":
                key = Path(arguments[-1])
                key.write_text("private", encoding="utf-8")
                key.with_suffix(".pub").write_text(
                    "ssh-ed25519 AAAA test", encoding="utf-8"
                )
                return completed()
            return completed("CODEX_SSH_OK")

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(server, "SSH_ROOT", Path(temporary) / "ssh"),
            patch.object(
                server,
                "_load_config",
                return_value={**server.DEFAULT_CONFIG, "ssh_tunnel_enabled": True},
            ),
            patch.object(server.shutil, "which", return_value="present"),
            patch.object(server.secrets, "token_urlsafe", return_value="fixed-nonce"),
            patch.object(
                server,
                "_colab",
                return_value=completed(
                    "CODEX_SSH_MANIFEST=" + server.json.dumps(manifest)
                ),
            ),
            patch.object(server, "_run", side_effect=fake_run),
            patch.object(server, "_secure_state_root"),
        ):
            result = server.enable_ssh("test-session", True, True)
            state = server._load_ssh_state("test-session")
        self.assertTrue(result["connected"])
        self.assertEqual(state["host"], "example.test")
        self.assertFalse(result["root_access"])

    def test_prepare_ssh_browser_returns_attached_notebook_bootstrap(self):
        def fake_run(arguments, **_kwargs):
            key = Path(arguments[-1])
            key.write_text("private", encoding="utf-8")
            key.with_suffix(".pub").write_text(
                "ssh-ed25519 AAAA test", encoding="utf-8"
            )
            return completed()

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(server, "SSH_ROOT", Path(temporary) / "ssh"),
            patch.object(
                server,
                "_load_config",
                return_value={**server.DEFAULT_CONFIG, "ssh_tunnel_enabled": True},
            ),
            patch.object(server.shutil, "which", return_value="present"),
            patch.object(server.secrets, "token_urlsafe", return_value="fixed-nonce"),
            patch.object(
                server,
                "_colab",
                return_value=completed("https://colab.example/notebook"),
            ),
            patch.object(server, "_run", side_effect=fake_run),
            patch.object(server, "_secure_state_root"),
        ):
            result = server.prepare_ssh_browser("test-session", True, True)
            state = server._load_ssh_state("test-session")
        self.assertTrue(result["browser_bootstrap_required"])
        self.assertIn("userdata.get", result["bootstrap_code"])
        self.assertEqual(result["session_url"], "https://colab.example/notebook")
        self.assertTrue(state["pending_browser_bootstrap"])

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

    def test_cost_acknowledgement_cannot_be_disabled_in_config_file(self):
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
                self.assertTrue(server._load_config()["require_cost_acknowledgement"])

    def test_create_requests_high_ram(self):
        with (
            patch.object(
                server, "_load_config", return_value=dict(server.DEFAULT_CONFIG)
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
                prefer_high_ram=True,
                acknowledge_cost=True,
            )
        self.assertTrue(result["prefer_high_ram"])
        self.assertEqual(colab.call_args_list[0].kwargs["machine_shape"], "hm")

    def test_create_can_disable_high_ram_and_prefer_latest_runtime(self):
        with (
            patch.object(
                server,
                "_load_config",
                return_value={**server.DEFAULT_CONFIG, "prefer_high_ram": True},
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
                prefer_high_ram=False,
                runtime_version="latest",
                acknowledge_cost=True,
            )
        self.assertFalse(result["prefer_high_ram"])
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
        self.assertNotIn("ngrok", captured["script"].lower())

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
        monitor.assert_called_once_with("test-session", "train", 30, True, True)

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
                patch.object(server, "mount_google_drive") as mount,
                patch.object(server, "upload_file", return_value={"exit_code": 0}),
                patch.object(
                    server, "_remote_shell", return_value=completed()
                ) as shell,
            ):
                result = server.save_notebook_to_drive(
                    "test-session", str(notebook), "Projects/drive.ipynb"
                )
            self.assertEqual(result["drive_path"], "MyDrive/Projects/drive.ipynb")
            mount.assert_called_once_with("test-session")
            self.assertIn("rm -f", shell.call_args.args[1])

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

    def test_monitor_is_detached_and_rejects_missing_job(self):
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
            patch.object(server.subprocess, "Popen", return_value=process),
        ):
            result = server._start_monitor("test-session", "detached", 30)
        self.assertEqual(result["watcher_pid"], 4321)
        self.assertFalse(result["already_running"])
        save.assert_called_once()

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
                return_value={**server.DEFAULT_CONFIG, "notifications_enabled": False},
            ),
        ):
            server._write_notification("Done", "safe")
            rows = server.notification_history()
        self.assertEqual(rows[0]["message"], "safe")

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
                return_value={**server.DEFAULT_CONFIG, "notifications_enabled": True},
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
                return_value={**server.DEFAULT_CONFIG, "notifications_enabled": True},
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
