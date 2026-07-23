"""Cross-platform user command for Colab Remote."""

from __future__ import annotations

import argparse
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Sequence
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from . import __version__, config_io, configuration, secret_broker

INSTALLER_URL = (
    "https://raw.githubusercontent.com/RemySkye/"
    "codex-colab-remote/main/install.py"
)
SENSITIVE_TRANSITIONS = {
    ("require_cost_acknowledgement", False):
        "This grants the AI standing permission to allocate Colab compute.",
    ("require_secret_enable_approval", False):
        "This grants the AI standing permission to enable named Colab Remote aliases.",
}


class _Formatter(argparse.RawDescriptionHelpFormatter):
    pass


def _installed_version() -> str:
    try:
        return version("codex-colab-remote")
    except PackageNotFoundError:
        return __version__


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="colab-remote",
        description=(
            "Manage Colab Remote on Windows, Linux, and macOS.\n\n"
            "Codex uses the plugin's MCP tools to create and control Colab sessions. "
            "This user command manages installation, preferences, and locally stored "
            "credential aliases; it does not execute remote jobs itself."
        ),
        epilog=(
            "Common examples:\n"
            "  colab-remote doctor\n"
            "  colab-remote config show\n"
            "  colab-remote config set default_accelerator a100\n"
            "  colab-remote config set require_cost_acknowledgement false --yes\n"
            "  colab-remote secrets add HF_TOKEN\n"
            "  colab-remote secrets list\n"
            "  colab-remote update\n\n"
            "Run 'colab-remote COMMAND --help' for command-specific help."
        ),
        formatter_class=_Formatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_installed_version()}",
    )
    commands = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="COMMAND",
    )

    commands.add_parser(
        "doctor",
        help="Check the local CLI, config, and secure credential backend",
        description=(
            "Run local, read-only health checks. This does not allocate Colab "
            "compute, authenticate, or reveal credential aliases or values."
        ),
    )
    commands.add_parser(
        "version",
        help="Print the installed Colab Remote version",
    )
    commands.add_parser(
        "update",
        help="Safely install the latest release from the official repository",
        description=(
            "Download the official Python installer over HTTPS and run its normal "
            "in-place update path. Existing authentication and user config are preserved."
        ),
    )

    config = commands.add_parser(
        "config",
        help="View or change documented Colab Remote preferences",
        description=(
            "Manage the commented config.jsonc file. Values are validated before "
            "they replace the current configuration."
        ),
    )
    config_commands = config.add_subparsers(
        dest="config_command",
        required=True,
        metavar="ACTION",
    )
    show = config_commands.add_parser("show", help="Print the active configuration")
    show.add_argument(
        "--json",
        action="store_true",
        help="Print strict JSON instead of documented JSONC",
    )
    get = config_commands.add_parser("get", help="Print one setting")
    get.add_argument(
        "name",
        choices=sorted(configuration.SETTINGS),
        metavar="NAME",
    )
    set_command = config_commands.add_parser("set", help="Validate and set one setting")
    set_command.add_argument(
        "name",
        choices=sorted(configuration.SETTINGS),
        metavar="NAME",
    )
    set_command.add_argument(
        "value",
        help="New value; arrays use JSON syntax such as '[\"/home/me/data\"]'",
    )
    set_command.add_argument(
        "--yes",
        action="store_true",
        help="Confirm a change that grants standing AI permission",
    )
    reset = config_commands.add_parser(
        "reset",
        help="Reset one setting, or every setting, to its default",
    )
    reset_group = reset.add_mutually_exclusive_group(required=True)
    reset_group.add_argument(
        "name",
        nargs="?",
        choices=sorted(configuration.SETTINGS),
        metavar="NAME",
    )
    reset_group.add_argument("--all", action="store_true", help="Reset every setting")
    reset.add_argument(
        "--yes",
        action="store_true",
        help="Confirm resetting every setting",
    )
    describe = config_commands.add_parser(
        "describe",
        help="Explain one setting, its type, default, and allowed values",
    )
    describe.add_argument(
        "name",
        choices=sorted(configuration.SETTINGS),
        metavar="NAME",
    )
    config_commands.add_parser("path", help="Print the config file path")
    config_commands.add_parser(
        "edit",
        help="Open a temporary copy in the system editor, then validate and save it",
    )
    allow_root = config_commands.add_parser(
        "allow-root",
        help="Allow file tools to access one existing local folder",
    )
    allow_root.add_argument("path")
    remove_root = config_commands.add_parser(
        "remove-root",
        help="Remove one local folder from the file-tool allowlist",
    )
    remove_root.add_argument("path")

    secrets = commands.add_parser(
        "secrets",
        help="Manage plugin-owned API-key aliases in the OS credential store",
        description=(
            "Add, list, or remove only aliases created by Colab Remote. Values are "
            "entered through hidden terminal input and are never printed. The command "
            "cannot enumerate unrelated operating-system credentials."
        ),
    )
    secret_commands = secrets.add_subparsers(
        dest="secrets_command",
        required=True,
        metavar="ACTION",
    )
    add = secret_commands.add_parser(
        "add",
        aliases=["set"],
        help="Create or replace an alias using hidden input",
    )
    add.add_argument("name", help="Environment variable name, for example HF_TOKEN")
    secret_commands.add_parser(
        "list",
        help="List Colab Remote alias names; never show values",
    )
    remove = secret_commands.add_parser(
        "remove",
        aliases=["delete"],
        help="Delete a Colab Remote alias",
    )
    remove.add_argument("name")
    secret_commands.add_parser(
        "doctor",
        help="Check the secure credential backend without listing aliases",
    )
    return parser


def _run_secrets(options: argparse.Namespace) -> int:
    command = options.secrets_command
    if command in {"add", "set"}:
        return secret_broker.main(["set", options.name])
    if command == "list":
        return secret_broker.main(["list"])
    if command in {"remove", "delete"}:
        return secret_broker.main(["delete", options.name])
    try:
        details = secret_broker.backend_status()
    except secret_broker.SecretBrokerError as exc:
        print(f"Credential store unavailable: {exc}", file=sys.stderr)
        return 1
    print(f"Credential store available: {details['backend']}")
    if details["platform_note"]:
        print(details["platform_note"])
    return 0


def _confirmed(message: str, yes: bool) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        print(f"{message} Re-run with --yes to confirm.", file=sys.stderr)
        return False
    answer = input(f"{message} Continue? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def _run_config(options: argparse.Namespace) -> int:
    action = options.config_command
    if action == "path":
        print(configuration.config_path())
        return 0
    try:
        current = configuration.load()
        if action == "show":
            if options.json:
                print(json.dumps(current, indent=2, ensure_ascii=False))
            else:
                print(
                    config_io.render(current, configuration.DOCUMENTATION),
                    end="",
                )
            return 0
        if action == "get":
            print(json.dumps(current[options.name], ensure_ascii=False))
            return 0
        if action == "describe":
            details = configuration.SETTINGS[options.name]
            print(f"{options.name}: {details['description']}")
            print(f"Type: {details['type']}")
            print(f"Default: {json.dumps(details['default'], ensure_ascii=False)}")
            print(f"Allowed: {json.dumps(details['allowed'], ensure_ascii=False)}")
            return 0
        if action == "set":
            value = configuration.parse_value(options.name, options.value)
            if options.name == "allowed_local_roots":
                missing = [
                    item
                    for item in value
                    if not Path(item).expanduser().is_dir()
                ]
                if missing:
                    raise ValueError(
                        "allowed_local_roots entries must be existing directories: "
                        + ", ".join(str(item) for item in missing)
                    )
            warning = SENSITIVE_TRANSITIONS.get((options.name, value))
            if (
                warning
                and current.get(options.name) != value
                and not _confirmed(warning, options.yes)
            ):
                return 2
            candidate = {**current, options.name: value}
            saved = configuration.save(candidate)
            print(f"{options.name} = {json.dumps(saved[options.name], ensure_ascii=False)}")
            return 0
        if action == "reset":
            if options.all:
                if not _confirmed(
                    "Reset every Colab Remote preference to its default?",
                    options.yes,
                ):
                    return 2
                configuration.save(dict(configuration.DEFAULT_CONFIG))
                print("Reset all settings.")
            else:
                candidate = {
                    **current,
                    options.name: configuration.DEFAULT_CONFIG[options.name],
                }
                configuration.save(candidate)
                print(f"Reset {options.name}.")
            return 0
        if action in {"allow-root", "remove-root"}:
            target = str(Path(options.path).expanduser().resolve())
            roots = list(current["allowed_local_roots"])
            if action == "allow-root":
                if not Path(target).is_dir():
                    raise ValueError(f"Folder does not exist: {target}")
                if target not in roots:
                    roots.append(target)
                message = f"Allowed local folder: {target}"
            else:
                roots = [
                    root for root in roots
                    if os.path.normcase(root) != os.path.normcase(target)
                ]
                message = f"Removed local folder: {target}"
            configuration.save({**current, "allowed_local_roots": roots})
            print(message)
            return 0
        if action == "edit":
            return _edit_config(current)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1
    return 2


def _editor_command(path: Path) -> list[str]:
    configured = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if configured:
        return [*shlex.split(configured, posix=os.name != "nt"), str(path)]
    if os.name == "nt":
        return ["notepad.exe", str(path)]
    if sys.platform == "darwin":
        return ["open", "-W", "-t", str(path)]
    editor = shutil.which("nano") or shutil.which("vi")
    if not editor:
        raise OSError("No editor found; set the VISUAL or EDITOR environment variable")
    return [editor, str(path)]


def _edit_config(current: dict[str, object]) -> int:
    with tempfile.TemporaryDirectory(prefix="colab-remote-config-") as directory:
        draft = Path(directory) / "config.jsonc"
        draft.write_text(
            config_io.render(current, configuration.DOCUMENTATION),
            encoding="utf-8",
        )
        result = subprocess.run(_editor_command(draft), check=False)
        if result.returncode != 0:
            print("Editor exited without saving the configuration.", file=sys.stderr)
            return result.returncode or 1
        edited = config_io.loads(draft.read_text(encoding="utf-8"))
        roots = edited.get("allowed_local_roots", [])
        if isinstance(roots, list):
            missing = [
                str(item)
                for item in roots
                if not Path(str(item)).expanduser().is_dir()
            ]
            if missing:
                raise ValueError(
                    "allowed_local_roots entries must be existing directories: "
                    + ", ".join(missing)
                )
        configuration.save(edited)
    print(f"Saved validated configuration: {configuration.config_path()}")
    return 0


def _download_installer(destination: Path) -> None:
    request = Request(
        INSTALLER_URL,
        headers={"User-Agent": f"codex-colab-remote/{_installed_version()} updater"},
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310
        final_host = (urlparse(response.geturl()).hostname or "").lower()
        if final_host != "raw.githubusercontent.com":
            raise ValueError(f"Refusing unexpected installer host: {final_host}")
        payload = response.read(2_000_001)
    if len(payload) > 2_000_000:
        raise ValueError("Refusing unexpectedly large installer")
    if b"RemySkye/codex-colab-remote" not in payload or b"def main(" not in payload:
        raise ValueError("Downloaded file is not the expected Colab Remote installer")
    destination.write_bytes(payload)


def _run_update() -> int:
    print("Downloading the official Colab Remote installer...")
    try:
        with tempfile.TemporaryDirectory(prefix="colab-remote-update-") as directory:
            installer = Path(directory) / "install.py"
            _download_installer(installer)
            result = subprocess.run(
                [sys.executable, str(installer), "--skip-authentication"],
                check=False,
            )
            return result.returncode
    except (OSError, ValueError) as exc:
        print(f"Update failed: {exc}", file=sys.stderr)
        return 1


def _run_doctor() -> int:
    failures = 0
    print(f"Colab Remote: {_installed_version()}")
    print(f"Platform: {platform.system()} {platform.machine()}")
    print(f"Python: {platform.python_version()}")
    for executable in ("codex", "uv"):
        location = shutil.which(executable)
        if location:
            print(f"{executable}: {location}")
        else:
            failures += 1
            print(f"{executable}: not found on PATH", file=sys.stderr)
    try:
        configuration.load()
        print(f"Config: valid ({configuration.config_path()})")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        failures += 1
        print(f"Config: invalid ({exc})", file=sys.stderr)
    try:
        details = secret_broker.backend_status()
        print(f"Credential store: {details['backend']}")
    except secret_broker.SecretBrokerError as exc:
        failures += 1
        print(f"Credential store: unavailable ({exc})", file=sys.stderr)
    if failures:
        print(f"Doctor found {failures} problem(s).", file=sys.stderr)
        return 1
    print("All local checks passed.")
    return 0


def main(arguments: Sequence[str] | None = None) -> int:
    options = _parser().parse_args(arguments)
    if options.command == "secrets":
        return _run_secrets(options)
    if options.command == "config":
        return _run_config(options)
    if options.command == "doctor":
        return _run_doctor()
    if options.command == "update":
        return _run_update()
    if options.command == "version":
        print(_installed_version())
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
