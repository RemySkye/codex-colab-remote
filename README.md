# Codex Colab Remote

Installable Codex plugin for provisioning and operating Google Colab CPU, GPU, and TPU runtimes from Codex. Normal operation uses Google's official Colab CLI; a short-lived, host-key-pinned SSH bridge is included only as a fallback.

## Fast installation on Windows

Requirements: Windows 10/11, Codex Desktop with the `codex` CLI available, and WSL2 with Ubuntu initialized once.

If WSL is not installed, run this in **Administrator PowerShell**, reboot if requested, open Ubuntu once, and create the Linux username when prompted:

```powershell
wsl --install -d Ubuntu
```

Then open a normal PowerShell window and run:

```powershell
irm https://raw.githubusercontent.com/RemySkye/codex-colab-remote/main/install.ps1 | iex
```

This installs `uv` on Windows and Ubuntu, installs Google's `google-colab-cli` in Ubuntu, adds the GitHub marketplace to Codex, installs the plugin, and starts Google sign-in. It does not ask for or receive your Google password.

Running code directly from the internet is convenient but should be a deliberate trust decision. To inspect the installer first:

```powershell
irm https://raw.githubusercontent.com/RemySkye/codex-colab-remote/main/install.ps1 -OutFile .\install-colab-remote.ps1
notepad .\install-colab-remote.ps1
& .\install-colab-remote.ps1
```

Use `-SkipAuthentication`, `-Distro <name>`, or `-RunSmokeTest` only with the downloaded-script form. The smoke test briefly creates a CPU runtime and always attempts to stop it.

## Native Codex installation

These are the closest equivalents to `pip install` or `npm install` for a Codex plugin:

```powershell
codex plugin marketplace add RemySkye/codex-colab-remote
codex plugin add colab-ssh@colab-remote
```

Start a new Codex task after installation so the plugin and MCP server are loaded.

## Fully manual setup

The following commands perform the same setup transparently.

### 1. Install WSL2 and Ubuntu

Run in Administrator PowerShell:

```powershell
wsl --install -d Ubuntu
```

Reboot if Windows asks, open Ubuntu once, and finish its username/password setup.

### 2. Install uv on Windows

The Windows copy runs the plugin's local MCP server:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Open a new PowerShell window afterward if `uv --version` is not immediately found.

### 3. Install uv and the Colab CLI inside Ubuntu

```powershell
wsl -d Ubuntu -- bash -lc 'curl -LsSf https://astral.sh/uv/install.sh | sh'
wsl -d Ubuntu -- bash -lc '~/.local/bin/uv tool install google-colab-cli'
wsl -d Ubuntu -- bash -lc '~/.local/bin/colab version'
```

The Colab CLI currently supports Linux and macOS, so Windows uses it through WSL2.

### 4. Install the Codex plugin

```powershell
codex plugin marketplace add RemySkye/codex-colab-remote
codex plugin add colab-ssh@colab-remote
```

### 5. Authenticate Google Colab

```powershell
wsl -d Ubuntu -- bash -lc '~/.local/bin/colab sessions'
```

On first use, the CLI prints a Google authorization link. Open it, choose the Google account that owns the Colab subscription or compute units, approve the requested scopes, and paste the one-time authorization code into the same PowerShell terminal if prompted. The code is exchanged locally for an OAuth token stored inside WSL at `~/.config/colab-cli/token.json`; the plugin sets that file to owner-only permissions (`600`).

This resembles `gcloud auth application-default login`, but the default plugin path authenticates directly with the Colab CLI and does **not** require Google Cloud CLI. The Colab CLI also exposes an `--auth adc` mode for users who intentionally manage Application Default Credentials with `gcloud`.

Never send the one-time code, OAuth token, Google password, or recovery codes to another person or commit them to Git.

## Updating

```powershell
codex plugin marketplace upgrade colab-remote
codex plugin add colab-ssh@colab-remote
wsl -d Ubuntu -- bash -lc '~/.local/bin/uv tool upgrade google-colab-cli'
```

Start a new Codex task after updating.

## What it provides

- CPU, T4, L4, G4, H100, A100, and TPU provisioning
- Python, shell-like console work, and Jupyter notebook execution
- Uploads, downloads, package installation, logs, and session inspection
- Google's built-in Colab CLI keep-alive daemon
- Windows path conversion through WSL2
- Restartable tmux jobs and checkpoint-oriented recovery guidance
- Fresh keys and strict host-key verification for the optional SSH fallback

Colab resources and maximum lifetime are not guaranteed. Keep-alive does not override quota, compute-unit exhaustion, account limits, or backend reclamation. Always stop completed sessions.

## Example

Ask Codex:

> Use the Colab Remote plugin to start a G4 session, run this project with checkpoints, monitor it, download the outputs, and stop the session when complete.

The underlying CLI remains available directly:

```powershell
wsl -d Ubuntu -- bash -lc '~/.local/bin/colab new -s training --gpu G4'
wsl -d Ubuntu -- bash -lc '~/.local/bin/colab status -s training'
wsl -d Ubuntu -- bash -lc '~/.local/bin/colab stop -s training'
```

## Development

```powershell
uv sync --project plugins\colab-ssh
uv run --project plugins\colab-ssh python -m unittest discover -s plugins\colab-ssh\tests -v
python plugins\colab-ssh\scripts\validate_repo.py
```

## Security

- Review remote installer scripts before executing them in sensitive environments.
- Never commit Colab OAuth tokens, generated SSH private keys, or session state.
- The official CLI is preferred over the SSH fallback.
- The fallback binds SSH to loopback, uses key-only authentication, pins the host key, and attempts remote revocation during cleanup.

See [SECURITY.md](SECURITY.md) for vulnerability reporting.

## License

MIT. Google Colab and the Colab CLI are Google products governed by their own terms and policies.
