# Installation

## What the installer changes

The public launchers always download the shared `install.py` from the latest `main` branch. A fresh install and an update use the same workflow:

1. Verify required host tools.
2. Install pinned `uv` 0.11.28 after checking the installer checksum.
3. Install Google's official `google-colab-cli` 0.6.0.
4. Add or refresh this Git repository as a Codex marketplace and install the latest `colab-remote` from `main`.
5. Install or update the user-facing `colab-remote` command through `uv`.
6. Write owner-only configuration under `~/.codex/colab-remote`.
7. Start Google Colab OAuth unless authentication is skipped.

They do not install system packages, change firewall rules, or copy OAuth tokens.

Python 3.11 or newer is required on Windows, Linux, and macOS.

The local secret broker uses Windows Credential Manager, macOS Keychain, or the desktop-neutral Freedesktop Secret Service standard on Linux. GNOME Keyring is one provider, but it is not specifically required; compatible KWallet Secret Service and KeePassXC configurations can also work. Linux users need a provider running and unlocked in their login session.

Verify the command and credential backend with:

```text
colab-remote --help
colab-remote secrets doctor
```

Manage credentials without exposing their values to Codex:

```text
colab-remote secrets add HF_TOKEN
colab-remote secrets list
colab-remote secrets remove HF_TOKEN
```

Adding uses masked double entry. Removing requires typing the alias again. Listing reads only Colab Remote's owner-only alias index and never enumerates unrelated system credentials.

## Updating an existing installation

Use the same command on Windows, Linux, and macOS:

```text
colab-remote update
```

It downloads the official repository's `install.py` from the latest `main` branch over HTTPS and invokes it directly with Python, without constructing a shell command. Rerunning the original installer follows the same latest-`main` path.

The installer detects `colab-remote@colab-remote`, refreshes Git-backed marketplaces, reuses a valid local development marketplace, repairs a stale local registration that no longer exposes the plugin, installs the new version, and verifies the result. It does not run a separate uninstall first; Codex handles the replacement.

After installing or updating, run `colab-remote --help` for the complete user command and `colab-remote doctor` for read-only local checks. Doctor never lists credential aliases or values. The installer places `colab-remote` beside `uv`, so it is immediately available whenever an existing `uv` was already on `PATH`; if the installer had to add that directory for the first time, open a new terminal once.

Existing Colab OAuth credentials are not reopened or replaced during an update. Existing `~/.codex/colab-remote/config.jsonc` values and future unknown settings are preserved. Older `config.json` files are migrated automatically. If an installer configuration option is explicitly supplied again, only that setting is updated.

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
