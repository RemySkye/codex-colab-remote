"""Keep the official Colab Drive mount alive in a real POSIX PTY."""

from __future__ import annotations

import argparse
import errno
import json
import os
from pathlib import Path
import pty
import re
import select
import signal
import struct
import termios
import time
from urllib.parse import urlparse


AUTH_URL_PATTERN = re.compile(r"https://accounts\.google\.com/[^\s\x1b]+")
MAX_TRANSCRIPT_CHARS = 131_072
MAX_LIFETIME_SECONDS = 660


def _restrict(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    _restrict(temporary, 0o600)
    os.replace(temporary, path)


def _write_status(state_dir: Path, event: str, child_pid: int, **extra) -> None:
    _write_json(
        state_dir / "status.json",
        {
            "event": event,
            "worker_pid": os.getpid(),
            "child_pid": child_pid,
            "updated_at": int(time.time()),
            **extra,
        },
    )


def _safe_authorization_url(value: str) -> str:
    cleaned = value.rstrip("\r\n.,;)")
    parsed = urlparse(cleaned)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "accounts.google.com":
        raise RuntimeError("Unexpected Drive authorization URL")
    return cleaned


def _terminate_child(child_pid: int) -> None:
    try:
        os.killpg(child_pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        waited, _ = os.waitpid(child_pid, os.WNOHANG)
        if waited == child_pid:
            return
        time.sleep(0.1)
    try:
        os.killpg(child_pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run(arguments: argparse.Namespace) -> int:
    state_dir = Path(arguments.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    _restrict(state_dir, 0o700)
    resume_path = state_dir / "resume.request"
    cancel_path = state_dir / "cancel.request"
    authorization_path = state_dir / "authorization.url"

    child_pid, master_fd = pty.fork()
    if child_pid == 0:
        environment = dict(os.environ)
        environment.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
        environment.pop("CLOUDSDK_CONFIG", None)
        command = [
            arguments.colab,
            "--auth",
            "oauth2",
            "drivemount",
            "-s",
            arguments.session,
            arguments.mount_path,
        ]
        os.execvpe(command[0], command, environment)

    try:
        # Prevent terminal formatting from wrapping the long Google approval URL.
        import fcntl

        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 4096, 0, 0))
    except OSError:
        pass

    _write_status(state_dir, "starting", child_pid)
    started = time.monotonic()
    transcript = ""
    authorization_found = False
    resume_sent = False
    return_code: int | None = None

    try:
        while True:
            if cancel_path.exists():
                cancel_path.unlink(missing_ok=True)
                _terminate_child(child_pid)
                _write_status(state_dir, "cancelled", child_pid)
                return 2

            if authorization_found and not resume_sent and resume_path.exists():
                resume_path.unlink(missing_ok=True)
                os.write(master_fd, b"\n")
                resume_sent = True
                _write_status(state_dir, "resuming", child_pid)

            ready, _, _ = select.select([master_fd], [], [], 0.25)
            if ready:
                try:
                    chunk = os.read(master_fd, 8192)
                except OSError as exc:
                    if exc.errno != errno.EIO:
                        raise
                    chunk = b""
                if chunk:
                    transcript = (transcript + chunk.decode("utf-8", "replace"))[
                        -MAX_TRANSCRIPT_CHARS:
                    ]
                    if not authorization_found:
                        match = AUTH_URL_PATTERN.search(transcript)
                        if match:
                            authorization_url = _safe_authorization_url(match.group(0))
                            authorization_path.write_text(
                                authorization_url, encoding="utf-8"
                            )
                            _restrict(authorization_path, 0o600)
                            authorization_found = True
                            _write_status(
                                state_dir, "authorization_required", child_pid
                            )

            waited, wait_status = os.waitpid(child_pid, os.WNOHANG)
            if waited == child_pid:
                return_code = os.waitstatus_to_exitcode(wait_status)
                break
            if time.monotonic() - started > MAX_LIFETIME_SECONDS:
                _terminate_child(child_pid)
                _write_status(state_dir, "timed_out", child_pid)
                return 3

        if return_code == 0:
            _write_status(state_dir, "completed", child_pid, exit_code=0)
            return 0
        _write_status(
            state_dir,
            "error",
            child_pid,
            exit_code=return_code,
            message="The official Colab Drive mount process failed.",
        )
        return return_code or 1
    except Exception:
        _terminate_child(child_pid)
        _write_status(
            state_dir,
            "error",
            child_pid,
            message="The Drive mount worker failed before completion.",
        )
        return 1
    finally:
        os.close(master_fd)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--colab", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--mount-path", required=True)
    parser.add_argument("--state-dir", required=True)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
