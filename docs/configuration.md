# Configuration

Use `get_config` to inspect settings and `set_config` to change them. Installer flags provide initial defaults.

| Setting | Default | Notes |
|---|---:|---|
| Accelerator | `cpu` | CPU, T4, L4, G4, H100, A100, TPU v5e-1/v6e-1 |
| Language | `python` | Native Python, R, or Julia kernel |
| Runtime version | `latest` | Recommended; older `YYYY.MM` labels depend on Google |
| High-RAM | off | L4, G4, H100, v5e-1, and v6e-1 force it on; CPU, T4, and A100 keep the selected value |
| Maximum lifetime | `0` | Plugin timer disabled; per-session override supported |
| Notification mode | `off` | `off`, `failures_only`, or `all`; silent history is always recorded |
| Concurrent sessions | `8` | Creation is blocked when this active-session limit is reached |
| Transfer compression | off | Default for managed transfers; folders are always archived |
| Transfer parallelism | `4` | Between 1 and 8 simultaneous chunks |
| Retry attempts | `3` | Retry-safe reads and transfer chunks only; mutating commands are never repeated |
| Drive checkpoint folder | `checkpoints` | Relative to `MyDrive/codex-colab` |
| Local file roots | none | Upload/download/notebook access is denied until allowlisted |
| SSH | off | Requires explicit sensitive-change confirmation |
| Recovery | off | Opt in per session/job because it may reallocate compute |

Creation requires `acknowledge_cost=true`. This prevents accidental allocations; it is not a price estimate. Use `stop_session_on_finish` for a final job when the runtime is no longer needed. Keep the session alive otherwise so Codex can reuse it.

Desktop popups are disabled by default. Set `notification_mode` to `failures_only` or `all`, or use the installer flag `--notification-mode`; jobs must also request `notify_on_completion=true`. Silent `notification_history` remains available in every mode.

The owner-only `~/.codex/colab-remote/config.jsonc` file places generated comments directly above every setting with its description, type, default, and allowed values. Standard `//` and `/* ... */` comments plus trailing commas are supported. `set_config` regenerates the canonical comments while preserving setting values and unknown future keys. Never place secrets in this file.

When `save_to_drive` or `save_notebook_to_drive` omits `drive_path`, the source name is saved beneath `default_drive_checkpoint_folder`. The folder remains strictly inside `MyDrive/codex-colab`.

Allowed roots should be narrow project/output folders rather than a home directory. Configuration and local state must never be committed.
