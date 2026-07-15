"""Explicit live CPU integration test with verified session cleanup."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "plugins" / "colab-remote" / "mcp" / "server.py"


def load_server():
    sys.path.insert(0, str(SERVER.parent))
    spec = importlib.util.spec_from_file_location("live_colab_remote", SERVER)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load the Colab Remote server")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--acknowledge-cost",
        action="store_true",
        help="Accept that the temporary CPU runtime may use Colab quota",
    )
    args = parser.parse_args()
    if not args.acknowledge_cost:
        parser.error("--acknowledge-cost is required for a live allocation")

    os.environ.setdefault(
        "COLAB_REMOTE_STATE_DIR", str(Path.home() / ".codex" / "colab-remote-live-test")
    )
    server = load_server()
    name = f"codex-live-smoke-{int(time.time())}"
    report: dict[str, object] = {"session_name": name}
    created = False
    try:
        doctor = server.doctor()
        report["doctor"] = {
            key: doctor.get(key)
            for key in (
                "platform",
                "host_transport",
                "host_available",
                "colab_cli_present",
                "colab_cli_version",
            )
        }
        credential = server.credential_status(validate_with_google=True)
        report["credential"] = {
            key: credential.get(key)
            for key in (
                "oauth_token_present",
                "owner_only_mode",
                "google_validation_succeeded",
                "token_contents_read",
                "gcloud_adc_used",
            )
        }
        result = server.create_session(
            name,
            accelerator="cpu",
            language="python",
            high_ram=False,
            runtime_version="latest",
            max_lifetime_minutes=15,
            acknowledge_cost=True,
        )
        created = True
        report["created"] = {
            key: result.get(key)
            for key in (
                "requested_accelerator",
                "language",
                "runtime_version",
                "high_ram_requested",
            )
        }
        report["python"] = server.execute_code(
            name,
            'print("COLAB_REMOTE_LIVE_OK")',
            language="python",
            timeout_seconds=120,
        )
        report["terminal"] = server.terminal_exec(
            name,
            "printf 'TERMINAL_OK\\n'; uname -s",
            timeout_seconds=120,
        )
    finally:
        if created:
            report["cleanup"] = server.stop_session(name, confirm=True)
        sessions = server.list_sessions()
        listing = str(sessions.get("stdout", "")) + str(sessions.get("stderr", ""))
        report["verified_absent_after_cleanup"] = name not in listing

    print(json.dumps(report, indent=2))
    return 0 if report["verified_absent_after_cleanup"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
