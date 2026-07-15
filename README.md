# Colab Remote for Codex

[![CI](https://github.com/RemySkye/codex-colab-remote/actions/workflows/ci.yml/badge.svg)](https://github.com/RemySkye/codex-colab-remote/actions/workflows/ci.yml)
[![Security](https://github.com/RemySkye/codex-colab-remote/actions/workflows/security.yml/badge.svg)](https://github.com/RemySkye/codex-colab-remote/actions/workflows/security.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Colab Remote lets Codex create and operate your Google Colab runtimes. It uses Google's official Colab CLI for normal work and offers optional key-only SSH when the actual SSH protocol is required.

Windows, Ubuntu/Linux, and macOS are supported. The same Python MCP server runs everywhere; only Windows uses WSL because Google's Colab CLI is Linux/macOS-native.

## Highlights

- CPU, T4, L4, G4, H100, A100, TPU v5e-1, and TPU v6e-1 runtimes
- Native Python, R, and Julia kernels; High-RAM and runtime-version selection
- Arbitrary Linux terminal commands without SSH or a public tunnel
- Monitored jobs, progress, desktop notifications, session lifetimes, cleanup, and opt-in recovery
- Resumable parallel file/folder transfers with compression, checksums, cancellation, and resume
- Create, edit, reorder, run, import, and export notebooks; save/load notebooks through Google Drive
- Optional short-lived Ed25519 SSH through ngrok, disabled by default
- OAuth token isolation, restricted local-file roots, output redaction, and cost acknowledgement

See [all MCP tools](docs/tools.md), the [architecture](docs/architecture.md), and the [security model](SECURITY.md).

## Requirements

- [Codex CLI](https://developers.openai.com/codex/cli)
- A Google account with Colab access
- Windows 10/11 with WSL2 and Ubuntu, or a supported Linux/macOS host
- `curl` on Linux/macOS (normally already installed)

Colab hardware availability depends on your plan, compute balance, capacity, and Google's policies.

## Quick install

These commands download the installer from this repository. For the safest approach, use the inspect-first commands in [Installation](docs/installation.md).

### Windows PowerShell

Install WSL once from Administrator PowerShell, reboot if requested, and finish the Ubuntu setup:

```powershell
wsl --install -d Ubuntu
```

Then use a normal PowerShell window:

```powershell
irm https://raw.githubusercontent.com/RemySkye/codex-colab-remote/main/install.ps1 | iex
```

### Ubuntu/Linux

```bash
tmp="$(mktemp)" && curl -fsSL https://raw.githubusercontent.com/RemySkye/codex-colab-remote/main/install.sh -o "$tmp" && bash "$tmp"; rc=$?; rm -f "$tmp"; exit $rc
```

### macOS

```bash
tmp="$(mktemp)" && curl -fsSL https://raw.githubusercontent.com/RemySkye/codex-colab-remote/main/install.sh -o "$tmp" && bash "$tmp"; rc=$?; rm -f "$tmp"; exit $rc
```

The installer pins `uv` and `google-colab-cli`, installs the Codex plugin, saves owner-only defaults, and starts Google Colab OAuth. Follow the Google link and paste any one-time code only into that terminal—never into Codex, chat, or an issue. Restart Codex or start a new task after installation.

## Example configuration

Windows:

```powershell
.\install.ps1 -DefaultAccelerator a100 -DefaultLanguage r `
  -DefaultRuntimeVersion latest -DefaultMaxLifetimeMinutes 180 `
  -PreferHighRam -AllowedLocalRoot C:\Users\me\Projects
```

Linux/macOS:

```bash
./install.sh --default-accelerator a100 --default-language r \
  --runtime-version latest --max-lifetime 180 --high-ram \
  --allowed-root "$HOME/Projects"
```

Python is the default language and `latest` is the recommended runtime version. A lifetime of `0` disables the plugin timer; Google may still end a runtime. Run either installer with its help option to see every setting.

## Use

Ask Codex naturally, for example:

> Use Colab Remote to create a High-RAM L4 Python session, upload this folder, run the training job, notify me when it finishes, download the results, and stop the session.

Codex checks authentication and configuration, explains the quota warning, and asks before allocating compute. Normal terminal work uses the official CLI and does not require ngrok. See [Configuration](docs/configuration.md) and [Tool reference](docs/tools.md).

## Optional SSH

SSH is not needed for commands, packages, files, or long jobs. Enable it only when a program specifically requires SSH/SCP. It needs an ngrok account with TCP endpoint support and a `NGROK_AUTHTOKEN` stored in Colab Secrets; the token is never copied to Codex.

```powershell
.\install.ps1 -EnableSshTunnel
```

```bash
./install.sh --enable-ssh
```

The SSH account is unprivileged and uses a short-lived key plus strict host-key pinning. Stop the tunnel when finished. More detail is in [Security](SECURITY.md).

## Documentation

- [Installation and manual setup](docs/installation.md)
- [Configuration](docs/configuration.md)
- [Architecture and dependencies](docs/architecture.md)
- [Complete tool reference](docs/tools.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Development and testing](docs/development.md)
- [Security policy](SECURITY.md)

The same guides are published in the repository's GitHub Wiki.

## Important limits

- Google controls capacity, quota, idle shutdown, and maximum duration. This project does not bypass those rules.
- `/content` is temporary. Download or checkpoint important data before cleanup.
- Recovery can recreate opted-in work but cannot restore VM memory or lost ephemeral files.
- High-RAM/runtime combinations may be unavailable. The plugin reports the measured result.
- A heartbeat monitors legitimate work; it is not an anti-idle bypass.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). CI tests Python 3.12 on GitHub-hosted Windows, Ubuntu, and macOS runners. Never include OAuth codes, tokens, session URLs, private keys, or personal logs in a report.
