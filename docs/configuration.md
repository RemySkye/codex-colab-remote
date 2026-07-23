# Configuration

Colab Remote stores preferences in an owner-local, commented `config.jsonc` file. Use `colab-remote config path` to locate it. Both the user CLI and MCP server validate settings before saving them.

## User CLI

```text
colab-remote config show
colab-remote config get default_accelerator
colab-remote config describe default_accelerator
colab-remote config set default_accelerator a100
colab-remote config set default_language julia
colab-remote config set notification_mode failures_only
colab-remote config allow-root /absolute/project/folder
colab-remote config edit
```

On Windows, `allow-root` accepts a Windows absolute path. On Linux and macOS, it accepts a POSIX absolute path. `edit` opens a temporary copy in `%VISUAL%` or `%EDITOR%` when configured, otherwise Notepad on Windows, TextEdit on macOS, or Nano/Vi on Linux. An invalid edit never replaces the working config.

Codex may also use the MCP `get_config` and `set_config` tools. Installer flags provide initial defaults.

## Settings

| Setting | Default | Allowed values and behavior |
|---|---:|---|
| `distro` | `Ubuntu` | WSL distribution on Windows; ignored on Linux/macOS |
| `default_accelerator` | `cpu` | `cpu`, `t4`, `l4`, `g4`, `h100`, `a100`, `v5e-1`, `v6e-1` |
| `default_language` | `python` | Native `python`, `r`, or `julia` kernel |
| `default_runtime_version` | `latest` | `latest` (recommended) or a Google-supported `YYYY.MM` label |
| `default_high_ram` | `false` | L4, G4, H100, v5e-1, and v6e-1 force High-RAM on because Google offers no off variant |
| `default_timeout_seconds` | `3600` | 30–86400 |
| `compute_warning_minutes` | `60` | 5–1440 |
| `default_max_lifetime_minutes` | `0` | 0 disables the plugin timer; otherwise 1–1440 |
| `notification_mode` | `off` | `off`, `failures_only`, or `all`; silent history is still recorded |
| `max_concurrent_sessions` | `8` | 1–64 independently named sessions |
| `transfer_compression` | `false` | Default folder/large-file compression behavior |
| `transfer_parallelism` | `4` | 1–8 transfer workers |
| `retry_attempts` | `3` | 1–10 attempts for retryable operations |
| `default_drive_checkpoint_folder` | `checkpoints` | Relative folder under `MyDrive/codex-colab`; no `.`, `..`, absolute path, or empty component |
| `require_cost_acknowledgement` | `true` | If false, Codex has standing permission to allocate sessions |
| `require_secret_enable_approval` | `false` | If false, Codex may enable specifically named Colab Remote aliases without another prompt |
| `allowed_local_roots` | `[]` | Exact existing local folders available to file and notebook tools |

The generated JSONC file contains these descriptions, types, defaults, and allowed values immediately above each setting.

## Permission controls

The default allocation policy asks before starting paid or quota-consuming compute. To grant standing allocation permission:

```text
colab-remote config set require_cost_acknowledgement false --yes
```

The default secret policy already lets Codex enable an alias that the user previously created with `colab-remote secrets add`. To require acknowledgement for every enable operation:

```text
colab-remote config set require_secret_enable_approval true
```

Turning that setting back off requires `--yes`. It grants access only to requested, plugin-owned aliases. It does not let MCP enumerate unrelated operating-system credentials or view values. Once enabled in a session, trusted code in that session can read the resulting environment variable.

Less restrictive changes made through MCP require `confirm_sensitive_change=true`.

## Sessions and Drive

Each named session keeps separate runtime settings, secret grants, jobs, files, transfers, and recovery state. Every session-bound MCP tool requires `session_name`; the default concurrent limit is eight.

When `save_to_drive` or `save_notebook_to_drive` omits `drive_path`, the source name is saved beneath `default_drive_checkpoint_folder`. Drive access remains strictly inside `MyDrive/codex-colab`.

Allowed roots should be narrow project/output folders rather than a home directory. Configuration and local state must never be committed.
