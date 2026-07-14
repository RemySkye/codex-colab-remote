"""Portable repository checks that do not depend on a Codex installation."""

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


manifest_path = ROOT / ".codex-plugin" / "plugin.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("name") != ROOT.name:
    fail("plugin name must match the repository installation folder")
if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", str(manifest.get("version", ""))):
    fail("plugin version is not valid semver")
if manifest.get("mcpServers") != "./.mcp.json":
    fail("plugin must reference .mcp.json")

mcp_config = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
if "colab-ssh" not in mcp_config.get("mcpServers", {}):
    fail("colab-ssh MCP server is missing")

required = [
    ROOT / "scripts" / "colab.ps1",
    ROOT / "scripts" / "runtime.ps1",
    ROOT / "scripts" / "run_mcp.ps1",
    ROOT / "skills" / "operate-colab-ssh" / "SKILL.md",
]
for path in required:
    if not path.exists():
        fail(f"required file is missing: {path.relative_to(ROOT)}")

marketplace_path = REPOSITORY_ROOT / ".agents" / "plugins" / "marketplace.json"
marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
if marketplace.get("name") != "colab-remote":
    fail("repository marketplace must be named colab-remote")
entries = {entry.get("name"): entry for entry in marketplace.get("plugins", [])}
entry = entries.get("colab-ssh")
if not entry or entry.get("source", {}).get("path") != "./plugins/colab-ssh":
    fail("marketplace must expose ./plugins/colab-ssh")
if not (REPOSITORY_ROOT / "install.ps1").exists():
    fail("root bootstrap installer is missing")

banned = {
    "C:\\Users\\Administrator": "hardcoded Windows user",
    "/home/administrator": "hardcoded WSL user",
    "4/0A": "possible OAuth authorization code",
}
for path in REPOSITORY_ROOT.rglob("*"):
    if not path.is_file() or any(part in IGNORED_PARTS for part in path.parts):
        continue
    if path.resolve() == Path(__file__).resolve():
        continue
    if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {"LICENSE", ".gitignore"}:
        continue
    text = path.read_text(encoding="utf-8")
    for needle, description in banned.items():
        if needle in text:
            fail(f"{description} found in {path.relative_to(REPOSITORY_ROOT)}")

print("Repository validation passed.")
