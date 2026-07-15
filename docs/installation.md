# Installation

## What the installer changes

The shared `install.py` used by both launchers:

1. Verify required host tools.
2. Install pinned `uv` 0.11.28 after checking the installer checksum.
3. Install Google's official `google-colab-cli` 0.6.0.
4. add this repository as a Codex marketplace and install `colab-remote`.
5. Write owner-only configuration under `~/.codex/colab-remote`.
6. Start Google Colab OAuth unless authentication is skipped.

They do not install system packages, change firewall rules, copy OAuth tokens, or enable SSH by default.

Python 3.11 or newer is required on Windows, Linux, and macOS.

## Updating an existing installation

Rerun the same installer command used for installation. No separate updater is required.

The installer detects `colab-remote@colab-remote`, refreshes Git-backed marketplaces, reuses a valid local development marketplace, repairs a stale local registration that no longer exposes the plugin, installs the new version, and verifies the result. It does not run a separate uninstall first; Codex handles the replacement.

Existing Colab OAuth credentials are not reopened or replaced during an update. Existing `~/.codex/colab-remote/config.json` values and future unknown settings are preserved. If an installer configuration option is explicitly supplied again, only that setting is updated.

Restart Codex and open a new task after the installer reports that the plugin was updated.

## Windows: inspect and run

Install WSL2/Ubuntu once from Administrator PowerShell:

```powershell
wsl --install -d Ubuntu
```

After reboot and Ubuntu account setup, use normal PowerShell:

```powershell
$url = 'https://raw.githubusercontent.com/RemySkye/codex-colab-remote/main/install.py'
Invoke-WebRequest $url -OutFile .\install-colab-remote.py
Get-Content .\install-colab-remote.py
python .\install-colab-remote.py
```

## Linux/macOS: inspect and run

```bash
curl -fsSL https://raw.githubusercontent.com/RemySkye/codex-colab-remote/main/install.py -o install-colab-remote.py
less install-colab-remote.py
python3 install-colab-remote.py
rm install-colab-remote.py
```

## Manual installation

### Windows

Install `uv` on Windows and inside Ubuntu using the official instructions. Then:

```powershell
wsl -d Ubuntu -- bash -lc '~/.local/bin/uv tool install google-colab-cli==0.6.0'
codex plugin marketplace add RemySkye/codex-colab-remote
codex plugin add colab-remote@colab-remote
wsl -d Ubuntu -- bash -lc 'umask 077; env -u GOOGLE_APPLICATION_CREDENTIALS -u CLOUDSDK_CONFIG ~/.local/bin/colab --auth oauth2 sessions; chmod 600 ~/.config/colab-cli/token.json'
```

### Linux/macOS

```bash
curl -LsSf https://astral.sh/uv/0.11.28/install.sh -o /tmp/uv-install.sh
# Compare its SHA-256 with install.py before running it.
sh /tmp/uv-install.sh
~/.local/bin/uv tool install google-colab-cli==0.6.0
codex plugin marketplace add RemySkye/codex-colab-remote
codex plugin add colab-remote@colab-remote
umask 077
env -u GOOGLE_APPLICATION_CREDENTIALS -u CLOUDSDK_CONFIG ~/.local/bin/colab --auth oauth2 sessions
chmod 600 ~/.config/colab-cli/token.json
```

The Python installer accepts PowerShell-style aliases such as `-SkipAuthentication` and standard options such as `--skip-authentication`. Add `-RunSmokeTest` or `--run-smoke-test` only when you want a real temporary CPU allocation and accept its quota usage.

## Authentication

Colab CLI OAuth is separate from `gcloud auth login` and Application Default Credentials. The terminal prints a Google URL. Complete sign-in in your browser; if shown a one-time code, enter it back in the same terminal. The plugin never accepts or displays that code.

The cached token is stored at `~/.config/colab-cli/token.json` in the environment where the CLI runs (inside WSL on Windows). Keep it out of repositories, backups shared with others, chats, and issue reports.

## Uninstall

Use `codex plugin --help` for the installed Codex version's removal command. Removing the plugin does not revoke Google access. Delete the local token yourself and revoke the application in Google Account security settings if you want full credential revocation.
