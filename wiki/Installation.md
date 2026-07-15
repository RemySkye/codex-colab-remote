# Installation

## Windows

Install WSL2/Ubuntu once, then run the installer in normal PowerShell:

```powershell
wsl --install -d Ubuntu
irm https://raw.githubusercontent.com/RemySkye/codex-colab-remote/main/install.ps1 | iex
```

## Linux and macOS

```bash
tmp="$(mktemp)" && curl -fsSL https://raw.githubusercontent.com/RemySkye/codex-colab-remote/main/install.sh -o "$tmp" && bash "$tmp"; rc=$?; rm -f "$tmp"; exit $rc
```

For inspect-first and manual commands, see the repository's [installation guide](https://github.com/RemySkye/codex-colab-remote/blob/main/docs/installation.md).

Follow the Google OAuth link and enter any one-time code only in the same terminal. Restart Codex or start a new task afterward.
