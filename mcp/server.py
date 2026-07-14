"""Local MCP controller for short-lived, user-authorized Colab SSH sessions."""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = Path(os.environ.get("COLAB_SSH_STATE_DIR", Path.home() / ".codex" / "colab-ssh"))
SESSIONS_ROOT = STATE_ROOT / "sessions"
ALLOWED_PROFILES = {"cpu", "gpu", "premium-gpu", "high-memory"}
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{5,63}$")
SAFE_JOB = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

mcp = FastMCP(
    "colab-ssh",
    instructions=(
        "Operate only Colab runtimes the user owns or may access. Consumer Colab "
        "accelerators are preferences, never guarantees."
    ),
)


def _now() -> int:
    return int(time.time())


def _session_dir(session_id: str) -> Path:
    if not SAFE_ID.fullmatch(session_id):
        raise ValueError("Invalid session_id")
    return SESSIONS_ROOT / session_id


def _load(session_id: str) -> dict[str, Any]:
    path = _session_dir(session_id) / "state.json"
    if not path.exists():
        raise ValueError(f"Unknown session: {session_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def _save(state: dict[str, Any]) -> None:
    directory = _session_dir(state["session_id"])
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "state.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _run(args: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"Required executable not found: {args[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(detail) from exc


def _ssh_base(state: dict[str, Any]) -> list[str]:
    if not state.get("connected"):
        raise ValueError("Session is not registered yet")
    return [
        "ssh", "-i", state["private_key"], "-p", str(state["port"]),
        "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={state['known_hosts']}",
        "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=20",
        "-o", "ServerAliveCountMax=3", f"codex@{state['host']}",
    ]


def _scp_base(state: dict[str, Any]) -> list[str]:
    if not state.get("connected"):
        raise ValueError("Session is not registered yet")
    return [
        "scp", "-i", state["private_key"], "-P", str(state["port"]),
        "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={state['known_hosts']}",
        "-o", "ConnectTimeout=15",
    ]


def _remote_bash(state: dict[str, Any], script: str, *, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    encoded = base64.b64encode(script.encode()).decode()
    command = f"printf %s {shlex.quote(encoded)} | base64 -d | bash"
    return _run(_ssh_base(state) + [command], timeout=timeout)


def _render_bootstrap(state: dict[str, Any]) -> str:
    template = (PLUGIN_ROOT / "assets" / "bootstrap_colab.py.tmpl").read_text(encoding="utf-8")
    for marker, value in {
        "__SESSION_ID_JSON__": state["session_id"],
        "__NONCE_JSON__": state["nonce"],
        "__PUBLIC_KEY_JSON__": state["public_key"],
        "__PROFILE_JSON__": state["requested_profile"],
        "__SECRET_JSON__": state["colab_secret_name"],
    }.items():
        template = template.replace(marker, json.dumps(value))
    return template


@mcp.tool()
def prepare_session(
    requested_profile: str = "gpu",
    tunnel_provider: str = "ngrok",
    colab_secret_name: str = "NGROK_AUTHTOKEN",
) -> dict[str, Any]:
    """Create a fresh SSH identity and a one-cell Colab bootstrap notebook."""
    if requested_profile not in ALLOWED_PROFILES:
        raise ValueError(f"requested_profile must be one of {sorted(ALLOWED_PROFILES)}")
    if tunnel_provider != "ngrok":
        raise ValueError("This release supports tunnel_provider='ngrok' only")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{2,63}", colab_secret_name):
        raise ValueError("Invalid Colab secret name")
    for executable in ("ssh", "scp", "ssh-keygen"):
        if not shutil.which(executable):
            raise RuntimeError(f"OpenSSH executable is required: {executable}")

    SESSIONS_ROOT.mkdir(parents=True, exist_ok=True)
    session_id = f"colab-{time.strftime('%Y%m%d')}-{secrets.token_hex(4)}"
    directory = _session_dir(session_id)
    directory.mkdir(parents=True)
    private_key = directory / "id_ed25519"
    _run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", session_id, "-f", str(private_key)])
    public_key = private_key.with_suffix(".pub").read_text(encoding="utf-8").strip()
    state: dict[str, Any] = {
        "session_id": session_id,
        "nonce": secrets.token_urlsafe(24),
        "requested_profile": requested_profile,
        "tunnel_provider": tunnel_provider,
        "colab_secret_name": colab_secret_name,
        "private_key": str(private_key),
        "public_key": public_key,
        "known_hosts": str(directory / "known_hosts"),
        "created_at": _now(),
        "connected": False,
    }
    bootstrap = _render_bootstrap(state)
    notebook = {
        "nbformat": 4,
        "nbformat_minor": 0,
        "metadata": {"colab": {"provenance": []}, "kernelspec": {"name": "python3", "display_name": "Python 3"}},
        "cells": [{"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": [line + "\n" for line in bootstrap.splitlines()]}],
    }
    notebook_path = directory / "Colab-SSH-Bootstrap.ipynb"
    notebook_path.write_text(json.dumps(notebook, indent=2), encoding="utf-8")
    state["notebook_path"] = str(notebook_path)
    _save(state)
    return {
        "session_id": session_id,
        "requested_profile": requested_profile,
        "notebook_path": str(notebook_path),
        "bootstrap_code": bootstrap,
        "next_step": "Open in Colab, choose the runtime class, run the cell, then pass its final manifest JSON to register_session.",
        "important": "The profile is a preference; Colab does not guarantee an accelerator model.",
    }


@mcp.tool()
def register_session(manifest_json: str) -> dict[str, Any]:
    """Verify a bootstrap manifest, pin its host key, and test SSH."""
    try:
        manifest = json.loads(manifest_json)
    except json.JSONDecodeError as exc:
        raise ValueError("manifest_json must be the exact JSON object printed by the notebook") from exc
    state = _load(str(manifest.get("session_id", "")))
    if not secrets.compare_digest(str(manifest.get("nonce", "")), state["nonce"]):
        raise ValueError("Manifest nonce did not match this prepared session")
    match = re.fullmatch(r"tcp://([A-Za-z0-9.-]+):(\d{1,5})", str(manifest.get("endpoint", "")))
    if not match:
        raise ValueError("Manifest endpoint must be tcp://host:port")
    host, port_text = match.groups()
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise ValueError("Invalid endpoint port")
    host_key = str(manifest.get("host_key", "")).strip()
    if not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/=]+", host_key):
        raise ValueError("Manifest host_key must be an Ed25519 public key")

    Path(state["known_hosts"]).write_text(f"[{host}]:{port} {host_key}\n", encoding="utf-8")
    state.update(host=host, port=port, host_key=host_key, runtime=manifest.get("runtime", {}), connected=True, registered_at=_now())
    _save(state)
    try:
        probe = _run(_ssh_base(state) + ["printf COLAB_SSH_OK"], timeout=30)
    except Exception:
        state["connected"] = False
        _save(state)
        raise
    if probe.stdout != "COLAB_SSH_OK":
        raise RuntimeError("SSH connected but returned an unexpected verification response")
    return {"session_id": state["session_id"], "connected": True, "runtime": state["runtime"]}


@mcp.tool()
def list_sessions() -> list[dict[str, Any]]:
    """List prepared sessions without exposing credentials."""
    if not SESSIONS_ROOT.exists():
        return []
    result = []
    for path in sorted(SESSIONS_ROOT.glob("*/state.json"), reverse=True):
        state = json.loads(path.read_text(encoding="utf-8"))
        result.append({key: state.get(key) for key in ("session_id", "requested_profile", "connected", "created_at", "registered_at", "runtime")})
    return result


@mcp.tool()
def session_status(session_id: str) -> dict[str, Any]:
    """Check SSH health and report the actual runtime accelerator."""
    state = _load(session_id)
    if not state.get("connected"):
        return {"session_id": session_id, "connected": False, "stage": "awaiting_manifest"}
    try:
        result = _remote_bash(state, "echo HOST=$(hostname); echo UPTIME=$(cut -d. -f1 /proc/uptime); echo GPU=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | paste -sd, - || true)", timeout=30)
        return {"session_id": session_id, "connected": True, "details": result.stdout.strip(), "runtime": state.get("runtime", {})}
    except Exception as exc:
        return {"session_id": session_id, "connected": False, "error": str(exc), "recovery": "Rerun the prepared notebook cell. If Colab allocated a new VM, prepare a new session."}


@mcp.tool()
def ssh_exec(session_id: str, command: str, timeout_seconds: int = 300) -> dict[str, Any]:
    """Run a shell command on the connected Colab VM."""
    if not command.strip():
        raise ValueError("command cannot be empty")
    state = _load(session_id)
    result = _remote_bash(state, command, timeout=max(1, min(timeout_seconds, 3600)))
    return {"exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


@mcp.tool()
def upload_file(session_id: str, local_path: str, remote_path: str) -> dict[str, Any]:
    """Copy one local file or directory to Colab."""
    state = _load(session_id)
    source = Path(local_path).expanduser().resolve()
    if not source.exists():
        raise ValueError(f"Local path does not exist: {source}")
    args = _scp_base(state)
    if source.is_dir():
        args.append("-r")
    result = _run(args + [str(source), f"codex@{state['host']}:{remote_path}"], timeout=1800)
    return {"uploaded": str(source), "remote_path": remote_path, "stderr": result.stderr}


@mcp.tool()
def download_file(session_id: str, remote_path: str, local_path: str) -> dict[str, Any]:
    """Copy one remote file or directory from Colab."""
    state = _load(session_id)
    destination = Path(local_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = _run(_scp_base(state) + ["-r", f"codex@{state['host']}:{remote_path}", str(destination)], timeout=1800)
    return {"downloaded": remote_path, "local_path": str(destination), "stderr": result.stderr}


@mcp.tool()
def start_job(session_id: str, job_name: str, command: str, workdir: str = "/content") -> dict[str, Any]:
    """Start a real workload in tmux with logs, status, and a process heartbeat."""
    if not SAFE_JOB.fullmatch(job_name):
        raise ValueError("Invalid job_name")
    if not command.strip():
        raise ValueError("command cannot be empty")
    state = _load(session_id)
    command_b64 = base64.b64encode(command.encode()).decode()
    script = f'''set -euo pipefail
job={shlex.quote(job_name)}
job_dir="$HOME/.codex-colab/jobs/$job"
mkdir -p "$job_dir"
if tmux has-session -t "codex-$job" 2>/dev/null; then echo "job already running" >&2; exit 17; fi
printf %s {shlex.quote(command_b64)} | base64 -d > "$job_dir/command.sh"
chmod 700 "$job_dir/command.sh"
cat > "$job_dir/wrapper.sh" <<'WRAPPER'
#!/usr/bin/env bash
set +e
JOB_DIR="$1"; WORKDIR="$2"
echo running > "$JOB_DIR/status"; date +%s > "$JOB_DIR/started_at"
rm -f "$JOB_DIR/exit_code" "$JOB_DIR/finished_at"
( while [[ ! -f "$JOB_DIR/exit_code" ]]; do date +%s > "$JOB_DIR/heartbeat"; sleep 30; done ) & heartbeat_pid=$!
if cd "$WORKDIR"; then
  bash "$JOB_DIR/command.sh" >> "$JOB_DIR/stdout.log" 2>> "$JOB_DIR/stderr.log"; rc=$?
else
  echo "workdir not found: $WORKDIR" >> "$JOB_DIR/stderr.log"; rc=125
fi
echo "$rc" > "$JOB_DIR/exit_code"; date +%s > "$JOB_DIR/finished_at"; echo finished > "$JOB_DIR/status"
kill "$heartbeat_pid" 2>/dev/null || true; exit "$rc"
WRAPPER
chmod 700 "$job_dir/wrapper.sh"
tmux new-session -d -s "codex-$job" "$job_dir/wrapper.sh $job_dir {shlex.quote(workdir)}"
echo "$job"
'''
    result = _remote_bash(state, script)
    return {"job_name": result.stdout.strip(), "status": "started", "note": "Use job_status/job_logs and checkpoint valuable outputs outside this ephemeral VM."}


@mcp.tool()
def job_status(session_id: str, job_name: str) -> dict[str, Any]:
    """Read job metadata and determine whether its process heartbeat is fresh."""
    if not SAFE_JOB.fullmatch(job_name):
        raise ValueError("Invalid job_name")
    state = _load(session_id)
    script = f'''d="$HOME/.codex-colab/jobs/{job_name}"
test -d "$d" || {{ echo NOT_FOUND; exit 4; }}
for f in status started_at heartbeat exit_code finished_at; do if test -f "$d/$f"; then printf '%s=' "$f"; cat "$d/$f"; fi; done
if tmux has-session -t "codex-{job_name}" 2>/dev/null; then echo tmux=running; else echo tmux=absent; fi
'''
    result = _remote_bash(state, script)
    fields: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key] = value
    age = _now() - int(fields["heartbeat"]) if fields.get("heartbeat", "").isdigit() else None
    return {"job_name": job_name, "fields": fields, "heartbeat_age_seconds": age, "healthy": fields.get("tmux") == "running" and age is not None and age < 120}


@mcp.tool()
def job_logs(session_id: str, job_name: str, lines: int = 200) -> dict[str, str]:
    """Tail stdout and stderr for a background job."""
    if not SAFE_JOB.fullmatch(job_name):
        raise ValueError("Invalid job_name")
    count = max(1, min(lines, 5000))
    state = _load(session_id)
    script = f'''d="$HOME/.codex-colab/jobs/{job_name}"
echo __STDOUT__; tail -n {count} "$d/stdout.log" 2>/dev/null || true
echo __STDERR__; tail -n {count} "$d/stderr.log" 2>/dev/null || true
'''
    output = _remote_bash(state, script).stdout
    stdout_part, _, stderr_part = output.partition("__STDERR__\n")
    return {"stdout": stdout_part.removeprefix("__STDOUT__\n"), "stderr": stderr_part}


@mcp.tool()
def stop_job(session_id: str, job_name: str) -> dict[str, Any]:
    """Stop a named tmux job and mark it stopped."""
    if not SAFE_JOB.fullmatch(job_name):
        raise ValueError("Invalid job_name")
    state = _load(session_id)
    script = f'''tmux send-keys -t "codex-{job_name}" C-c 2>/dev/null || true
sleep 2
tmux kill-session -t "codex-{job_name}" 2>/dev/null || true
d="$HOME/.codex-colab/jobs/{job_name}"; mkdir -p "$d"; echo stopped > "$d/status"; date +%s > "$d/finished_at"
'''
    _remote_bash(state, script)
    return {"job_name": job_name, "status": "stopped"}


@mcp.tool()
def close_session(session_id: str) -> dict[str, Any]:
    """Revoke remote access when reachable, then delete the local private key."""
    state = _load(session_id)
    remote_revoked = False
    if state.get("connected"):
        try:
            _remote_bash(
                state,
                "rm -f ~/.ssh/authorized_keys; sudo pkill -F /run/sshd_codex.pid 2>/dev/null || true; sudo pkill -f 'ngrok tcp 2222' 2>/dev/null || true",
                timeout=30,
            )
            remote_revoked = True
        except Exception:
            pass
    key = Path(state["private_key"])
    if key.exists():
        key.unlink()
    public = key.with_suffix(".pub")
    if public.exists():
        public.unlink()
    state["connected"] = False
    state["closed_at"] = _now()
    _save(state)
    return {"session_id": session_id, "closed": True, "remote_revoked": remote_revoked, "note": "Disconnect the runtime in Colab when finished."}


if __name__ == "__main__":
    mcp.run(transport="stdio")
