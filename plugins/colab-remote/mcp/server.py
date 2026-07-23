"""Security-first, cross-platform MCP tools for Google's official Colab CLI."""

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
import stat
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal
from urllib.parse import parse_qs, quote, urlparse

from mcp.server.fastmcp import FastMCP
from pydantic import Field

import drive_ops
import config_io
import managed_transfer
import notebook_ops
import process_utils
import secret_broker


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
CONFIG_SCHEMA_PATH = PLUGIN_ROOT / "config_schema.json"
CONFIG_DOCUMENTATION = json.loads(CONFIG_SCHEMA_PATH.read_text(encoding="utf-8"))
STATE_ROOT = Path(
    os.environ.get("COLAB_REMOTE_STATE_DIR", Path.home() / ".codex" / "colab-remote")
)
CONFIG_PATH = STATE_ROOT / "config.jsonc"
NOTIFICATIONS_PATH = STATE_ROOT / "notifications.jsonl"
SESSIONS_PATH = STATE_ROOT / "sessions.json"
MONITORS_ROOT = STATE_ROOT / "monitors"
LEASES_ROOT = STATE_ROOT / "leases"
TRANSFERS_ROOT = STATE_ROOT / "transfers"
DRIVE_MOUNTS_ROOT = STATE_ROOT / "drive-mounts"

ACCELERATORS = {"cpu", "t4", "l4", "g4", "h100", "a100", "v5e-1", "v6e-1"}
HIGH_RAM_REQUIRED_ACCELERATORS = {"l4", "g4", "h100", "v5e-1", "v6e-1"}
LANGUAGES = {"python", "julia", "r"}
DIRECT_TRANSFER_LIMIT = 64 * 1024 * 1024
TRANSFER_CHUNK_SIZE = 32 * 1024 * 1024
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
SAFE_PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+\-\[\],<>=!~]*$")
SENSITIVE_PATTERNS = [
    (re.compile(r"\b4/[0-9A-Za-z._~-]{10,}\b"), "[REDACTED_AUTH_CODE]"),
    (re.compile(r"\bya29\.[0-9A-Za-z._~-]+\b"), "[REDACTED_ACCESS_TOKEN]"),
    (
        re.compile(r"(?i)(colab-runtime-proxy-token)=([^&\s]+)"),
        r"\1=[REDACTED]",
    ),
    (
        re.compile(r"(?i)(code|access_token|refresh_token|client_secret)=([^&\s]+)"),
        r"\1=[REDACTED]",
    ),
    (
        re.compile(
            r'(?i)("(?:token|access_token|id_token|refresh_token|client_secret)"\s*:\s*")[^"]+'
        ),
        r"\1[REDACTED]",
    ),
    (re.compile(r"(?i)(authtoken\s*:\s*)\S+"), r"\1[REDACTED]"),
]
ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

DEFAULT_CONFIG: dict[str, Any] = {
    name: details["default"]
    for name, details in CONFIG_DOCUMENTATION["settings"].items()
}

# Reusable public MCP types keep the generated tool schemas precise and compact.
SessionName = Annotated[
    str,
    Field(
        description="Unique session name: 1-64 letters, digits, dots, underscores, or hyphens.",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$",
    ),
]
JobName = Annotated[
    str,
    Field(
        description="Unique job name within the session; uses the same rules as session names.",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$",
    ),
]
SecretName = Annotated[
    str,
    Field(
        description="Configured local secret alias such as HF_TOKEN; values are never accepted.",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Z_][A-Z0-9_]{0,127}$",
    ),
]
AcceleratorName = Annotated[
    Literal["cpu", "t4", "l4", "g4", "h100", "a100", "v5e-1", "v6e-1"],
    Field(
        description="Colab hardware accelerator. Availability depends on plan and capacity."
    ),
]
LanguageName = Annotated[
    Literal["python", "r", "julia"],
    Field(description="Native Colab runtime language. Python is the default."),
]
RuntimeVersion = Annotated[
    str,
    Field(
        description="Use 'latest' (recommended) or a pinned Colab runtime in YYYY.MM format.",
        pattern=r"^(latest|recommended|20\d{2}\.\d{2})$",
    ),
]
TimeoutSeconds = Annotated[
    int,
    Field(description="Operation timeout in seconds.", ge=1, le=86400),
]
MaxLifetimeMinutes = Annotated[
    int,
    Field(
        description="Maximum session lifetime in minutes; 0 disables automatic shutdown.",
        ge=0,
        le=1440,
    ),
]
RemotePath = Annotated[
    str,
    Field(
        description="Absolute path inside the Colab VM; '..' traversal is not allowed."
    ),
]
DrivePath = Annotated[
    str,
    Field(
        description=(
            "Path relative to the protected Google Drive folder MyDrive/codex-colab. "
            "Absolute paths and '..' traversal are rejected."
        ),
        max_length=drive_ops.MAX_DRIVE_PATH_LENGTH,
    ),
]
OptionalDrivePath = Annotated[
    str | None,
    Field(
        description=(
            "Path relative to MyDrive/codex-colab; null saves under the configured "
            "default_drive_checkpoint_folder using the source name."
        ),
        max_length=drive_ops.MAX_DRIVE_PATH_LENGTH,
    ),
]
DriveMountPath = Annotated[
    Literal["/content/drive"],
    Field(
        description=(
            "Fixed internal Drive mount; typed Drive tools expose only MyDrive/codex-colab."
        )
    ),
]
RemoteWorkdir = Annotated[
    str,
    Field(
        description="Absolute working directory inside the Colab VM; defaults to /content."
    ),
]
LocalPath = Annotated[
    str,
    Field(description="Absolute local path under a root approved with set_config."),
]
TransferId = Annotated[
    str,
    Field(
        description="Managed transfer identifier returned by start_upload or start_download."
    ),
]
LineCount = Annotated[
    int,
    Field(description="Maximum number of recent lines to return.", ge=1, le=5000),
]
OptionalParallelism = Annotated[
    int | None,
    Field(
        description="Parallel transfer chunks; null uses transfer_parallelism from config.",
        ge=1,
        le=8,
    ),
]
OptionalCompression = Annotated[
    bool | None,
    Field(
        description="Compress the transfer; null uses transfer_compression from config. Folders are always archived."
    ),
]
NotificationMode = Literal["off", "failures_only", "all"]
OptionalAcceleratorName = Annotated[
    AcceleratorName | None,
    Field(description="Colab hardware accelerator; null uses the configured default."),
]
OptionalLanguageName = Annotated[
    LanguageName | None,
    Field(
        description="Native Colab language; null uses the session or configured default."
    ),
]
OptionalRuntimeVersion = Annotated[
    RuntimeVersion | None,
    Field(
        description="Use 'latest' (recommended), YYYY.MM, or null for the configured default."
    ),
]
OptionalTimeoutSeconds = Annotated[
    TimeoutSeconds | None,
    Field(
        description="Operation timeout in seconds; null uses the configured default."
    ),
]
OptionalMaxLifetimeMinutes = Annotated[
    MaxLifetimeMinutes | None,
    Field(
        description="Maximum lifetime in minutes; null uses the configured default and 0 disables it."
    ),
]

mcp = FastMCP(
    "colab-remote",
    instructions=(
        "Use only Google's official Colab CLI with OAuth2. Never request, read, print, or transmit "
        "Google authorization codes, token files, gcloud credentials, or Application Default Credentials. "
        "Start with doctor, credential_status, and get_config. Before create_session, inspect "
        "require_cost_acknowledgement: obtain explicit approval when it is true; when false, the user has "
        "configured standing authorization and no per-session approval is needed. Prefer execute_code for kernel code and terminal_exec "
        "for Linux commands. For API keys, use list_local_secrets and enable only required aliases; if an alias "
        "is missing, use prepare_local_secret and ask the user to run its masked-input command in their own "
        "terminal. When require_secret_enable_approval is true, ask before enable_local_secrets; otherwise "
        "enable required aliases automatically. Never request or accept a secret value through MCP or chat, "
        "and disable aliases after use. "
        "Google Drive tools are restricted to MyDrive/codex-colab; never inspect or modify "
        "other mounted Drive paths through code or terminal commands. Present a returned session_url exactly "
        "inside a fenced code block for the user to copy into the browser address bar; never make it a Markdown "
        "link or open it with browser automation because encoding its fragment breaks runtime attachment."
    ),
)

_monitor_lock = threading.Lock()
_monitors: dict[str, dict[str, Any]] = {}
_cli_lock_guard = threading.Lock()
_cli_thread_locks: dict[str, threading.Lock] = {}
_secret_redaction_lock = threading.Lock()
_secret_redaction_values: set[str] = set()
_secret_redaction_checked_at = 0.0


def _remember_secret_redactions(environment: dict[str, str]) -> None:
    with _secret_redaction_lock:
        _secret_redaction_values.update(
            value for value in environment.values() if value
        )


def _known_secret_redactions() -> set[str]:
    global _secret_redaction_checked_at
    with _secret_redaction_lock:
        now = time.monotonic()
        if now - _secret_redaction_checked_at > 30:
            try:
                _secret_redaction_values.update(secret_broker.all_values(STATE_ROOT))
            except Exception:
                pass
            _secret_redaction_checked_at = now
        return set(_secret_redaction_values)


def _redact(value: str) -> str:
    text = ANSI_ESCAPE.sub("", value)
    for pattern, replacement in SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    for secret_value in sorted(_known_secret_redactions(), key=len, reverse=True):
        variants = {
            secret_value,
            quote(secret_value, safe=""),
            base64.b64encode(secret_value.encode()).decode(),
            base64.urlsafe_b64encode(secret_value.encode()).decode().rstrip("="),
            secret_value.encode().hex(),
        }
        for variant in sorted((item for item in variants if item), key=len, reverse=True):
            text = text.replace(variant, "[REDACTED_LOCAL_SECRET]")
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
                [
                    "icacls",
                    str(STATE_ROOT),
                    "/inheritance:r",
                    "/grant:r",
                    f"{user}:(OI)(CI)F",
                ],
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
            if result.returncode != 0:
                raise PermissionError(
                    "Could not restrict the Colab Remote state directory to the current user"
                )
            marker = STATE_ROOT / ".acl-secured"
            marker.write_text(
                "owner-only Colab Remote state\n",
                encoding="utf-8",
            )
            try:
                os.chmod(marker, 0o600)
            except OSError:
                pass


def _config_integer(config: dict[str, Any], name: str, minimum: int, maximum: int) -> int:
    value = config[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _normalize_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = {**DEFAULT_CONFIG, **config}
    normalized.pop("_documentation", None)
    normalized.pop("prefer_high_ram", None)
    normalized.pop("ssh_secret_name", None)
    normalized.pop("ssh_tunnel_enabled", None)
    legacy_notifications = normalized.pop("notifications_enabled", None)
    if "notification_mode" not in config and legacy_notifications is not None:
        normalized["notification_mode"] = "all" if legacy_notifications else "off"
    mode = str(normalized["notification_mode"]).strip().lower()
    if mode not in {"off", "failures_only", "all"}:
        raise ValueError("notification_mode must be off, failures_only, or all")
    normalized["notification_mode"] = mode
    normalized["max_concurrent_sessions"] = _config_integer(
        normalized, "max_concurrent_sessions", 1, 64
    )
    normalized["transfer_parallelism"] = _config_integer(
        normalized, "transfer_parallelism", 1, 8
    )
    normalized["retry_attempts"] = _config_integer(
        normalized, "retry_attempts", 1, 10
    )
    normalized["default_timeout_seconds"] = _config_integer(
        normalized, "default_timeout_seconds", 30, 86400
    )
    normalized["compute_warning_minutes"] = _config_integer(
        normalized, "compute_warning_minutes", 5, 1440
    )
    normalized["default_max_lifetime_minutes"] = _config_integer(
        normalized, "default_max_lifetime_minutes", 0, 1440
    )
    for name in ("default_high_ram", "transfer_compression"):
        if not isinstance(normalized[name], bool):
            raise ValueError(f"{name} must be true or false")
    folder = drive_ops.normalize_drive_path(
        str(normalized["default_drive_checkpoint_folder"]), allow_root=False
    )
    normalized["default_drive_checkpoint_folder"] = folder
    for setting in (
        "require_cost_acknowledgement",
        "require_secret_enable_approval",
    ):
        if not isinstance(normalized[setting], bool):
            raise ValueError(f"{setting} must be true or false")
    return normalized


def _load_config() -> dict[str, Any]:
    _secure_state_root()
    loaded: dict[str, Any] = {}
    legacy_path = CONFIG_PATH.with_suffix(".json")
    source = CONFIG_PATH if CONFIG_PATH.exists() else legacy_path
    if source.exists():
        loaded = config_io.loads(source.read_text(encoding="utf-8"))
        if "default_high_ram" not in loaded and "prefer_high_ram" in loaded:
            loaded["default_high_ram"] = bool(loaded["prefer_high_ram"])
    config = _normalize_config(loaded)
    rendered = config_io.render(config, CONFIG_DOCUMENTATION)
    if not CONFIG_PATH.exists() or CONFIG_PATH.read_text(encoding="utf-8") != rendered:
        _save_config(config)
    if legacy_path != CONFIG_PATH:
        legacy_path.unlink(missing_ok=True)
    return config


def _save_config(config: dict[str, Any]) -> None:
    _secure_state_root()
    config = _normalize_config(config)
    temporary = CONFIG_PATH.with_suffix(".tmp")
    temporary.write_text(
        config_io.render(config, CONFIG_DOCUMENTATION), encoding="utf-8"
    )
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
            if (
                isinstance(value, dict)
                and "session_name" in value
                and "job_name" in value
            ):
                ledger[f"{value['session_name']}/{value['job_name']}"] = value
        except (OSError, ValueError):
            continue
    return ledger


def _save_monitor_record(
    session_name: str, job_name: str, record: dict[str, Any] | None
) -> None:
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


def _record_session(
    session_name: str,
    accelerator: str,
    language: str,
    high_ram_requested: bool = False,
    runtime_version: str = "latest",
    max_lifetime_minutes: int = 0,
    recovery_enabled: bool = False,
    max_recovery_attempts: int = 1,
) -> None:
    ledger = _load_session_ledger()
    ledger[session_name] = {
        "started_at": int(time.time()),
        "accelerator": accelerator,
        "language": language,
        "high_ram_requested": high_ram_requested,
        "runtime_version": runtime_version,
        "max_lifetime_minutes": max_lifetime_minutes,
        "expires_at": int(time.time()) + max_lifetime_minutes * 60
        if max_lifetime_minutes
        else None,
        "recovery_enabled": recovery_enabled,
        "max_recovery_attempts": max_recovery_attempts,
        "recovery_attempts": 0,
        "jobs": {},
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
        detail = _redact(
            (result.stderr or result.stdout or f"exit code {result.returncode}").strip()
        )
        raise RuntimeError(detail)
    return result


@contextmanager
def _session_cli_lock(session_name: str, timeout_seconds: int):
    """Serialize Colab CLI operations for one runtime across threads and processes."""
    session = _validate_session_name(session_name)
    with _cli_lock_guard:
        thread_lock = _cli_thread_locks.setdefault(session, threading.Lock())
    if not thread_lock.acquire(timeout=timeout_seconds):
        raise RuntimeError(
            f"Timed out waiting for another Colab operation on session {session}"
        )

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


def _uses_wsl() -> bool:
    """Return whether the host needs WSL for Google's Linux/macOS-only CLI."""
    return sys.platform == "win32"


def _wsl(
    args: list[str],
    *,
    input_text: str | None = None,
    timeout: int = 300,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a command in the Colab CLI host: WSL on Windows, locally on POSIX."""
    command = ["wsl.exe", "-d", _distro(), "--", *args] if _uses_wsl() else args
    return _run(
        command,
        input_text=input_text,
        timeout=timeout,
        check=check,
    )


def _linux_home() -> str:
    if not _uses_wsl():
        return str(Path.home())
    result = _wsl(["sh", "-lc", 'printf %s "$HOME"'], timeout=15)
    home = result.stdout.strip()
    if not home.startswith("/"):
        raise RuntimeError("Could not resolve the WSL home directory")
    return home


def _colab_path() -> str:
    return f"{_linux_home()}/.local/bin/colab"


def _credential_metadata() -> dict[str, Any]:
    token = f"{_linux_home()}/.config/colab-cli/token.json"
    mode = None
    symlink = False
    if _uses_wsl():
        exists = _wsl(["test", "-f", token], timeout=10, check=False).returncode == 0
        if exists:
            symlink = (
                _wsl(["test", "-L", token], timeout=10, check=False).returncode
                == 0
            )
            mode_result = _wsl(["stat", "-c", "%a", token], timeout=10, check=False)
            if mode_result.returncode == 0:
                mode = mode_result.stdout.strip()
    else:
        token_path = Path(token)
        exists = token_path.is_file()
        if exists:
            symlink = token_path.is_symlink()
            mode = f"{stat.S_IMODE(token_path.stat().st_mode):o}"
    return {
        "oauth_token_present": exists,
        "owner_only_mode": mode == "600",
        "mode": mode,
        "symlink": symlink,
        "gcloud_adc_used": False,
        "token_contents_read": False,
    }


def _repair_credential_permissions() -> bool:
    """Make an existing OAuth token owner-only without reading its contents."""
    metadata = _credential_metadata()
    if not metadata["oauth_token_present"] or metadata["owner_only_mode"]:
        return False
    if metadata.get("symlink"):
        raise PermissionError("The Colab OAuth token path cannot be a symlink")
    token = f"{_linux_home()}/.config/colab-cli/token.json"
    if _uses_wsl():
        result = _wsl(["chmod", "600", "--", token], timeout=10, check=False)
        if result.returncode != 0:
            raise PermissionError(
                "Could not automatically restrict the Colab OAuth token inside WSL"
            )
    else:
        try:
            os.chmod(Path(token), 0o600)
        except OSError as exc:
            raise PermissionError(
                "Could not automatically restrict the Colab OAuth token"
            ) from exc
    if not _credential_metadata()["owner_only_mode"]:
        raise PermissionError("The Colab OAuth token is still not owner-only")
    return True


def _require_credentials() -> None:
    metadata = _credential_metadata()
    if not metadata["oauth_token_present"]:
        raise RuntimeError(
            "Colab OAuth is not configured. Run the repository installer or the documented "
            "'colab --auth oauth2 sessions' command yourself in a trusted terminal. Never paste the code into Codex."
        )
    if not metadata["owner_only_mode"]:
        _repair_credential_permissions()


def _colab(
    arguments: list[str],
    *,
    input_text: str | None = None,
    timeout: int = 300,
    require_credentials: bool = True,
    check: bool = True,
    machine_shape: str | None = None,
    runtime_version: str | None = None,
    runtime_language: str | None = None,
    serialize_session: bool = True,
) -> subprocess.CompletedProcess[str]:
    if require_credentials:
        _require_credentials()
    path = _colab_path()
    if _wsl(["test", "-x", path], timeout=10, check=False).returncode != 0:
        location = "the configured WSL distribution" if _uses_wsl() else "this host"
        raise RuntimeError(f"Google Colab CLI is not installed on {location}")
    command = [
        "env",
        "-u",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "-u",
        "CLOUDSDK_CONFIG",
    ]
    if runtime_language is not None:
        runtime_language = runtime_language.strip().lower()
        if runtime_language not in LANGUAGES:
            raise ValueError(f"runtime_language must be one of {sorted(LANGUAGES)}")
    if machine_shape or runtime_version or runtime_language:
        if machine_shape:
            command.append(f"COLAB_REMOTE_MACHINE_SHAPE={machine_shape}")
        if runtime_version:
            command.append(f"COLAB_REMOTE_RUNTIME_VERSION={runtime_version}")
        if runtime_language:
            command.append(f"COLAB_REMOTE_LANGUAGE={runtime_language}")
        command.extend(
            [
                f"{_linux_home()}/.local/share/uv/tools/google-colab-cli/bin/python",
                _wsl_path(PLUGIN_ROOT / "scripts" / "colab_compat.py"),
            ]
        )
    else:
        command.append(path)
    command.extend(["--auth", "oauth2", *arguments])
    session = None
    if "-s" in arguments:
        index = arguments.index("-s")
        if index + 1 < len(arguments):
            session = _validate_session_name(arguments[index + 1])
    if (
        session is not None
        and serialize_session
        and arguments
        and arguments[0] not in {"sessions", "status", "stop"}
        and _drive_mount_is_active(session)
    ):
        raise RuntimeError(
            "Google Drive mounting is waiting on this session. Complete it with "
            "complete_google_drive_mount before running another session command."
        )

    def invoke() -> subprocess.CompletedProcess[str]:
        if session is None or not serialize_session:
            return _wsl(command, input_text=input_text, timeout=timeout, check=check)
        with _session_cli_lock(session, max(30, min(timeout, 300))):
            return _wsl(command, input_text=input_text, timeout=timeout, check=check)

    retry_safe = bool(arguments and arguments[0] in {"sessions", "status", "ls"})
    attempts = int(_load_config()["retry_attempts"]) if retry_safe and check else 1
    for attempt in range(1, attempts + 1):
        try:
            return invoke()
        except RuntimeError:
            if attempt == attempts:
                raise
            time.sleep(min(2 ** (attempt - 1), 4))
    raise AssertionError("unreachable")


def _output(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {
        "exit_code": result.returncode,
        "stdout": _redact(result.stdout),
        "stderr": _redact(result.stderr),
    }


def _normalize_accelerator(value: str) -> str:
    raw = value.strip().lower()
    aliases = {
        "tpu-v5e1": "v5e-1",
        "tpu-v5e-1": "v5e-1",
        "v5e1": "v5e-1",
        "tpu-v6e1": "v6e-1",
        "tpu-v6e-1": "v6e-1",
        "v6e1": "v6e-1",
    }
    normalized = aliases.get(raw, raw)
    if normalized not in ACCELERATORS:
        raise ValueError(f"accelerator must be one of {sorted(ACCELERATORS)}")
    return normalized


def _normalize_runtime_version(value: str) -> str:
    selected = value.strip().lower()
    if selected in {"", "latest", "recommended"}:
        return "latest"
    if not re.fullmatch(r"20\d{2}\.\d{2}", selected):
        raise ValueError(
            "runtime_version must be 'latest' or a Colab version in YYYY.MM format"
        )
    return selected


def _accelerator_args(accelerator: str) -> list[str]:
    if accelerator == "cpu":
        return []
    if accelerator.startswith("v"):
        return ["--tpu", accelerator.replace("-", "")]
    return ["--gpu", accelerator.upper()]


def _cost_warning(accelerator: str, high_ram: bool) -> str:
    parts = [
        f"Requested accelerator: {accelerator}.",
        "Starting any Colab session may consume quota or compute units.",
        "Exact rates and availability are controlled by Google Colab and are not estimated by this plugin.",
    ]
    if high_ram:
        parts.append(
            "High-RAM allocation was requested and may consume additional compute units."
        )
    return " ".join(parts)


def _validate_session_name(name: str) -> str:
    if not SAFE_NAME.fullmatch(name):
        raise ValueError(
            "session_name must use 1-64 letters, digits, dots, underscores, or hyphens"
        )
    return name


def _validate_job_name(name: str) -> str:
    if not SAFE_NAME.fullmatch(name):
        raise ValueError(
            "job_name must use 1-64 letters, digits, dots, underscores, or hyphens"
        )
    return name


def _validate_remote_workdir(path: str) -> str:
    if not re.fullmatch(r"/[A-Za-z0-9_./-]{0,255}", path) or ".." in Path(path).parts:
        raise ValueError("workdir must be a simple absolute remote path without '..'")
    return path


def _validate_remote_path(path: str) -> str:
    if not re.fullmatch(r"/[A-Za-z0-9_./-]{0,1023}", path) or ".." in Path(path).parts:
        raise ValueError("remote path must be a simple absolute path without '..'")
    return path


def _wsl_path(local_path: Path) -> str:
    if not _uses_wsl():
        return str(local_path.resolve())
    normalized = str(local_path).replace("\\", "/")
    result = _wsl(["wslpath", "-a", "-u", "--", normalized], timeout=15)
    return result.stdout.strip()


def _allowed_local_path(path: str, *, must_exist: bool) -> Path:
    config = _load_config()
    roots = [
        Path(root).expanduser().resolve() for root in config["allowed_local_roots"]
    ]
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
                raise RuntimeError(
                    f"Remote marker {marker} did not contain a JSON object"
                )
            return decoded
    raise RuntimeError(f"Remote command did not return {marker}")


def _remote_shell(
    session_name: str, script: str, *, timeout: int = 300
) -> subprocess.CompletedProcess[str]:
    session = _validate_session_name(session_name)
    script_encoded = base64.b64encode(script.encode()).decode()
    python = (
        "import base64,json,subprocess\n"
        f"script=base64.b64decode({script_encoded!r}).decode()\n"
        "result=subprocess.run(['bash','-lc',script],capture_output=True,text=True)\n"
        "print('CODEX_REMOTE_SHELL='+json.dumps({"
        "'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr},separators=(',',':')))\n"
    )
    # ``colab console`` is interactive and can corrupt long or rapidly pasted
    # input. Stage the generated helper with the official upload command, then
    # execute it with one short console command.
    _secure_state_root()
    nonce = secrets.token_hex(12)
    local_helper = STATE_ROOT / f"remote-shell-{nonce}.py"
    remote_helper = f"/content/.codex-remote-shell-{nonce}.py"
    uploaded = False
    try:
        with local_helper.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(python)
        try:
            os.chmod(local_helper, 0o600)
        except OSError:
            pass
        _colab(
            ["upload", "-s", session, _wsl_path(local_helper), remote_helper],
            timeout=min(timeout + 30, 1800),
        )
        uploaded = True
        command = (
            f"python3 {shlex.quote(remote_helper)}; _codex_remote_status=$?; "
            f"rm -f {shlex.quote(remote_helper)}; (exit $_codex_remote_status)\n"
        )
        cli_result = _colab(
            ["console", "-s", session],
            input_text=command,
            timeout=timeout + 30,
        )
    finally:
        local_helper.unlink(missing_ok=True)
        if uploaded:
            _colab(
                ["rm", "-s", session, remote_helper],
                timeout=60,
                check=False,
            )
    payload = _extract_json_marker(cli_result.stdout, "CODEX_REMOTE_SHELL=")
    result = subprocess.CompletedProcess(
        ["remote-shell", session],
        int(payload.get("returncode", 1)),
        str(payload.get("stdout", "")),
        str(payload.get("stderr", "")),
    )
    if result.returncode != 0:
        detail = _redact(
            (
                result.stderr
                or result.stdout
                or f"remote exit code {result.returncode}"
            ).strip()
        )
        raise RuntimeError(detail)
    return result


def _secret_environment(session_name: str) -> dict[str, str]:
    environment = secret_broker.enabled_environment(STATE_ROOT, session_name)
    _remember_secret_redactions(environment)
    return environment


@contextmanager
def _staged_secret_file(
    session_name: str, environment: dict[str, str]
):
    if not environment:
        yield None
        return
    session = _validate_session_name(session_name)
    _secure_state_root()
    nonce = secrets.token_hex(12)
    local_path = STATE_ROOT / f"secret-environment-{nonce}.hex"
    remote_path = f"/content/.codex-secret-{nonce}.hex"
    payload = "".join(
        f"{name}\t{value.encode().hex()}\n"
        for name, value in sorted(environment.items())
    )
    local_path.write_text(payload, encoding="utf-8")
    try:
        os.chmod(local_path, 0o600)
    except OSError:
        pass
    uploaded = False
    try:
        _colab(
            ["upload", "-s", session, _wsl_path(local_path), remote_path],
            timeout=180,
        )
        uploaded = True
        _remote_shell(
            session,
            f"chmod 600 {shlex.quote(remote_path)}",
            timeout=60,
        )
        yield remote_path
    finally:
        local_path.unlink(missing_ok=True)
        if uploaded:
            _colab(
                ["rm", "-s", session, remote_path],
                timeout=60,
                check=False,
            )


def _bash_secret_prelude(remote_path: str | None) -> str:
    if remote_path is None:
        return ""
    path = shlex.quote(remote_path)
    return (
        f"_codex_secret_file={path}\n"
        "_codex_secret_env=\"${_codex_secret_file}.sh\"\n"
        "python3 - \"$_codex_secret_file\" \"$_codex_secret_env\" <<'PY'\n"
        "import os,shlex,sys\n"
        "source,target=sys.argv[1:]\n"
        "fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)\n"
        "with open(source,encoding='utf-8') as stream,os.fdopen(fd,'w',encoding='utf-8') as out:\n"
        "    for line in stream:\n"
        "        name,encoded=line.rstrip('\\n').split('\\t',1)\n"
        "        out.write('export '+name+'='+shlex.quote(bytes.fromhex(encoded).decode())+'\\n')\n"
        "PY\n"
        "source \"$_codex_secret_env\"\n"
        "rm -f \"$_codex_secret_file\" \"$_codex_secret_env\"\n"
        "unset _codex_secret_file _codex_secret_env\n"
    )


def _kernel_secret_prelude(language: str, remote_path: str | None) -> str:
    if remote_path is None:
        return ""
    path = repr(remote_path)
    if language == "python":
        return (
            "import os as _codex_os\n"
            f"_codex_secret_path={path}\n"
            "with open(_codex_secret_path,encoding='utf-8') as _codex_stream:\n"
            "    for _codex_line in _codex_stream:\n"
            "        _codex_name,_codex_hex=_codex_line.rstrip('\\n').split('\\t',1)\n"
            "        _codex_os.environ[_codex_name]=bytes.fromhex(_codex_hex).decode()\n"
            "_codex_os.unlink(_codex_secret_path)\n"
            "del _codex_secret_path,_codex_stream,_codex_line,_codex_name,_codex_hex\n"
        )
    if language == "r":
        return (
            f".codex_secret_path <- {json.dumps(remote_path)}\n"
            ".codex_secret_lines <- readLines(.codex_secret_path, warn=FALSE)\n"
            "for (.codex_secret_line in .codex_secret_lines) {\n"
            "  .codex_secret_parts <- strsplit(.codex_secret_line, '\\t', fixed=TRUE)[[1]]\n"
            "  .codex_secret_hex <- .codex_secret_parts[[2]]\n"
            "  .codex_secret_pos <- seq.int(1L, nchar(.codex_secret_hex), by=2L)\n"
            "  .codex_secret_raw <- as.raw(strtoi(substring(.codex_secret_hex, "
            ".codex_secret_pos, .codex_secret_pos + 1L), 16L))\n"
            "  do.call(Sys.setenv, setNames(list(rawToChar(.codex_secret_raw)), "
            ".codex_secret_parts[[1]]))\n"
            "}\n"
            "unlink(.codex_secret_path)\n"
            "rm(.codex_secret_path,.codex_secret_lines,.codex_secret_line,"
            ".codex_secret_parts,.codex_secret_hex,.codex_secret_pos,.codex_secret_raw)\n"
        )
    return (
        f"_codex_secret_path = {json.dumps(remote_path)}\n"
        "for _codex_secret_line in eachline(_codex_secret_path)\n"
        "    _codex_secret_name, _codex_secret_hex = split(_codex_secret_line, '\\t'; limit=2)\n"
        "    ENV[_codex_secret_name] = String(hex2bytes(_codex_secret_hex))\n"
        "end\n"
        "rm(_codex_secret_path; force=true)\n"
    )


def _inject_enabled_secrets_into_kernel(
    session_name: str, language: str
) -> list[str]:
    environment = _secret_environment(session_name)
    if not environment:
        return []
    with _staged_secret_file(session_name, environment) as remote_path:
        result = _colab(
            ["exec", "-s", session_name, "--timeout", "120"],
            input_text=_kernel_secret_prelude(language, remote_path),
            timeout=150,
            runtime_language=language,
        )
    if result.returncode != 0:
        raise RuntimeError(_redact(result.stderr or result.stdout))
    return sorted(environment)


def _clear_kernel_secret_names(
    session_name: str, language: str, names: list[str]
) -> None:
    if not names:
        return
    if language == "python":
        code = (
            "import os as _codex_os\n"
            f"for _codex_name in {names!r}: _codex_os.environ.pop(_codex_name,None)\n"
            "del _codex_name\n"
        )
    elif language == "r":
        r_names = "c(" + ",".join(json.dumps(name) for name in names) + ")"
        code = f"Sys.unsetenv({r_names})\n"
    else:
        code = (
            f"for _codex_name in {json.dumps(names)}\n"
            "    pop!(ENV, _codex_name, nothing)\n"
            "end\n"
        )
    _colab(
        ["exec", "-s", session_name, "--timeout", "120"],
        input_text=code,
        timeout=150,
        runtime_language=language,
    )


def _session_language(session_name: str) -> str:
    record = _load_session_ledger().get(session_name, {})
    language = str(record.get("language") or _load_config()["default_language"]).lower()
    return language if language in LANGUAGES else "python"


def _secret_manager_command(secret_name: str) -> str:
    arguments = ["colab-remote", "secrets", "add", secret_name]
    return subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)


def _memory_status(session_name: str) -> dict[str, Any]:
    script = (
        "python3 - <<'PY'\n"
        "import json, os\n"
        "pages=os.sysconf('SC_PHYS_PAGES'); size=os.sysconf('SC_PAGE_SIZE')\n"
        "print('CODEX_MEMORY='+json.dumps({'bytes':pages*size,'gib':round(pages*size/1024**3,2)}))\n"
        "PY"
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            result = _remote_shell(session_name, script, timeout=60)
            return _extract_json_marker(result.stdout, "CODEX_MEMORY=")
        except RuntimeError as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2)
    raise RuntimeError(f"RAM probe failed after three attempts: {last_error}")


def _initialize_native_language(session_name: str, language: str) -> dict[str, Any]:
    """Start and verify the requested native Colab kernel."""
    selected = language.strip().lower()
    if selected not in LANGUAGES:
        raise ValueError(f"language must be one of {sorted(LANGUAGES)}")
    probes = {
        "python": (
            "import json,sys\n"
            "print('CODEX_NATIVE_LANGUAGE='+json.dumps({"
            "'language':'python','version':sys.version.split()[0]}))"
        ),
        "r": (
            'cat(sprintf(\'CODEX_NATIVE_LANGUAGE={\\"language\\":\\"r\\",'
            '\\"version\\":\\"%s\\"}\\n\', as.character(getRversion())))'
        ),
        "julia": (
            'println("CODEX_NATIVE_LANGUAGE={\\"language\\":\\"julia\\",\\"version\\":\\"", '
            'VERSION, "\\"}")'
        ),
    }
    result = _colab(
        ["exec", "-s", session_name, "--timeout", "120"],
        input_text=probes[selected],
        timeout=150,
        runtime_language=selected,
    )
    details = _extract_json_marker(result.stdout, "CODEX_NATIVE_LANGUAGE=")
    return {
        "language": selected,
        "kernel": {"python": "python3", "r": "ir", "julia": "julia"}[selected],
        "native": True,
        "version": details.get("version"),
        **_output(result),
    }


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
    status["heartbeat_age_seconds"] = (
        int(time.time()) - int(heartbeat) if str(heartbeat).isdigit() else None
    )
    return status


def _write_notification(
    title: str,
    message: str,
    level: str = "info",
    *,
    desktop_popup: bool = True,
) -> dict[str, Any]:
    config = _load_config()
    event = {
        "time": int(time.time()),
        "title": title[:100],
        "message": message[:500],
        "level": level,
    }
    _secure_state_root()
    with NOTIFICATIONS_PATH.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event) + "\n")
    try:
        os.chmod(NOTIFICATIONS_PATH, 0o600)
    except OSError:
        pass

    delivered = False
    notification_mode = config["notification_mode"]
    mode_allows_popup = notification_mode == "all" or (
        notification_mode == "failures_only"
        and level.lower() in {"warning", "error", "failure", "failed"}
    )
    popup_enabled = bool(desktop_popup and mode_allows_popup)
    if popup_enabled and sys.platform == "win32":
        script = r"""
$ErrorActionPreference='Stop'
$appId='Codex.ColabRemote'
$appKey="HKCU:\Software\Classes\AppUserModelId\$appId"
New-Item -Path $appKey -Force | Out-Null
New-ItemProperty -Path $appKey -Name DisplayName -Value 'Colab Remote' -PropertyType String -Force | Out-Null
New-ItemProperty -Path $appKey -Name ShowInSettings -Value 1 -PropertyType DWord -Force | Out-Null
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] > $null
$title=[Security.SecurityElement]::Escape($env:COLAB_REMOTE_NOTIFY_TITLE)
$body=[Security.SecurityElement]::Escape($env:COLAB_REMOTE_NOTIFY_BODY)
$xml=New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$title</text><text>$body</text></binding></visual></toast>")
$toast=[Windows.UI.Notifications.ToastNotification]::new($xml)
$toast.SuppressPopup=$false
try { $toast.Priority=[Windows.UI.Notifications.ToastNotificationPriority]::High } catch { }
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
"""
        env = os.environ.copy()
        env["COLAB_REMOTE_NOTIFY_TITLE"] = event["title"]
        env["COLAB_REMOTE_NOTIFY_BODY"] = event["message"]
        delivered = (
            _run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                timeout=15,
                check=False,
                env=env,
            ).returncode
            == 0
        )
    elif popup_enabled and sys.platform == "darwin":
        apple_script = (
            "on run argv\n"
            "display notification (item 2 of argv) with title (item 1 of argv)\n"
            "end run"
        )
        delivered = (
            _run(
                [
                    "osascript",
                    "-e",
                    apple_script,
                    event["title"],
                    event["message"],
                ],
                timeout=15,
                check=False,
            ).returncode
            == 0
        )
    elif popup_enabled and shutil.which("notify-send"):
        delivered = (
            _run(
                [
                    "notify-send",
                    "--app-name=Colab Remote",
                    event["title"],
                    event["message"],
                ],
                timeout=15,
                check=False,
            ).returncode
            == 0
        )
    return {**event, "desktop_delivered": delivered}


def _monitor_job(
    session_name: str,
    job_name: str,
    interval_seconds: int,
    notify_on_completion: bool = False,
    stop_session_on_finish: bool = False,
    recover_on_runtime_loss: bool = False,
) -> None:
    key = f"{session_name}/{job_name}"
    failures = 0
    recovery_handoff = False
    while True:
        record = {
            "session_name": session_name,
            "job_name": job_name,
            "interval_seconds": interval_seconds,
            "notify_on_completion": notify_on_completion,
            "stop_session_on_finish": stop_session_on_finish,
            "recover_on_runtime_loss": recover_on_runtime_loss,
            "watcher_pid": os.getpid(),
            "heartbeat": int(time.time()),
        }
        _save_monitor_record(session_name, job_name, record)
        try:
            status = _job_status_impl(session_name, job_name)
            failures = 0
            if not status.get("exists"):
                _write_notification(
                    "Colab monitor stopped",
                    f"Job {job_name} no longer exists.",
                    "warning",
                    desktop_popup=notify_on_completion,
                )
                break
            state = status.get("status")
            if (
                state in {"finished", "stopped", "failed"}
                or status.get("exit_code") is not None
            ):
                exit_code = status.get("exit_code", "unknown")
                level = "success" if str(exit_code) == "0" else "warning"
                _write_notification(
                    "Colab job completed",
                    f"{job_name} on {session_name} finished with exit code {exit_code}.",
                    level,
                    desktop_popup=notify_on_completion,
                )
                if stop_session_on_finish:
                    try:
                        stop_session(session_name, confirm=True)
                    except Exception as exc:
                        _write_notification(
                            "Colab automatic shutdown failed",
                            f"{session_name}: {_redact(str(exc))}",
                            "warning",
                            desktop_popup=notify_on_completion,
                        )
                break
        except Exception as exc:
            failures += 1
            if failures >= 3:
                recovered = False
                if recover_on_runtime_loss:
                    try:
                        listing = _colab(["sessions"], timeout=30)
                        if session_name not in (listing.stdout + listing.stderr):
                            _recover_session_impl(session_name, preauthorized=True)
                            recovered = True
                            recovery_handoff = True
                    except Exception as recovery_exc:
                        _write_notification(
                            "Colab automatic recovery failed",
                            f"{job_name}: {_redact(str(recovery_exc))}",
                            "warning",
                            desktop_popup=notify_on_completion,
                        )
                if recovered:
                    break
                _write_notification(
                    "Colab monitor stopped",
                    f"{job_name}: {_redact(str(exc))}",
                    "warning",
                    desktop_popup=notify_on_completion,
                )
                break
        time.sleep(interval_seconds)
    if not recovery_handoff:
        with _monitor_lock:
            _monitors.pop(key, None)
        _save_monitor_record(session_name, job_name, None)


def _start_monitor(
    session_name: str,
    job_name: str,
    interval_seconds: int,
    notify_on_completion: bool = False,
    stop_session_on_finish: bool = False,
    recover_on_runtime_loss: bool = False,
) -> dict[str, Any]:
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
        if (
            isinstance(heartbeat, int)
            and int(time.time()) - heartbeat < interval_seconds * 2 + 30
        ):
            return {"watching": True, "already_running": True, **existing}
        metadata = {
            "session_name": session_name,
            "job_name": job_name,
            "interval_seconds": interval_seconds,
            "notify_on_completion": notify_on_completion,
            "stop_session_on_finish": stop_session_on_finish,
            "recover_on_runtime_loss": recover_on_runtime_loss,
            "heartbeat": int(time.time()),
        }
        _save_monitor_record(session_name, job_name, metadata)
        try:
            process = process_utils.background_popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--monitor-job",
                    session_name,
                    job_name,
                    str(interval_seconds),
                    "1" if notify_on_completion else "0",
                    "1" if stop_session_on_finish else "0",
                    "1" if recover_on_runtime_loss else "0",
                ],
                windowless_python_entrypoint=True,
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
            _start_monitor(
                str(record["session_name"]),
                str(record["job_name"]),
                interval,
                bool(record.get("notify_on_completion")),
                bool(record.get("stop_session_on_finish")),
                bool(record.get("recover_on_runtime_loss")),
            )
        except Exception:
            pass

    for saved in _load_monitor_ledger().values():
        if (
            not isinstance(saved, dict)
            or "session_name" not in saved
            or "job_name" not in saved
        ):
            continue
        thread = threading.Thread(
            target=resume, args=(saved,), daemon=True, name="colab-monitor-resume"
        )
        thread.start()


def _lease_path(session_name: str) -> Path:
    return LEASES_ROOT / f"{_validate_session_name(session_name)}.json"


def _save_lease_record(session_name: str, record: dict[str, Any] | None) -> None:
    _secure_state_root()
    LEASES_ROOT.mkdir(parents=True, exist_ok=True)
    target = _lease_path(session_name)
    if record is None:
        target.unlink(missing_ok=True)
        return
    temporary = target.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, target)


def _lease_session(session_name: str) -> None:
    session = _validate_session_name(session_name)
    while True:
        record = _load_session_ledger().get(session)
        if not record or not record.get("expires_at"):
            _save_lease_record(session, None)
            return
        expires_at = int(record["expires_at"])
        now = int(time.time())
        _save_lease_record(
            session,
            {
                "session_name": session,
                "expires_at": expires_at,
                "watcher_pid": os.getpid(),
                "heartbeat": now,
            },
        )
        if now >= expires_at:
            try:
                stop_session(session, confirm=True)
                _write_notification(
                    "Colab session lifetime reached",
                    f"{session} was stopped automatically.",
                    "success",
                )
            except Exception as exc:
                _write_notification(
                    "Colab lifetime shutdown failed",
                    f"{session}: {_redact(str(exc))}",
                    "warning",
                )
            finally:
                _save_lease_record(session, None)
            return
        time.sleep(max(1, min(60, expires_at - now)))


def _start_session_lease(session_name: str) -> dict[str, Any]:
    session = _validate_session_name(session_name)
    record = _load_session_ledger().get(session, {})
    expires_at = record.get("expires_at")
    if not isinstance(expires_at, int):
        _save_lease_record(session, None)
        return {"enabled": False, "session_name": session}
    existing_path = _lease_path(session)
    if existing_path.exists():
        try:
            existing = json.loads(existing_path.read_text(encoding="utf-8"))
            if int(time.time()) - int(existing.get("heartbeat", 0)) < 120:
                return {"enabled": True, "already_running": True, **existing}
        except (OSError, ValueError, TypeError):
            pass
    process = process_utils.background_popen(
        [sys.executable, str(Path(__file__).resolve()), "--lease-session", session],
        windowless_python_entrypoint=True,
    )
    metadata = {
        "enabled": True,
        "already_running": False,
        "session_name": session,
        "expires_at": expires_at,
        "watcher_pid": process.pid,
        "heartbeat": int(time.time()),
    }
    _save_lease_record(session, metadata)
    return metadata


def _resume_saved_leases() -> None:
    for session, record in _load_session_ledger().items():
        if isinstance(record, dict) and isinstance(record.get("expires_at"), int):
            try:
                _start_session_lease(session)
            except Exception:
                pass


def _remember_recovery_job(
    session_name: str,
    job_name: str,
    command: str,
    workdir: str,
    notify_on_completion: bool,
    monitor_interval_seconds: int,
    stop_session_on_finish: bool,
) -> None:
    ledger = _load_session_ledger()
    record = ledger.get(session_name)
    if not isinstance(record, dict) or not record.get("recovery_enabled"):
        raise ValueError(
            "Session recovery is not enabled; create the session with recovery_enabled=true"
        )
    jobs = record.setdefault("jobs", {})
    jobs[job_name] = {
        "command": command,
        "workdir": workdir,
        "notify_on_completion": notify_on_completion,
        "monitor_interval_seconds": monitor_interval_seconds,
        "stop_session_on_finish": stop_session_on_finish,
        "recover_on_runtime_loss": True,
    }
    ledger[session_name] = record
    _save_session_ledger(ledger)


def _recover_session_impl(
    session_name: str, *, preauthorized: bool = False
) -> dict[str, Any]:
    session = _validate_session_name(session_name)
    ledger = _load_session_ledger()
    saved = ledger.get(session)
    if not isinstance(saved, dict):
        raise ValueError(f"No recovery record exists for session: {session}")
    if not saved.get("recovery_enabled") and not preauthorized:
        raise PermissionError("Recovery was not enabled for this session")
    attempts = int(saved.get("recovery_attempts", 0))
    maximum = max(1, min(int(saved.get("max_recovery_attempts", 1)), 10))
    if attempts >= maximum:
        raise RuntimeError(f"Recovery attempt limit reached for {session}")
    listing = _colab(["sessions"], timeout=30)
    if session in (listing.stdout + listing.stderr):
        return {"session_name": session, "recovered": False, "already_active": True}
    jobs = dict(saved.get("jobs", {}))
    created = create_session(
        session,
        accelerator=str(saved.get("accelerator", "cpu")),
        language=str(saved.get("language", "python")),
        high_ram=bool(
            saved.get("high_ram_requested", saved.get("prefer_high_ram", False))
        ),
        runtime_version=str(saved.get("runtime_version", "latest")),
        max_lifetime_minutes=int(saved.get("max_lifetime_minutes", 0)),
        recovery_enabled=True,
        max_recovery_attempts=maximum,
        acknowledge_cost=True,
    )
    ledger = _load_session_ledger()
    current = ledger[session]
    current["recovery_attempts"] = attempts + 1
    current["jobs"] = jobs
    ledger[session] = current
    _save_session_ledger(ledger)
    restarted = []
    for job_name, recipe in jobs.items():
        _save_monitor_record(session, job_name, None)
        restarted.append(
            start_job(
                session,
                job_name,
                str(recipe["command"]),
                workdir=str(recipe.get("workdir", "/content")),
                notify_on_completion=bool(recipe.get("notify_on_completion", False)),
                monitor_interval_seconds=int(
                    recipe.get("monitor_interval_seconds", 30)
                ),
                stop_session_on_finish=bool(
                    recipe.get("stop_session_on_finish", False)
                ),
                recover_on_runtime_loss=True,
            )
        )
    _write_notification(
        "Colab session recovered",
        f"{session} was recreated and {len(restarted)} job(s) restarted.",
        "success",
    )
    return {
        "session_name": session,
        "recovered": True,
        "recovery_attempt": attempts + 1,
        "create": created,
        "restarted_jobs": restarted,
    }


@mcp.tool()
def get_config() -> dict[str, Any]:
    """Return non-secret Colab Remote defaults and approved local roots."""
    return _load_config()


@mcp.tool()
def set_config(
    default_accelerator: OptionalAcceleratorName = None,
    default_language: OptionalLanguageName = None,
    default_runtime_version: OptionalRuntimeVersion = None,
    default_high_ram: Annotated[
        bool | None,
        Field(
            description="Default High-RAM request. L4, G4, H100, v5e-1, and v6e-1 always use High-RAM."
        ),
    ] = None,
    default_timeout_seconds: Annotated[
        int | None,
        Field(description="Default operation timeout in seconds.", ge=30, le=86400),
    ] = None,
    compute_warning_minutes: Annotated[
        int | None,
        Field(description="Warn when a session reaches this runtime.", ge=5, le=1440),
    ] = None,
    default_max_lifetime_minutes: OptionalMaxLifetimeMinutes = None,
    notification_mode: Annotated[
        NotificationMode | None,
        Field(
            description="Desktop popup policy: off, failures_only, all, or null to keep the current mode."
        ),
    ] = None,
    max_concurrent_sessions: Annotated[
        int | None,
        Field(description="Maximum simultaneous active Colab assignments.", ge=1, le=64),
    ] = None,
    transfer_compression: Annotated[
        bool | None,
        Field(description="Default managed-transfer compression behavior."),
    ] = None,
    transfer_parallelism: Annotated[
        int | None,
        Field(description="Default simultaneous managed-transfer chunks.", ge=1, le=8),
    ] = None,
    retry_attempts: Annotated[
        int | None,
        Field(description="Total attempts for retry-safe operations.", ge=1, le=10),
    ] = None,
    default_drive_checkpoint_folder: Annotated[
        str | None,
        Field(
            description="Relative folder inside MyDrive/codex-colab used when a Drive save omits drive_path."
        ),
    ] = None,
    require_cost_acknowledgement: Annotated[
        bool | None,
        Field(
            description="True asks before each allocation; false grants standing authorization for automatic session creation."
        ),
    ] = None,
    require_secret_enable_approval: Annotated[
        bool | None,
        Field(
            description="True asks before enabling aliases; false lets the LLM enable required aliases automatically."
        ),
    ] = None,
    allowed_local_roots: Annotated[
        list[str] | None,
        Field(description="Existing absolute local directories the plugin may access."),
    ] = None,
    distro: Annotated[
        str | None,
        Field(description="WSL distribution on Windows; ignored on Linux and macOS."),
    ] = None,
    confirm_sensitive_change: Annotated[
        bool,
        Field(
            description="True only after user approval for standing allocation, secret-access, or local-root security changes."
        ),
    ] = False,
) -> dict[str, Any]:
    """Update supplied defaults; less restrictive security settings require explicit confirmation."""
    config = _load_config()
    if default_accelerator is not None:
        config["default_accelerator"] = _normalize_accelerator(default_accelerator)
    if default_language is not None:
        language = default_language.lower()
        if language not in LANGUAGES:
            raise ValueError(f"default_language must be one of {sorted(LANGUAGES)}")
        config["default_language"] = language
    if default_runtime_version is not None:
        config["default_runtime_version"] = _normalize_runtime_version(
            default_runtime_version
        )
    if default_high_ram is not None:
        config["default_high_ram"] = default_high_ram
    if default_timeout_seconds is not None:
        config["default_timeout_seconds"] = max(30, min(default_timeout_seconds, 86400))
    if compute_warning_minutes is not None:
        config["compute_warning_minutes"] = max(5, min(compute_warning_minutes, 1440))
    if default_max_lifetime_minutes is not None:
        config["default_max_lifetime_minutes"] = max(
            0, min(default_max_lifetime_minutes, 1440)
        )
    if notification_mode is not None:
        config["notification_mode"] = notification_mode
    if max_concurrent_sessions is not None:
        config["max_concurrent_sessions"] = max_concurrent_sessions
    if transfer_compression is not None:
        config["transfer_compression"] = transfer_compression
    if transfer_parallelism is not None:
        config["transfer_parallelism"] = transfer_parallelism
    if retry_attempts is not None:
        config["retry_attempts"] = retry_attempts
    if default_drive_checkpoint_folder is not None:
        config["default_drive_checkpoint_folder"] = (
            drive_ops.normalize_drive_path(
                default_drive_checkpoint_folder, allow_root=False
            )
        )
    if require_cost_acknowledgement is not None:
        if (
            config["require_cost_acknowledgement"]
            and not require_cost_acknowledgement
            and not confirm_sensitive_change
        ):
            raise PermissionError(
                "The user must explicitly confirm standing authorization for Colab allocations"
            )
        config["require_cost_acknowledgement"] = require_cost_acknowledgement
    if require_secret_enable_approval is not None:
        if (
            config["require_secret_enable_approval"]
            and not require_secret_enable_approval
            and not confirm_sensitive_change
        ):
            raise PermissionError(
                "The user must explicitly confirm automatic local-secret access"
            )
        config["require_secret_enable_approval"] = require_secret_enable_approval
    if distro is not None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,63}", distro):
            raise ValueError("Invalid WSL distribution name")
        config["distro"] = distro
    if allowed_local_roots is not None:
        if not confirm_sensitive_change:
            raise PermissionError(
                "The user must explicitly confirm changes to allowed_local_roots"
            )
        roots = []
        for root in allowed_local_roots:
            path = Path(root).expanduser().resolve()
            if not path.is_absolute() or not path.is_dir():
                raise ValueError(
                    f"Allowed root must be an existing absolute directory: {root}"
                )
            roots.append(str(path))
        config["allowed_local_roots"] = sorted(set(roots))
    _save_config(config)
    return config


@mcp.tool()
def authentication_instructions() -> dict[str, Any]:
    """Return the safe user-terminal OAuth command without starting or handling authentication."""
    shell_command = (
        "umask 077; env -u GOOGLE_APPLICATION_CREDENTIALS -u CLOUDSDK_CONFIG "
        "~/.local/bin/colab --auth oauth2 sessions; rc=$?; "
        'token="$HOME/.config/colab-cli/token.json"; '
        'test ! -f "$token" || chmod 600 "$token"; exit $rc'
    )
    command = (
        f'wsl -d "{_distro()}" -- bash -lc {shlex.quote(shell_command)}'
        if _uses_wsl()
        else shell_command
    )
    return {
        "command": command,
        "host_transport": "wsl" if _uses_wsl() else "native",
        "must_be_run_by_user": True,
        "warning": "Run this yourself in a trusted terminal. Never paste an authorization code into Codex.",
        "gcloud_adc_used": False,
    }


@mcp.tool()
def credential_status(
    validate_with_google: Annotated[
        bool,
        Field(description="Also make a harmless authenticated CLI request to Google."),
    ] = False,
) -> dict[str, Any]:
    """Check OAuth safely and automatically repair owner-only token permissions."""
    status = _credential_metadata()
    repaired = False
    if status["oauth_token_present"] and not status["owner_only_mode"]:
        repaired = _repair_credential_permissions()
        status = _credential_metadata()
    status["permissions_repaired"] = repaired
    if validate_with_google and status["oauth_token_present"]:
        result = _colab(["sessions"], timeout=30, check=False)
        status["google_validation_succeeded"] = result.returncode == 0
        status["validation_message"] = _redact((result.stderr or result.stdout).strip())
    return status


@mcp.tool()
def prepare_local_secret(secret_name: SecretName) -> dict[str, Any]:
    """Return a trusted-terminal command that stores one value with masked input; values never pass through MCP."""
    name = secret_broker.validate_name(secret_name)
    return {
        "secret_name": name,
        "command": _secret_manager_command(name),
        "must_be_run_by_user": True,
        "input_is_masked": True,
        "value_enters_mcp": False,
        "next_step": (
            "Run this command yourself in a trusted local terminal, enter the value twice, "
            "then ask Codex to call list_local_secrets again."
        ),
    }


@mcp.tool()
def list_local_secrets(
    session_name: Annotated[
        str | None,
        Field(
            description="Optional Colab session name for including its enabled aliases."
        ),
    ] = None,
) -> dict[str, Any]:
    """List local secret aliases and optional session grants; values are never returned."""
    names = secret_broker.list_names(STATE_ROOT)
    available: list[str] = []
    unavailable: list[str] = []
    for name in names:
        try:
            value = secret_broker.get_secret(STATE_ROOT, name)
            _remember_secret_redactions({name: value})
            available.append(name)
        except secret_broker.SecretBrokerError:
            unavailable.append(name)
    session = _validate_session_name(session_name) if session_name is not None else None
    return {
        "configured_names": names,
        "available_names": available,
        "unavailable_names": unavailable,
        "session_name": session,
        "enabled_names": (
            secret_broker.enabled_names(STATE_ROOT, session) if session is not None else []
        ),
        "values_exposed": False,
        "refresh": "Call list_local_secrets again after the user stores a new alias.",
    }


@mcp.tool()
def enable_local_secrets(
    session_name: SessionName,
    secret_names: Annotated[
        list[SecretName],
        Field(
            description="One or more configured aliases to expose as environment variables in this session.",
            min_length=1,
        ),
    ],
    acknowledge_access: Annotated[
        bool,
        Field(
            description="True only after user approval when require_secret_enable_approval is enabled."
        ),
    ] = False,
) -> dict[str, Any]:
    """Enable named local secrets for a session and inject them into its native kernel without returning values."""
    if _load_config()["require_secret_enable_approval"] and not acknowledge_access:
        raise PermissionError(
            "User approval is required before enabling local secrets for this session"
        )
    session = _validate_session_name(session_name)
    previous = set(secret_broker.enabled_names(STATE_ROOT, session))
    enabled = secret_broker.enable_names(STATE_ROOT, session, list(secret_names))
    language = _session_language(session)
    try:
        injected = _inject_enabled_secrets_into_kernel(session, language)
    except Exception:
        newly_added = sorted(set(enabled) - previous)
        if newly_added:
            secret_broker.disable_names(STATE_ROOT, session, newly_added)
        raise
    return {
        "session_name": session,
        "enabled_names": enabled,
        "injected_names": injected,
        "language": language,
        "values_exposed": False,
        "applies_to": [
            "execute_code",
            "execute_file",
            "terminal_exec",
            "start_job",
        ],
        "security_note": (
            "Code running with an enabled environment variable can read it. "
            "Enable only aliases required by the workload."
        ),
    }


@mcp.tool()
def disable_local_secrets(
    session_name: SessionName,
    secret_names: Annotated[
        list[SecretName] | None,
        Field(
            description="Aliases to disable, or null to disable every local secret for this session."
        ),
    ] = None,
) -> dict[str, Any]:
    """Disable selected or all local secret aliases without changing their keychain values."""
    session = _validate_session_name(session_name)
    removed, remaining = secret_broker.disable_names(
        STATE_ROOT, session, None if secret_names is None else list(secret_names)
    )
    language = _session_language(session)
    kernel_cleared = True
    clear_error = ""
    if removed:
        try:
            _clear_kernel_secret_names(session, language, removed)
        except Exception as exc:
            kernel_cleared = False
            clear_error = _redact(str(exc))
    return {
        "session_name": session,
        "disabled_names": removed,
        "enabled_names": remaining,
        "kernel_environment_cleared": kernel_cleared,
        "clear_error": clear_error,
        "values_exposed": False,
        "warning": (
            "Already-running jobs retain their inherited environment until they finish or are stopped."
            if removed
            else ""
        ),
    }


@mcp.tool()
def doctor() -> dict[str, Any]:
    """Diagnose host transport, Colab CLI, OAuth, config, and notifications."""
    checks: dict[str, Any] = {"config": _load_config()}
    checks["platform"] = sys.platform
    checks["host_transport"] = "wsl" if _uses_wsl() else "native"
    host_available = True
    if _uses_wsl():
        wsl = _run(["wsl.exe", "--status"], timeout=15, check=False)
        host_available = wsl.returncode == 0
        checks["wsl_available"] = host_available
    checks["host_available"] = host_available
    if host_available:
        path = _colab_path()
        checks["colab_cli_present"] = (
            _wsl(["test", "-x", path], timeout=10, check=False).returncode == 0
        )
        if checks["colab_cli_present"]:
            version = _colab(
                ["version"], timeout=20, require_credentials=False, check=False
            )
            checks["colab_cli_version"] = _redact(
                (version.stdout or version.stderr).strip()
            )
        credentials = _credential_metadata()
        repaired = False
        if credentials["oauth_token_present"] and not credentials["owner_only_mode"]:
            repaired = _repair_credential_permissions()
            credentials = _credential_metadata()
        credentials["permissions_repaired"] = repaired
        checks["credentials"] = credentials
    checks["local_file_access_enabled"] = bool(checks["config"]["allowed_local_roots"])
    checks["high_ram_supported_by_cli"] = False
    checks["high_ram_supported_by_compatibility_wrapper"] = True
    checks["runtime_version_supported_by_compatibility_wrapper"] = True
    checks["desktop_notification_mode"] = checks["config"]["notification_mode"]
    if checks["config"]["notification_mode"] == "off":
        checks["desktop_notification_backend"] = "disabled"
    else:
        checks["desktop_notification_backend"] = (
            "windows-toast"
            if sys.platform == "win32"
            else "macos-notification-center"
            if sys.platform == "darwin"
            else "notify-send"
            if shutil.which("notify-send")
            else "history-only"
        )
    checks["native_runtime_languages"] = {
        "default": "python",
        "python": "python3",
        "r": "ir",
        "julia": "julia",
    }
    return checks


@mcp.tool()
def list_sessions() -> dict[str, Any]:
    """List active Colab sessions."""
    return _output(_colab(["sessions"], timeout=30))


def _active_session_count(output: str) -> int:
    return sum(
        1
        for line in output.splitlines()
        if re.match(r"^\[[^\]]+\]\s+\S+\s+\|\s+Hardware:", line.strip())
    )


def _enforce_session_limit(config: dict[str, Any]) -> dict[str, int]:
    listing = _colab(["sessions"], timeout=30)
    active = _active_session_count(listing.stdout + "\n" + listing.stderr)
    maximum = int(config["max_concurrent_sessions"])
    if active >= maximum:
        raise RuntimeError(
            f"Active Colab session limit reached ({active}/{maximum}). Stop a session or increase max_concurrent_sessions."
        )
    return {"active_sessions": active, "max_concurrent_sessions": maximum}


def _created_session_url(session_name: str) -> str:
    result = _colab(["url", "-s", session_name], timeout=30)
    if result.returncode != 0:
        detail = _redact(result.stderr.strip() or result.stdout.strip())
        raise RuntimeError(f"Could not get the Colab session URL: {detail}")
    for candidate in re.findall(r"https://[^\s\"'<>]+", result.stdout):
        parsed = urlparse(candidate)
        query = parse_qs(parsed.query, keep_blank_values=True)
        backend_path = query.get("dbu", [None])[0]
        fragment_prefix = "datalabBackendUrl="
        fragment_url = (
            parsed.fragment.removeprefix(fragment_prefix)
            if parsed.fragment.startswith(fragment_prefix)
            else ""
        )
        fragment_backend = urlparse(fragment_url)
        if (
            parsed.scheme == "https"
            and parsed.hostname == "colab.research.google.com"
            and parsed.path.startswith("/notebooks/")
            and backend_path is not None
            and backend_path.startswith("/tun/m/")
            and fragment_backend.scheme == "https"
            and fragment_backend.hostname == "colab.research.google.com"
            and fragment_backend.path == backend_path
        ):
            return candidate
    raise RuntimeError(
        "The official Colab CLI did not return a valid raw Colab attachment URL"
    )


@mcp.tool()
def create_session(
    session_name: SessionName,
    accelerator: OptionalAcceleratorName = None,
    language: OptionalLanguageName = None,
    high_ram: Annotated[
        bool | None,
        Field(
            description="Request High-RAM (true), standard RAM (false), or use the default (null). L4, G4, H100, v5e-1, and v6e-1 force High-RAM because no standard-RAM option exists."
        ),
    ] = None,
    runtime_version: OptionalRuntimeVersion = None,
    max_lifetime_minutes: OptionalMaxLifetimeMinutes = None,
    recovery_enabled: Annotated[
        bool, Field(description="Allow bounded recreation if the runtime is lost.")
    ] = False,
    max_recovery_attempts: Annotated[
        int, Field(description="Maximum automatic runtime reallocations.", ge=1, le=10)
    ] = 1,
    acknowledge_cost: Annotated[
        bool,
        Field(
            description="True after per-session approval when require_cost_acknowledgement is enabled; otherwise false is allowed."
        ),
    ] = False,
) -> dict[str, Any]:
    """Create a named CPU/GPU/TPU session under the configured approval policy."""
    config = _load_config()
    session = _validate_session_name(session_name)
    selected = _normalize_accelerator(accelerator or config["default_accelerator"])
    selected_language = (language or config["default_language"]).lower()
    if selected_language not in LANGUAGES:
        raise ValueError(f"language must be one of {sorted(LANGUAGES)}")
    requested_high_ram = config["default_high_ram"] if high_ram is None else high_ram
    high_ram_forced = selected in HIGH_RAM_REQUIRED_ACCELERATORS and not requested_high_ram
    selected_high_ram = requested_high_ram or selected in HIGH_RAM_REQUIRED_ACCELERATORS
    selected_runtime_version = _normalize_runtime_version(
        runtime_version or config["default_runtime_version"]
    )
    selected_max_lifetime = max(
        0,
        min(
            config["default_max_lifetime_minutes"]
            if max_lifetime_minutes is None
            else max_lifetime_minutes,
            1440,
        ),
    )
    selected_recovery_attempts = max(1, min(max_recovery_attempts, 10))
    warning = _cost_warning(selected, selected_high_ram)
    if config["require_cost_acknowledgement"] and not acknowledge_cost:
        raise PermissionError(
            warning + " Re-run with acknowledge_cost=true after the user accepts."
        )
    concurrency = _enforce_session_limit(config)
    warnings = [warning]
    if high_ram_forced:
        warnings.append(
            f"High-RAM was enabled automatically because {selected} is only available with High-RAM."
        )
    result = _colab(
        ["new", "-s", session, *_accelerator_args(selected)],
        timeout=900,
        machine_shape="hm" if selected_high_ram else None,
        runtime_version=None
        if selected_runtime_version == "latest"
        else selected_runtime_version,
    )
    try:
        native_language = _initialize_native_language(session, selected_language)
    except Exception as exc:
        _colab(["stop", "-s", session], timeout=120, check=False)
        raise RuntimeError(
            f"Colab allocated the VM, but its native {selected_language} kernel failed to start; "
            f"the session was stopped: {_redact(str(exc))}"
        ) from exc
    try:
        _record_session(
            session,
            selected,
            selected_language,
            selected_high_ram,
            selected_runtime_version,
            selected_max_lifetime,
            recovery_enabled,
            selected_recovery_attempts,
        )
    except Exception as exc:
        warnings.append(
            f"Session was created, but local duration tracking failed: {_redact(str(exc))}"
        )
    try:
        status = _output(_colab(["status", "-s", session], timeout=60))
    except Exception as exc:
        status = {"available": False, "error": _redact(str(exc))}
        warnings.append(
            "Session was created, but its status probe failed. Use session_status to retry."
        )
    try:
        memory = _memory_status(session)
    except Exception as exc:
        memory = {"available": False, "error": _redact(str(exc))}
        warnings.append(
            "Session was created, but RAM measurement failed. Use session_status to retry."
        )
    if selected_high_ram and memory.get("gib", 0) < 20:
        measured = f" Allocated RAM is {memory['gib']} GiB." if "gib" in memory else ""
        warnings.append(
            f"Google accepted the request but did not provide a High-RAM VM.{measured}"
        )
    try:
        compute = _session_compute_metadata(session)
    except Exception as exc:
        compute = {
            "tracked": False,
            "exact_cost_available": False,
            "error": _redact(str(exc)),
        }
    try:
        browser_url = _created_session_url(session)
    except Exception as exc:
        browser_url = None
        warnings.append(
            "Session was created, but its Colab webpage link could not be retrieved. "
            f"Use session_url to retry: {_redact(str(exc))}"
        )
    lease = (
        _start_session_lease(session) if selected_max_lifetime else {"enabled": False}
    )
    return {
        "session_name": session,
        "requested_accelerator": selected,
        "language": selected_language,
        "native_language": native_language,
        "high_ram_requested": selected_high_ram,
        "high_ram_forced_by_accelerator": high_ram_forced,
        "runtime_version": selected_runtime_version,
        "max_lifetime_minutes": selected_max_lifetime,
        "recovery_enabled": recovery_enabled,
        "max_recovery_attempts": selected_recovery_attempts,
        "concurrency": concurrency,
        "lease": lease,
        "memory": memory,
        "compute": compute,
        "session_url": browser_url,
        "session_url_presentation": "copy_paste_only",
        "session_url_instructions": (
            "Show session_url exactly inside a fenced code block, never as a Markdown link. "
            "Ask the user to copy the entire raw URL into the browser address bar. Do not open "
            "it through browser automation, which may encode the attachment fragment. Do not "
            "click Colab's normal Connect button; if the page remains disconnected, stop and "
            "report that the attachment failed."
        ),
        "secrets_setup": (
            "Copy session_url from its fenced code block into the browser address bar, use "
            "Colab's Secrets sidebar to add or enable a required secret, enter its value only "
            "in Colab, and then tell Codex it is ready."
        ),
        "create_output": _output(result),
        "status": status,
        "warnings": warnings,
    }


@mcp.tool()
def session_status(session_name: SessionName) -> dict[str, Any]:
    """Return Colab status plus measured RAM."""
    session = _validate_session_name(session_name)
    result = _colab(["status", "-s", session], timeout=60)
    ledger_record = _load_session_ledger().get(session, {})
    return {
        **_output(result),
        "memory": _memory_status(session),
        "compute": _session_compute_metadata(session),
        "lease": {
            "enabled": bool(ledger_record.get("expires_at")),
            "expires_at": ledger_record.get("expires_at"),
            "max_lifetime_minutes": ledger_record.get("max_lifetime_minutes", 0),
        },
        "recovery": {
            "enabled": bool(ledger_record.get("recovery_enabled")),
            "attempts": ledger_record.get("recovery_attempts", 0),
            "max_attempts": ledger_record.get("max_recovery_attempts", 0),
        },
    }


@mcp.tool()
def set_session_lifetime(
    session_name: SessionName, max_lifetime_minutes: MaxLifetimeMinutes = 0
) -> dict[str, Any]:
    """Set or remove an automatic maximum lifetime for an existing session; zero disables it."""
    session = _validate_session_name(session_name)
    minutes = max(0, min(max_lifetime_minutes, 1440))
    ledger = _load_session_ledger()
    if session not in ledger:
        raise ValueError(f"Session is not tracked locally: {session}")
    ledger[session]["max_lifetime_minutes"] = minutes
    ledger[session]["expires_at"] = int(time.time()) + minutes * 60 if minutes else None
    _save_session_ledger(ledger)
    lease = _start_session_lease(session) if minutes else {"enabled": False}
    if not minutes:
        _save_lease_record(session, None)
    return {
        "session_name": session,
        "max_lifetime_minutes": minutes,
        "expires_at": ledger[session]["expires_at"],
        "lease": lease,
    }


@mcp.tool()
def recovery_status(session_name: SessionName) -> dict[str, Any]:
    """Return the saved automatic-recovery recipe without exposing credential contents."""
    session = _validate_session_name(session_name)
    record = _load_session_ledger().get(session)
    if not isinstance(record, dict):
        return {"session_name": session, "tracked": False}
    jobs = record.get("jobs", {})
    return {
        "session_name": session,
        "tracked": True,
        "enabled": bool(record.get("recovery_enabled")),
        "attempts": int(record.get("recovery_attempts", 0)),
        "max_attempts": int(record.get("max_recovery_attempts", 0)),
        "recoverable_jobs": sorted(jobs) if isinstance(jobs, dict) else [],
        "recipe_stored_in_owner_only_state": bool(jobs),
    }


@mcp.tool()
def recover_session(
    session_name: SessionName,
    confirm_reallocate: Annotated[
        bool,
        Field(
            description="True only after user approval to consume quota by reallocating a VM."
        ),
    ] = False,
) -> dict[str, Any]:
    """Recreate a lost session and restart opted-in jobs from their saved recipes."""
    session = _validate_session_name(session_name)
    record = _load_session_ledger().get(session, {})
    if not record.get("recovery_enabled") and not confirm_reallocate:
        raise PermissionError(
            "Reallocation may consume compute units; confirm or pre-enable recovery on session creation"
        )
    return _recover_session_impl(
        session,
        preauthorized=bool(record.get("recovery_enabled")) or confirm_reallocate,
    )


@mcp.tool()
def prepare_language(
    session_name: SessionName,
    language: LanguageName,
) -> dict[str, Any]:
    """Switch to and verify a native Python, R, or Julia kernel; no installation is performed."""
    session = _validate_session_name(session_name)
    selected = language.lower()
    if selected not in LANGUAGES:
        raise ValueError(f"language must be one of {sorted(LANGUAGES)}")
    result = _initialize_native_language(session, selected)
    result["external_download_required"] = False
    return result


@mcp.tool()
def execute_code(
    session_name: SessionName,
    code: Annotated[
        str,
        Field(
            description="Source code to run in the selected native kernel.",
            min_length=1,
        ),
    ],
    language: OptionalLanguageName = None,
    timeout_seconds: OptionalTimeoutSeconds = None,
) -> dict[str, Any]:
    """Execute code in a native Python, R, or Julia Colab kernel."""
    if not code.strip():
        raise ValueError("code cannot be empty")
    config = _load_config()
    session = _validate_session_name(session_name)
    selected = (language or config["default_language"]).lower()
    timeout = max(1, min(timeout_seconds or config["default_timeout_seconds"], 86400))
    if selected not in LANGUAGES:
        raise ValueError(f"language must be one of {sorted(LANGUAGES)}")
    environment = _secret_environment(session)
    with _staged_secret_file(session, environment) as remote_path:
        result = _colab(
            ["exec", "-s", session, "--timeout", str(timeout)],
            input_text=_kernel_secret_prelude(selected, remote_path) + code,
            timeout=timeout + 30,
            runtime_language=selected,
        )
    return _output(result)


@mcp.tool()
def terminal_exec(
    session_name: SessionName,
    command: Annotated[
        str, Field(description="Linux shell command to run on Colab.", min_length=1)
    ],
    workdir: RemoteWorkdir = "/content",
    timeout_seconds: OptionalTimeoutSeconds = None,
) -> dict[str, Any]:
    """Run an arbitrary Linux shell command through the official Colab CLI."""
    if not command.strip():
        raise ValueError("command cannot be empty")
    session = _validate_session_name(session_name)
    remote_workdir = _validate_remote_workdir(workdir)
    timeout = max(
        1,
        min(
            timeout_seconds or _load_config()["default_timeout_seconds"],
            86400,
        ),
    )
    environment = _secret_environment(session)
    with _staged_secret_file(session, environment) as remote_path:
        script = (
            "set -o pipefail\n"
            + _bash_secret_prelude(remote_path)
            + f"cd {shlex.quote(remote_workdir)}\n{command}"
        )
        result = _remote_shell(session, script, timeout=timeout)
    return {
        "session_name": session,
        "workdir": remote_workdir,
        "transport": "official-colab-cli",
        "enabled_secret_names": sorted(environment),
        **_output(result),
    }


@mcp.tool()
def execute_file(
    session_name: SessionName,
    local_path: LocalPath,
    language: OptionalLanguageName = None,
    timeout_seconds: OptionalTimeoutSeconds = None,
) -> dict[str, Any]:
    """Execute an approved local Python, R, Julia, or notebook file."""
    source = _allowed_local_path(local_path, must_exist=True)
    selected = (language or _load_config()["default_language"]).lower()
    if selected not in LANGUAGES:
        raise ValueError(f"language must be one of {sorted(LANGUAGES)}")
    if source.suffix.lower() == ".ipynb":
        pass
    elif selected == "julia":
        if source.suffix.lower() != ".jl":
            raise ValueError("Julia execution requires a .jl file")
        return execute_code(
            session_name, source.read_text(encoding="utf-8"), "julia", timeout_seconds
        )
    elif selected == "r":
        if source.suffix.lower() != ".r":
            raise ValueError("R execution requires a .R file")
        return execute_code(
            session_name, source.read_text(encoding="utf-8"), "r", timeout_seconds
        )
    elif source.suffix.lower() != ".py":
        raise ValueError("Python execution requires a .py or .ipynb file")
    timeout = max(
        1, min(timeout_seconds or _load_config()["default_timeout_seconds"], 86400)
    )
    session = _validate_session_name(session_name)
    _inject_enabled_secrets_into_kernel(session, selected)
    result = _colab(
        [
            "exec",
            "-s",
            session,
            "-f",
            _wsl_path(source),
            "--timeout",
            str(timeout),
        ],
        timeout=timeout + 30,
        runtime_language=selected,
    )
    return _output(result)


def _notebook_path(local_path: str, *, must_exist: bool) -> Path:
    path = _allowed_local_path(local_path, must_exist=must_exist)
    if path.suffix.lower() != ".ipynb":
        raise ValueError("Notebook paths must end in .ipynb")
    if must_exist and not path.is_file():
        raise ValueError("Notebook path is not a file")
    return path


def _drive_relative_path(path: str) -> str:
    return drive_ops.normalize_drive_path(path, allow_root=False)


def _drive_save_path(source_name: str, requested: str | None) -> str:
    if requested is not None:
        return _drive_relative_path(requested)
    folder = str(_load_config()["default_drive_checkpoint_folder"])
    name = PurePosixPath(source_name.replace("\\", "/")).name
    if not name or name in {".", ".."}:
        raise ValueError("Could not derive a safe Drive item name from the source path")
    return _drive_relative_path(f"{folder}/{name}")


@mcp.tool()
def create_notebook(
    local_path: LocalPath,
    language: LanguageName = "python",
    title: Annotated[
        str | None, Field(description="Optional human-readable notebook title.")
    ] = None,
    overwrite: Annotated[
        bool, Field(description="Replace an existing notebook at this path.")
    ] = False,
) -> dict[str, Any]:
    """Create a local nbformat 4 notebook in an approved folder."""
    path = _notebook_path(local_path, must_exist=False)
    if path.exists() and not overwrite:
        raise FileExistsError(f"Notebook already exists: {path}")
    notebook = notebook_ops.new_notebook(language.lower(), title)
    notebook_ops.save(path, notebook)
    return {"local_path": str(path), **notebook_ops.summary(notebook)}


@mcp.tool()
def read_notebook(
    local_path: LocalPath,
    include_outputs: Annotated[
        bool, Field(description="Include stored cell outputs in the response.")
    ] = True,
) -> dict[str, Any]:
    """Read notebook cells, metadata, and optional outputs from an approved local path."""
    path = _notebook_path(local_path, must_exist=True)
    return {
        "local_path": str(path),
        **notebook_ops.summary(notebook_ops.load(path), include_outputs),
    }


@mcp.tool()
def add_notebook_cell(
    local_path: LocalPath,
    cell_type: Annotated[
        Literal["code", "markdown", "raw"], Field(description="Notebook cell type.")
    ],
    source: Annotated[str, Field(description="Complete cell source text.")],
    index: Annotated[
        int | None, Field(description="Insertion index; null appends the cell.", ge=0)
    ] = None,
) -> dict[str, Any]:
    """Add a code, markdown, or raw cell at an optional index."""
    path = _notebook_path(local_path, must_exist=True)
    notebook = notebook_ops.load(path)
    inserted = notebook_ops.add_cell(notebook, cell_type, source, index)
    notebook_ops.save(path, notebook)
    return {
        "local_path": str(path),
        "index": inserted,
        "cell_count": len(notebook["cells"]),
    }


@mcp.tool()
def edit_notebook_cell(
    local_path: LocalPath,
    index: Annotated[int, Field(description="Zero-based cell index.", ge=0)],
    source: Annotated[str, Field(description="Replacement cell source text.")],
    cell_type: Annotated[
        Literal["code", "markdown", "raw"] | None,
        Field(description="Optional replacement cell type."),
    ] = None,
) -> dict[str, Any]:
    """Replace one cell's source and optionally its type."""
    path = _notebook_path(local_path, must_exist=True)
    notebook = notebook_ops.load(path)
    notebook_ops.edit_cell(notebook, index, source, cell_type)
    notebook_ops.save(path, notebook)
    return {
        "local_path": str(path),
        "index": index,
        "cell": notebook_ops.summary(notebook)["cells"][index],
    }


@mcp.tool()
def delete_notebook_cell(
    local_path: LocalPath,
    index: Annotated[int, Field(description="Zero-based cell index.", ge=0)],
) -> dict[str, Any]:
    """Delete one notebook cell."""
    path = _notebook_path(local_path, must_exist=True)
    notebook = notebook_ops.load(path)
    deleted = notebook_ops.delete_cell(notebook, index)
    notebook_ops.save(path, notebook)
    return {
        "local_path": str(path),
        "deleted_cell_type": deleted["cell_type"],
        "cell_count": len(notebook["cells"]),
    }


@mcp.tool()
def move_notebook_cell(
    local_path: LocalPath,
    source_index: Annotated[
        int, Field(description="Current zero-based cell index.", ge=0)
    ],
    destination_index: Annotated[
        int, Field(description="New zero-based cell index.", ge=0)
    ],
) -> dict[str, Any]:
    """Move one notebook cell to a new index."""
    path = _notebook_path(local_path, must_exist=True)
    notebook = notebook_ops.load(path)
    notebook_ops.move_cell(notebook, source_index, destination_index)
    notebook_ops.save(path, notebook)
    return {
        "local_path": str(path),
        "source_index": source_index,
        "destination_index": destination_index,
    }


@mcp.tool()
def run_notebook_cells(
    session_name: SessionName,
    local_path: LocalPath,
    cell_indices: Annotated[
        list[int] | None,
        Field(description="Zero-based code-cell indices; null runs every code cell."),
    ] = None,
    language: OptionalLanguageName = None,
    timeout_seconds: OptionalTimeoutSeconds = None,
    stop_on_error: Annotated[
        bool, Field(description="Stop after the first failing cell.")
    ] = True,
) -> dict[str, Any]:
    """Run selected code cells on Colab and save their outputs into the local notebook."""
    session = _validate_session_name(session_name)
    path = _notebook_path(local_path, must_exist=True)
    notebook = notebook_ops.load(path)
    selected_language = (
        language
        or notebook.get("metadata", {}).get("kernelspec", {}).get("language")
        or _load_config()["default_language"]
    ).lower()
    indices = (
        cell_indices
        if cell_indices is not None
        else list(range(len(notebook["cells"])))
    )
    if len(indices) != len(set(indices)):
        raise ValueError("cell_indices cannot contain duplicates")
    results = []
    execution_count = max(
        [
            int(cell.get("execution_count") or 0)
            for cell in notebook["cells"]
            if cell.get("cell_type") == "code"
        ],
        default=0,
    )
    for index in indices:
        cell = notebook_ops.get_cell(notebook, index)
        if cell["cell_type"] != "code":
            results.append(
                {"index": index, "skipped": True, "reason": "not a code cell"}
            )
            continue
        execution_count += 1
        cell["execution_count"] = execution_count
        try:
            result = execute_code(
                session,
                notebook_ops.source_text(cell),
                selected_language,
                timeout_seconds,
            )
            outputs = []
            if result.get("stdout"):
                outputs.append(
                    {
                        "output_type": "stream",
                        "name": "stdout",
                        "text": result["stdout"],
                    }
                )
            if result.get("stderr"):
                outputs.append(
                    {
                        "output_type": "stream",
                        "name": "stderr",
                        "text": result["stderr"],
                    }
                )
            cell["outputs"] = outputs
            results.append(
                {"index": index, "success": True, "output_count": len(outputs)}
            )
        except Exception as exc:
            message = _redact(str(exc))
            cell["outputs"] = [
                {
                    "output_type": "error",
                    "ename": type(exc).__name__,
                    "evalue": message,
                    "traceback": [message],
                }
            ]
            results.append({"index": index, "success": False, "error": message})
            notebook_ops.save(path, notebook)
            if stop_on_error:
                break
    notebook.setdefault("metadata", {}).setdefault("colab_remote", {})[
        "last_session"
    ] = session
    notebook["metadata"]["colab_remote"]["last_run_at"] = int(time.time())
    notebook_ops.save(path, notebook)
    return {
        "session_name": session,
        "local_path": str(path),
        "language": selected_language,
        "results": results,
    }


@mcp.tool()
def import_notebook(
    source_path: LocalPath,
    destination_path: LocalPath,
    overwrite: Annotated[
        bool, Field(description="Replace an existing destination notebook.")
    ] = False,
) -> dict[str, Any]:
    """Validate and copy an existing local notebook into another approved location."""
    source = _notebook_path(source_path, must_exist=True)
    destination = _notebook_path(destination_path, must_exist=False)
    if destination.exists() and not overwrite:
        raise FileExistsError(f"Notebook already exists: {destination}")
    notebook = notebook_ops.load(source)
    notebook_ops.save(destination, notebook)
    return {
        "source_path": str(source),
        "local_path": str(destination),
        **notebook_ops.summary(notebook, False),
    }


@mcp.tool()
def export_session_notebook(
    session_name: SessionName,
    local_path: LocalPath,
    lines: Annotated[
        int,
        Field(
            description="History lines to export; 0 exports all available history.",
            ge=0,
            le=5000,
        ),
    ] = 0,
) -> dict[str, Any]:
    """Export Colab session history as a replayable local .ipynb notebook."""
    session = _validate_session_name(session_name)
    destination = _notebook_path(local_path, must_exist=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    arguments = ["log", "-s", session]
    if lines > 0:
        arguments.extend(["-n", str(max(1, min(lines, 5000)))])
    arguments.extend(["-o", _wsl_path(destination)])
    result = _colab(arguments, timeout=120)
    if not destination.is_file():
        raise RuntimeError("Colab CLI did not create the exported notebook")
    notebook = notebook_ops.load(destination)
    return {
        "session_name": session,
        "local_path": str(destination),
        **_output(result),
        **notebook_ops.summary(notebook, False),
    }


def _drive_operation(session_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    session = _validate_session_name(session_name)
    python_source = drive_ops.remote_script(payload)
    record = _load_session_ledger().get(session, {})
    language = str(record.get("language") or "python").lower()
    if language not in LANGUAGES:
        language = "python"

    _secure_state_root()
    nonce = secrets.token_hex(12)
    helper = STATE_ROOT / f"drive-operation-{nonce}.py"
    wrapper: Path | None = None
    remote_helper = f"/content/.codex-drive-operation-{nonce}.py"
    uploaded = False
    try:
        with helper.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(python_source)
        try:
            os.chmod(helper, 0o600)
        except OSError:
            pass

        execute_path = helper
        if language != "python":
            _colab(
                ["upload", "-s", session, _wsl_path(helper), remote_helper],
                timeout=1800,
            )
            uploaded = True
            suffix = ".R" if language == "r" else ".jl"
            wrapper = STATE_ROOT / f"drive-operation-{nonce}{suffix}"
            if language == "r":
                wrapper_source = (
                    f'status <- system2("python3", {json.dumps(remote_helper)})\n'
                    'if (!identical(status, 0L)) stop("Drive helper failed")\n'
                )
            else:
                wrapper_source = f'run(`python3 {remote_helper}`)\n'
            with wrapper.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(wrapper_source)
            try:
                os.chmod(wrapper, 0o600)
            except OSError:
                pass
            execute_path = wrapper

        result = _colab(
            [
                "exec",
                "-s",
                session,
                "-f",
                _wsl_path(execute_path),
                "--timeout",
                "1800",
            ],
            timeout=1830,
        )
        try:
            return _extract_json_marker(result.stdout, drive_ops.RESULT_MARKER)
        except RuntimeError as exc:
            detail = _redact((result.stderr or result.stdout).strip())
            raise RuntimeError(detail or str(exc)) from exc
    finally:
        helper.unlink(missing_ok=True)
        if wrapper is not None:
            wrapper.unlink(missing_ok=True)
        if uploaded:
            _colab(
                ["rm", "-s", session, remote_helper], timeout=60, check=False
            )


_DRIVE_MOUNT_ACTIVE_EVENTS = {"starting", "authorization_required", "resuming"}
_DRIVE_MOUNT_TERMINAL_EVENTS = {"completed", "error", "cancelled", "timed_out"}
_DRIVE_MOUNT_STATE_FILES = {
    "status.json",
    "authorization.url",
    "resume.request",
    "cancel.request",
}


def _drive_mount_state_dir(session_name: str) -> Path:
    session = _validate_session_name(session_name)
    _secure_state_root()
    DRIVE_MOUNTS_ROOT.mkdir(parents=True, exist_ok=True)
    root = DRIVE_MOUNTS_ROOT / session
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(DRIVE_MOUNTS_ROOT, 0o700)
        os.chmod(root, 0o700)
    except OSError:
        pass
    return root


def _drive_mount_status(session_name: str) -> dict[str, Any]:
    path = _drive_mount_state_dir(session_name) / "status.json"
    if not path.is_file():
        return {"event": "missing"}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"event": "missing"}
    return value if isinstance(value, dict) else {"event": "missing"}


def _drive_mount_worker_alive(status: dict[str, Any]) -> bool:
    try:
        pid = int(status.get("worker_pid", 0))
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    return _wsl(["kill", "-0", str(pid)], timeout=10, check=False).returncode == 0


def _drive_mount_is_active(session_name: str) -> bool:
    status = _drive_mount_status(session_name)
    return (
        status.get("event") in _DRIVE_MOUNT_ACTIVE_EVENTS
        and _drive_mount_worker_alive(status)
    )


def _clear_drive_mount_state(session_name: str) -> Path:
    root = _drive_mount_state_dir(session_name)
    for name in _DRIVE_MOUNT_STATE_FILES:
        (root / name).unlink(missing_ok=True)
    return root


def _start_drive_mount_worker(session_name: str) -> dict[str, Any]:
    session = _validate_session_name(session_name)
    existing = _drive_mount_status(session)
    if (
        existing.get("event") in _DRIVE_MOUNT_ACTIVE_EVENTS
        and _drive_mount_worker_alive(existing)
    ):
        return existing
    _require_credentials()
    colab = _colab_path()
    if _wsl(["test", "-x", colab], timeout=10, check=False).returncode != 0:
        raise RuntimeError("Google Colab CLI is not installed")
    state_dir = _clear_drive_mount_state(session)
    python = f"{_linux_home()}/.local/share/uv/tools/google-colab-cli/bin/python"
    command = [
        "env",
        "-u",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "-u",
        "CLOUDSDK_CONFIG",
        python,
        _wsl_path(PLUGIN_ROOT / "scripts" / "drive_mount_worker.py"),
        "--colab",
        colab,
        "--session",
        session,
        "--mount-path",
        drive_ops.DRIVE_MOUNT_PATH,
        "--state-dir",
        _wsl_path(state_dir),
    ]
    host_command = (
        ["wsl.exe", "-d", _distro(), "--", *command] if _uses_wsl() else command
    )
    process = process_utils.background_popen(
        host_command,
    )
    return {
        "event": "starting",
        "launcher_pid": process.pid,
        "started_at": int(time.time()),
    }


def _wait_for_drive_mount(
    session_name: str, *, wait_seconds: int, include_authorization: bool
) -> dict[str, Any]:
    deadline = time.monotonic() + max(0, wait_seconds)
    while True:
        status = _drive_mount_status(session_name)
        event = status.get("event")
        if event in _DRIVE_MOUNT_TERMINAL_EVENTS or (
            include_authorization and event == "authorization_required"
        ):
            return status
        if event in _DRIVE_MOUNT_ACTIVE_EVENTS and not _drive_mount_worker_alive(
            status
        ):
            return {
                "event": "error",
                "message": "The Drive mount worker stopped unexpectedly.",
            }
        if time.monotonic() >= deadline:
            return status
        time.sleep(0.25)


def _consume_drive_authorization_url(session_name: str) -> str | None:
    path = _drive_mount_state_dir(session_name) / "authorization.url"
    if not path.is_file():
        return None
    try:
        if path.stat().st_size > 16_384:
            raise RuntimeError("Google returned an invalid Drive authorization URL")
        value = path.read_text(encoding="utf-8").strip()
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() != "accounts.google.com"
        ):
            raise RuntimeError("Google returned an unexpected Drive authorization URL")
        return value
    finally:
        path.unlink(missing_ok=True)


def _request_drive_mount_resume(session_name: str) -> None:
    path = _drive_mount_state_dir(session_name) / "resume.request"
    path.write_text("resume\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _cancel_drive_mount_worker(session_name: str) -> dict[str, Any]:
    session = _validate_session_name(session_name)
    status = _drive_mount_status(session)
    if not (
        status.get("event") in _DRIVE_MOUNT_ACTIVE_EVENTS
        and _drive_mount_worker_alive(status)
    ):
        return {"cancelled": False, "event": status.get("event", "missing")}
    request = _drive_mount_state_dir(session) / "cancel.request"
    request.write_text("cancel\n", encoding="utf-8")
    try:
        os.chmod(request, 0o600)
    except OSError:
        pass
    result = _wait_for_drive_mount(
        session, wait_seconds=5, include_authorization=False
    )
    if result.get("event") in _DRIVE_MOUNT_ACTIVE_EVENTS:
        pid = int(result.get("worker_pid", 0) or 0)
        if pid > 0:
            _wsl(["kill", "-TERM", str(pid)], timeout=10, check=False)
    (_drive_mount_state_dir(session) / "authorization.url").unlink(missing_ok=True)
    return {"cancelled": True, "event": result.get("event")}


def _drive_mount_error(status: dict[str, Any]) -> RuntimeError:
    event = str(status.get("event", "error"))
    message = str(status.get("message") or "The official Colab Drive mount failed.")
    return RuntimeError(f"{message} Mount state: {event}.")


def _drive_mount_pending_result(
    session_name: str, status: dict[str, Any]
) -> dict[str, Any]:
    session = _validate_session_name(session_name)
    event = str(status.get("event", "starting"))
    browser_opened = False
    if event == "authorization_required":
        authorization_url = _consume_drive_authorization_url(session)
        if authorization_url:
            try:
                browser_opened = bool(webbrowser.open(authorization_url, new=2))
            except webbrowser.Error:
                browser_opened = False
        next_step = (
            "Approve Google Drive in the opened browser, then call "
            "complete_google_drive_mount for this session. Do not copy or paste a code."
        )
    else:
        next_step = "The official mount is starting; call mount_google_drive again shortly."
    return {
        "session_name": session,
        "mount_path": drive_ops.DRIVE_MOUNT_PATH,
        "already_mounted": False,
        "mount_state": event,
        "mount_in_progress": True,
        "authorization_required": event == "authorization_required",
        "browser_opened": browser_opened,
        "scope": "MyDrive/codex-colab only",
        "next_step": next_step,
    }


def _ensure_drive_workspace(
    session_name: str, *, mount_if_needed: bool
) -> dict[str, Any]:
    if mount_if_needed:
        return mount_google_drive(session_name)
    workspace = _drive_operation(session_name, {"action": "bootstrap"})
    return {"session_name": session_name, **workspace}


@mcp.tool()
def mount_google_drive(
    session_name: SessionName,
    mount_path: DriveMountPath = drive_ops.DRIVE_MOUNT_PATH,
) -> dict[str, Any]:
    """Start a persistent official Drive mount; first use may require completion."""
    session = _validate_session_name(session_name)
    if mount_path != drive_ops.DRIVE_MOUNT_PATH:
        raise ValueError(f"mount_path must be {drive_ops.DRIVE_MOUNT_PATH}")
    pending = _drive_mount_status(session)
    if (
        pending.get("event") in _DRIVE_MOUNT_ACTIVE_EVENTS
        and _drive_mount_worker_alive(pending)
    ):
        pending = _wait_for_drive_mount(
            session, wait_seconds=5, include_authorization=True
        )
        if pending.get("event") in {"error", "cancelled", "timed_out"}:
            raise _drive_mount_error(pending)
        return _drive_mount_pending_result(session, pending)
    try:
        workspace = _drive_operation(session, {"action": "bootstrap"})
        return {
            "session_name": session,
            "mount_path": drive_ops.DRIVE_MOUNT_PATH,
            "already_mounted": True,
            "mount_state": "completed",
            "mount_in_progress": False,
            "authorization_required": False,
            "scope": "MyDrive/codex-colab only",
            **workspace,
        }
    except RuntimeError:
        _start_drive_mount_worker(session)
    status = _wait_for_drive_mount(
        session, wait_seconds=45, include_authorization=True
    )
    if status.get("event") == "completed":
        workspace = _drive_operation(session, {"action": "bootstrap"})
        return {
            "session_name": session,
            "mount_path": drive_ops.DRIVE_MOUNT_PATH,
            "already_mounted": False,
            "mount_state": "completed",
            "mount_in_progress": False,
            "authorization_required": False,
            "scope": "MyDrive/codex-colab only",
            **workspace,
        }
    if status.get("event") in {"error", "cancelled", "timed_out"}:
        raise _drive_mount_error(status)
    return _drive_mount_pending_result(session, status)


@mcp.tool()
def complete_google_drive_mount(
    session_name: SessionName,
    wait_seconds: Annotated[
        int,
        Field(
            description="Seconds to wait for Google to finish mounting; call again if still running.",
            ge=1,
            le=120,
        ),
    ] = 60,
) -> dict[str, Any]:
    """Resume the same PTY mount after the user approves Google Drive."""
    session = _validate_session_name(session_name)
    status = _drive_mount_status(session)
    event = status.get("event")
    if event == "completed":
        workspace = _drive_operation(session, {"action": "bootstrap"})
        return {
            "session_name": session,
            "mount_path": drive_ops.DRIVE_MOUNT_PATH,
            "mount_state": "completed",
            "mount_in_progress": False,
            "authorization_required": False,
            "scope": "MyDrive/codex-colab only",
            **workspace,
        }
    if event not in _DRIVE_MOUNT_ACTIVE_EVENTS or not _drive_mount_worker_alive(
        status
    ):
        raise RuntimeError(
            "No active Google Drive authorization is waiting. Call mount_google_drive first."
        )
    if event == "authorization_required":
        _request_drive_mount_resume(session)
    status = _wait_for_drive_mount(
        session,
        wait_seconds=max(1, min(wait_seconds, 120)),
        include_authorization=False,
    )
    if status.get("event") == "completed":
        workspace = _drive_operation(session, {"action": "bootstrap"})
        return {
            "session_name": session,
            "mount_path": drive_ops.DRIVE_MOUNT_PATH,
            "mount_state": "completed",
            "mount_in_progress": False,
            "authorization_required": False,
            "scope": "MyDrive/codex-colab only",
            **workspace,
        }
    if status.get("event") in {"error", "cancelled", "timed_out"}:
        raise _drive_mount_error(status)
    return {
        "session_name": session,
        "mount_path": drive_ops.DRIVE_MOUNT_PATH,
        "mount_state": status.get("event", "resuming"),
        "mount_in_progress": True,
        "authorization_required": False,
        "scope": "MyDrive/codex-colab only",
        "next_step": "The official mount is still running; call complete_google_drive_mount again.",
    }


@mcp.tool()
def list_drive_files(
    session_name: SessionName,
    drive_path: DrivePath = ".",
    recursive: Annotated[
        bool, Field(description="List all descendants instead of direct children only.")
    ] = False,
    max_entries: Annotated[
        int, Field(description="Maximum Drive entries to return.", ge=1, le=1000)
    ] = 200,
    mount_if_needed: Annotated[
        bool,
        Field(description="Mount Drive and create codex-colab when needed."),
    ] = True,
) -> dict[str, Any]:
    """List files or folders only inside MyDrive/codex-colab."""
    session = _validate_session_name(session_name)
    _ensure_drive_workspace(session, mount_if_needed=mount_if_needed)
    return {
        "session_name": session,
        **_drive_operation(
            session,
            {
                "action": "list",
                "drive_path": drive_ops.normalize_drive_path(drive_path),
                "recursive": recursive,
                "max_entries": max_entries,
            },
        ),
    }


@mcp.tool()
def create_drive_folder(
    session_name: SessionName,
    drive_path: DrivePath,
    mount_if_needed: Annotated[
        bool,
        Field(description="Mount Drive and create codex-colab when needed."),
    ] = True,
) -> dict[str, Any]:
    """Create a folder only inside MyDrive/codex-colab."""
    session = _validate_session_name(session_name)
    relative = _drive_relative_path(drive_path)
    _ensure_drive_workspace(session, mount_if_needed=mount_if_needed)
    return {
        "session_name": session,
        **_drive_operation(
            session, {"action": "mkdir", "drive_path": relative}
        ),
    }


@mcp.tool()
def save_to_drive(
    session_name: SessionName,
    remote_path: RemotePath,
    drive_path: OptionalDrivePath = None,
    overwrite: Annotated[
        bool, Field(description="Replace an existing Drive file or folder.")
    ] = False,
    mount_if_needed: Annotated[
        bool,
        Field(description="Mount Drive and create codex-colab when needed."),
    ] = True,
) -> dict[str, Any]:
    """Copy a Colab file or folder into MyDrive/codex-colab."""
    session = _validate_session_name(session_name)
    source = _validate_remote_path(remote_path)
    relative = _drive_save_path(source, drive_path)
    _ensure_drive_workspace(session, mount_if_needed=mount_if_needed)
    return {
        "session_name": session,
        **_drive_operation(
            session,
            {
                "action": "save",
                "remote_path": source,
                "drive_path": relative,
                "overwrite": overwrite,
            },
        ),
    }


@mcp.tool()
def restore_from_drive(
    session_name: SessionName,
    drive_path: DrivePath,
    remote_path: RemotePath,
    overwrite: Annotated[
        bool, Field(description="Replace an existing Colab file or folder.")
    ] = False,
    mount_if_needed: Annotated[
        bool,
        Field(description="Mount Drive and create codex-colab when needed."),
    ] = True,
) -> dict[str, Any]:
    """Restore a file or folder from MyDrive/codex-colab into /content."""
    session = _validate_session_name(session_name)
    relative = _drive_relative_path(drive_path)
    destination = _validate_remote_path(remote_path)
    _ensure_drive_workspace(session, mount_if_needed=mount_if_needed)
    return {
        "session_name": session,
        **_drive_operation(
            session,
            {
                "action": "restore",
                "drive_path": relative,
                "remote_path": destination,
                "overwrite": overwrite,
            },
        ),
    }


@mcp.tool()
def move_drive_path(
    session_name: SessionName,
    source_drive_path: DrivePath,
    destination_drive_path: DrivePath,
    overwrite: Annotated[
        bool, Field(description="Replace an existing Drive destination.")
    ] = False,
    mount_if_needed: Annotated[
        bool,
        Field(description="Mount Drive and create codex-colab when needed."),
    ] = True,
) -> dict[str, Any]:
    """Move or rename an item within MyDrive/codex-colab."""
    session = _validate_session_name(session_name)
    source = _drive_relative_path(source_drive_path)
    destination = _drive_relative_path(destination_drive_path)
    _ensure_drive_workspace(session, mount_if_needed=mount_if_needed)
    return {
        "session_name": session,
        **_drive_operation(
            session,
            {
                "action": "move",
                "source_drive_path": source,
                "destination_drive_path": destination,
                "overwrite": overwrite,
            },
        ),
    }


@mcp.tool()
def delete_drive_path(
    session_name: SessionName,
    drive_path: DrivePath,
    confirm: Annotated[
        bool,
        Field(description="Must be true to remove this Drive file or folder."),
    ] = False,
    mount_if_needed: Annotated[
        bool,
        Field(description="Mount Drive and create codex-colab when needed."),
    ] = True,
) -> dict[str, Any]:
    """Delete one confirmed item within MyDrive/codex-colab; the root is protected."""
    session = _validate_session_name(session_name)
    relative = _drive_relative_path(drive_path)
    if not confirm:
        raise PermissionError("Set confirm=true to delete this Drive path")
    _ensure_drive_workspace(session, mount_if_needed=mount_if_needed)
    return {
        "session_name": session,
        **_drive_operation(
            session,
            {"action": "delete", "drive_path": relative, "confirm": True},
        ),
    }


@mcp.tool()
def save_notebook_to_drive(
    session_name: SessionName,
    local_path: LocalPath,
    drive_path: OptionalDrivePath = None,
    mount_if_needed: Annotated[
        bool,
        Field(description="Mount Drive and create codex-colab when needed."),
    ] = True,
) -> dict[str, Any]:
    """Save an approved local notebook inside MyDrive/codex-colab."""
    session = _validate_session_name(session_name)
    source = _notebook_path(local_path, must_exist=True)
    relative = _drive_save_path(source.name, drive_path)
    notebook_ops.load(source)
    _ensure_drive_workspace(session, mount_if_needed=mount_if_needed)
    temporary = f"/content/.codex-remote/notebooks/{secrets.token_hex(8)}.ipynb"
    upload = upload_file(session, str(source), temporary)
    try:
        saved = _drive_operation(
            session,
            {
                "action": "save",
                "remote_path": temporary,
                "drive_path": relative,
                "overwrite": True,
            },
        )
    finally:
        _remote_shell(session, f"rm -f {shlex.quote(temporary)}", timeout=120)
    return {
        "session_name": session,
        "local_path": str(source),
        "drive_path": saved["drive_path"],
        "workspace_path": drive_ops.DRIVE_WORKSPACE_PATH,
        "upload": upload,
    }


@mcp.tool()
def load_notebook_from_drive(
    session_name: SessionName,
    drive_path: DrivePath,
    local_path: LocalPath,
    mount_if_needed: Annotated[
        bool,
        Field(description="Mount Drive and create codex-colab when needed."),
    ] = True,
) -> dict[str, Any]:
    """Load a notebook from MyDrive/codex-colab into an approved local path."""
    session = _validate_session_name(session_name)
    relative = _drive_relative_path(drive_path)
    destination = _notebook_path(local_path, must_exist=False)
    _ensure_drive_workspace(session, mount_if_needed=mount_if_needed)
    temporary = f"/content/.codex-remote/notebooks/{secrets.token_hex(8)}.ipynb"
    _drive_operation(
        session,
        {
            "action": "restore",
            "drive_path": relative,
            "remote_path": temporary,
            "overwrite": True,
        },
    )
    try:
        download = download_file(session, temporary, str(destination))
        notebook = notebook_ops.load(destination)
    finally:
        _remote_shell(session, f"rm -f {shlex.quote(temporary)}", timeout=120)
    return {
        "session_name": session,
        "drive_path": drive_ops.display_drive_path(relative),
        "local_path": str(destination),
        "download": download,
        **notebook_ops.summary(notebook, False),
    }


def _remote_file_metadata(session_name: str, remote_path: str) -> dict[str, Any]:
    source = _validate_remote_path(remote_path)
    script = f"""python3 - <<'PY'
import hashlib, json
from pathlib import Path
p = Path({source!r})
if not p.is_file():
    raise SystemExit("remote path is not a file")
h = hashlib.sha256()
with p.open("rb") as stream:
    for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
        h.update(block)
print("CODEX_FILE_METADATA=" + json.dumps({{"bytes": p.stat().st_size, "sha256": h.hexdigest()}}, separators=(",", ":")))
PY"""
    result = _remote_shell(session_name, script, timeout=600)
    return _extract_json_marker(result.stdout, "CODEX_FILE_METADATA=")


def _transfer_stage(nonce: str) -> Path:
    _secure_state_root()
    TRANSFERS_ROOT.mkdir(parents=True, exist_ok=True)
    stage = TRANSFERS_ROOT / nonce
    stage.mkdir(mode=0o700)
    return stage


@mcp.tool()
def upload_file(
    session_name: SessionName, local_path: LocalPath, remote_path: RemotePath
) -> dict[str, Any]:
    """Upload one file from a user-approved local root."""
    source = _allowed_local_path(local_path, must_exist=True)
    if not source.is_file():
        raise ValueError("Google Colab CLI 0.6.0 upload accepts files, not directories")
    destination = _validate_remote_path(remote_path)
    session = _validate_session_name(session_name)
    size = source.stat().st_size
    if size <= DIRECT_TRANSFER_LIMIT:
        result = _colab(
            ["upload", "-s", session, _wsl_path(source), destination], timeout=1800
        )
        return {
            "local_path": str(source),
            "remote_path": destination,
            "transfer_mode": "direct",
            "bytes": size,
            **_output(result),
        }

    nonce = secrets.token_hex(12)
    local_stage = _transfer_stage(nonce)
    remote_stage = f"/content/.codex-remote/transfers/{nonce}"
    digest = hashlib.sha256()
    chunks = 0
    try:
        _remote_shell(session, f"mkdir -p {shlex.quote(remote_stage)}", timeout=120)
        with source.open("rb") as stream:
            while block := stream.read(TRANSFER_CHUNK_SIZE):
                digest.update(block)
                part_name = f"part-{chunks:06d}"
                local_part = local_stage / part_name
                local_part.write_bytes(block)
                try:
                    _colab(
                        [
                            "upload",
                            "-s",
                            session,
                            _wsl_path(local_part),
                            f"{remote_stage}/{part_name}",
                        ],
                        timeout=1800,
                    )
                finally:
                    local_part.unlink(missing_ok=True)
                chunks += 1
        expected_hash = digest.hexdigest()
        temporary = f"{destination}.codex-{nonce}.part"
        _remote_shell(
            session,
            "set -euo pipefail; "
            f"mkdir -p {shlex.quote(destination.rsplit('/', 1)[0] or '/')}; "
            f"cat {shlex.quote(remote_stage)}/part-* > {shlex.quote(temporary)}; "
            f"test \"$(sha256sum {shlex.quote(temporary)} | cut -d' ' -f1)\" = {shlex.quote(expected_hash)}; "
            f"mv -f {shlex.quote(temporary)} {shlex.quote(destination)}; "
            f"rm -rf {shlex.quote(remote_stage)}",
            timeout=1800,
        )
        metadata = _remote_file_metadata(session, destination)
        if metadata.get("bytes") != size or metadata.get("sha256") != expected_hash:
            raise RuntimeError(
                "Remote file checksum or size did not match after chunked upload"
            )
        return {
            "local_path": str(source),
            "remote_path": destination,
            "transfer_mode": "chunked",
            "bytes": size,
            "chunks": chunks,
            "chunk_bytes": TRANSFER_CHUNK_SIZE,
            "sha256": expected_hash,
            "exit_code": 0,
            "stdout": f"Uploaded {size} bytes in {chunks} verified chunks.\n",
            "stderr": "",
        }
    finally:
        shutil.rmtree(local_stage, ignore_errors=True)
        try:
            _remote_shell(session, f"rm -rf {shlex.quote(remote_stage)}", timeout=120)
        except Exception:
            pass


@mcp.tool()
def download_file(
    session_name: SessionName, remote_path: RemotePath, local_path: LocalPath
) -> dict[str, Any]:
    """Download into a user-approved local root."""
    destination = _allowed_local_path(local_path, must_exist=False)
    source = _validate_remote_path(remote_path)
    session = _validate_session_name(session_name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    metadata = _remote_file_metadata(session, source)
    size = int(metadata["bytes"])
    if size <= DIRECT_TRANSFER_LIMIT:
        result = _colab(
            ["download", "-s", session, source, _wsl_path(destination)], timeout=1800
        )
        return {
            "remote_path": source,
            "local_path": str(destination),
            "transfer_mode": "direct",
            "bytes": size,
            **_output(result),
        }

    nonce = secrets.token_hex(12)
    local_stage = _transfer_stage(nonce)
    remote_stage = f"/content/.codex-remote/transfers/{nonce}"
    temporary = destination.with_name(f".{destination.name}.{nonce}.part")
    try:
        result = _remote_shell(
            session,
            "set -euo pipefail; "
            f"mkdir -p {shlex.quote(remote_stage)}; "
            f"split -b {TRANSFER_CHUNK_SIZE} -d -a 6 {shlex.quote(source)} {shlex.quote(remote_stage)}/part-; "
            f"find {shlex.quote(remote_stage)} -maxdepth 1 -type f -name 'part-*' -printf '%f\\n' | sort",
            timeout=1800,
        )
        part_names = [
            line.strip()
            for line in result.stdout.splitlines()
            if SAFE_NAME.fullmatch(line.strip())
        ]
        if not part_names:
            raise RuntimeError("Remote file split did not produce any chunks")
        for part_name in part_names:
            _colab(
                [
                    "download",
                    "-s",
                    session,
                    f"{remote_stage}/{part_name}",
                    _wsl_path(local_stage / part_name),
                ],
                timeout=1800,
            )
        digest = hashlib.sha256()
        written = 0
        with temporary.open("wb") as output:
            for part_name in part_names:
                part = local_stage / part_name
                with part.open("rb") as stream:
                    while block := stream.read(8 * 1024 * 1024):
                        output.write(block)
                        digest.update(block)
                        written += len(block)
        if written != size or digest.hexdigest() != metadata["sha256"]:
            raise RuntimeError("Downloaded file checksum or size did not match")
        os.replace(temporary, destination)
        return {
            "remote_path": source,
            "local_path": str(destination),
            "transfer_mode": "chunked",
            "bytes": size,
            "chunks": len(part_names),
            "chunk_bytes": TRANSFER_CHUNK_SIZE,
            "sha256": metadata["sha256"],
            "exit_code": 0,
            "stdout": f"Downloaded {size} bytes in {len(part_names)} verified chunks.\n",
            "stderr": "",
        }
    finally:
        temporary.unlink(missing_ok=True)
        shutil.rmtree(local_stage, ignore_errors=True)
        try:
            _remote_shell(session, f"rm -rf {shlex.quote(remote_stage)}", timeout=120)
        except Exception:
            pass


@mcp.tool()
def start_upload(
    session_name: SessionName,
    local_path: LocalPath,
    remote_path: RemotePath,
    compress: OptionalCompression = None,
    parallelism: OptionalParallelism = None,
    resume: Annotated[
        bool, Field(description="Reuse verified completed chunks after interruption.")
    ] = True,
) -> dict[str, Any]:
    """Start a resumable parallel file/folder upload, optionally compressed as tar.gz."""
    source = _allowed_local_path(local_path, must_exist=True)
    config = _load_config()
    selected_compression = (
        bool(config["transfer_compression"]) if compress is None else compress
    )
    selected_parallelism = (
        int(config["transfer_parallelism"])
        if parallelism is None
        else parallelism
    )
    return managed_transfer.spawn(
        sys.modules[__name__],
        {
            "transfer_id": secrets.token_hex(12),
            "direction": "upload",
            "session_name": _validate_session_name(session_name),
            "local_path": str(source),
            "remote_path": _validate_remote_path(remote_path),
            "compress": selected_compression or source.is_dir(),
            "parallelism": max(1, min(selected_parallelism, 8)),
            "retry_attempts": int(config["retry_attempts"]),
            "resume": resume,
            "created_at": int(time.time()),
            "bytes_done": 0,
            "chunks_done": 0,
        },
    )


@mcp.tool()
def start_download(
    session_name: SessionName,
    remote_path: RemotePath,
    local_path: LocalPath,
    compress: OptionalCompression = None,
    parallelism: OptionalParallelism = None,
    resume: Annotated[
        bool, Field(description="Reuse verified completed chunks after interruption.")
    ] = True,
    overwrite: Annotated[
        bool, Field(description="Replace an existing local destination.")
    ] = False,
) -> dict[str, Any]:
    """Start a resumable parallel file/folder download, optionally compressed as tar.gz."""
    destination = _allowed_local_path(local_path, must_exist=False)
    config = _load_config()
    selected_compression = (
        bool(config["transfer_compression"]) if compress is None else compress
    )
    selected_parallelism = (
        int(config["transfer_parallelism"])
        if parallelism is None
        else parallelism
    )
    return managed_transfer.spawn(
        sys.modules[__name__],
        {
            "transfer_id": secrets.token_hex(12),
            "direction": "download",
            "session_name": _validate_session_name(session_name),
            "local_path": str(destination),
            "remote_path": _validate_remote_path(remote_path),
            "compress": selected_compression,
            "parallelism": max(1, min(selected_parallelism, 8)),
            "retry_attempts": int(config["retry_attempts"]),
            "resume": resume,
            "overwrite": overwrite,
            "created_at": int(time.time()),
            "bytes_done": 0,
            "chunks_done": 0,
        },
    )


@mcp.tool()
def transfer_status(transfer_id: TransferId) -> dict[str, Any]:
    """Return progress, bytes, chunks, and resumable state for a managed transfer."""
    return managed_transfer.load_state(sys.modules[__name__], transfer_id)


@mcp.tool()
def cancel_transfer(
    transfer_id: TransferId,
    confirm: Annotated[
        bool,
        Field(description="True only after user approval to cancel this transfer."),
    ] = False,
) -> dict[str, Any]:
    """Request cooperative cancellation; completed chunks remain available for resume."""
    if not confirm:
        raise PermissionError("Re-run with confirm=true to cancel the transfer safely")
    state = managed_transfer.load_state(sys.modules[__name__], transfer_id)
    (
        managed_transfer.directory(sys.modules[__name__], transfer_id)
        / "cancel.requested"
    ).touch()
    state["status"] = "cancelling"
    state["updated_at"] = int(time.time())
    managed_transfer.save_state(sys.modules[__name__], state)
    return state


@mcp.tool()
def resume_transfer(transfer_id: TransferId) -> dict[str, Any]:
    """Resume a cancelled, failed, or interrupted transfer from completed chunks."""
    state = managed_transfer.load_state(sys.modules[__name__], transfer_id)
    if state.get("status") in {"running", "starting", "cancelling"}:
        raise RuntimeError("Transfer is still active")
    state.pop("error", None)
    return managed_transfer.spawn(sys.modules[__name__], state)


@mcp.tool()
def list_transfers(
    limit: Annotated[
        int, Field(description="Maximum recent transfers to return.", ge=1, le=200)
    ] = 50,
) -> list[dict[str, Any]]:
    """List recent managed transfers and their progress."""
    return managed_transfer.list_states(sys.modules[__name__], limit)


@mcp.tool()
def list_files(
    session_name: SessionName, remote_path: RemotePath = "/content"
) -> dict[str, Any]:
    """List files on a Colab session."""
    return _output(
        _colab(
            [
                "ls",
                "-s",
                _validate_session_name(session_name),
                _validate_remote_path(remote_path),
            ],
            timeout=60,
        )
    )


@mcp.tool()
def install_packages(
    session_name: SessionName,
    packages: Annotated[
        list[str],
        Field(
            description="Python package specifiers only; URLs and installer options are rejected.",
            min_length=1,
        ),
    ],
) -> dict[str, Any]:
    """Install validated Python package specifiers on a Colab session."""
    if not packages or any(not SAFE_PACKAGE.fullmatch(item) for item in packages):
        raise ValueError(
            "packages must be non-empty standard Python package specifiers; URLs and options are not accepted"
        )
    return _output(
        _colab(
            ["install", "-s", _validate_session_name(session_name), *packages],
            timeout=1800,
        )
    )


@mcp.tool()
def get_logs(session_name: SessionName, lines: LineCount = 200) -> dict[str, Any]:
    """Return redacted structured Colab CLI history."""
    count = max(1, min(lines, 5000))
    return _output(
        _colab(
            ["log", "-s", _validate_session_name(session_name), "-n", str(count)],
            timeout=60,
        )
    )


@mcp.tool()
def session_url(session_name: SessionName) -> dict[str, Any]:
    """Return the raw copy-paste-only attachment URL for a running session.

    Present the URL exactly in a fenced code block. Never turn it into a Markdown
    link or open it through browser automation because URL encoding can break the
    Colab runtime attachment fragment.
    """
    return {
        "session_url": _created_session_url(_validate_session_name(session_name)),
        "session_url_presentation": "copy_paste_only",
        "session_url_instructions": (
            "Show session_url exactly inside a fenced code block, never as a Markdown link. "
            "Ask the user to copy the entire raw URL into the browser address bar."
        ),
    }


@mcp.tool()
def restart_kernel(
    session_name: SessionName,
    confirm: Annotated[
        bool,
        Field(
            description="True only after user approval to lose in-memory kernel state."
        ),
    ] = False,
) -> dict[str, Any]:
    """Restart a session kernel after explicit confirmation; in-memory state is lost."""
    if not confirm:
        raise PermissionError(
            "Kernel restart clears in-memory state; re-run with confirm=true after user approval"
        )
    return _output(
        _colab(
            ["restart-kernel", "-s", _validate_session_name(session_name)], timeout=120
        )
    )


@mcp.tool()
def start_job(
    session_name: SessionName,
    job_name: JobName,
    command: Annotated[
        str,
        Field(
            description="Linux shell command for the persistent tmux job.", min_length=1
        ),
    ],
    workdir: RemoteWorkdir = "/content",
    notify_on_completion: Annotated[
        bool,
        Field(
            description="Request a completion popup if desktop notifications are also enabled globally."
        ),
    ] = False,
    monitor_interval_seconds: Annotated[
        int, Field(description="Local monitoring interval in seconds.", ge=10, le=300)
    ] = 30,
    stop_session_on_finish: Annotated[
        bool,
        Field(description="Release the Colab VM automatically when this job finishes."),
    ] = False,
    recover_on_runtime_loss: Annotated[
        bool,
        Field(
            description="Restart this command after approved automatic runtime recovery."
        ),
    ] = False,
) -> dict[str, Any]:
    """Start a monitored tmux job with optional auto-stop and runtime-loss recovery."""
    if not command.strip():
        raise ValueError("command cannot be empty")
    session = _validate_session_name(session_name)
    job = _validate_job_name(job_name)
    remote_workdir = _validate_remote_workdir(workdir)
    encoded = base64.b64encode(command.encode()).decode()
    environment = _secret_environment(session)
    with _staged_secret_file(session, environment) as remote_secret_path:
        secret_copy = (
            f"cp {shlex.quote(remote_secret_path)} \"$d/secrets.hex\"; "
            "chmod 600 \"$d/secrets.hex\""
            if remote_secret_path is not None
            else "rm -f \"$d/secrets.hex\""
        )
        script = f"""set -euo pipefail
d=/content/.codex-remote/jobs/{job}; mkdir -p "$d"
tmux has-session -t codex-{job} 2>/dev/null && {{ echo "job already running" >&2; exit 17; }}
{secret_copy}
printf %s {encoded} | base64 -d > "$d/command.sh"; chmod 700 "$d/command.sh"
cat > "$d/wrapper.sh" <<'WRAP'
#!/usr/bin/env bash
set +e
d="$1"; workdir="$2"; export CODEX_PROGRESS_FILE="$d/progress.json"
if test -f "$d/secrets.hex"; then
  python3 - "$d/secrets.hex" "$d/secrets.env" <<'PY'
import os,shlex,sys
source,target=sys.argv[1:]
fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with open(source,encoding='utf-8') as stream,os.fdopen(fd,'w',encoding='utf-8') as out:
    for line in stream:
        name,encoded=line.rstrip('\n').split('\t',1)
        out.write('export '+name+'='+shlex.quote(bytes.fromhex(encoded).decode())+'\n')
PY
  source "$d/secrets.env"
  rm -f "$d/secrets.hex" "$d/secrets.env"
fi
touch "$d/secrets_loaded"
echo running > "$d/status"; date +%s > "$d/started_at"; rm -f "$d/exit_code" "$d/finished_at"
(while ! test -f "$d/exit_code"; do date +%s > "$d/heartbeat"; sleep 30; done) & hp=$!
if cd "$workdir"; then bash "$d/command.sh" >>"$d/stdout.log" 2>>"$d/stderr.log"; rc=$?; else echo "workdir not found" >>"$d/stderr.log"; rc=125; fi
echo "$rc" > "$d/exit_code"; date +%s > "$d/finished_at"; echo finished > "$d/status"; kill "$hp" 2>/dev/null || true; exit "$rc"
WRAP
chmod 700 "$d/wrapper.sh"; tmux new-session -d -s codex-{job} "$d/wrapper.sh $d {shlex.quote(remote_workdir)}"
for _codex_wait in $(seq 1 100); do test -f "$d/secrets_loaded" && break; sleep 0.1; done
test -f "$d/secrets_loaded" || {{ echo "job did not load its environment" >&2; exit 18; }}
echo CODEX_JOB_STARTED={job}"""
        result = _remote_shell(session, script, timeout=120)
    interval = max(10, min(monitor_interval_seconds, 300))
    if recover_on_runtime_loss:
        _remember_recovery_job(
            session,
            job,
            command,
            remote_workdir,
            notify_on_completion,
            interval,
            stop_session_on_finish,
        )
    response: dict[str, Any] = {
        "session_name": session,
        "job_name": job,
        "status": "started",
        "progress_file": f"/content/.codex-remote/jobs/{job}/progress.json",
        "stop_session_on_finish": stop_session_on_finish,
        "recover_on_runtime_loss": recover_on_runtime_loss,
        "enabled_secret_names": sorted(environment),
        "output": _output(result),
    }
    if notify_on_completion or stop_session_on_finish or recover_on_runtime_loss:
        response["monitor"] = _start_monitor(
            session,
            job,
            interval,
            notify_on_completion,
            stop_session_on_finish,
            recover_on_runtime_loss,
        )
    return response


@mcp.tool()
def job_status(session_name: SessionName, job_name: JobName) -> dict[str, Any]:
    """Return job lifecycle, heartbeat age, and application-written JSON progress."""
    return _job_status_impl(session_name, job_name)


@mcp.tool()
def job_logs(
    session_name: SessionName, job_name: JobName, lines: LineCount = 200
) -> dict[str, Any]:
    """Tail stdout and stderr for a background job."""
    session = _validate_session_name(session_name)
    job = _validate_job_name(job_name)
    count = max(1, min(lines, 5000))
    script = f"""python3 - <<'PY'
import json
from pathlib import Path
d=Path("/content/.codex-remote/jobs/{job}")
def tail(name):
    p=d/name
    return "\\n".join(p.read_text(errors="replace").splitlines()[-{count}:]) if p.exists() else ""
print("CODEX_JOB_LOGS="+json.dumps({{"stdout":tail("stdout.log"),"stderr":tail("stderr.log")}},separators=(",",":")))
PY"""
    result = _remote_shell(session, script, timeout=60)
    logs = _extract_json_marker(result.stdout, "CODEX_JOB_LOGS=")
    return {
        "stdout": _redact(str(logs.get("stdout", ""))),
        "stderr": _redact(str(logs.get("stderr", ""))),
    }


@mcp.tool()
def watch_job(
    session_name: SessionName,
    job_name: JobName,
    interval_seconds: Annotated[
        int, Field(description="Polling interval in seconds.", ge=10, le=300)
    ] = 30,
    notify_on_completion: Annotated[
        bool,
        Field(
            description="Request a completion popup if desktop notifications are also enabled globally."
        ),
    ] = False,
    stop_session_on_finish: Annotated[
        bool,
        Field(description="Release the Colab VM automatically when this job finishes."),
    ] = False,
    recover_on_runtime_loss: Annotated[
        bool,
        Field(
            description="Restart an opted-in command after approved runtime recovery."
        ),
    ] = False,
) -> dict[str, Any]:
    """Start a background monitor with optional auto-stop, recovery, and popup."""
    return _start_monitor(
        _validate_session_name(session_name),
        _validate_job_name(job_name),
        max(10, min(interval_seconds, 300)),
        notify_on_completion,
        stop_session_on_finish,
        recover_on_runtime_loss,
    )


@mcp.tool()
def stop_job(
    session_name: SessionName,
    job_name: JobName,
    confirm: Annotated[
        bool, Field(description="True only after user approval to interrupt the job.")
    ] = False,
) -> dict[str, Any]:
    """Stop a background job after explicit confirmation."""
    if not confirm:
        raise PermissionError(
            "Stopping a job may lose work; re-run with confirm=true after user approval"
        )
    session = _validate_session_name(session_name)
    job = _validate_job_name(job_name)
    script = f'tmux send-keys -t codex-{job} C-c 2>/dev/null || true; sleep 2; tmux kill-session -t codex-{job} 2>/dev/null || true; d=/content/.codex-remote/jobs/{job}; mkdir -p "$d"; echo stopped > "$d/status"; date +%s > "$d/finished_at"'
    return {
        "job_name": job,
        "status": "stopped",
        **_output(_remote_shell(session, script, timeout=60)),
    }


@mcp.tool()
def stop_session(
    session_name: SessionName,
    confirm: Annotated[
        bool,
        Field(
            description="True only after user approval to release the VM and ephemeral data."
        ),
    ] = False,
) -> dict[str, Any]:
    """Stop and release one Colab session after explicit confirmation."""
    if not confirm:
        raise PermissionError(
            "Stopping releases the VM and ephemeral data; re-run with confirm=true after user approval"
        )
    session = _validate_session_name(session_name)
    drive_mount_cleanup = _cancel_drive_mount_worker(session)
    result = _colab(["stop", "-s", session], timeout=120)
    verification = _colab(["sessions"], timeout=30)
    listing = verification.stdout + verification.stderr
    verified_absent = session not in listing
    if not verified_absent:
        raise RuntimeError(
            f"Colab reported that session {session} still exists after stop"
        )
    ledger = _load_session_ledger()
    ledger.pop(session, None)
    _save_session_ledger(ledger)
    secret_broker.clear_session(STATE_ROOT, session)
    _save_lease_record(session, None)
    for key, monitor in _load_monitor_ledger().items():
        if monitor.get("session_name") == session:
            _save_monitor_record(session, str(monitor["job_name"]), None)
    return {
        "session_name": session,
        "stopped": True,
        "verified_absent": True,
        "stop": _output(result),
        "sessions_after": _output(verification),
        "drive_mount_cleanup": drive_mount_cleanup,
    }


@mcp.tool()
def notification_history(
    limit: Annotated[
        int,
        Field(
            description="Maximum recent notification records to return.", ge=1, le=200
        ),
    ] = 20,
) -> list[dict[str, Any]]:
    """Return recent non-secret completion notification metadata."""
    if not NOTIFICATIONS_PATH.exists():
        return []
    rows = NOTIFICATIONS_PATH.read_text(encoding="utf-8").splitlines()
    return [json.loads(row) for row in rows[-max(1, min(limit, 200)) :]]


@mcp.tool()
def test_notification() -> dict[str, Any]:
    """Test the opt-in popup backend; when disabled, only add silent history."""
    return _write_notification(
        "Colab Remote", "Completion notifications are working.", "success"
    )


if __name__ == "__main__":
    if len(sys.argv) == 8 and sys.argv[1] == "--monitor-job":
        _monitor_job(
            sys.argv[2],
            sys.argv[3],
            int(sys.argv[4]),
            sys.argv[5] == "1",
            sys.argv[6] == "1",
            sys.argv[7] == "1",
        )
    elif len(sys.argv) == 3 and sys.argv[1] == "--lease-session":
        _lease_session(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] == "--run-transfer":
        managed_transfer.run_worker(sys.modules[__name__], sys.argv[2])
    else:
        _resume_saved_monitors()
        _resume_saved_leases()
        mcp.run(transport="stdio")
