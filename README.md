# Colab Remote for Codex

[![CI](https://github.com/RemySkye/codex-colab-remote/actions/workflows/ci.yml/badge.svg)](https://github.com/RemySkye/codex-colab-remote/actions/workflows/ci.yml)
[![Security](https://github.com/RemySkye/codex-colab-remote/actions/workflows/security.yml/badge.svg)](https://github.com/RemySkye/codex-colab-remote/actions/workflows/security.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Colab Remote lets Codex create and operate your Google Colab runtimes through Google's official Colab CLI. Codex can choose hardware, run Python/R/Julia, use a Linux terminal, manage long jobs, move large files, edit notebooks, checkpoint to Google Drive, recover opted-in work, and release compute when finished.

Version 0.7.1 supports Windows, Linux, and macOS. It does not use SSH, ngrok, a public inbound tunnel, Google Cloud ADC, or service-account credentials.

## What you get

- CPU, T4, L4, G4, H100, A100, TPU v5e-1, and TPU v6e-1 sessions
- Native Python, R, and Julia Colab kernels
- High-RAM and runtime-version selection, with `latest` recommended by default
- Arbitrary Linux commands through the official Colab connection
- Monitored background jobs, completion tracking, logs, lifetime limits, and optional recovery
- Resumable and cancellable files/folders with compression and parallel transfer controls
- Complete notebook creation, editing, cell execution, output retrieval, import, and export
- A protected Google Drive workspace at `MyDrive/codex-colab`
- Local API-key aliases backed by the operating system's credential manager
- Up to eight concurrent sessions by default, each addressed by its own name
- Notifications that are off by default and configurable as `off`, `failures_only`, or `all`

## Requirements

All platforms require:

- [Codex](https://github.com/openai/codex)
- Python 3.11 or newer
- A Google account that can use Colab

Windows additionally requires WSL 2 with Ubuntu because Google's Colab CLI is Linux/macOS-native. The installer uses the configured Ubuntu distribution but does not install WSL or Python for you.

GPU, TPU, High-RAM, session duration, and availability remain subject to your Colab plan, compute-unit balance, capacity, and Google's policies.

## Install or update

The same command performs a fresh install or safely updates an existing installation from the latest `main` branch. Existing Colab authentication, credential aliases, and configuration are preserved.

### Windows PowerShell

```powershell
irm https://raw.githubusercontent.com/RemySkye/codex-colab-remote/main/install.ps1 | iex
```

### Linux

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/RemySkye/codex-colab-remote/main/install.sh)"
```

### macOS

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/RemySkye/codex-colab-remote/main/install.sh)"
```

The installer places `colab-remote` beside `uv`. If it installed `uv` for the first time, open a new terminal once before running `colab-remote --help`.

The small PowerShell and shell launchers download the shared cross-platform `install.py` from `main`. That installer:

1. Checks the platform and required host tools.
2. Installs a checksum-pinned `uv` when needed.
3. Installs Google's official `google-colab-cli`.
4. refreshes this Git marketplace and installs the latest plugin from `main`.
5. Installs or updates the `colab-remote` user command.
6. Writes an owner-only commented configuration.
7. Starts official Colab OAuth when authentication is needed.

Google sign-in is completed by you in the browser and terminal. Never paste its authorization material into Codex, chat, or a GitHub issue.

After installation, restart Codex or start a new task so the MCP tools are registered.

### Inspect before running

If you prefer to review the installer first:

```bash
curl -fsSLO https://raw.githubusercontent.com/RemySkye/codex-colab-remote/main/install.py
python3 install.py --help
python3 install.py
```

On Windows:

```powershell
irm https://raw.githubusercontent.com/RemySkye/codex-colab-remote/main/install.py -OutFile install.py
python install.py --help
python install.py
```

The full manual and platform-specific setup is in [Installation](docs/installation.md).

## Start using it

Talk to Codex normally. For example:

```text
Create a normal-RAM CPU Colab session named analysis, run Python to inspect this
dataset, and stop the session when finished.
```

```text
Create an A100 High-RAM session named trainer, upload my approved project folder,
run training as a monitored job, and save checkpoints under Google Drive.
```

```text
Create two sessions named baseline and experiment. Run the same notebook on CPU
and T4, compare their outputs, then stop both sessions.
```

Codex should run local readiness checks and explain the compute warning before allocating unless you explicitly grant standing allocation permission in configuration.

## LLM tool surface

The plugin exposes 59 MCP tools. Every session operation uses a `session_name`, which lets Codex safely manage multiple runtimes at once.

| Area | Tools | What Codex can do |
|---|---|---|
| Setup and configuration | `authentication_instructions`, `credential_status`, `doctor`, `get_config`, `set_config` | Check prerequisites, guide OAuth, inspect preferences, and make validated changes |
| Sessions and runtimes | `create_session`, `list_sessions`, `session_status`, `session_url`, `set_session_lifetime`, `prepare_language`, `restart_kernel`, `stop_session` | Allocate named runtimes, select hardware/language/RAM/version, inspect them, limit lifetime, and release compute |
| Code and terminal | `execute_code`, `execute_file`, `terminal_exec`, `install_packages` | Run Python/R/Julia, execute approved local source files, use the Linux shell, and install packages |
| Long jobs | `start_job`, `job_status`, `job_logs`, `get_logs`, `watch_job`, `stop_job` | Run persistent work, monitor progress, retrieve logs, receive configured completion notifications, and stop jobs |
| Local secret aliases | `prepare_local_secret`, `list_local_secrets`, `enable_local_secrets`, `disable_local_secrets` | Ask the user to add a masked credential locally, see names only, and grant/revoke selected aliases per session |
| Direct files | `list_files`, `upload_file`, `download_file` | Inspect remote paths and move individual files through the official CLI |
| Managed transfers | `start_upload`, `start_download`, `transfer_status`, `list_transfers`, `resume_transfer`, `cancel_transfer` | Transfer large files or folders with checkpoints, optional compression, parallelism, resume, and safe cancellation |
| Notebooks | `create_notebook`, `read_notebook`, `import_notebook`, `export_session_notebook`, `add_notebook_cell`, `edit_notebook_cell`, `delete_notebook_cell`, `move_notebook_cell`, `run_notebook_cells` | Create valid notebooks, edit/reorder cells, run selected cells, retain outputs, and import/export |
| Google Drive | `mount_google_drive`, `complete_google_drive_mount`, `list_drive_files`, `create_drive_folder`, `move_drive_path`, `delete_drive_path`, `save_to_drive`, `restore_from_drive`, `save_notebook_to_drive`, `load_notebook_from_drive` | Mount and use only `MyDrive/codex-colab`, checkpoint files/folders, and persist notebooks |
| Recovery | `recovery_status`, `recover_session` | Inspect recovery metadata and explicitly recreate opted-in work after a lost runtime |
| Notifications | `notification_history`, `test_notification` | Inspect notification history or test the selected notification mode |

Destructive and expensive operations retain explicit safeguards. For example, Drive deletion and session shutdown require confirmation, while session allocation approval can be changed only through an explicit configuration choice.

## Configuration

The owner-only configuration is a documented JSONC file. View or change it without editing JSON by hand:

```text
colab-remote config show
colab-remote config describe default_accelerator
colab-remote config set default_accelerator a100
colab-remote config set default_language julia
colab-remote config set default_high_ram true
colab-remote config path
```

Sensitive standing permissions require `--yes` when changed non-interactively.

| Setting | Default | Meaning |
|---|---:|---|
| `distro` | `Ubuntu` | WSL distribution used on Windows; ignored elsewhere |
| `default_accelerator` | `cpu` | `cpu`, `t4`, `l4`, `g4`, `h100`, `a100`, `v5e-1`, or `v6e-1` |
| `default_language` | `python` | Native `python`, `r`, or `julia` kernel |
| `default_runtime_version` | `latest` | `latest` or a supported `YYYY.MM` runtime image |
| `default_high_ram` | `false` | Request High-RAM by default; forced on where Colab requires it |
| `default_timeout_seconds` | `3600` | Default supported operation timeout |
| `compute_warning_minutes` | `60` | When elapsed compute use is highlighted |
| `default_max_lifetime_minutes` | `0` | Automatic session lifetime; `0` disables the plugin timer |
| `notification_mode` | `off` | `off`, `failures_only`, or `all` |
| `max_concurrent_sessions` | `8` | Maximum sessions managed by this plugin instance |
| `transfer_compression` | `auto` | `auto`, `always`, or `never` |
| `transfer_parallelism` | `4` | Number of parallel transfer workers |
| `retry_attempts` | `3` | Retry count for safe transient failures |
| `default_drive_checkpoint_folder` | `checkpoints` | Default relative folder inside `MyDrive/codex-colab` |
| `require_cost_acknowledgement` | `true` | Ask before each new session; `false` grants standing allocation permission |
| `require_secret_enable_approval` | `false` | Whether each per-session secret enable needs another approval |
| `allowed_local_roots` | `[]` | Existing local folders file/notebook tools may access |

Examples:

```text
colab-remote config set notification_mode failures_only
colab-remote config set max_concurrent_sessions 4
colab-remote config set require_cost_acknowledgement false --yes
colab-remote config allow-root "/path/to/project"
```

Use `colab-remote config edit` for a validated editor workflow. Every setting is documented directly above its value in `config.jsonc`.

## Local API keys

Credential values are stored by Windows Credential Manager, macOS Keychain, or a supported Linux keyring backend. They are not stored in MCP configuration and are never returned to the LLM.

```text
colab-remote secrets add HF_TOKEN
colab-remote secrets list
colab-remote secrets remove HF_TOKEN
colab-remote secrets doctor
```

`secrets add` uses a masked terminal prompt. `secrets list` can enumerate only aliases created by Colab Remote, never unrelated operating-system credentials.

When a workload needs a key, Codex can:

1. Refresh the available alias names.
2. Ask you to add a missing alias in your own terminal.
3. Enable only the required names for one selected session.
4. Disable them after the workload.

Code running inside a session with an enabled variable can read it, so enable secrets only for trusted workloads.

## Google Drive

The Drive tools are limited to:

```text
MyDrive/codex-colab
```

The folder is created when needed. Paths outside it, traversal, symlink escapes, and deletion of the workspace root are rejected. Codex can save files, folders, notebooks, and training checkpoints there without filling the local PC.

On first mount, Google may open an approval page. Approve it in the browser; the plugin keeps the same official Drive-mount process alive and completes credential propagation afterward.

Use `/content` for fast temporary training data and periodically save important outputs to Drive. The plugin provides the tools but does not force an autosave policy.

## Large files and folders

For small files, Codex can use `upload_file` and `download_file`. For large data, ask it to use a managed transfer:

```text
Upload this approved folder using a compressed managed transfer, monitor it,
and safely resume it if interrupted.
```

Managed transfers support:

- Whole files or folders
- Optional compression
- Parallel workers
- Persisted progress and checksums
- Resume after interruption
- Safe cancellation

Local access remains denied until you add an exact existing folder with `colab-remote config allow-root`.

## Updates and diagnostics

```text
colab-remote update
colab-remote doctor
colab-remote version
```

`colab-remote update` downloads the official installer from the latest `main` branch over HTTPS, runs it with the current Python interpreter, refreshes the Git marketplace, reinstalls the plugin and user command, and preserves authentication and configuration.

Running the platform installation one-liner again performs the same install-or-update flow.

## Safety and limitations

- Colab controls quota, idle policy, maximum duration, available accelerators, and compute-unit use.
- `/content` and VM memory are ephemeral. Save important outputs before stopping or losing a runtime.
- Heartbeats monitor legitimate work; they do not bypass Colab policies.
- Recovery can recreate opted-in setup and restart commands, but cannot restore lost RAM or unsaved ephemeral files.
- L4, G4, H100, TPU v5e-1, and TPU v6e-1 automatically request High-RAM because Colab requires it.
- Notifications are disabled by default.
- Google Cloud Storage integration is planned; the current persistent-storage integration is Google Drive.

## More documentation

The README covers normal installation and use. For implementation details and troubleshooting:

- [Installation and manual setup](docs/installation.md)
- [Complete configuration reference](docs/configuration.md)
- [Tool behavior](docs/tools.md)
- [Architecture](docs/architecture.md)
- [Security model](SECURITY.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Development and testing](docs/development.md)
- [GitHub Wiki](https://github.com/RemySkye/codex-colab-remote/wiki)
- [Changelog](CHANGELOG.md)

## Contributing

Issues and pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) first. CI runs on GitHub-hosted Windows, Ubuntu, and macOS runners.

Never include OAuth codes, tokens, API keys, session URLs, personal paths, or unredacted logs in an issue.

## License

[MIT](LICENSE)
