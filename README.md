# Codex Colab Remote

Give Codex a supported remote execution environment on Google Colab. The plugin uses Google's official Colab CLI for normal operation and includes a short-lived, host-key-pinned SSH fallback for exceptional cases.

## What it provides

- CPU, T4, L4, G4, H100, A100, and TPU provisioning
- Remote Python scripts and Jupyter notebooks
- Upload, download, package installation, session logs, and console commands
- Google's built-in CLI keep-alive daemon
- Windows path conversion for WSL2
- Restartable tmux jobs and checkpoint-oriented recovery guidance
- Fresh SSH keys and strict host-key verification in the optional fallback

Consumer Colab resources and maximum lifetime are not guaranteed. The keep-alive does not override quota, compute-unit exhaustion, or backend reclamation.

## Windows installation

Requirements: Windows 10/11, Codex Desktop, Git, and WSL2. If WSL2 is not installed, first run this in Administrator PowerShell and complete Ubuntu's username setup:

```powershell
wsl --install -d Ubuntu
```

Then install or update the plugin:

```powershell
$target = Join-Path $HOME 'plugins\colab-ssh'
if (Test-Path $target) {
    git -C $target pull --ff-only
} else {
    git clone https://github.com/RemySkye/codex-colab-remote.git $target
}
& "$target\install.ps1" -Authenticate
```

The installer:

1. Detects the Windows and WSL usernames.
2. Installs `uv` on Windows for the local MCP server.
3. Installs `google-colab-cli` inside the selected WSL distribution.
4. Registers the plugin in the personal Codex marketplace without removing other entries.
5. Opens the Codex plugin page.
6. Optionally handles Google OAuth and an explicit CPU smoke test.

Use `-Distro <name>` for a non-default WSL distribution, `-RunSmokeTest` for a short billable CPU verification, or `-NoOpenPluginPage` for unattended setup.

After installation, choose **Install** or **Update** on the Codex plugin page and start a new task.

## Use

Ask Codex naturally:

> Start a G4 Colab session, upload this project, install its dependencies, run training with checkpoints, monitor it, and download the results.

Or use the wrapper directly:

```powershell
$colab = Join-Path $HOME 'plugins\colab-ssh\scripts\colab.ps1'
& $colab new -s training --gpu G4
& $colab status -s training
& $colab exec -s training -f 'C:\absolute\path\train.py' --timeout 86400
& $colab download -s training /content/checkpoint.bin 'C:\absolute\path\checkpoint.bin'
& $colab log -s training --output 'C:\absolute\path\training.ipynb'
& $colab stop -s training
```

Always stop completed sessions because an allocated runtime consumes compute units. Treat `/content` and the VM home directory as ephemeral and export valuable checkpoints throughout long runs.

## Authentication

`install.ps1 -Authenticate` starts the official Google OAuth flow. Sign in directly on Google's page and enter the one-time code in the installer terminal. Codex never needs your Google password. OAuth tokens remain in the WSL user's configuration directory and are excluded from Git.

For non-interactive Codex setup, the plugin also includes a FIFO-based authorization handoff in `scripts/start_colab_auth.sh`, `scripts/finish_colab_auth.ps1`, and `scripts/submit_colab_auth.sh`.

## Development

```powershell
uv sync
uv run python -m unittest discover -s tests -v
python scripts\validate_repo.py
```

Validate the Codex components when the creator skills are available:

```powershell
python $HOME\.codex\skills\.system\skill-creator\scripts\quick_validate.py skills\operate-colab-ssh
python $HOME\.codex\skills\.system\plugin-creator\scripts\validate_plugin.py .
```

## Security

- Never commit Colab OAuth tokens, ngrok tokens, generated SSH private keys, or session state.
- The official CLI is preferred over the SSH fallback.
- The fallback binds SSH to loopback, uses key-only authentication, pins the host key, and attempts remote revocation during cleanup.
- Review third-party installer scripts before executing them in security-sensitive environments.

## License

MIT. Google Colab and the Colab CLI are Google products and are governed by their own terms and policies.
