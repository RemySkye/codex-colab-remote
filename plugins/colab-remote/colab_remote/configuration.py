"""Shared, cross-platform configuration handling for Colab Remote."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any

from . import config_io

_SOURCE_SCHEMA = Path(__file__).resolve().parents[1] / "config_schema.json"
_PACKAGED_SCHEMA = Path(__file__).with_name("config_schema.json")
SCHEMA_PATH = _PACKAGED_SCHEMA if _PACKAGED_SCHEMA.exists() else _SOURCE_SCHEMA
DOCUMENTATION = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
SETTINGS: dict[str, dict[str, Any]] = DOCUMENTATION["settings"]
DEFAULT_CONFIG = {
    name: details["default"] for name, details in SETTINGS.items()
}

_ACCELERATORS = {"cpu", "t4", "l4", "g4", "h100", "a100", "v5e-1", "v6e-1"}
_LANGUAGES = {"python", "r", "julia"}
_NOTIFICATION_MODES = {"off", "failures_only", "all"}
_INTEGER_RANGES = {
    "default_timeout_seconds": (30, 86400),
    "compute_warning_minutes": (5, 1440),
    "default_max_lifetime_minutes": (0, 1440),
    "max_concurrent_sessions": (1, 64),
    "transfer_parallelism": (1, 8),
    "retry_attempts": (1, 10),
}
_BOOLEAN_SETTINGS = {
    "default_high_ram",
    "transfer_compression",
    "require_cost_acknowledgement",
    "require_secret_enable_approval",
}


def state_root() -> Path:
    return Path(
        os.environ.get(
            "COLAB_REMOTE_STATE_DIR",
            str(Path.home() / ".codex" / "colab-remote"),
        )
    ).expanduser()


def config_path() -> Path:
    return state_root() / "config.jsonc"


def _integer(config: dict[str, Any], name: str) -> int:
    value = config[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    minimum, maximum = _INTEGER_RANGES[name]
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _drive_folder(value: Any) -> str:
    candidate = str(value).strip().replace("\\", "/")
    path = PurePosixPath(candidate)
    if (
        not candidate
        or candidate.startswith("/")
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.split("/"))
    ):
        raise ValueError(
            "default_drive_checkpoint_folder must be a non-empty relative path "
            "without '.', '..', or empty components"
        )
    return path.as_posix()


def normalize(config: dict[str, Any]) -> dict[str, Any]:
    """Merge defaults, migrate legacy keys, and strictly validate values."""
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a JSON object")
    normalized = {**DEFAULT_CONFIG, **config}
    normalized.pop("_documentation", None)
    normalized.pop("ssh_secret_name", None)
    normalized.pop("ssh_tunnel_enabled", None)
    if "default_high_ram" not in config and "prefer_high_ram" in config:
        normalized["default_high_ram"] = bool(config["prefer_high_ram"])
    normalized.pop("prefer_high_ram", None)
    legacy_notifications = normalized.pop("notifications_enabled", None)
    if "notification_mode" not in config and legacy_notifications is not None:
        normalized["notification_mode"] = "all" if legacy_notifications else "off"

    distro = normalized["distro"]
    if not isinstance(distro, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_. -]{0,63}", distro.strip()
    ):
        raise ValueError("distro must be a non-empty configured distribution name")
    normalized["distro"] = distro.strip()

    accelerator = str(normalized["default_accelerator"]).strip().lower()
    if accelerator not in _ACCELERATORS:
        raise ValueError(
            "default_accelerator must be one of " + ", ".join(sorted(_ACCELERATORS))
        )
    normalized["default_accelerator"] = accelerator

    language = str(normalized["default_language"]).strip().lower()
    if language not in _LANGUAGES:
        raise ValueError("default_language must be python, r, or julia")
    normalized["default_language"] = language

    runtime = str(normalized["default_runtime_version"]).strip().lower()
    if runtime != "latest" and not re.fullmatch(r"\d{4}\.\d{2}", runtime):
        raise ValueError("default_runtime_version must be latest or YYYY.MM")
    normalized["default_runtime_version"] = runtime

    mode = str(normalized["notification_mode"]).strip().lower()
    if mode not in _NOTIFICATION_MODES:
        raise ValueError("notification_mode must be off, failures_only, or all")
    normalized["notification_mode"] = mode

    for name in _INTEGER_RANGES:
        normalized[name] = _integer(normalized, name)
    for name in _BOOLEAN_SETTINGS:
        if not isinstance(normalized[name], bool):
            raise ValueError(f"{name} must be true or false")

    normalized["default_drive_checkpoint_folder"] = _drive_folder(
        normalized["default_drive_checkpoint_folder"]
    )

    roots = normalized["allowed_local_roots"]
    if not isinstance(roots, list) or not all(isinstance(item, str) for item in roots):
        raise ValueError("allowed_local_roots must be an array of absolute paths")
    clean_roots: list[str] = []
    for item in roots:
        root = Path(item).expanduser()
        if not root.is_absolute():
            raise ValueError(f"allowed_local_roots entry is not absolute: {item}")
        resolved = str(root.resolve())
        if resolved not in clean_roots:
            clean_roots.append(resolved)
    normalized["allowed_local_roots"] = clean_roots
    return normalized


def load() -> dict[str, Any]:
    path = config_path()
    legacy_path = path.with_suffix(".json")
    source = path if path.exists() else legacy_path
    if not source.exists():
        return dict(DEFAULT_CONFIG)
    return normalize(config_io.loads(source.read_text(encoding="utf-8")))


def save(config: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize(config)
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        config_io.render(normalized, DOCUMENTATION),
        encoding="utf-8",
    )
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, path)
    legacy_path = path.with_suffix(".json")
    if legacy_path != path:
        legacy_path.unlink(missing_ok=True)
    return normalized


def parse_value(name: str, value: str) -> Any:
    if name not in SETTINGS:
        raise ValueError(f"Unknown setting: {name}")
    kind = SETTINGS[name]["type"]
    if kind == "boolean":
        lowered = value.strip().lower()
        if lowered not in {"true", "false"}:
            raise ValueError(f"{name} must be true or false")
        return lowered == "true"
    if kind == "integer":
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc
    if kind == "array of absolute paths":
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{name} must be a JSON array, for example [\"/home/me/data\"]"
            ) from exc
        return parsed
    return value
