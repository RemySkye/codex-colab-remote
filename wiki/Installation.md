# Installation

The optional local secret broker uses Windows Credential Manager, macOS Keychain, or an unlocked Linux Secret Service keyring.

Python 3.11 or newer is required on every platform. Both launchers run the same cross-platform Python installer.

## Update

Rerun the same installer command. It safely refreshes or reuses the correct marketplace, asks Codex to replace the plugin without a separate uninstall, verifies the result, and preserves existing Google authentication and configuration.

## Windows

Install WSL2/Ubuntu once, then run the installer in normal PowerShell:

```powershell
wsl --install -d Ubuntu
irm https://raw.githubusercontent.com/RemySkye/codex-colab-remote/main/install.ps1 | iex
```

## Linux and macOS

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/RemySkye/codex-colab-remote/main/install.sh)"
```

For inspect-first and manual commands, see the repository's [installation guide](https://github.com/RemySkye/codex-colab-remote/blob/main/docs/installation.md).

Follow the Google OAuth link and enter any one-time code only in the same terminal. Restart Codex or start a new task afterward.
