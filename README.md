# Colab Remote for Codex

Securely let Codex create and operate Google Colab runtimes through Google's official Colab CLI. An optional, explicitly enabled SSH mode adds a direct terminal and file transfer through ngrok.

## What it adds

- CPU, T4, L4, G4, H100, A100, TPU v5e-1, or TPU v6e-1 session creation
- Configurable default accelerator, Python/Julia mode, high-RAM preference, timeout, compute-duration warning, and notifications
- Python execution and best-effort Julia LTS execution
- Restricted upload/download, remote files, logs, packages, URLs, and kernel restart
- Persistent tmux jobs with heartbeat, logs, exit status, JSON progress, and detached Windows completion watchers
- Cost/quota warning and explicit approval before every allocation
- Verified cleanup, diagnostics, credential metadata checks, and output redaction
- Optional key-only SSH terminal and SCP, with a pinned host key and automatic cleanup

## Install on Windows

Requirements: Windows 10/11, WSL2 with Ubuntu, Codex CLI, and a Google account with Colab access.

Open PowerShell:

```powershell
wsl --install -d Ubuntu
```

Reboot if asked and finish the Ubuntu username setup. Then download and inspect the installer before running it:

```powershell
irm https://raw.githubusercontent.com/RemySkye/codex-colab-remote/v0.5.0/install.ps1 -OutFile install-colab-remote.ps1
Get-Content .\install-colab-remote.ps1
.\install-colab-remote.ps1
```

The short form is available after the `v0.5.0` release is published:

```powershell
irm https://raw.githubusercontent.com/RemySkye/codex-colab-remote/v0.5.0/install.ps1 | iex
```

The installer pins uv `0.11.28` and Google Colab CLI `0.6.0`. It installs the Codex plugin, writes safe defaults, and then opens Google authentication in your terminal.

Authentication is simple: follow the Google link shown in the terminal and complete sign-in there. If Google gives a one-time code, paste it only into that same terminal—never into Codex or a chat.

## Choose defaults during install

```powershell
.\install-colab-remote.ps1 `
  -DefaultAccelerator a100 `
  -DefaultLanguage python `
  -PreferHighRam `
  -AllowedLocalRoot C:\Users\me\Projects
```

Use `-DefaultLanguage julia`, `-DisableNotifications`, `-SkipAuthentication`, or `-RunSmokeTest` when needed. The smoke test briefly allocates a CPU runtime and may use quota.

Restart Codex after installation. Then ask: “Use Colab Remote to create a T4 session, run this job, notify me when it finishes, download the results, and stop the session.”

## Optional direct SSH terminal

SSH is off by default. Google says SSH on free managed runtimes without a positive compute-unit balance may be terminated. Use a paid Colab plan with a positive balance and an ngrok account that supports TCP endpoints.

1. In Colab, add `NGROK_AUTHTOKEN` under **Secrets** and enable notebook access. Never paste the token into Codex.
2. Install with SSH enabled:

```powershell
.\install-colab-remote.ps1 -EnableSshTunnel
```

3. Create a session normally, then ask Codex to enable SSH. Codex will show the Colab-policy and public-tunnel warnings before it proceeds.

The tunnel uses a short-lived Ed25519 key, key-only login, strict host-key pinning, an unprivileged `codex` user, and no SSH password, root login, sudo, agent forwarding, X11 forwarding, or TCP forwarding. SSH work can use `/content/codex-ssh`; use the normal typed tools for privileged package installation. `disable_ssh` revokes the key and stops both SSH and ngrok; `stop_session` also attempts this cleanup automatically.

## Important limits

- Google controls capacity, idle shutdown, session duration, and quota. A heartbeat monitors work but does not bypass Colab policies.
- A public ngrok TCP endpoint increases exposure even though login is key-only. Disable SSH when it is not needed.
- The official CLI does not currently expose a high-RAM allocation switch. The plugin records your preference, measures actual RAM, and warns you honestly.
- Julia is not a native CLI kernel. The plugin can install Juliaup LTS inside the Python VM only after your approval.
- `/content` is temporary. Download or checkpoint important results before stopping.
- The plugin cannot show an exact price because Colab plan pricing and compute-unit consumption are account-specific. It warns before allocating expensive hardware.
- Completion watchers are detached and saved for restart recovery, but Windows shutdown or security software can still stop them. Reopen Codex and call `watch_job` to recover a watcher.

## Security model

- OAuth2 only; ADC environment variables are removed from every CLI call.
- Codex never reads or returns token contents. It only checks existence and owner-only permissions.
- Local file access is disabled by default and limited to explicitly approved folders.
- Credentials and authorization codes are redacted from errors and blocked by repository secret scanning.
- Configuration and notification state are restricted to the current Windows user.
- SSH private keys stay in the plugin's owner-only local state and are deleted when SSH is disabled.

No software can promise zero risk. See [SECURITY.md](SECURITY.md) for the threat model and revocation steps.

## Develop and test

```powershell
uv sync --project plugins\colab-remote
uv run --project plugins\colab-remote ruff check plugins\colab-remote
uv run --project plugins\colab-remote python -m unittest discover -s plugins\colab-remote\tests -v
python plugins\colab-remote\scripts\validate_repo.py
powershell -NoProfile -ExecutionPolicy Bypass -File tests\installer-smoke.ps1
```

The test suite does not allocate paid Colab hardware. `-RunSmokeTest` is the explicit live integration test.
