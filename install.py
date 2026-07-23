#!/usr/bin/env python3
"""Cross-platform installer and safe updater for Colab Remote."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.request


REPOSITORY = "RemySkye/codex-colab-remote"
MARKETPLACE = "colab-remote"
PLUGIN = "colab-remote"
UV_VERSION = "0.11.28"
COLAB_CLI_VERSION = "0.6.0"
UV_WINDOWS_SHA256 = "09ac738e5c5eea1d94284b80ceb49b81097891218a79751d08116cd8552b492d"
UV_SHELL_SHA256 = "b7b3fe80cad1142a2a5794050b7db7b3291d1bac1423b0732571dd9366e8ca8b"
ACCELERATORS = ("cpu", "t4", "l4", "g4", "h100", "a100", "v5e-1", "v6e-1")
LANGUAGES = ("python", "r", "julia")
CONFIG_FLAG_DESTINATIONS = {
    "--distro": "distro",
    "-Distro": "distro",
    "--default-accelerator": "default_accelerator",
    "-DefaultAccelerator": "default_accelerator",
    "--default-language": "default_language",
    "-DefaultLanguage": "default_language",
    "--runtime-version": "runtime_version",
    "-DefaultRuntimeVersion": "runtime_version",
    "--max-lifetime": "max_lifetime",
    "-DefaultMaxLifetimeMinutes": "max_lifetime",
    "--high-ram": "high_ram",
    "-PreferHighRam": "high_ram",
    "--allowed-root": "allowed_root",
    "-AllowedLocalRoot": "allowed_root",
    "--notification-mode": "notification_mode",
    "--enable-notifications": "notification_mode",
    "-EnableNotifications": "notification_mode",
    "--disable-notifications": "notification_mode",
    "-DisableNotifications": "notification_mode",
}
ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _strip_jsonc_comments(text: str) -> str:
    output = []
    index = 0
    in_string = escaped = line_comment = block_comment = False
    while index < len(text):
        character = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if line_comment:
            if character in "\r\n":
                line_comment = False
                output.append(character)
            else:
                output.append(" ")
        elif block_comment:
            if character == "*" and following == "/":
                output.extend((" ", " "))
                index += 1
                block_comment = False
            else:
                output.append(character if character in "\r\n" else " ")
        elif in_string:
            output.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
            output.append(character)
        elif character == "/" and following == "/":
            output.extend((" ", " "))
            index += 1
            line_comment = True
        elif character == "/" and following == "*":
            output.extend((" ", " "))
            index += 1
            block_comment = True
        else:
            output.append(character)
        index += 1
    if block_comment:
        raise ValueError("unterminated block comment in config.jsonc")
    return "".join(output)


def parse_jsonc(text: str) -> dict[str, object]:
    uncommented = _strip_jsonc_comments(text)
    output = []
    index = 0
    in_string = escaped = False
    while index < len(uncommented):
        character = uncommented[index]
        if in_string:
            output.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
            output.append(character)
        elif character == ",":
            lookahead = index + 1
            while lookahead < len(uncommented) and uncommented[lookahead].isspace():
                lookahead += 1
            if lookahead >= len(uncommented) or uncommented[lookahead] not in "}]":
                output.append(character)
        else:
            output.append(character)
        index += 1
    value = json.loads("".join(output))
    if not isinstance(value, dict):
        raise ValueError("Colab Remote config must be a JSON object")
    return value


def render_jsonc(config: dict[str, object], documentation: dict[str, object]) -> str:
    settings = documentation["settings"]
    assert isinstance(settings, dict)
    names = list(settings)
    names.extend(sorted(name for name in config if name not in settings))
    lines = [
        "{",
        "  // Colab Remote configuration. Comments and trailing commas are supported.",
        "  // Never store passwords, OAuth codes, tokens, or other secrets here.",
    ]
    for position, name in enumerate(names):
        details = settings.get(name)
        if isinstance(details, dict):
            allowed = details["allowed"]
            allowed_text = (
                ", ".join(json.dumps(item) for item in allowed)
                if isinstance(allowed, list)
                else str(allowed)
            )
            lines.extend(
                [
                    f"  // {details['description']}",
                    f"  // Type: {details['type']}. Default: {json.dumps(details['default'])}.",
                    f"  // Allowed: {allowed_text}.",
                ]
            )
        else:
            lines.append("  // Preserved additional setting not known by this version.")
        encoded = json.dumps(config[name], indent=2).splitlines()
        suffix = "," if position + 1 < len(names) else ""
        lines.append(f"  {json.dumps(name)}: {encoded[0]}")
        lines.extend(f"  {line}" for line in encoded[1:-1])
        if len(encoded) > 1:
            lines.append(f"  {encoded[-1]}{suffix}")
        else:
            lines[-1] += suffix
        if suffix:
            lines.append("")
    return "\n".join([*lines, "}"]) + "\n"


def lifetime_minutes(value: str) -> int:
    minutes = int(value)
    if not 0 <= minutes <= 1440:
        raise argparse.ArgumentTypeError("must be between 0 and 1440")
    return minutes


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Install or safely update Colab Remote on Windows, Linux, or macOS."
    )
    result.add_argument(
        "--distro", "-Distro", default="Ubuntu", help="Windows WSL distribution"
    )
    result.add_argument(
        "--default-accelerator",
        "-DefaultAccelerator",
        choices=ACCELERATORS,
        default="cpu",
    )
    result.add_argument(
        "--default-language",
        "-DefaultLanguage",
        choices=LANGUAGES,
        default="python",
    )
    result.add_argument(
        "--runtime-version",
        "-DefaultRuntimeVersion",
        default="latest",
        metavar="LATEST_OR_YYYY.MM",
    )
    result.add_argument(
        "--max-lifetime",
        "-DefaultMaxLifetimeMinutes",
        type=lifetime_minutes,
        default=0,
        metavar="MINUTES",
    )
    result.add_argument("--high-ram", "-PreferHighRam", action="store_true")
    result.add_argument(
        "--allowed-root",
        "-AllowedLocalRoot",
        action="append",
        default=[],
        metavar="PATH",
    )
    notifications = result.add_mutually_exclusive_group()
    notifications.add_argument(
        "--notification-mode",
        choices=("off", "failures_only", "all"),
        dest="notification_mode",
        help="desktop popup policy (off by default)",
    )
    notifications.add_argument(
        "--enable-notifications",
        "-EnableNotifications",
        dest="notification_mode",
        action="store_const",
        const="all",
        help="enable all desktop completion popups",
    )
    notifications.add_argument(
        "--disable-notifications",
        "-DisableNotifications",
        dest="notification_mode",
        action="store_const",
        const="off",
        help=argparse.SUPPRESS,
    )
    result.set_defaults(notification_mode="off")
    result.add_argument(
        "--skip-authentication", "-SkipAuthentication", action="store_true"
    )
    result.add_argument("--run-smoke-test", "-RunSmokeTest", action="store_true")
    result.add_argument(
        "--state-root", "-StateRoot", type=Path, default=None, metavar="PATH"
    )
    return result


def validate_options(options: argparse.Namespace) -> None:
    if not re.fullmatch(r"(?:latest|20\d{2}\.\d{2})", options.runtime_version.lower()):
        raise ValueError("runtime version must be 'latest' or YYYY.MM")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,63}", options.distro):
        raise ValueError("invalid WSL distribution name")


def explicit_config_options(arguments: list[str]) -> set[str]:
    explicit = set()
    for argument in arguments:
        flag = argument.split("=", 1)[0]
        destination = CONFIG_FLAG_DESTINATIONS.get(flag)
        if destination:
            explicit.add(destination)
    return explicit


class Installer:
    def __init__(
        self, options: argparse.Namespace, *, platform: str | None = None
    ) -> None:
        self.options = options
        self.platform = platform or sys.platform
        self.windows = self.platform == "win32"
        if not self.windows and self.platform not in {"linux", "darwin"}:
            raise RuntimeError(f"unsupported operating system: {self.platform}")
        self.state_root = (
            options.state_root or Path.home() / ".codex" / "colab-remote"
        ).expanduser()
        self.restore_existing_distro()
        self.linux_home = ""
        self.colab_bin = ""
        self.installed_plugin_root: Path | None = None
        local_root = Path(__file__).resolve().parent
        self.marketplace_is_local = self.valid_local_marketplace(local_root)
        self.marketplace_source = (
            str(local_root) if self.marketplace_is_local else REPOSITORY
        )

    def restore_existing_distro(self) -> None:
        """Keep using the WSL distribution selected by an earlier install."""
        explicit = set(getattr(self.options, "explicit_config_options", set()))
        config_path = self.state_root / "config.jsonc"
        legacy_path = self.state_root / "config.json"
        source = config_path if config_path.is_file() else legacy_path
        if not self.windows or "distro" in explicit or not source.is_file():
            return
        try:
            config = parse_jsonc(source.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("existing Colab Remote config is not valid JSON") from exc
        if not isinstance(config, dict):
            raise ValueError("existing Colab Remote config must be a JSON object")
        distro = config.get("distro")
        if distro is None:
            return
        if not isinstance(distro, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_. -]{0,63}", distro
        ):
            raise ValueError("existing Colab Remote config has an invalid WSL distro")
        self.options.distro = distro

    @staticmethod
    def step(message: str) -> None:
        print(f"\n==> {message}", flush=True)

    @staticmethod
    def run(
        command: list[str],
        *,
        input_text: str | None = None,
        check: bool = True,
        capture: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                command,
                input=input_text,
                text=True,
                capture_output=capture,
                check=False,
                env=env,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"required executable was not found: {command[0]}"
            ) from exc
        if check and result.returncode != 0:
            detail = (
                result.stderr or result.stdout or f"exit code {result.returncode}"
            ).strip()
            raise RuntimeError(f"command failed: {command[0]}: {detail}")
        return result

    @classmethod
    def output(cls, command: list[str]) -> str:
        return cls.run(command, capture=True).stdout.strip()

    @staticmethod
    def download(url: str, destination: Path, expected_sha256: str) -> None:
        request = urllib.request.Request(
            url, headers={"User-Agent": "colab-remote-installer"}
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            content = response.read()
        digest = hashlib.sha256(content).hexdigest()
        if not secrets.compare_digest(digest, expected_sha256.lower()):
            raise RuntimeError("downloaded installer checksum did not match")
        destination.write_bytes(content)

    def check_host(self) -> None:
        if shutil.which("codex") is None:
            raise RuntimeError(
                "Codex CLI was not found. Install Codex, reopen the terminal, and retry."
            )
        if not self.windows:
            return
        if shutil.which("wsl.exe") is None:
            raise RuntimeError(
                "WSL is missing. In Administrator PowerShell run: wsl --install -d Ubuntu"
            )
        raw = self.output(["wsl.exe", "--list", "--quiet"]).replace("\x00", "")
        if self.options.distro not in {
            line.strip() for line in raw.splitlines() if line.strip()
        }:
            raise RuntimeError(
                f"WSL distribution '{self.options.distro}' is missing. Run: wsl --install -d {self.options.distro}"
            )

    def wsl_shell(self, script: str) -> None:
        encoded = base64.b64encode(script.replace("\r\n", "\n").encode()).decode()
        self.run(
            [
                "wsl.exe",
                "-d",
                self.options.distro,
                "--",
                "bash",
                "-lc",
                f"printf %s {shlex.quote(encoded)} | base64 -d | bash",
            ]
        )

    def install_uv(self, temporary: Path) -> Path:
        executable = "uv.exe" if self.windows else "uv"
        candidate = shutil.which("uv") or str(
            Path.home() / ".local" / "bin" / executable
        )
        if Path(candidate).is_file():
            return Path(candidate)
        self.step(f"Installing pinned uv {UV_VERSION}")
        if self.windows:
            source = temporary / "uv-install.ps1"
            self.download(
                f"https://astral.sh/uv/{UV_VERSION}/install.ps1",
                source,
                UV_WINDOWS_SHA256,
            )
            self.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(source),
                ]
            )
        else:
            source = temporary / "uv-install.sh"
            self.download(
                f"https://astral.sh/uv/{UV_VERSION}/install.sh", source, UV_SHELL_SHA256
            )
            self.run(["sh", str(source)])
        installed = Path.home() / ".local" / "bin" / executable
        if not installed.is_file():
            raise RuntimeError("uv installation did not produce an executable")
        return installed

    def install_colab_cli(self, uv_bin: Path) -> None:
        self.step(f"Installing Google's official Colab CLI {COLAB_CLI_VERSION}")
        if self.windows:
            script = f"""set -euo pipefail
if [ ! -x "$HOME/.local/bin/uv" ]; then
  command -v curl >/dev/null 2>&1 || {{ echo "curl is required inside WSL" >&2; exit 12; }}
  curl -LsSf https://astral.sh/uv/{UV_VERSION}/install.sh -o /tmp/colab-remote-uv-install.sh
  printf '%s  %s\n' '{UV_SHELL_SHA256}' /tmp/colab-remote-uv-install.sh | sha256sum -c -
  sh /tmp/colab-remote-uv-install.sh
  rm -f /tmp/colab-remote-uv-install.sh
fi
"$HOME/.local/bin/uv" tool install --force "google-colab-cli=={COLAB_CLI_VERSION}"
"$HOME/.local/bin/colab" version
"""
            self.wsl_shell(script)
            self.linux_home = self.output(
                [
                    "wsl.exe",
                    "-d",
                    self.options.distro,
                    "--",
                    "sh",
                    "-lc",
                    'printf %s "$HOME"',
                ]
            )
            self.colab_bin = f"{self.linux_home}/.local/bin/colab"
        else:
            self.run(
                [
                    str(uv_bin),
                    "tool",
                    "install",
                    "--force",
                    f"google-colab-cli=={COLAB_CLI_VERSION}",
                ]
            )
            self.colab_bin = str(Path.home() / ".local" / "bin" / "colab")
            self.run([self.colab_bin, "version"])

    @staticmethod
    def marketplace_manifest(root: Path) -> Path:
        return root / ".agents" / "plugins" / "marketplace.json"

    @classmethod
    def valid_local_marketplace(cls, root: Path) -> bool:
        try:
            manifest = json.loads(
                cls.marketplace_manifest(root).read_text(encoding="utf-8")
            )
            if manifest.get("name") != MARKETPLACE:
                return False
            entry = next(
                item
                for item in manifest.get("plugins", [])
                if item.get("name") == PLUGIN
            )
            source = entry.get("source", {})
            if isinstance(source, dict):
                relative = source.get("path")
            else:
                relative = source
            if not isinstance(relative, str) or not relative.startswith("./"):
                return False
            plugin_root = (root / relative[2:]).resolve()
            plugin_manifest = plugin_root / ".codex-plugin" / "plugin.json"
            metadata = json.loads(plugin_manifest.read_text(encoding="utf-8"))
            return metadata.get("name") == PLUGIN
        except (OSError, StopIteration, TypeError, ValueError, json.JSONDecodeError):
            return False

    @staticmethod
    def command_error(result: subprocess.CompletedProcess[str]) -> str:
        return (result.stderr or result.stdout or f"exit code {result.returncode}").strip()

    def marketplace_root(self) -> Path | None:
        result = self.run(
            ["codex", "plugin", "marketplace", "list"],
            check=False,
            capture=True,
        )
        if result.returncode != 0:
            return None
        output = ANSI_ESCAPE.sub("", result.stdout or "")
        pattern = re.compile(rf"^\s*{re.escape(MARKETPLACE)}\s+(.+?)\s*$")
        for line in output.splitlines():
            match = pattern.match(line)
            if match:
                value = match.group(1)
                if value.startswith("\\\\?\\"):
                    value = value[4:]
                return Path(value).expanduser().resolve(strict=False)
        return None

    def plugin_is_installed(self) -> bool:
        result = self.run(["codex", "plugin", "list"], check=False, capture=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"could not inspect installed Codex plugins: {self.command_error(result)}"
            )
        output = ANSI_ESCAPE.sub("", result.stdout or "")
        return bool(
            re.search(
                rf"(?m)^\s*{re.escape(PLUGIN)}@{re.escape(MARKETPLACE)}\s+installed(?:,|\s)",
                output,
            )
        )

    def refresh_marketplace(self) -> None:
        added = self.run(
            ["codex", "plugin", "marketplace", "add", self.marketplace_source],
            check=False,
            capture=True,
        )
        if self.marketplace_is_local:
            configured = self.marketplace_root()
            expected = Path(self.marketplace_source).resolve(strict=False)
            if configured == expected and self.valid_local_marketplace(configured):
                add_output = (added.stdout or "") + (added.stderr or "")
                if "already added" in add_output.lower():
                    print(f"Using configured local marketplace: {configured}")
                return
            raise RuntimeError(
                "a different local marketplace named 'colab-remote' is already configured; "
                "remove that stale marketplace or run its own local installer"
            )

        add_output = (added.stdout or "") + (added.stderr or "")
        if added.returncode == 0 and "already added" not in add_output.lower():
            return

        upgraded = self.run(
            ["codex", "plugin", "marketplace", "upgrade", MARKETPLACE],
            check=False,
            capture=True,
        )
        if upgraded.returncode == 0:
            return
        upgrade_error = self.command_error(upgraded)
        if "not configured as a Git marketplace" not in upgrade_error:
            raise RuntimeError(f"could not refresh marketplace: {upgrade_error}")

        configured = self.marketplace_root()
        if configured is not None and self.valid_local_marketplace(configured):
            print(f"Using configured local marketplace: {configured}")
            self.marketplace_source = str(configured)
            self.marketplace_is_local = True
            return

        self.step("Repairing a stale local Colab Remote marketplace")
        removed = self.run(
            ["codex", "plugin", "marketplace", "remove", MARKETPLACE],
            check=False,
            capture=True,
        )
        if removed.returncode != 0:
            raise RuntimeError(
                f"could not remove stale marketplace: {self.command_error(removed)}"
            )
        self.run(
            ["codex", "plugin", "marketplace", "add", self.marketplace_source]
        )

    def install_plugin(self) -> None:
        self.step("Adding or refreshing the Codex plugin marketplace")
        self.refresh_marketplace()
        self.step("Installing or updating Colab Remote")
        result = self.run(
            ["codex", "plugin", "add", f"{PLUGIN}@{MARKETPLACE}"],
            capture=True,
        )
        output = ANSI_ESCAPE.sub("", result.stdout or "")
        root_match = re.search(r"(?m)^Installed plugin root:\s*(.+?)\s*$", output)
        if root_match:
            candidate = Path(root_match.group(1)).expanduser().resolve()
            if candidate.is_dir():
                self.installed_plugin_root = candidate
        if not self.plugin_is_installed():
            raise RuntimeError(
                "Codex did not report Colab Remote as installed after the update"
            )

    def plugin_source_path(self) -> Path:
        if self.installed_plugin_root is not None:
            if (
                (self.installed_plugin_root / "pyproject.toml").is_file()
                and (self.installed_plugin_root / "colab_remote" / "cli.py").is_file()
            ):
                return self.installed_plugin_root
        result = self.run(
            ["codex", "plugin", "list", "--json"],
            check=False,
            capture=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"could not locate the installed Colab Remote plugin: {self.command_error(result)}"
            )
        try:
            installed = json.loads(result.stdout or "{}").get("installed", [])
        except (AttributeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Codex returned invalid plugin metadata") from exc
        for plugin in installed:
            if plugin.get("pluginId") != f"{PLUGIN}@{MARKETPLACE}":
                continue
            source = plugin.get("source")
            path = source.get("path") if isinstance(source, dict) else None
            if not isinstance(path, str):
                break
            plugin_root = Path(path).expanduser().resolve()
            if (
                (plugin_root / "pyproject.toml").is_file()
                and (plugin_root / "colab_remote" / "cli.py").is_file()
            ):
                return plugin_root
            break
        raise RuntimeError("installed Colab Remote source directory was not found")

    def install_user_cli(self, uv_bin: Path) -> None:
        self.step("Installing or updating the colab-remote command")
        plugin_root = self.plugin_source_path()
        self.run(
            [
                str(uv_bin),
                "tool",
                "install",
                "--force",
                "--refresh-package",
                "codex-colab-remote",
                str(plugin_root),
            ]
        )
        executable = "colab-remote.exe" if self.windows else "colab-remote"
        installed = Path.home() / ".local" / "bin" / executable
        if not installed.is_file():
            raise RuntimeError(
                f"uv did not create the expected user command: {installed}"
            )
        path_update = self.run(
            [str(uv_bin), "tool", "update-shell"],
            check=False,
            capture=True,
        )
        if path_update.returncode != 0:
            print(
                f"Warning: add {installed.parent} to PATH to run colab-remote directly."
            )
        self.run([str(installed), "--help"], capture=True)

    def approved_roots(self) -> list[str]:
        roots = []
        for raw in self.options.allowed_root:
            path = Path(raw).expanduser().resolve()
            if not path.is_dir():
                raise ValueError(f"allowed root must be a directory: {raw}")
            roots.append(str(path))
        return sorted(set(roots))

    def configuration_documentation(self) -> dict[str, object]:
        cached = getattr(self, "_configuration_documentation", None)
        if cached is not None:
            return cached
        documentation_path = (
            Path(__file__).resolve().parent
            / "plugins"
            / "colab-remote"
            / "config_schema.json"
        )
        if documentation_path.is_file():
            documentation = json.loads(
                documentation_path.read_text(encoding="utf-8")
            )
        else:
            url = (
                f"https://raw.githubusercontent.com/{REPOSITORY}/main/"
                "plugins/colab-remote/config_schema.json"
            )
            request = urllib.request.Request(
                url, headers={"User-Agent": "colab-remote-installer"}
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                documentation = json.loads(response.read().decode("utf-8"))
        if not isinstance(documentation, dict) or not isinstance(
            documentation.get("settings"), dict
        ):
            raise RuntimeError("Colab Remote configuration documentation is invalid")
        self._configuration_documentation = documentation
        return documentation

    def default_config(self) -> dict[str, object]:
        documentation = self.configuration_documentation()
        defaults = {
            name: details["default"]
            for name, details in documentation["settings"].items()
        }
        defaults.update({
            "distro": self.options.distro,
            "default_accelerator": self.options.default_accelerator,
            "default_language": self.options.default_language,
            "default_runtime_version": self.options.runtime_version.lower(),
            "default_high_ram": self.options.high_ram,
            "default_timeout_seconds": 3600,
            "compute_warning_minutes": 60,
            "default_max_lifetime_minutes": self.options.max_lifetime,
            "notification_mode": self.options.notification_mode,
            "max_concurrent_sessions": 8,
            "transfer_compression": False,
            "transfer_parallelism": 4,
            "retry_attempts": 3,
            "default_drive_checkpoint_folder": "checkpoints",
            "require_cost_acknowledgement": True,
            "allowed_local_roots": self.approved_roots(),
        })
        return defaults

    def write_config(self) -> None:
        self.state_root.mkdir(parents=True, exist_ok=True)
        destination = self.state_root / "config.jsonc"
        legacy_destination = self.state_root / "config.json"
        defaults = self.default_config()
        explicit = set(getattr(self.options, "explicit_config_options", set()))
        source = destination if destination.exists() else legacy_destination
        if source.exists():
            self.step("Preserving Colab Remote configuration")
            loaded = parse_jsonc(source.read_text(encoding="utf-8"))
            if "notification_mode" not in loaded and "notifications_enabled" in loaded:
                loaded["notification_mode"] = (
                    "all" if loaded["notifications_enabled"] else "off"
                )
            loaded.pop("notifications_enabled", None)
            loaded.pop("_documentation", None)
            loaded.pop("ssh_secret_name", None)
            loaded.pop("ssh_tunnel_enabled", None)
            config = {**defaults, **loaded}
            updates = {
                "distro": "distro",
                "default_accelerator": "default_accelerator",
                "default_language": "default_language",
                "runtime_version": "default_runtime_version",
                "high_ram": "default_high_ram",
                "max_lifetime": "default_max_lifetime_minutes",
                "notification_mode": "notification_mode",
                "allowed_root": "allowed_local_roots",
            }
            for option_name in explicit:
                config_name = updates[option_name]
                config[config_name] = defaults[config_name]
        else:
            self.step("Saving owner-only Colab Remote defaults")
            config = defaults
        config["require_cost_acknowledgement"] = True
        documentation = self.configuration_documentation()
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(render_jsonc(config, documentation), encoding="utf-8")
        if self.windows:
            username = os.environ.get("USERNAME")
            domain = os.environ.get("USERDOMAIN")
            identity = f"{domain}\\{username}" if domain and username else username
            if not identity:
                raise RuntimeError("could not determine the current Windows identity")
            self.run(
                [
                    "icacls.exe",
                    str(self.state_root),
                    "/inheritance:r",
                    "/grant:r",
                    f"{identity}:(OI)(CI)F",
                ]
            )
        else:
            self.state_root.chmod(0o700)
            temporary.chmod(0o600)
        temporary.replace(destination)
        legacy_destination.unlink(missing_ok=True)
        if not self.windows:
            destination.chmod(0o600)

    def colab_command(self, arguments: list[str]) -> list[str]:
        base = ["env", "-u", "GOOGLE_APPLICATION_CREDENTIALS", "-u", "CLOUDSDK_CONFIG"]
        if self.windows:
            return [
                "wsl.exe",
                "-d",
                self.options.distro,
                "--",
                *base,
                self.colab_bin,
                "--auth",
                "oauth2",
                *arguments,
            ]
        return [*base, self.colab_bin, "--auth", "oauth2", *arguments]

    def authenticate(self) -> None:
        if self.options.skip_authentication:
            return
        self.step("Authenticating directly with Google Colab")
        print(
            "Follow the Google sign-in link. Enter any one-time code only in this terminal."
        )
        self.run(self.colab_command(["sessions"]))
        if self.windows:
            self.run(
                [
                    "wsl.exe",
                    "-d",
                    self.options.distro,
                    "--",
                    "sh",
                    "-lc",
                    'token="$HOME/.config/colab-cli/token.json"; test ! -f "$token" || chmod 600 "$token"',
                ]
            )
        else:
            token = Path.home() / ".config" / "colab-cli" / "token.json"
            if token.is_file():
                token.chmod(0o600)

    def smoke_test(self) -> None:
        if not self.options.run_smoke_test:
            return
        self.step("Creating a temporary CPU runtime for verification")
        session = f"codex-install-smoke-{secrets.randbelow(900000) + 100000}"
        created = False
        try:
            self.run(self.colab_command(["new", "-s", session]))
            created = True
            self.run(
                self.colab_command(["exec", "-s", session, "--timeout", "120"]),
                input_text='print("COLAB_REMOTE_INSTALL_OK")\n',
            )
        finally:
            if created:
                stopped = self.run(
                    self.colab_command(["stop", "-s", session]), check=False
                )
                listing = self.run(
                    self.colab_command(["sessions"]), check=False, capture=True
                )
                if (
                    stopped.returncode != 0
                    or listing.returncode != 0
                    or session in (listing.stdout or "") + (listing.stderr or "")
                ):
                    raise RuntimeError(
                        f"smoke-test cleanup could not be verified for {session}"
                    )

    def execute(self) -> None:
        self.check_host()
        existing_install = self.plugin_is_installed()
        with tempfile.TemporaryDirectory(prefix="colab-remote-") as raw_temporary:
            uv_bin = self.install_uv(Path(raw_temporary))
            self.install_colab_cli(uv_bin)
        self.install_plugin()
        self.install_user_cli(uv_bin)
        self.write_config()
        if existing_install:
            self.step("Preserving existing Google Colab authentication")
        else:
            self.authenticate()
        self.smoke_test()
        action = "updated" if existing_install else "installed"
        print(f"\nColab Remote is {action}. Restart Codex or start a new task.")
        if self.options.skip_authentication:
            print(
                "Run the installer again without --skip-authentication when you are ready to sign in."
            )


def main(arguments: list[str] | None = None) -> int:
    if sys.version_info < (3, 11):
        print("ERROR: Python 3.11 or newer is required.", file=sys.stderr)
        return 2
    try:
        raw_arguments = list(sys.argv[1:] if arguments is None else arguments)
        options = parser().parse_args(raw_arguments)
        options.explicit_config_options = explicit_config_options(raw_arguments)
        validate_options(options)
        Installer(options).execute()
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
