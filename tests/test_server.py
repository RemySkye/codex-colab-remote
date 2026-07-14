import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SERVER_PATH = Path(__file__).resolve().parents[1] / "mcp" / "server.py"
SPEC = importlib.util.spec_from_file_location("colab_ssh_server", SERVER_PATH)
server = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(server)


class ServerTests(unittest.TestCase):
    def test_expected_tools_are_registered(self):
        names = {tool.name for tool in server.mcp._tool_manager.list_tools()}
        self.assertEqual(
            names,
            {
                "prepare_session",
                "register_session",
                "list_sessions",
                "session_status",
                "ssh_exec",
                "upload_file",
                "download_file",
                "start_job",
                "job_status",
                "job_logs",
                "stop_job",
                "close_session",
            },
        )

    def test_prepare_creates_key_and_valid_notebook(self):
        with tempfile.TemporaryDirectory() as temporary:
            sessions = Path(temporary) / "sessions"
            with patch.object(server, "SESSIONS_ROOT", sessions):
                result = server.prepare_session("gpu")
                state = json.loads(
                    (sessions / result["session_id"] / "state.json").read_text(encoding="utf-8")
                )
                self.assertTrue(Path(state["private_key"]).exists())
                notebook = json.loads(Path(result["notebook_path"]).read_text(encoding="utf-8"))
                code = "".join(notebook["cells"][0]["source"])
                compile(code, "bootstrap.py", "exec")
                self.assertNotIn("__SESSION_ID_JSON__", code)
                self.assertIn(result["session_id"], code)
                self.assertNotIn("PRIVATE KEY", code)

    def test_scp_uses_uppercase_port_and_host_key_pinning(self):
        state = {
            "connected": True,
            "private_key": "key",
            "port": 1234,
            "known_hosts": "known",
            "host": "example.test",
        }
        args = server._scp_base(state)
        self.assertIn("-P", args)
        self.assertNotIn("-p", args)
        self.assertIn("StrictHostKeyChecking=yes", args)

    def test_manifest_nonce_is_required_before_network_access(self):
        with tempfile.TemporaryDirectory() as temporary:
            sessions = Path(temporary) / "sessions"
            with patch.object(server, "SESSIONS_ROOT", sessions):
                state = {
                    "session_id": "colab-20260714-1234abcd",
                    "nonce": "correct",
                    "private_key": "unused",
                    "known_hosts": str(Path(temporary) / "known_hosts"),
                    "connected": False,
                }
                server._save(state)
                manifest = {"session_id": state["session_id"], "nonce": "wrong"}
                with self.assertRaisesRegex(ValueError, "nonce"):
                    server.register_session(json.dumps(manifest))

    def test_job_wrapper_tracks_real_process_heartbeat(self):
        captured = {}

        def fake_remote(_state, script, timeout=300):
            captured["script"] = script
            return type("Result", (), {"stdout": "train\n", "stderr": ""})()

        with patch.object(server, "_load", return_value={"connected": True}), patch.object(
            server, "_remote_bash", side_effect=fake_remote
        ):
            result = server.start_job("colab-20260714-1234abcd", "train", "python train.py")
        self.assertEqual(result["status"], "started")
        self.assertIn("heartbeat", captured["script"])
        self.assertIn("exit_code", captured["script"])
        self.assertIn("tmux new-session", captured["script"])
        self.assertNotIn("while true", captured["script"].lower())

    def test_bootstrap_rerun_preserves_remote_home_and_jobs(self):
        template = (
            Path(__file__).resolve().parents[1] / "assets" / "bootstrap_colab.py.tmpl"
        ).read_text(encoding="utf-8")
        self.assertNotIn('userdel", "-r", "codex', template)
        self.assertIn('id", "-u", "codex', template)
        self.assertIn("exist_ok=True", template)


if __name__ == "__main__":
    unittest.main()
