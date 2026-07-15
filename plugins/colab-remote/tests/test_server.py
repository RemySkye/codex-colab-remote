import importlib.util
from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SERVER_PATH = Path(__file__).resolve().parents[1] / "mcp" / "server.py"
SPEC = importlib.util.spec_from_file_location("colab_remote_server", SERVER_PATH)
server = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(server)


def completed(stdout="", stderr="", returncode=0):
    return server.subprocess.CompletedProcess([], returncode, stdout, stderr)


class ServerTests(unittest.TestCase):
    def test_subprocesses_cannot_read_mcp_stdin(self):
        with patch.object(server.subprocess, "run", return_value=completed()) as run:
            server._run(["example"])
        self.assertEqual(run.call_args.kwargs["stdin"], server.subprocess.DEVNULL)
        self.assertNotIn("input", run.call_args.kwargs)

    def test_expected_tools_are_registered(self):
        names = {tool.name for tool in server.mcp._tool_manager.list_tools()}
        self.assertEqual(
            names,
            {
                "authentication_instructions",
                "create_session",
                "credential_status",
                "doctor",
                "disable_ssh",
                "download_file",
                "execute_code",
                "execute_file",
                "enable_ssh",
                "get_config",
                "get_logs",
                "install_packages",
                "job_logs",
                "job_status",
                "list_files",
                "list_sessions",
                "notification_history",
                "prepare_language",
                "restart_kernel",
                "session_status",
                "session_url",
                "set_config",
                "ssh_download",
                "ssh_exec",
                "ssh_requirements",
                "ssh_status",
                "ssh_upload",
                "start_job",
                "stop_job",
                "stop_session",
                "test_notification",
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

    def test_json_marker_ignores_terminal_control_codes(self):
        output = '\x1b[32mCODEX_JOB_STATUS={"exists":true,"status":"running"}\x1b[0m prompt>'
        result = server._extract_json_marker(output, "CODEX_JOB_STATUS=")
        self.assertTrue(result["exists"])

    def test_remote_shell_uses_real_remote_exit_code(self):
        payload = 'CODEX_REMOTE_SHELL={"returncode":7,"stdout":"","stderr":"failed"}\n'
        with patch.object(server, "_colab", return_value=completed(payload)), self.assertRaisesRegex(
            RuntimeError, "failed"
        ):
            server._remote_shell("test-session", "exit 7")

    def test_config_requires_confirmation_for_local_roots(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            server, "STATE_ROOT", Path(temporary) / "state"
        ), patch.object(server, "CONFIG_PATH", Path(temporary) / "state" / "config.json"), patch.object(
            server, "NOTIFICATIONS_PATH", Path(temporary) / "state" / "notifications.jsonl"
        ):
            with self.assertRaises(PermissionError):
                server.set_config(allowed_local_roots=[temporary])
            result = server.set_config(
                allowed_local_roots=[temporary], confirm_sensitive_change=True
            )
            self.assertEqual(result["allowed_local_roots"], [str(Path(temporary).resolve())])

    def test_config_requires_confirmation_to_enable_ssh(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            server, "STATE_ROOT", Path(temporary) / "state"
        ), patch.object(server, "CONFIG_PATH", Path(temporary) / "state" / "config.json"), patch.object(
            server, "_secure_state_root", lambda: (Path(temporary) / "state").mkdir(parents=True, exist_ok=True)
        ):
            with self.assertRaises(PermissionError):
                server.set_config(ssh_tunnel_enabled=True)
            result = server.set_config(ssh_tunnel_enabled=True, confirm_sensitive_change=True)
        self.assertTrue(result["ssh_tunnel_enabled"])

    def test_ssh_requires_config_and_two_acknowledgements(self):
        disabled = {**server.DEFAULT_CONFIG, "ssh_tunnel_enabled": False}
        enabled = {**server.DEFAULT_CONFIG, "ssh_tunnel_enabled": True}
        with patch.object(server, "_load_config", return_value=disabled), self.assertRaises(PermissionError):
            server.enable_ssh("test-session", True, True)
        with patch.object(server, "_load_config", return_value=enabled), self.assertRaises(PermissionError):
            server.enable_ssh("test-session")
        with patch.object(server, "_load_config", return_value=enabled), self.assertRaises(PermissionError):
            server.enable_ssh("test-session", acknowledge_colab_policy=True)

    def test_ssh_bootstrap_and_client_are_key_only_and_pinned(self):
        template = (SERVER_PATH.parents[1] / "assets" / "bootstrap_ssh.py.tmpl").read_text(encoding="utf-8")
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
        self.assertEqual(server._redact("authtoken: secret-value"), "authtoken: [REDACTED]")

    def test_disable_ssh_retains_retry_state_when_remote_revoke_fails(self):
        with patch.object(server, "_remote_shell", side_effect=RuntimeError("runtime unavailable")), patch.object(
            server, "_delete_local_ssh_state"
        ) as delete_state:
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
        with patch.object(server, "_ssh_state_path") as state_path, patch.object(
            server, "disable_ssh", return_value=failed_cleanup
        ), patch.object(server, "_colab", side_effect=[completed(), completed()]), patch.object(
            server, "_delete_local_ssh_state"
        ) as delete_state, patch.object(server, "_load_session_ledger", return_value={}), patch.object(
            server, "_save_session_ledger"
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
                key.with_suffix(".pub").write_text("ssh-ed25519 AAAA test", encoding="utf-8")
                return completed()
            return completed("CODEX_SSH_OK")

        with tempfile.TemporaryDirectory() as temporary, patch.object(
            server, "SSH_ROOT", Path(temporary) / "ssh"
        ), patch.object(
            server, "_load_config", return_value={**server.DEFAULT_CONFIG, "ssh_tunnel_enabled": True}
        ), patch.object(server.shutil, "which", return_value="present"), patch.object(
            server.secrets, "token_urlsafe", return_value="fixed-nonce"
        ), patch.object(
            server, "_colab", return_value=completed("CODEX_SSH_MANIFEST=" + server.json.dumps(manifest))
        ), patch.object(server, "_run", side_effect=fake_run), patch.object(server, "_secure_state_root"):
            result = server.enable_ssh("test-session", True, True)
            state = server._load_ssh_state("test-session")
        self.assertTrue(result["connected"])
        self.assertEqual(state["host"], "example.test")
        self.assertFalse(result["root_access"])

    def test_local_file_access_is_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            server, "STATE_ROOT", Path(temporary) / "state"
        ), patch.object(server, "CONFIG_PATH", Path(temporary) / "state" / "config.json"):
            with self.assertRaises(PermissionError):
                server._allowed_local_path(str(Path(temporary) / "file.py"), must_exist=False)

    def test_official_cli_forces_oauth2_and_unsets_adc(self):
        calls = []

        def fake_wsl(args, **_kwargs):
            calls.append(args)
            return completed()

        with patch.object(server, "_require_credentials"), patch.object(
            server, "_colab_path", return_value="/home/test/.local/bin/colab"
        ), patch.object(server, "_wsl", side_effect=fake_wsl):
            server._colab(["sessions"])
        command = calls[-1]
        self.assertIn("GOOGLE_APPLICATION_CREDENTIALS", command)
        self.assertIn("CLOUDSDK_CONFIG", command)
        self.assertIn("oauth2", command)
        self.assertNotIn("adc", command)

    def test_authentication_instructions_secure_new_token(self):
        with patch.object(server, "_distro", return_value="Ubuntu"):
            result = server.authentication_instructions()
        self.assertIn("umask 077", result["command"])
        self.assertIn("chmod 600", result["command"])

    def test_create_requires_cost_acknowledgement(self):
        with patch.object(server, "_load_config", return_value=dict(server.DEFAULT_CONFIG)):
            with self.assertRaises(PermissionError):
                server.create_session("test-session", accelerator="A100")

    def test_cost_acknowledgement_cannot_be_disabled_in_config_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            config_path = state / "config.json"
            config_path.write_text('{"require_cost_acknowledgement": false}', encoding="utf-8")
            with patch.object(server, "STATE_ROOT", state), patch.object(
                server, "CONFIG_PATH", config_path
            ), patch.object(server, "_secure_state_root"):
                self.assertTrue(server._load_config()["require_cost_acknowledgement"])

    def test_create_reports_high_ram_as_unavailable(self):
        with patch.object(server, "_load_config", return_value=dict(server.DEFAULT_CONFIG)), patch.object(
            server, "_colab", return_value=completed("ok")
        ), patch.object(server, "_memory_status", return_value={"bytes": 16, "gib": 16.0}), patch.object(
            server, "_record_session"
        ), patch.object(server, "_session_compute_metadata", return_value={"tracked": True}):
            result = server.create_session(
                "test-session", accelerator="cpu", prefer_high_ram=True, acknowledge_cost=True
            )
        self.assertTrue(any("cannot be requested" in warning for warning in result["warnings"]))

    def test_create_returns_session_when_post_create_probes_fail(self):
        with patch.object(server, "_load_config", return_value=dict(server.DEFAULT_CONFIG)), patch.object(
            server, "_colab", side_effect=[completed("created"), RuntimeError("status unavailable")]
        ), patch.object(server, "_memory_status", side_effect=RuntimeError("memory unavailable")), patch.object(
            server, "_record_session"
        ), patch.object(server, "_session_compute_metadata", return_value={"tracked": True}):
            result = server.create_session("created-session", acknowledge_cost=True)
        self.assertEqual(result["session_name"], "created-session")
        self.assertFalse(result["status"]["available"])
        self.assertFalse(result["memory"]["available"])

    def test_session_compute_duration_warning(self):
        with patch.object(
            server,
            "_load_session_ledger",
            return_value={"demo": {"started_at": 100, "accelerator": "A100"}},
        ), patch.object(
            server, "_load_config", return_value={**server.DEFAULT_CONFIG, "compute_warning_minutes": 5}
        ), patch.object(server.time, "time", return_value=401):
            result = server._session_compute_metadata("demo")
        self.assertEqual(result["elapsed_seconds"], 301)
        self.assertIn("consuming quota", result["warning"])
        self.assertFalse(result["exact_cost_available"])

    def test_julia_download_requires_acknowledgement(self):
        with self.assertRaises(PermissionError):
            server.prepare_language("test-session", "julia")

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

    def test_monitor_is_detached_and_rejects_missing_job(self):
        with patch.object(server, "_job_status_impl", return_value={"exists": False}), self.assertRaises(ValueError):
            server._start_monitor("test-session", "missing", 30)

        process = type("Process", (), {"pid": 4321})()
        with patch.object(server, "_job_status_impl", return_value={"exists": True}), patch.object(
            server, "_load_monitor_ledger", return_value={}
        ), patch.object(server, "_save_monitor_record") as save, patch.object(
            server.subprocess, "Popen", return_value=process
        ):
            result = server._start_monitor("test-session", "detached", 30)
        self.assertEqual(result["watcher_pid"], 4321)
        self.assertFalse(result["already_running"])
        save.assert_called_once()

    def test_monitor_records_use_collision_safe_files(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            server, "STATE_ROOT", Path(temporary)
        ), patch.object(server, "MONITORS_ROOT", Path(temporary) / "monitors"), patch.object(
            server, "_secure_state_root"
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
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            server, "STATE_ROOT", Path(temporary) / "state"
        ), patch.object(server, "CONFIG_PATH", Path(temporary) / "state" / "config.json"), patch.object(
            server, "NOTIFICATIONS_PATH", Path(temporary) / "state" / "notifications.jsonl"
        ), patch.object(
            server,
            "_load_config",
            return_value={**server.DEFAULT_CONFIG, "notifications_enabled": False},
        ):
            server._write_notification("Done", "safe")
            rows = server.notification_history()
        self.assertEqual(rows[0]["message"], "safe")


if __name__ == "__main__":
    unittest.main()
