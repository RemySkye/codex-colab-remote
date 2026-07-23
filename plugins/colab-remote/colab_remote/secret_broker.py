"""Local OS-keychain storage and name-only session grants for Colab secrets."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

import keyring
from keyring.errors import KeyringError, PasswordDeleteError


SERVICE_NAME = "codex-colab-remote"
SECRET_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_LOCK = threading.RLock()


class SecretBrokerError(RuntimeError):
    """A safe error that never contains a secret value."""


def validate_name(name: str) -> str:
    value = name.strip()
    if not SECRET_NAME.fullmatch(value):
        raise ValueError(
            "Secret names must be uppercase environment-variable names such as HF_TOKEN"
        )
    return value


def _secure_root(state_root: Path) -> Path:
    root = state_root.expanduser().resolve() / "secrets"
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    return root


def _index_path(state_root: Path) -> Path:
    return _secure_root(state_root) / "index.json"


def _grants_path(state_root: Path) -> Path:
    return _secure_root(state_root) / "session-grants.json"


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SecretBrokerError(f"Local secret metadata is invalid: {path.name}") from exc


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _backend_ready() -> None:
    try:
        backend = keyring.get_keyring()
        priority = float(getattr(backend, "priority", 0))
    except Exception as exc:
        raise SecretBrokerError("The operating-system credential store is unavailable") from exc
    if priority <= 0:
        raise SecretBrokerError(
            "No secure operating-system credential store is available. "
            "On Linux, start or install a Freedesktop Secret Service provider first."
        )


def backend_status() -> dict[str, str]:
    """Return safe backend metadata without enumerating or reading credentials."""
    _backend_ready()
    backend = keyring.get_keyring()
    platform_note = ""
    if sys.platform.startswith("linux"):
        platform_note = (
            "Linux uses the Freedesktop Secret Service standard. Compatible providers "
            "include GNOME Keyring, KWallet Secret Service, and KeePassXC."
        )
    elif sys.platform == "darwin":
        platform_note = "macOS stores values in the user's Keychain."
    elif os.name == "nt":
        platform_note = "Windows stores values in Windows Credential Manager."
    return {
        "backend": f"{type(backend).__module__}.{type(backend).__name__}",
        "platform_note": platform_note,
    }


def list_names(state_root: Path) -> list[str]:
    with _LOCK:
        raw = _read_json(_index_path(state_root), {"names": []})
        if not isinstance(raw, dict) or not isinstance(raw.get("names"), list):
            raise SecretBrokerError("Local secret name index is invalid")
        names: list[str] = []
        for item in raw["names"]:
            if isinstance(item, str) and SECRET_NAME.fullmatch(item):
                names.append(item)
        return sorted(set(names))


def set_secret(state_root: Path, name: str, value: str) -> None:
    secret_name = validate_name(name)
    if len(value) < 8:
        raise ValueError("Secret values must contain at least 8 characters")
    if "\x00" in value:
        raise ValueError("Secret values cannot contain a null byte")
    _backend_ready()
    try:
        keyring.set_password(SERVICE_NAME, secret_name, value)
    except KeyringError as exc:
        raise SecretBrokerError("The operating-system credential store rejected the secret") from exc
    with _LOCK:
        names = set(list_names(state_root))
        names.add(secret_name)
        _write_json(_index_path(state_root), {"version": 1, "names": sorted(names)})


def get_secret(state_root: Path, name: str) -> str:
    secret_name = validate_name(name)
    if secret_name not in list_names(state_root):
        raise SecretBrokerError(f"Local secret is not configured: {secret_name}")
    _backend_ready()
    try:
        value = keyring.get_password(SERVICE_NAME, secret_name)
    except KeyringError as exc:
        raise SecretBrokerError("The operating-system credential store could not read the secret") from exc
    if value is None:
        raise SecretBrokerError(
            f"Local secret metadata exists but its keychain entry is missing: {secret_name}"
        )
    return value


def delete_secret(state_root: Path, name: str) -> None:
    secret_name = validate_name(name)
    _backend_ready()
    try:
        keyring.delete_password(SERVICE_NAME, secret_name)
    except PasswordDeleteError:
        pass
    except KeyringError as exc:
        raise SecretBrokerError("The operating-system credential store rejected deletion") from exc
    with _LOCK:
        names = set(list_names(state_root))
        names.discard(secret_name)
        _write_json(_index_path(state_root), {"version": 1, "names": sorted(names)})
        grants = _load_grants(state_root)
        for session in list(grants):
            grants[session] = [item for item in grants[session] if item != secret_name]
            if not grants[session]:
                grants.pop(session)
        _save_grants(state_root, grants)


def _load_grants(state_root: Path) -> dict[str, list[str]]:
    raw = _read_json(_grants_path(state_root), {"sessions": {}})
    sessions = raw.get("sessions") if isinstance(raw, dict) else None
    if not isinstance(sessions, dict):
        raise SecretBrokerError("Local secret session grants are invalid")
    result: dict[str, list[str]] = {}
    for session, names in sessions.items():
        if isinstance(session, str) and isinstance(names, list):
            valid = [
                item
                for item in names
                if isinstance(item, str) and SECRET_NAME.fullmatch(item)
            ]
            if valid:
                result[session] = sorted(set(valid))
    return result


def _save_grants(state_root: Path, grants: dict[str, list[str]]) -> None:
    _write_json(
        _grants_path(state_root),
        {"version": 1, "updated_at": int(time.time()), "sessions": grants},
    )


def enabled_names(state_root: Path, session_name: str) -> list[str]:
    with _LOCK:
        return list(_load_grants(state_root).get(session_name, []))


def enable_names(state_root: Path, session_name: str, names: list[str]) -> list[str]:
    requested = sorted({validate_name(item) for item in names})
    if not requested:
        raise ValueError("At least one secret name is required")
    available = set(list_names(state_root))
    missing = [item for item in requested if item not in available]
    if missing:
        raise SecretBrokerError(
            "These local secret names are not configured: " + ", ".join(missing)
        )
    # Resolve every value before saving the grant so a broken keychain entry cannot be enabled.
    for item in requested:
        get_secret(state_root, item)
    with _LOCK:
        grants = _load_grants(state_root)
        grants[session_name] = sorted(set(grants.get(session_name, [])) | set(requested))
        _save_grants(state_root, grants)
        return list(grants[session_name])


def disable_names(
    state_root: Path, session_name: str, names: list[str] | None = None
) -> tuple[list[str], list[str]]:
    requested = None if names is None else sorted({validate_name(item) for item in names})
    with _LOCK:
        grants = _load_grants(state_root)
        previous = set(grants.get(session_name, []))
        removed = previous if requested is None else previous.intersection(requested)
        remaining = previous - removed
        if remaining:
            grants[session_name] = sorted(remaining)
        else:
            grants.pop(session_name, None)
        _save_grants(state_root, grants)
        return sorted(removed), sorted(remaining)


def clear_session(state_root: Path, session_name: str) -> None:
    disable_names(state_root, session_name)


def enabled_environment(state_root: Path, session_name: str) -> dict[str, str]:
    return {
        name: get_secret(state_root, name)
        for name in enabled_names(state_root, session_name)
    }


def all_values(state_root: Path) -> list[str]:
    values: list[str] = []
    for name in list_names(state_root):
        try:
            values.append(get_secret(state_root, name))
        except SecretBrokerError:
            continue
    return values


def _default_state_root() -> Path:
    return Path(
        os.environ.get(
            "COLAB_REMOTE_STATE_DIR",
            str(Path.home() / ".codex" / "colab-remote"),
        )
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="colab-remote-secret",
        description="Manage local Colab Remote secrets in the operating-system keychain.",
    )
    parser.add_argument(
        "--state-root",
        type=Path,
        default=_default_state_root(),
        help=argparse.SUPPRESS,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    set_command = commands.add_parser("set", help="Create or replace a secret using a masked prompt")
    set_command.add_argument("name")
    delete_command = commands.add_parser("delete", help="Delete a secret after confirmation")
    delete_command.add_argument("name")
    commands.add_parser("list", help="List configured secret names only")
    return parser


def main(argv: list[str] | None = None) -> int:
    options = _parser().parse_args(argv)
    try:
        if options.command == "list":
            for name in list_names(options.state_root):
                print(name)
            return 0
        name = validate_name(options.name)
        if options.command == "set":
            value = getpass.getpass(f"Enter {name} (input hidden): ")
            confirmation = getpass.getpass(f"Enter {name} again: ")
            if value != confirmation:
                raise SecretBrokerError("The two secret values did not match")
            set_secret(options.state_root, name, value)
            print(f"Stored {name} in the operating-system credential store.")
            return 0
        confirmation = input(f"Type {name} to permanently delete it: ").strip()
        if confirmation != name:
            raise SecretBrokerError("Deletion cancelled")
        delete_secret(options.state_root, name)
        print(f"Deleted {name}.")
        return 0
    except (SecretBrokerError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
