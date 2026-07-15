"""Security-first MCP tools for Google's official Colab CLI on Windows/WSL."""

from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = Path(os.environ.get("COLAB_REMOTE_STATE_DIR", Path.home() / ".codex" / "colab-remote"))
CONFIG_PATH = STATE_ROOT / "config.json"
NOTIFICATIONS_PATH = STATE_ROOT / "notifications.jsonl"
SESSIONS_PATH = STATE_ROOT / "sessions.json"
MONITORS_ROOT = STATE_ROOT / "monitors"
SSH_ROOT = STATE_ROOT / "ssh"

ACCELERATORS = {"cpu", "T4", "L4", "G4", "H100", "A100", "v5e1", "v6e1"}
LANGUAGES = {"python", "julia"}
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
SAFE_PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+\-\[\],<>=!~]*$")
SENSITIVE_PATTERNS = [
    (re.compile(r"\b4/[0-9A-Za-z._~-]{10,}\b"), "[REDACTED_AUTH_CODE]"),
    (re.compile(r"\bya29\.[0-9A-Za-z._~-]+\b"), "[REDACTED_ACCESS_TOKEN]"),
    (re.compile(r"(?i)(code|access_token|refresh_token|client_secret)=([^&\s]+)"), r"\1=[REDACTED]"),
    (
        re.compile(r'(?i)("(?:token|access_token|id_token|refresh_token|client_secret)"\s*:\s*")[^"]+'),
        r"\1[REDACTED]",
    ),
    (re.compile(r"(?i)(NGROK_AUTHTOKEN\s*[=:]\s*)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(authtoken\s*:\s*)\S+"), r"\1[REDACTED]"),
]
ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

DEFAULT_CONFIG: dict[str, Any] = {
    "distro": "Ubuntu",
    "default_accelerator": "cpu",
    "default_language": "python",
    "prefer_high_ram": False,
    "default_timeout_seconds": 3600,
    "compute_warning_minutes": 60,
    "notifications_enabled": True,
    "require_cost_acknowledgement": True,
    "allowed_local_roots": [],
    "ssh_tunnel_enabled": False,
    "ssh_secret_name": "NGROK_AUTHTOKEN",
}

mcp = FastMCP(
    "colab-remote",
    instructions=(
        "Use only Google's official Colab CLI with OAuth2. Never request, read, print, or transmit "
        "Google authorization codes, token files, gcloud credentials, Application Default Credentials, or ngrok "
        "tokens. Optional SSH must remain user-approved, key-only, host-key-pinned, and short-lived."
    ),
)

_monitor_lock = threading.Lock()
_monitors: dict[str, dict[str, Any]] = {}
_cli_lock_guard = threading.Lock()
_cli_thread_locks: dict[str, threading.Lock] = {}


def _redact(value: str) -> str:
    text = ANSI_ESCAPE.sub("", value)
    for pattern, replacement in SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _secure_state_root() -> None:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(STATE_ROOT, 0o700)
    except OSError:
        pass
    if os.name == "nt" and not (STATE_ROOT / ".acl-secured").exists():
        username = os.environ.get("USERNAME")
        domain = os.environ.get("USERDOMAIN")
        user = f"{domain}\\{username}" if domain and username else username
        if user:
            result = subprocess.run(
                ["icacls", str(STATE_ROOT), "/inheritance:r", "/grant:r", f"{user}:(OI)(CI)F"],
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
            if result.returncode != 0:
                raise PermissionError("Could not restrict the Colab Remote state directory to the current user")
            marker = STATE_ROOT / ".acl-secured"
            marker.write_text("owner-only Colab Remote state; may include short-lived SSH keys\n", encoding="utf-8")
            try:
                os.chmod(marker, 0o600)
            except OSError:
                pass


def _load_config() -> dict[str, Any]:
    _secure_state_root()
    config = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("Colab Remote config must be a JSON object")
        config.update(loaded)
    config["require_cost_acknowledgement"] = True
    return config


def _save_config(config: dict[str, Any]) -> None:
    _secure_state_root()
    temporary = CONFIG_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, CONFIG_PATH)


def _load_session_ledger() -> dict[str, dict[str, Any]]:
    _secure_state_root()
    if not SESSIONS_PATH.exists():
        return {}
    value = json.loads(SESSIONS_PATH.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _save_session_ledger(ledger: dict[str, dict[str, Any]]) -> None:
    _secure_state_root()
    temporary = SESSIONS_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, SESSIONS_PATH)


def _load_monitor_ledger() -> dict[str, dict[str, Any]]:
    _secure_state_root()
    MONITORS_ROOT.mkdir(parents=True, exist_ok=True)
    ledger: dict[str, dict[str, Any]] = {}
    for path in MONITORS_ROOT.glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and "session_name" in value and "job_name" in value:
                ledger[f"{value['session_name']}/{value['job_name']}"] = value
        except (OSError, ValueError):
            continue
    return ledger


def _save_monitor_record(session_name: str, job_name: str, record: dict[str, Any] | None) -> None:
    _secure_state_root()
    session = _validate_session_name(session_name)
    job = _validate_job_name(job_name)
    MONITORS_ROOT.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(f"{session}\0{job}".encode()).hexdigest()
    target = MONITORS_ROOT / f"{digest}.json"
    if record is None:
        target.unlink(missing_ok=True)
        return
    temporary = target.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    for attempt in range(8):
        try:
            os.replace(temporary, target)
            break
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.01 * (attempt + 1))


def _record_session(session_name: str, accelerator: str, language: str) -> None:
    ledger = _load_session_ledger()
    ledger[session_name] = {
        "started_at": int(time.time()),
        "accelerator": accelerator,
        "language": language,
    }
    _save_session_ledger(ledger)


def _session_compute_metadata(session_name: str) -> dict[str, Any]:
    record = _load_session_ledger().get(session_name)
    if not record or not isinstance(record.get("started_at"), int):
        return {"tracked": False, "exact_cost_available": False}
    elapsed = max(0, int(time.time()) - record["started_at"])
    threshold = int(_load_config()["compute_warning_minutes"]) * 60
    result = {
        "tracked": True,
        "started_at": record["started_at"],
        "elapsed_seconds": elapsed,
        "accelerator": record.get("accelerator"),
        "exact_cost_available": False,
    }
    if elapsed >= threshold:
        result["warning"] = (
            f"This session has run for {elapsed // 60} minutes and may be consuming quota or compute units."
        )
    else:
        result["next_warning_in_seconds"] = threshold - elapsed
    return result


def _run(
    args: list[str],
    *,
    input_text: str | None = None,
    timeout: int = 300,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    run_kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
        "check": False,
        "env": env,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }
    if input_text is None:
        run_kwargs["stdin"] = subprocess.DEVNULL
    else:
        run_kwargs["input"] = input_text
    try:
        result = subprocess.run(args, **run_kwargs)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Required executable not found: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Command timed out after {timeout} seconds") from exc
    if check and result.returncode != 0:
        detail = _redact((result.stderr or result.stdout or f"exit code {result.returncode}").strip())
        raise RuntimeError(detail)
    return result


@contextmanager
def _session_cli_lock(session_name: str, timeout_seconds: int):
    """Serialize Colab CLI operations for one runtime across threads and processes."""
    session = _validate_session_name(session_name)
    with _cli_lock_guard:
        thread_lock = _cli_thread_locks.setdefault(session, threading.Lock())
    if not thread_lock.acquire(timeout=timeout_seconds):
        raise RuntimeError(f"Timed out waiting for another Colab operation on session {session}")

    stream = None
    try:
        _secure_state_root()
        lock_root = STATE_ROOT / "locks"
        lock_root.mkdir(parents=True, exist_ok=True)
        lock_path = lock_root / f"{session}.lock"
        stream = lock_path.open("a+b")
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"\0")
            stream.flush()
        try:
            os.chmod(lock_path, 0o600)
        except OSError:
            pass
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"Timed out waiting for another Colab operation on session {session}"
                    ) from exc
                time.sleep(0.1)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        if stream is not None:
            stream.close()
        thread_lock.release()


def _distro() -> str:
    distro = str(_load_config()["distro"])
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,63}", distro):
        raise ValueError("Invalid WSL distribution name in config")
    return distro


def _wsl(args: list[str], *, input_text: str | None = None, timeout: int = 300, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run(["wsl.exe", "-d", _distro(), "--", *args], input_text=input_text, timeout=timeout, check=check)


def _linux_home() -> str:
    result = _wsl(["sh", "-lc", 'printf %s "$HOME"'], timeout=15)
    home = result.stdout.strip()
    if not home.startswith("/"):
        raise RuntimeError("Could not resolve the WSL home directory")
    return home


def _colab_path() -> str:
    return f"{_linux_home()}/.local/bin/colab"


def _credential_metadata() -> dict[str, Any]:
    token = f"{_linux_home()}/.config/colab-cli/token.json"
    exists = _wsl(["test", "-f", token], timeout=10, check=False).returncode == 0
    mode = None
    if exists:
        mode_result = _wsl(["stat", "-c", "%a", token], timeout=10, check=False)
        if mode_result.returncode == 0:
            mode = mode_result.stdout.strip()
    return {
        "oauth_token_present": exists,
        "owner_only_mode": mode == "600",
        "mode": mode,
        "gcloud_adc_used": False,
        "token_contents_read": False,
    }


def _require_credentials() -> None:
    metadata = _credential_metadata()
    if not metadata["oauth_token_present"]:
        raise RuntimeError(
            "Colab OAuth is not configured. Run the repository installer or the documented "
            "'colab --auth oauth2 sessions' command yourself in a trusted terminal. Never paste the code into Codex."
        )
    if not metadata["owner_only_mode"]:
        raise RuntimeError(
            "The Colab OAuth token file is not mode 600. Run "
            "'chmod 600 ~/.config/colab-cli/token.json' yourself inside WSL before continuing."
        )


def _colab(
    arguments: list[str],
    *,
    input_text: str | None = None,
    timeout: int = 300,
    require_credentials: bool = True,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    if require_credentials:
        _require_credentials()
    path = _colab_path()
    if _wsl(["test", "-x", path], timeout=10, check=False).returncode != 0:
        raise RuntimeError("Google Colab CLI is not installed in the configured WSL distribution")
    command = [
        "env",
        "-u",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "-u",
        "CLOUDSDK_CONFIG",
        path,
        "--auth",
        "oauth2",
        *arguments,
    ]
    session = None
    if "-s" in arguments:
        index = arguments.index("-s")
        if index + 1 < len(arguments):
            session = _validate_session_name(arguments[index + 1])
    if session is None:
        return _wsl(command, input_text=input_text, timeout=timeout, check=check)
    with _session_cli_lock(session, max(30, min(timeout, 300))):
        return _wsl(command, input_text=input_text, timeout=timeout, check=check)


def _output(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {
        "exit_code": result.returncode,
        "stdout": _redact(result.stdout),
        "stderr": _redact(result.stderr),
    }


def _normalize_accelerator(value: str) -> str:
    raw = value.strip()
    aliases = {
        "tpu-v5e1": "v5e1",
        "tpu-v5e-1": "v5e1",
        "v5e-1": "v5e1",
        "tpu-v6e1": "v6e1",
        "tpu-v6e-1": "v6e1",
        "v6e-1": "v6e1",
    }
    if raw.lower() in aliases:
        return aliases[raw.lower()]
    if raw.lower() == "cpu":
        normalized = "cpu"
    elif raw.lower().startswith("v"):
        normalized = raw.lower()
    else:
        normalized = raw.upper()
    if normalized not in ACCELERATORS:
        raise ValueError(f"accelerator must be one of {sorted(ACCELERATORS)}")
    return normalized


def _accelerator_args(accelerator: str) -> list[str]:
    if accelerator == "cpu":
        return []
    if accelerator.startswith("v"):
        return ["--tpu", accelerator]
    return ["--gpu", accelerator]


def _cost_warning(accelerator: str, prefer_high_ram: bool) -> str:
    parts = [
        f"Requested accelerator: {accelerator}.",
        "Starting any Colab session may consume quota or compute units.",
        "Exact rates and availability are controlled by Google Colab and are not estimated by this plugin.",
    ]
    if prefer_high_ram:
        parts.append("High RAM is only a preference: Colab CLI 0.6.0 has no high-memory allocation flag.")
    return " ".join(parts)


def _validate_session_name(name: str) -> str:
    if not SAFE_NAME.fullmatch(name):
        raise ValueError("session_name must use 1-64 letters, digits, dots, underscores, or hyphens")
    return name


def _validate_job_name(name: str) -> str:
    if not SAFE_NAME.fullmatch(name):
        raise ValueError("job_name must use 1-64 letters, digits, dots, underscores, or hyphens")
    return name


def _validate_remote_workdir(path: str) -> str:
    if not re.fullmatch(r"/[A-Za-z0-9_./-]{0,255}", path) or ".." in Path(path).parts:
        raise ValueError("workdir must be a simple absolute remote path without '..'")
    return path


def _validate_remote_path(path: str) -> str:
    if not re.fullmatch(r"/[A-Za-z0-9_./-]{0,1023}", path) or ".." in Path(path).parts:
        raise ValueError("remote path must be a simple absolute path without '..'")
    return path


def _ssh_dir(session_name: str) -> Path:
    return SSH_ROOT / _validate_session_name(session_name)


def _ssh_state_path(session_name: str) -> Path:
    return _ssh_dir(session_name) / "state.json"


def _load_ssh_state(session_name: str) -> dict[str, Any]:
    path = _ssh_state_path(session_name)
    if not path.exists():
        raise ValueError(f"SSH is not enabled for session: {session_name}")
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or state.get("session_name") != session_name:
        raise ValueError("Invalid local SSH state")
    return state


def _save_ssh_state(state: dict[str, Any]) -> None:
    _secure_state_root()
    directory = _ssh_dir(str(state["session_name"]))
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    target = directory / "state.json"
    temporary = directory / f"state.{os.getpid()}.tmp"
    temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, target)


def _delete_local_ssh_state(session_name: str) -> None:
    directory = _ssh_dir(session_name).resolve()
    expected_parent = SSH_ROOT.resolve()
    if directory.parent != expected_parent:
        raise PermissionError("Refusing to clean SSH state outside the SSH state root")
    if not directory.exists():
        return
    for name in ("id_ed25519", "id_ed25519.pub", "known_hosts", "state.json"):
        (directory / name).unlink(missing_ok=True)
    for temporary in directory.glob("state.*.tmp"):
        temporary.unlink(missing_ok=True)
    directory.rmdir()


def _ssh_base(state: dict[str, Any]) -> list[str]:
    return [
        "ssh",
        "-i",
        str(state["private_key"]),
        "-p",
        str(state["port"]),
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={state['known_hosts']}",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "ServerAliveInterval=20",
        "-o",
        "ServerAliveCountMax=3",
        f"codex@{state['host']}",
    ]


def _scp_base(state: dict[str, Any]) -> list[str]:
    return [
        "scp",
        "-i",
        str(state["private_key"]),
        "-P",
        str(state["port"]),
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={state['known_hosts']}",
        "-o",
        "ConnectTimeout=15",
    ]


def _ssh_run(state: dict[str, Any], command: str, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    encoded = base64.b64encode(command.encode()).decode()
    remote = f"printf %s {shlex.quote(encoded)} | base64 -d | bash"
    return _run(_ssh_base(state) + [remote], timeout=timeout)


def _render_ssh_bootstrap(
    session_name: str,
    nonce: str,
    public_key: str,
    secret_name: str,
) -> str:
    template = (PLUGIN_ROOT / "assets" / "bootstrap_ssh.py.tmpl").read_text(encoding="utf-8")
    replacements = {
        "__SESSION_NAME_JSON__": session_name,
        "__NONCE_JSON__": nonce,
        "__PUBLIC_KEY_JSON__": public_key,
        "__SECRET_NAME_JSON__": secret_name,
    }
    for marker, value in replacements.items():
        template = template.replace(marker, json.dumps(value))
    return template


def _wsl_path(local_path: Path) -> str:
    normalized = str(local_path).replace("\\", "/")
    result = _wsl(["wslpath", "-a", "-u", "--", normalized], timeout=15)
    return result.stdout.strip()


def _allowed_local_path(path: str, *, must_exist: bool) -> Path:
    config = _load_config()
    roots = [Path(root).expanduser().resolve() for root in config["allowed_local_roots"]]
    if not roots:
        raise PermissionError(
            "Local file access is disabled until the user explicitly configures allowed_local_roots with set_config."
        )
    candidate = Path(path).expanduser().resolve(strict=False)
    if not any(candidate == root or root in candidate.parents for root in roots):
        raise PermissionError("Local path is outside the user-approved roots")
    if must_exist and not candidate.exists():
        raise ValueError(f"Local path does not exist: {candidate}")
    return candidate


def _extract_json_marker(output: str, marker: str) -> dict[str, Any]:
    clean = ANSI_ESCAPE.sub("", output)
    for line in reversed(clean.splitlines()):
        if marker in line:
            value = line.split(marker, 1)[1].strip()
            decoded, _ = json.JSONDecoder().raw_decode(value)
            if not isinstance(decoded, dict):
                raise RuntimeError(f"Remote marker {marker} did not contain a JSON object")
            return decoded
    raise RuntimeError(f"Remote command did not return {marker}")


def _remote_shell(session_name: str, script: str, *, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    session = _validate_session_name(session_name)
    encoded = base64.b64encode(script.encode()).decode()
    python = (
        "import base64,json,subprocess\n"
        f"script=base64.b64decode({encoded!r}).decode()\n"
        "result=subprocess.run(['bash','-lc',script],capture_output=True,text=True)\n"
        "print('CODEX_REMOTE_SHELL='+json.dumps({"
        "'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr},separators=(',',':')))\n"
    )
    cli_result = _colab(
        ["exec", "-s", session, "--timeout", str(timeout)],
        input_text=python,
        timeout=timeout + 30,
    )
    payload = _extract_json_marker(cli_result.stdout, "CODEX_REMOTE_SHELL=")
    result = subprocess.CompletedProcess(
        ["remote-shell", session],
        int(payload.get("returncode", 1)),
        str(payload.get("stdout", "")),
        str(payload.get("stderr", "")),
    )
    if result.returncode != 0:
        detail = _redact((result.stderr or result.stdout or f"remote exit code {result.returncode}").strip())
        raise RuntimeError(detail)
    return result


def _memory_status(session_name: str) -> dict[str, Any]:
    code = (
        "import json, os\n"
        "pages=os.sysconf('SC_PHYS_PAGES'); size=os.sysconf('SC_PAGE_SIZE')\n"
        "print('CODEX_MEMORY='+json.dumps({'bytes':pages*size,'gib':round(pages*size/1024**3,2)}))\n"
    )
    result = _colab(["exec", "-s", session_name, "--timeout", "60"], input_text=code, timeout=90)
    return _extract_json_marker(result.stdout, "CODEX_MEMORY=")


def _job_status_impl(session_name: str, job_name: str) -> dict[str, Any]:
    session = _validate_session_name(session_name)
    job = _validate_job_name(job_name)
    script = f'''python3 - <<'PY'
import json
from pathlib import Path
d=Path("/content/.codex-remote/jobs/{job}")
result={{"exists":d.is_dir(),"job_name":"{job}"}}
if d.is_dir():
    for key in ("status","started_at","heartbeat","exit_code","finished_at"):
        p=d/key
        if p.exists(): result[key]=p.read_text().strip()
    p=d/"progress.json"
    if p.exists():
        try: result["progress"]=json.loads(p.read_text())
        except Exception: result["progress_error"]="invalid progress.json"
print("CODEX_JOB_STATUS="+json.dumps(result,separators=(",",":")))
PY'''
    result = _remote_shell(session, script, timeout=60)
    status = _extract_json_marker(result.stdout, "CODEX_JOB_STATUS=")
    heartbeat = status.get("heartbeat")
    status["heartbeat_age_seconds"] = int(time.time()) - int(heartbeat) if str(heartbeat).isdigit() else None
    return status


def _write_notification(title: str, message: str, level: str = "info") -> dict[str, Any]:
    config = _load_config()
    event = {"time": int(time.time()), "title": title[:100], "message": message[:500], "level": level}
    _secure_state_root()
    with NOTIFICATIONS_PATH.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event) + "\n")
    try:
        os.chmod(NOTIFICATIONS_PATH, 0o600)
    except OSError:
        pass

    delivered = False
    if config["notifications_enabled"] and os.name == "nt":
        script = r'''
$ErrorActionPreference='Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] > $null
$title=[Security.SecurityElement]::Escape($env:COLAB_REMOTE_NOTIFY_TITLE)
$body=[Security.SecurityElement]::Escape($env:COLAB_REMOTE_NOTIFY_BODY)
$xml=New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$title</text><text>$body</text></binding></visual></toast>")
$toast=[Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Codex Colab Remote').Show($toast)
'''
        env = os.environ.copy()
        env["COLAB_REMOTE_NOTIFY_TITLE"] = event["title"]
        env["COLAB_REMOTE_NOTIFY_BODY"] = event["message"]
        delivered = _run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            timeout=15,
            check=False,
            env=env,
        ).returncode == 0
    return {**event, "desktop_delivered": delivered}


def _monitor_job(session_name: str, job_name: str, interval_seconds: int) -> None:
    key = f"{session_name}/{job_name}"
    failures = 0
    while True:
        record = {
            "session_name": session_name,
            "job_name": job_name,
            "interval_seconds": interval_seconds,
            "watcher_pid": os.getpid(),
            "heartbeat": int(time.time()),
        }
        _save_monitor_record(session_name, job_name, record)
        try:
            status = _job_status_impl(session_name, job_name)
            failures = 0
            if not status.get("exists"):
                _write_notification("Colab monitor stopped", f"Job {job_name} no longer exists.", "warning")
                break
            state = status.get("status")
            if state in {"finished", "stopped", "failed"} or status.get("exit_code") is not None:
                exit_code = status.get("exit_code", "unknown")
                level = "success" if str(exit_code) == "0" else "warning"
                _write_notification(
                    "Colab job completed",
                    f"{job_name} on {session_name} finished with exit code {exit_code}.",
                    level,
                )
                break
        except Exception as exc:
            failures += 1
            if failures >= 3:
                _write_notification("Colab monitor stopped", f"{job_name}: {_redact(str(exc))}", "warning")
                break
        time.sleep(interval_seconds)
    with _monitor_lock:
        _monitors.pop(key, None)
    _save_monitor_record(session_name, job_name, None)


def _start_monitor(session_name: str, job_name: str, interval_seconds: int) -> dict[str, Any]:
    key = f"{session_name}/{job_name}"
    current_status = _job_status_impl(session_name, job_name)
    if not current_status.get("exists"):
        raise ValueError(f"Remote job does not exist: {job_name}")
    with _monitor_lock:
        if key in _monitors:
            return {"watching": True, "already_running": True, **_monitors[key]}
        ledger = _load_monitor_ledger()
        existing = ledger.get(key, {})
        heartbeat = existing.get("heartbeat")
        if isinstance(heartbeat, int) and int(time.time()) - heartbeat < interval_seconds * 2 + 30:
            return {"watching": True, "already_running": True, **existing}
        creationflags = 0
        if os.name == "nt":
            creationflags = (
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
        metadata = {
            "session_name": session_name,
            "job_name": job_name,
            "interval_seconds": interval_seconds,
            "heartbeat": int(time.time()),
        }
        _save_monitor_record(session_name, job_name, metadata)
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--monitor-job",
                    session_name,
                    job_name,
                    str(interval_seconds),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=creationflags,
                start_new_session=os.name != "nt",
            )
        except Exception:
            _save_monitor_record(session_name, job_name, None)
            raise
        metadata["watcher_pid"] = process.pid
        _monitors[key] = metadata
    return {"watching": True, "already_running": False, **metadata}


def _resume_saved_monitors() -> None:
    def resume(record: dict[str, Any]) -> None:
        interval = max(10, min(int(record.get("interval_seconds", 30)), 300))
        heartbeat = record.get("heartbeat", 0)
        if isinstance(heartbeat, int):
            time.sleep(max(0, interval * 2 + 31 - (int(time.time()) - heartbeat)))
        try:
            _start_monitor(str(record["session_name"]), str(record["job_name"]), interval)
        except Exception:
            pass

    for saved in _load_monitor_ledger().values():
        if not isinstance(saved, dict) or "session_name" not in saved or "job_name" not in saved:
            continue
        thread = threading.Thread(target=resume, args=(saved,), daemon=True, name="colab-monitor-resume")
        thread.start()


@mcp.tool()
def get_config() -> dict[str, Any]:
    """Return non-secret Colab Remote defaults and approved local roots."""
    return _load_config()


@mcp.tool()
def set_config(
    default_accelerator: str | None = None,
    default_language: str | None = None,
    prefer_high_ram: bool | None = None,
    default_timeout_seconds: int | None = None,
    compute_warning_minutes: int | None = None,
    notifications_enabled: bool | None = None,
    allowed_local_roots: list[str] | None = None,
    ssh_tunnel_enabled: bool | None = None,
    ssh_secret_name: str | None = None,
    distro: str | None = None,
    confirm_sensitive_change: bool = False,
) -> dict[str, Any]:
    """Change defaults. Explicit confirmation is required before enabling local file roots."""
    config = _load_config()
    if default_accelerator is not None:
        config["default_accelerator"] = _normalize_accelerator(default_accelerator)
    if default_language is not None:
        language = default_language.lower()
        if language not in LANGUAGES:
            raise ValueError(f"default_language must be one of {sorted(LANGUAGES)}")
        config["default_language"] = language
    if prefer_high_ram is not None:
        config["prefer_high_ram"] = prefer_high_ram
    if default_timeout_seconds is not None:
        config["default_timeout_seconds"] = max(30, min(default_timeout_seconds, 86400))
    if compute_warning_minutes is not None:
        config["compute_warning_minutes"] = max(5, min(compute_warning_minutes, 1440))
    if notifications_enabled is not None:
        config["notifications_enabled"] = notifications_enabled
    if distro is not None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,63}", distro):
            raise ValueError("Invalid WSL distribution name")
        config["distro"] = distro
    if allowed_local_roots is not None:
        if not confirm_sensitive_change:
            raise PermissionError("The user must explicitly confirm changes to allowed_local_roots")
        roots = []
        for root in allowed_local_roots:
            path = Path(root).expanduser().resolve()
            if not path.is_absolute() or not path.is_dir():
                raise ValueError(f"Allowed root must be an existing absolute directory: {root}")
            roots.append(str(path))
        config["allowed_local_roots"] = sorted(set(roots))
    if ssh_tunnel_enabled is not None:
        if ssh_tunnel_enabled and not confirm_sensitive_change:
            raise PermissionError("The user must explicitly confirm enabling a public SSH tunnel")
        config["ssh_tunnel_enabled"] = ssh_tunnel_enabled
    if ssh_secret_name is not None:
        if not confirm_sensitive_change:
            raise PermissionError("The user must explicitly confirm changing the Colab tunnel secret name")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{2,63}", ssh_secret_name):
            raise ValueError("Invalid Colab secret name")
        config["ssh_secret_name"] = ssh_secret_name
    _save_config(config)
    return config


@mcp.tool()
def authentication_instructions() -> dict[str, Any]:
    """Return the safe user-terminal OAuth command without starting or handling authentication."""
    distro = _distro()
    command = (
        f'wsl -d "{distro}" -- bash -lc \'umask 077; env -u GOOGLE_APPLICATION_CREDENTIALS '
        "-u CLOUDSDK_CONFIG ~/.local/bin/colab --auth oauth2 sessions; rc=$?; "
        'token="$HOME/.config/colab-cli/token.json"; test ! -f "$token" || chmod 600 "$token"; exit $rc\''
    )
    return {
        "command": command,
        "must_be_run_by_user": True,
        "warning": "Run this yourself in a trusted terminal. Never paste an authorization code into Codex.",
        "gcloud_adc_used": False,
    }


@mcp.tool()
def credential_status(validate_with_google: bool = False) -> dict[str, Any]:
    """Check OAuth presence and file mode without reading or returning token contents."""
    status = _credential_metadata()
    if validate_with_google and status["oauth_token_present"]:
        result = _colab(["sessions"], timeout=30, check=False)
        status["google_validation_succeeded"] = result.returncode == 0
        status["validation_message"] = _redact((result.stderr or result.stdout).strip())
    return status


@mcp.tool()
def doctor() -> dict[str, Any]:
    """Diagnose WSL, Colab CLI, OAuth, configuration, and notification readiness."""
    checks: dict[str, Any] = {"config": _load_config()}
    wsl = _run(["wsl.exe", "--status"], timeout=15, check=False)
    checks["wsl_available"] = wsl.returncode == 0
    if checks["wsl_available"]:
        path = _colab_path()
        checks["colab_cli_present"] = _wsl(["test", "-x", path], timeout=10, check=False).returncode == 0
        if checks["colab_cli_present"]:
            version = _colab(["version"], timeout=20, require_credentials=False, check=False)
            checks["colab_cli_version"] = _redact((version.stdout or version.stderr).strip())
        checks["credentials"] = _credential_metadata()
    checks["local_file_access_enabled"] = bool(checks["config"]["allowed_local_roots"])
    checks["high_ram_supported_by_cli"] = False
    checks["julia_mode"] = "Best-effort Juliaup LTS inside a Python-based Colab VM"
    checks["ssh_tunnel_enabled"] = bool(checks["config"]["ssh_tunnel_enabled"])
    checks["openssh_client_present"] = all(shutil.which(name) for name in ("ssh", "scp", "ssh-keygen"))
    checks["ssh_policy"] = "Managed-runtime SSH requires a paid plan with a positive compute-unit balance"
    return checks


@mcp.tool()
def list_sessions() -> dict[str, Any]:
    """List active Colab sessions."""
    return _output(_colab(["sessions"], timeout=30))


@mcp.tool()
def create_session(
    session_name: str,
    accelerator: str | None = None,
    language: str | None = None,
    prefer_high_ram: bool | None = None,
    acknowledge_cost: bool = False,
) -> dict[str, Any]:
    """Create a named CPU/GPU/TPU session with explicit quota/cost acknowledgement."""
    config = _load_config()
    session = _validate_session_name(session_name)
    selected = _normalize_accelerator(accelerator or config["default_accelerator"])
    selected_language = (language or config["default_language"]).lower()
    if selected_language not in LANGUAGES:
        raise ValueError(f"language must be one of {sorted(LANGUAGES)}")
    high_ram = config["prefer_high_ram"] if prefer_high_ram is None else prefer_high_ram
    warning = _cost_warning(selected, high_ram)
    if config["require_cost_acknowledgement"] and not acknowledge_cost:
        raise PermissionError(warning + " Re-run with acknowledge_cost=true after the user accepts.")
    warnings = [warning]
    result = _colab(["new", "-s", session, *_accelerator_args(selected)], timeout=900)
    try:
        _record_session(session, selected, selected_language)
    except Exception as exc:
        warnings.append(f"Session was created, but local duration tracking failed: {_redact(str(exc))}")
    try:
        status = _output(_colab(["status", "-s", session], timeout=60))
    except Exception as exc:
        status = {"available": False, "error": _redact(str(exc))}
        warnings.append("Session was created, but its status probe failed. Use session_status to retry.")
    try:
        memory = _memory_status(session)
    except Exception as exc:
        memory = {"available": False, "error": _redact(str(exc))}
        warnings.append("Session was created, but RAM measurement failed. Use session_status to retry.")
    if high_ram:
        measured = f" Allocated RAM is {memory['gib']} GiB." if "gib" in memory else ""
        warnings.append(f"High-RAM preference cannot be requested through Colab CLI 0.6.0.{measured}")
    if selected_language == "julia":
        warnings.append("Julia is not a native Colab CLI kernel. Run prepare_language before Julia code.")
    try:
        compute = _session_compute_metadata(session)
    except Exception as exc:
        compute = {"tracked": False, "exact_cost_available": False, "error": _redact(str(exc))}
    return {
        "session_name": session,
        "requested_accelerator": selected,
        "language": selected_language,
        "memory": memory,
        "compute": compute,
        "create_output": _output(result),
        "status": status,
        "warnings": warnings,
    }


@mcp.tool()
def session_status(session_name: str) -> dict[str, Any]:
    """Return Colab status plus measured RAM."""
    session = _validate_session_name(session_name)
    result = _colab(["status", "-s", session], timeout=60)
    return {
        **_output(result),
        "memory": _memory_status(session),
        "compute": _session_compute_metadata(session),
    }


@mcp.tool()
def prepare_language(
    session_name: str,
    language: str,
    acknowledge_external_download: bool = False,
) -> dict[str, Any]:
    """Prepare Python or best-effort Julia LTS; Julia requires explicit download acknowledgement."""
    session = _validate_session_name(session_name)
    selected = language.lower()
    if selected not in LANGUAGES:
        raise ValueError(f"language must be one of {sorted(LANGUAGES)}")
    if selected == "python":
        result = _colab(["exec", "-s", session, "--timeout", "30"], input_text="import sys; print(sys.version)", timeout=60)
        return {"language": "python", **_output(result)}
    if not acknowledge_external_download:
        raise PermissionError(
            "Julia setup uses Julia's official install.julialang.org bootstrap inside the Colab VM. "
            "Re-run with acknowledge_external_download=true after the user accepts."
        )
    script = (
        "set -euo pipefail; "
        "if ! test -x \"$HOME/.juliaup/bin/julia\"; then "
        "curl -fsSL https://install.julialang.org | sh -s -- --yes --default-channel=lts --add-to-path=no; fi; "
        "\"$HOME/.juliaup/bin/julia\" --version"
    )
    result = _remote_shell(session, script, timeout=1800)
    return {"language": "julia", "mode": "Juliaup LTS inside the Colab VM", **_output(result)}


@mcp.tool()
def execute_code(
    session_name: str,
    code: str,
    language: str | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Execute Python natively or Julia through an already-prepared Juliaup runtime."""
    if not code.strip():
        raise ValueError("code cannot be empty")
    config = _load_config()
    session = _validate_session_name(session_name)
    selected = (language or config["default_language"]).lower()
    timeout = max(1, min(timeout_seconds or config["default_timeout_seconds"], 86400))
    if selected == "python":
        return _output(_colab(["exec", "-s", session, "--timeout", str(timeout)], input_text=code, timeout=timeout + 30))
    if selected == "julia":
        encoded = base64.b64encode(code.encode()).decode()
        script = f'test -x "$HOME/.juliaup/bin/julia" || {{ echo "Julia is not prepared" >&2; exit 12; }}; printf %s {encoded} | base64 -d | "$HOME/.juliaup/bin/julia" -'
        return _output(_remote_shell(session, script, timeout=timeout + 30))
    raise ValueError(f"language must be one of {sorted(LANGUAGES)}")


@mcp.tool()
def execute_file(
    session_name: str,
    local_path: str,
    language: str | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Execute an approved local Python, notebook, or Julia file."""
    source = _allowed_local_path(local_path, must_exist=True)
    selected = (language or _load_config()["default_language"]).lower()
    if selected == "julia":
        if source.suffix.lower() != ".jl":
            raise ValueError("Julia execution requires a .jl file")
        return execute_code(session_name, source.read_text(encoding="utf-8"), "julia", timeout_seconds)
    if source.suffix.lower() not in {".py", ".ipynb"}:
        raise ValueError("Python execution requires a .py or .ipynb file")
    timeout = max(1, min(timeout_seconds or _load_config()["default_timeout_seconds"], 86400))
    result = _colab(
        ["exec", "-s", _validate_session_name(session_name), "-f", _wsl_path(source), "--timeout", str(timeout)],
        timeout=timeout + 30,
    )
    return _output(result)


@mcp.tool()
def upload_file(session_name: str, local_path: str, remote_path: str) -> dict[str, Any]:
    """Upload one file from a user-approved local root."""
    source = _allowed_local_path(local_path, must_exist=True)
    if not source.is_file():
        raise ValueError("Google Colab CLI 0.6.0 upload accepts files, not directories")
    destination = _validate_remote_path(remote_path)
    result = _colab(["upload", "-s", _validate_session_name(session_name), _wsl_path(source), destination], timeout=1800)
    return {"local_path": str(source), "remote_path": destination, **_output(result)}


@mcp.tool()
def download_file(session_name: str, remote_path: str, local_path: str) -> dict[str, Any]:
    """Download into a user-approved local root."""
    destination = _allowed_local_path(local_path, must_exist=False)
    source = _validate_remote_path(remote_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = _colab(["download", "-s", _validate_session_name(session_name), source, _wsl_path(destination)], timeout=1800)
    return {"remote_path": source, "local_path": str(destination), **_output(result)}


@mcp.tool()
def list_files(session_name: str, remote_path: str = "/content") -> dict[str, Any]:
    """List files on a Colab session."""
    return _output(
        _colab(["ls", "-s", _validate_session_name(session_name), _validate_remote_path(remote_path)], timeout=60)
    )


@mcp.tool()
def install_packages(session_name: str, packages: list[str]) -> dict[str, Any]:
    """Install validated Python package specifiers on a Colab session."""
    if not packages or any(not SAFE_PACKAGE.fullmatch(item) for item in packages):
        raise ValueError("packages must be non-empty standard Python package specifiers; URLs and options are not accepted")
    return _output(_colab(["install", "-s", _validate_session_name(session_name), *packages], timeout=1800))


@mcp.tool()
def get_logs(session_name: str, lines: int = 200) -> dict[str, Any]:
    """Return redacted structured Colab CLI history."""
    count = max(1, min(lines, 5000))
    return _output(_colab(["log", "-s", _validate_session_name(session_name), "-n", str(count)], timeout=60))


@mcp.tool()
def session_url(session_name: str) -> dict[str, Any]:
    """Return the browser URL for a running session."""
    return _output(_colab(["url", "-s", _validate_session_name(session_name)], timeout=30))


@mcp.tool()
def restart_kernel(session_name: str, confirm: bool = False) -> dict[str, Any]:
    """Restart a session kernel after explicit confirmation; in-memory state is lost."""
    if not confirm:
        raise PermissionError("Kernel restart clears in-memory state; re-run with confirm=true after user approval")
    return _output(_colab(["restart-kernel", "-s", _validate_session_name(session_name)], timeout=120))


@mcp.tool()
def start_job(
    session_name: str,
    job_name: str,
    command: str,
    workdir: str = "/content",
    notify_on_completion: bool = True,
    monitor_interval_seconds: int = 30,
) -> dict[str, Any]:
    """Start a tmux job with logs, heartbeat, optional JSON progress, and completion notification."""
    if not command.strip():
        raise ValueError("command cannot be empty")
    session = _validate_session_name(session_name)
    job = _validate_job_name(job_name)
    remote_workdir = _validate_remote_workdir(workdir)
    encoded = base64.b64encode(command.encode()).decode()
    script = f'''set -euo pipefail
d=/content/.codex-remote/jobs/{job}; mkdir -p "$d"
tmux has-session -t codex-{job} 2>/dev/null && {{ echo "job already running" >&2; exit 17; }}
printf %s {encoded} | base64 -d > "$d/command.sh"; chmod 700 "$d/command.sh"
cat > "$d/wrapper.sh" <<'WRAP'
#!/usr/bin/env bash
set +e
d="$1"; workdir="$2"; export CODEX_PROGRESS_FILE="$d/progress.json"
echo running > "$d/status"; date +%s > "$d/started_at"; rm -f "$d/exit_code" "$d/finished_at"
(while ! test -f "$d/exit_code"; do date +%s > "$d/heartbeat"; sleep 30; done) & hp=$!
if cd "$workdir"; then bash "$d/command.sh" >>"$d/stdout.log" 2>>"$d/stderr.log"; rc=$?; else echo "workdir not found" >>"$d/stderr.log"; rc=125; fi
echo "$rc" > "$d/exit_code"; date +%s > "$d/finished_at"; echo finished > "$d/status"; kill "$hp" 2>/dev/null || true; exit "$rc"
WRAP
chmod 700 "$d/wrapper.sh"; tmux new-session -d -s codex-{job} "$d/wrapper.sh $d {shlex.quote(remote_workdir)}"
echo CODEX_JOB_STARTED={job}'''
    result = _remote_shell(session, script, timeout=120)
    response: dict[str, Any] = {
        "session_name": session,
        "job_name": job,
        "status": "started",
        "progress_file": f"/content/.codex-remote/jobs/{job}/progress.json",
        "output": _output(result),
    }
    if notify_on_completion:
        response["monitor"] = _start_monitor(session, job, max(10, min(monitor_interval_seconds, 300)))
    return response


@mcp.tool()
def job_status(session_name: str, job_name: str) -> dict[str, Any]:
    """Return job lifecycle, heartbeat age, and application-written JSON progress."""
    return _job_status_impl(session_name, job_name)


@mcp.tool()
def job_logs(session_name: str, job_name: str, lines: int = 200) -> dict[str, Any]:
    """Tail stdout and stderr for a background job."""
    session = _validate_session_name(session_name)
    job = _validate_job_name(job_name)
    count = max(1, min(lines, 5000))
    script = f'''python3 - <<'PY'
import json
from pathlib import Path
d=Path("/content/.codex-remote/jobs/{job}")
def tail(name):
    p=d/name
    return "\\n".join(p.read_text(errors="replace").splitlines()[-{count}:]) if p.exists() else ""
print("CODEX_JOB_LOGS="+json.dumps({{"stdout":tail("stdout.log"),"stderr":tail("stderr.log")}},separators=(",",":")))
PY'''
    result = _remote_shell(session, script, timeout=60)
    logs = _extract_json_marker(result.stdout, "CODEX_JOB_LOGS=")
    return {
        "stdout": _redact(str(logs.get("stdout", ""))),
        "stderr": _redact(str(logs.get("stderr", ""))),
    }


@mcp.tool()
def watch_job(session_name: str, job_name: str, interval_seconds: int = 30) -> dict[str, Any]:
    """Start a local background monitor and desktop completion notification."""
    return _start_monitor(
        _validate_session_name(session_name),
        _validate_job_name(job_name),
        max(10, min(interval_seconds, 300)),
    )


@mcp.tool()
def stop_job(session_name: str, job_name: str, confirm: bool = False) -> dict[str, Any]:
    """Stop a background job after explicit confirmation."""
    if not confirm:
        raise PermissionError("Stopping a job may lose work; re-run with confirm=true after user approval")
    session = _validate_session_name(session_name)
    job = _validate_job_name(job_name)
    script = f'tmux send-keys -t codex-{job} C-c 2>/dev/null || true; sleep 2; tmux kill-session -t codex-{job} 2>/dev/null || true; d=/content/.codex-remote/jobs/{job}; mkdir -p "$d"; echo stopped > "$d/status"; date +%s > "$d/finished_at"'
    return {"job_name": job, "status": "stopped", **_output(_remote_shell(session, script, timeout=60))}


@mcp.tool()
def ssh_requirements() -> dict[str, Any]:
    """Explain the optional SSH tunnel prerequisites and current local readiness."""
    config = _load_config()
    return {
        "enabled_in_config": bool(config["ssh_tunnel_enabled"]),
        "tunnel_provider": "ngrok TCP",
        "colab_secret_name": config["ssh_secret_name"],
        "openssh_client_present": all(shutil.which(name) for name in ("ssh", "scp", "ssh-keygen")),
        "policy_warning": (
            "Google says SSH shells are disallowed on free managed runtimes without a positive Colab compute-unit "
            "balance and may be terminated. Use a paid plan with positive compute units."
        ),
        "security": (
            "The endpoint is public but accepts only a short-lived key. Passwords, root login, TCP forwarding, "
            "agent forwarding, and X11 forwarding are disabled. The ngrok token stays in Colab Secrets."
        ),
    }


@mcp.tool()
def enable_ssh(
    session_name: str,
    acknowledge_colab_policy: bool = False,
    acknowledge_public_tunnel: bool = False,
) -> dict[str, Any]:
    """Enable short-lived, key-only SSH over ngrok for an existing Colab session."""
    session = _validate_session_name(session_name)
    config = _load_config()
    if not config["ssh_tunnel_enabled"]:
        raise PermissionError(
            "SSH tunneling is disabled. The user must explicitly enable ssh_tunnel_enabled with set_config."
        )
    if not acknowledge_colab_policy:
        raise PermissionError(
            "Google restricts SSH on free managed runtimes without positive compute units. Re-run only after the "
            "user confirms a paid plan with a positive compute-unit balance."
        )
    if not acknowledge_public_tunnel:
        raise PermissionError(
            "ngrok creates a public TCP endpoint protected by a short-lived SSH key. Re-run only after user approval."
        )
    for executable in ("ssh", "scp", "ssh-keygen"):
        if not shutil.which(executable):
            raise RuntimeError(f"OpenSSH executable is required: {executable}")

    directory = _ssh_dir(session)
    if directory.exists():
        _delete_local_ssh_state(session)
    directory.mkdir(parents=True, exist_ok=False)
    private_key = directory / "id_ed25519"
    try:
        _run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"colab-remote-{session}", "-f", str(private_key)],
            timeout=30,
        )
        public_key = private_key.with_suffix(".pub").read_text(encoding="utf-8").strip()
        nonce = secrets.token_urlsafe(24)
        bootstrap = _render_ssh_bootstrap(session, nonce, public_key, str(config["ssh_secret_name"]))
        result = _colab(
            ["exec", "-s", session, "--timeout", "900"],
            input_text=bootstrap,
            timeout=930,
        )
        manifest = _extract_json_marker(result.stdout, "CODEX_SSH_MANIFEST=")
        if manifest.get("session_name") != session or not secrets.compare_digest(
            str(manifest.get("nonce", "")), nonce
        ):
            raise RuntimeError("SSH bootstrap identity verification failed")
        endpoint = str(manifest.get("endpoint", ""))
        match = re.fullmatch(r"tcp://([A-Za-z0-9.-]+):(\d{1,5})", endpoint)
        if not match:
            raise RuntimeError("SSH bootstrap returned an invalid ngrok TCP endpoint")
        host, port_text = match.groups()
        port = int(port_text)
        if not 1 <= port <= 65535:
            raise RuntimeError("SSH bootstrap returned an invalid port")
        host_key = str(manifest.get("host_key", "")).strip()
        if not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/=]+", host_key):
            raise RuntimeError("SSH bootstrap returned an invalid Ed25519 host key")
        known_hosts = directory / "known_hosts"
        known_hosts.write_text(f"[{host}]:{port} {host_key}\n", encoding="utf-8")
        state = {
            "session_name": session,
            "host": host,
            "port": port,
            "host_key": host_key,
            "host_fingerprint": manifest.get("host_fingerprint"),
            "private_key": str(private_key),
            "known_hosts": str(known_hosts),
            "runtime": manifest.get("runtime", {}),
            "connected": True,
            "created_at": int(time.time()),
            "root_access": False,
        }
        _save_ssh_state(state)
        probe = _run(_ssh_base(state) + ["printf CODEX_SSH_OK"], timeout=30)
        if probe.stdout != "CODEX_SSH_OK":
            raise RuntimeError("SSH connected but returned an unexpected probe response")
    except Exception:
        try:
            _remote_shell(
                session,
                "rm -f /home/codex/.ssh/authorized_keys; "
                "pkill -x sshd -F /run/sshd_codex.pid 2>/dev/null || true; "
                "pkill -x ngrok -F /run/codex-ngrok.pid 2>/dev/null || true; "
                "rm -f /run/sshd_codex.pid /run/codex-ngrok.pid /tmp/.codex-ngrok-*.yml /tmp/codex-ngrok.log",
                timeout=30,
            )
        except Exception:
            pass
        _delete_local_ssh_state(session)
        raise

    terminal_command = (
        f'ssh -i "{private_key}" -p {port} -o StrictHostKeyChecking=yes '
        f'-o UserKnownHostsFile="{known_hosts}" codex@{host}'
    )
    return {
        "session_name": session,
        "connected": True,
        "runtime": state["runtime"],
        "host_fingerprint": state["host_fingerprint"],
        "root_access": False,
        "terminal_command": terminal_command,
        "warning": "Keep the Colab session and ngrok tunnel short-lived; call disable_ssh when finished.",
    }


@mcp.tool()
def ssh_status(session_name: str) -> dict[str, Any]:
    """Check the optional SSH tunnel without exposing private key material."""
    session = _validate_session_name(session_name)
    state = _load_ssh_state(session)
    try:
        result = _ssh_run(
            state,
            "echo HOST=$(hostname); echo USER=$(id -un); echo UPTIME=$(cut -d. -f1 /proc/uptime)",
            timeout=30,
        )
        return {
            "session_name": session,
            "connected": True,
            "details": _redact(result.stdout.strip()),
            "runtime": state.get("runtime", {}),
            "root_access": False,
        }
    except Exception as exc:
        return {"session_name": session, "connected": False, "error": _redact(str(exc))}


@mcp.tool()
def ssh_exec(session_name: str, command: str, timeout_seconds: int = 300) -> dict[str, Any]:
    """Run an arbitrary shell command through the explicitly enabled SSH tunnel."""
    if not command.strip():
        raise ValueError("command cannot be empty")
    state = _load_ssh_state(_validate_session_name(session_name))
    result = _ssh_run(state, command, timeout=max(1, min(timeout_seconds, 86400)))
    return _output(result)


@mcp.tool()
def ssh_upload(session_name: str, local_path: str, remote_path: str) -> dict[str, Any]:
    """Copy an approved local file or directory to Colab through SCP."""
    state = _load_ssh_state(_validate_session_name(session_name))
    source = _allowed_local_path(local_path, must_exist=True)
    destination = _validate_remote_path(remote_path)
    arguments = _scp_base(state)
    if source.is_dir():
        arguments.append("-r")
    result = _run(arguments + [str(source), f"codex@{state['host']}:{destination}"], timeout=1800)
    return {"local_path": str(source), "remote_path": destination, **_output(result)}


@mcp.tool()
def ssh_download(session_name: str, remote_path: str, local_path: str) -> dict[str, Any]:
    """Copy a remote file or directory from Colab through SCP into an approved local root."""
    state = _load_ssh_state(_validate_session_name(session_name))
    source = _validate_remote_path(remote_path)
    destination = _allowed_local_path(local_path, must_exist=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = _run(
        _scp_base(state) + ["-r", f"codex@{state['host']}:{source}", str(destination)],
        timeout=1800,
    )
    return {"remote_path": source, "local_path": str(destination), **_output(result)}


@mcp.tool()
def disable_ssh(session_name: str, confirm: bool = False) -> dict[str, Any]:
    """Revoke the remote SSH key/tunnel and delete the short-lived local private key."""
    if not confirm:
        raise PermissionError("Re-run with confirm=true after the user approves closing SSH access")
    session = _validate_session_name(session_name)
    remote_revoked = False
    remote_error = None
    try:
        _remote_shell(
            session,
            "rm -f /home/codex/.ssh/authorized_keys; "
            "pkill -x sshd -F /run/sshd_codex.pid 2>/dev/null || true; "
            "pkill -x ngrok -F /run/codex-ngrok.pid 2>/dev/null || true; "
            "rm -f /run/sshd_codex.pid /run/codex-ngrok.pid /tmp/.codex-ngrok-*.yml /tmp/codex-ngrok.log",
            timeout=30,
        )
        remote_revoked = True
    except Exception as exc:
        remote_error = _redact(str(exc))
    if remote_revoked:
        _delete_local_ssh_state(session)
    return {
        "session_name": session,
        "closed": remote_revoked,
        "remote_revoked": remote_revoked,
        "remote_error": remote_error,
        "private_key_deleted": remote_revoked,
        "retry_required": not remote_revoked,
    }


@mcp.tool()
def stop_session(session_name: str, confirm: bool = False) -> dict[str, Any]:
    """Stop and release one Colab session after explicit confirmation."""
    if not confirm:
        raise PermissionError("Stopping releases the VM and ephemeral data; re-run with confirm=true after user approval")
    session = _validate_session_name(session_name)
    ssh_cleanup = disable_ssh(session, confirm=True) if _ssh_state_path(session).exists() else None
    result = _colab(["stop", "-s", session], timeout=120)
    verification = _colab(["sessions"], timeout=30)
    listing = verification.stdout + verification.stderr
    verified_absent = session not in listing
    if not verified_absent:
        raise RuntimeError(f"Colab reported that session {session} still exists after stop")
    if _ssh_state_path(session).exists():
        _delete_local_ssh_state(session)
        if ssh_cleanup is not None:
            ssh_cleanup.update(
                {
                    "closed": True,
                    "private_key_deleted": True,
                    "retry_required": False,
                    "terminated_by_session_stop": True,
                }
            )
    ledger = _load_session_ledger()
    ledger.pop(session, None)
    _save_session_ledger(ledger)
    return {
        "session_name": session,
        "stopped": True,
        "verified_absent": True,
        "stop": _output(result),
        "sessions_after": _output(verification),
        "ssh_cleanup": ssh_cleanup,
    }


@mcp.tool()
def notification_history(limit: int = 20) -> list[dict[str, Any]]:
    """Return recent non-secret completion notification metadata."""
    if not NOTIFICATIONS_PATH.exists():
        return []
    rows = NOTIFICATIONS_PATH.read_text(encoding="utf-8").splitlines()
    return [json.loads(row) for row in rows[-max(1, min(limit, 200)) :]]


@mcp.tool()
def test_notification() -> dict[str, Any]:
    """Send a harmless desktop notification to verify notification support."""
    return _write_notification("Colab Remote", "Completion notifications are working.", "success")


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--monitor-job":
        _monitor_job(sys.argv[2], sys.argv[3], int(sys.argv[4]))
    else:
        _resume_saved_monitors()
        mcp.run(transport="stdio")
