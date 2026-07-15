# Colab Remote for Codex

Securely let Codex create and operate your Google Colab runtimes through Google's official Colab CLI. No SSH tunnel, ngrok, browser automation, service account, or Google Cloud ADC is used.

## What it adds

- CPU, T4, L4, G4, H100, A100, TPU v5e-1, or TPU v6e-1 session creation
- Configurable default accelerator, Python/Julia mode, high-RAM preference, timeout, compute-duration warning, and notifications
- Python execution and best-effort Julia LTS execution
- Restricted upload/download, remote files, logs, packages, URLs, and kernel restart
- Persistent tmux jobs with heartbeat, logs, exit status, JSON progress, and detached Windows completion watchers
- Cost/quota warning and explicit approval before every allocation
- Verified cleanup, diagnostics, credential metadata checks, and output redaction

## Install on Windows

Requirements: Windows 10/11, WSL2 with Ubuntu, Codex CLI, and a Google account with Colab access.

Open PowerShell:

```powershell
wsl --install -d Ubuntu
```

Reboot if asked and finish the Ubuntu username setup. Then download and inspect the installer before running it:

```powershell
irm https://raw.githubusercontent.com/RemySkye/codex-colab-remote/v0.4.0/install.ps1 -OutFile install-colab-remote.ps1
Get-Content .\install-colab-remote.ps1
.\install-colab-remote.ps1
```

The short form is available after the `v0.4.0` release is published:

```powershell
irm https://raw.githubusercontent.com/RemySkye/codex-colab-remote/v0.4.0/install.ps1 | iex
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

## Important limits

- Google controls capacity, idle shutdown, session duration, and quota. A heartbeat monitors work but does not bypass Colab policies.
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
