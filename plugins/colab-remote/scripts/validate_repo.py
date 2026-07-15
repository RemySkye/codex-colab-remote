"""Portable repository and secret-safety checks."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parents[1]
IGNORED_PARTS = {".git", ".venv", ".ruff_cache", ".local", "__pycache__"}
TEXT_SUFFIXES = {".json", ".md", ".ps1", ".py", ".sh", ".toml", ".yaml", ".yml"}


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


manifest = json.loads(
    (ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
)
if manifest.get("name") != "colab-remote":
    fail("plugin name must be colab-remote")
if not re.fullmatch(
    r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", str(manifest.get("version", ""))
):
    fail("plugin version is not valid semver")
if manifest.get("mcpServers") != "./.mcp.json":
    fail("plugin must reference .mcp.json")

mcp_config = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
if set(mcp_config.get("mcpServers", {})) != {"colab-remote"}:
    fail(".mcp.json must expose only colab-remote")
launcher = mcp_config["mcpServers"]["colab-remote"]
if launcher.get("command") != "uv" or "--project" not in launcher.get("args", []):
    fail(".mcp.json must use the portable uv launcher")

required = [
    REPOSITORY_ROOT / "install.ps1",
    REPOSITORY_ROOT / "install.sh",
    REPOSITORY_ROOT / ".gitleaks.toml",
    REPOSITORY_ROOT / "docs" / "installation.md",
    REPOSITORY_ROOT / "docs" / "troubleshooting.md",
    ROOT / "scripts" / "colab.ps1",
    ROOT / "scripts" / "runtime.ps1",
    ROOT / "scripts" / "run_mcp.ps1",
    ROOT / "skills" / "operate-colab-remote" / "SKILL.md",
    ROOT / "assets" / "bootstrap_ssh.py.tmpl",
]
for path in required:
    if not path.exists():
        fail(f"required file is missing: {path.relative_to(REPOSITORY_ROOT)}")

for obsolete in (
    ROOT / "assets" / "bootstrap_colab.py.tmpl",
    ROOT / "scripts" / "start_colab_auth.sh",
    ROOT / "scripts" / "submit_colab_auth.sh",
    ROOT / "scripts" / "finish_colab_auth.ps1",
):
    if obsolete.exists():
        fail(f"obsolete credential/tunnel helper remains: {obsolete.relative_to(ROOT)}")

marketplace = json.loads(
    (REPOSITORY_ROOT / ".agents" / "plugins" / "marketplace.json").read_text(
        encoding="utf-8"
    )
)
entries = {entry.get("name"): entry for entry in marketplace.get("plugins", [])}
entry = entries.get("colab-remote")
if marketplace.get("name") != "colab-remote" or not entry:
    fail("repository marketplace must expose colab-remote")
if entry.get("source", {}).get("path") != "./plugins/colab-remote":
    fail("marketplace path must be ./plugins/colab-remote")

banned = {
    "C:\\Users\\Administrator": "hardcoded Windows user",
    "/home/administrator": "hardcoded WSL user",
    "4/0A": "possible OAuth authorization code",
    "--auth adc": "ADC authentication",
    "PasswordAuthentication yes": "password-based SSH authentication",
    "StrictHostKeyChecking=no": "disabled SSH host-key verification",
    "PermitRootLogin yes": "SSH root login",
    "codex ALL=(ALL) NOPASSWD": "passwordless SSH sudo access",
}
secret_patterns = {
    r"AIza[0-9A-Za-z_-]{30,}": "Google API key",
    r"ya29\.[0-9A-Za-z_-]{20,}": "Google OAuth access token",
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----": "private key",
}
for path in REPOSITORY_ROOT.rglob("*"):
    if any(part in IGNORED_PARTS for part in path.parts) or not path.is_file():
        continue
    if path.resolve() == Path(__file__).resolve():
        continue
    if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {
        "LICENSE",
        ".gitignore",
    }:
        continue
    text = path.read_text(encoding="utf-8")
    for needle, description in banned.items():
        if needle in text:
            fail(f"{description} found in {path.relative_to(REPOSITORY_ROOT)}")
    for pattern, description in secret_patterns.items():
        if re.search(pattern, text):
            fail(f"{description} found in {path.relative_to(REPOSITORY_ROOT)}")

print("Repository validation passed.")
