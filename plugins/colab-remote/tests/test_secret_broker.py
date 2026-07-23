import importlib.util
import base64
import io
import json
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


MCP_ROOT = Path(__file__).resolve().parents[1] / "mcp"
sys.path.insert(0, str(MCP_ROOT))

BROKER_SPEC = importlib.util.spec_from_file_location(
    "colab_remote_secret_broker", MCP_ROOT / "secret_broker.py"
)
secret_broker = importlib.util.module_from_spec(BROKER_SPEC)
assert BROKER_SPEC.loader is not None
BROKER_SPEC.loader.exec_module(secret_broker)

SERVER_SPEC = importlib.util.spec_from_file_location(
    "colab_remote_secret_server", MCP_ROOT / "server.py"
)
server = importlib.util.module_from_spec(SERVER_SPEC)
assert SERVER_SPEC.loader is not None
sys.modules[SERVER_SPEC.name] = server
SERVER_SPEC.loader.exec_module(server)


def completed(stdout="", stderr="", returncode=0):
    return server.subprocess.CompletedProcess([], returncode, stdout, stderr)


class SecretBrokerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.state_root = Path(self.temporary.name)
        self.vault: dict[tuple[str, str], str] = {}
        self.patches = [
            patch.object(secret_broker, "_backend_ready"),
            patch.object(
                secret_broker.keyring,
                "set_password",
                side_effect=lambda service, name, value: self.vault.__setitem__(
                    (service, name), value
                ),
            ),
            patch.object(
                secret_broker.keyring,
                "get_password",
                side_effect=lambda service, name: self.vault.get((service, name)),
            ),
            patch.object(
                secret_broker.keyring,
                "delete_password",
                side_effect=lambda service, name: self.vault.pop((service, name), None),
            ),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def test_name_validation_is_limited_to_environment_aliases(self):
        self.assertEqual(secret_broker.validate_name("HF_TOKEN"), "HF_TOKEN")
        for invalid in ("hf_token", "HF-TOKEN", "../TOKEN", "A=B", ""):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                secret_broker.validate_name(invalid)

    def test_keychain_value_never_enters_metadata(self):
        value = "hf_super_secret_value"
        secret_broker.set_secret(self.state_root, "HF_TOKEN", value)
        self.assertEqual(secret_broker.list_names(self.state_root), ["HF_TOKEN"])
        self.assertEqual(secret_broker.get_secret(self.state_root, "HF_TOKEN"), value)
        metadata = "\n".join(
            path.read_text(encoding="utf-8")
            for path in self.state_root.rglob("*.json")
        )
        self.assertNotIn(value, metadata)
        self.assertIn("HF_TOKEN", metadata)

    def test_session_grants_store_names_only(self):
        value = "wandb_super_secret"
        secret_broker.set_secret(self.state_root, "WANDB_API_KEY", value)
        enabled = secret_broker.enable_names(
            self.state_root, "training-session", ["WANDB_API_KEY"]
        )
        self.assertEqual(enabled, ["WANDB_API_KEY"])
        grants = (
            self.state_root / "secrets" / "session-grants.json"
        ).read_text(encoding="utf-8")
        self.assertIn("WANDB_API_KEY", grants)
        self.assertNotIn(value, grants)
        removed, remaining = secret_broker.disable_names(
            self.state_root, "training-session"
        )
        self.assertEqual(removed, ["WANDB_API_KEY"])
        self.assertEqual(remaining, [])

    def test_cli_uses_masked_prompt_and_never_prints_value(self):
        value = "secure_api_value"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(secret_broker.getpass, "getpass", side_effect=[value, value]),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            status = secret_broker.main(
                ["--state-root", str(self.state_root), "set", "HF_TOKEN"]
            )
        self.assertEqual(status, 0)
        self.assertNotIn(value, stdout.getvalue())
        self.assertNotIn(value, stderr.getvalue())


class SecretToolTests(unittest.TestCase):
    def setUp(self):
        server._secret_redaction_values.clear()
        server._secret_redaction_checked_at = server.time.monotonic()

    def test_prepare_command_contains_name_but_no_value_argument(self):
        result = server.prepare_local_secret("HF_TOKEN")
        self.assertIn("HF_TOKEN", result["command"])
        self.assertIn("secret_broker.py", result["command"])
        self.assertFalse(result["value_enters_mcp"])

    def test_list_returns_aliases_only(self):
        with (
            patch.object(server.secret_broker, "list_names", return_value=["HF_TOKEN"]),
            patch.object(
                server.secret_broker,
                "get_secret",
                return_value="hf_secret_value",
            ),
            patch.object(
                server.secret_broker,
                "enabled_names",
                return_value=["HF_TOKEN"],
            ),
        ):
            result = server.list_local_secrets("training")
        serialized = json.dumps(result)
        self.assertIn("HF_TOKEN", serialized)
        self.assertNotIn("hf_secret_value", serialized)
        self.assertFalse(result["values_exposed"])

    def test_execute_code_injects_by_file_path_and_redacts_output(self):
        value = "hf_secret_value_123"

        @contextmanager
        def staged(_session, _environment):
            yield "/content/.secret.hex"

        def environment(_session):
            server._remember_secret_redactions({"HF_TOKEN": value})
            return {"HF_TOKEN": value}

        with (
            patch.object(server, "_secret_environment", side_effect=environment),
            patch.object(server, "_staged_secret_file", side_effect=staged),
            patch.object(
                server,
                "_colab",
                return_value=completed(stdout=f"token={value}\n"),
            ) as colab,
        ):
            result = server.execute_code("training", "print('done')", "python")
        submitted = colab.call_args.kwargs["input_text"]
        self.assertIn("/content/.secret.hex", submitted)
        self.assertNotIn(value, submitted)
        self.assertNotIn(value, result["stdout"])
        self.assertIn("[REDACTED_LOCAL_SECRET]", result["stdout"])

    def test_terminal_exec_does_not_embed_value_in_remote_script(self):
        value = "wandb_secret_value_123"

        @contextmanager
        def staged(_session, _environment):
            yield "/content/.secret.hex"

        with (
            patch.object(
                server,
                "_secret_environment",
                return_value={"WANDB_API_KEY": value},
            ),
            patch.object(server, "_staged_secret_file", side_effect=staged),
            patch.object(server, "_remote_shell", return_value=completed()) as remote,
        ):
            result = server.terminal_exec("training", "python train.py")
        script = remote.call_args.args[1]
        self.assertIn("/content/.secret.hex", script)
        self.assertNotIn(value, script)
        self.assertEqual(result["enabled_secret_names"], ["WANDB_API_KEY"])

    def test_start_job_stages_names_without_embedding_values(self):
        value = "training_secret_value_123"

        @contextmanager
        def staged(_session, _environment):
            yield "/content/.secret.hex"

        with (
            patch.object(
                server,
                "_secret_environment",
                return_value={"HF_TOKEN": value},
            ),
            patch.object(server, "_staged_secret_file", side_effect=staged),
            patch.object(server, "_remote_shell", return_value=completed()) as remote,
        ):
            result = server.start_job("training", "fine-tune", "python train.py")
        script = remote.call_args.args[1]
        self.assertIn("/content/.secret.hex", script)
        self.assertNotIn(value, script)
        self.assertEqual(result["enabled_secret_names"], ["HF_TOKEN"])

    def test_ssh_exec_uses_staged_aliases_without_embedding_values(self):
        value = "ssh_secret_value_123"

        @contextmanager
        def staged(_state, _environment):
            yield "/content/codex-ssh/.secret.hex"

        with (
            patch.object(server, "_load_ssh_state", return_value={"host": "example"}),
            patch.object(
                server,
                "_secret_environment",
                return_value={"HF_TOKEN": value},
            ),
            patch.object(server, "_staged_ssh_secret_file", side_effect=staged),
            patch.object(server, "_ssh_run", return_value=completed()) as ssh,
        ):
            result = server.ssh_exec("training", "python train.py")
        command = ssh.call_args.args[1]
        self.assertIn("/content/codex-ssh/.secret.hex", command)
        self.assertNotIn(value, command)
        self.assertEqual(result["enabled_secret_names"], ["HF_TOKEN"])

    def test_redaction_covers_raw_base64_url_and_hex_forms(self):
        value = "sensitive_value_123"
        server._remember_secret_redactions({"TOKEN": value})
        encoded = base64.b64encode(value.encode()).decode()
        url_encoded = server.quote(value, safe="")
        hex_encoded = value.encode().hex()
        output = server._redact(f"{value} {encoded} {url_encoded} {hex_encoded}")
        self.assertNotIn(value, output)
        self.assertNotIn(encoded, output)
        self.assertNotIn(hex_encoded, output)
        self.assertEqual(output.count("[REDACTED_LOCAL_SECRET]"), 4)

    def test_enable_rolls_back_grant_when_kernel_injection_fails(self):
        with (
            patch.object(server.secret_broker, "enabled_names", return_value=[]),
            patch.object(
                server.secret_broker,
                "enable_names",
                return_value=["HF_TOKEN"],
            ),
            patch.object(
                server,
                "_inject_enabled_secrets_into_kernel",
                side_effect=RuntimeError("runtime unavailable"),
            ),
            patch.object(server.secret_broker, "disable_names") as disable,
        ):
            with self.assertRaises(RuntimeError):
                server.enable_local_secrets("training", ["HF_TOKEN"])
        disable.assert_called_once_with(server.STATE_ROOT, "training", ["HF_TOKEN"])

    def test_disable_removes_kernel_environment_without_values(self):
        with (
            patch.object(
                server.secret_broker,
                "disable_names",
                return_value=(["HF_TOKEN"], []),
            ),
            patch.object(server, "_clear_kernel_secret_names") as clear,
        ):
            result = server.disable_local_secrets("training", ["HF_TOKEN"])
        clear.assert_called_once_with("training", server._session_language("training"), ["HF_TOKEN"])
        self.assertEqual(result["disabled_names"], ["HF_TOKEN"])
        self.assertFalse(result["values_exposed"])


if __name__ == "__main__":
    unittest.main()
