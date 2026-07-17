---
name: operate-colab-remote
description: Provision and operate user-authorized Google Colab runtimes from Codex using Google's official CLI, with optional direct key-only SSH. Use for CPU, GPU, TPU, High-RAM, runtime-version, native Python, R, or Julia sessions; remote code and large files; monitored jobs; terminal access; notifications; logs; recovery; and cleanup.
---

# Operate Colab Remote

Use the `colab-remote` MCP tools. Do not read tokens, handle authorization codes, or use Google Cloud ADC. Prefer the typed official-CLI tools, including `terminal_exec` for arbitrary Linux commands. Use optional SSH only when a concrete requirement needs the SSH network protocol.

Codex may defer MCP tool definitions until a relevant tool is requested. Do not claim that Colab tools are unregistered merely because an abbreviated task tool list does not show them. First attempt the harmless `doctor`, `credential_status`, or `list_sessions` tool. Report a registration problem only if that MCP call is unavailable or the MCP server returns a startup error.

## Safe startup

1. Call `doctor` and `credential_status`.
2. If authentication is missing, call `authentication_instructions`. The user must run the command and complete Google sign-in in their own trusted terminal. Never ask them to paste a code into Codex.
3. Call `get_config`. Confirm the requested accelerator, language, High-RAM setting, runtime version, maximum lifetime, and local-file roots. Prefer `runtime_version="latest"` unless the user needs a reproducible older image. A maximum lifetime of zero disables the plugin timer.
4. Explain the quota/compute warning and get explicit approval. Then call `create_session` with `acknowledge_cost=true`, `high_ram=true` or `false`, and the confirmed settings.

Use only these accelerator values: `cpu`, `t4`, `l4`, `g4`, `h100`, `a100`, `v5e-1`, and `v6e-1`. Use only `python`, `r`, or `julia` for language. Availability depends on the user's plan and capacity. L4, G4, H100, v5e-1, and v6e-1 automatically force High-RAM even when `high_ram=false`; CPU, T4, and A100 preserve the requested High-RAM setting. Report measured memory from the result.

## Execute work

- Use `execute_code` for short Python, R, or Julia code.
- Python, R, and Julia use Colab's native kernels (`python3`, `ir`, and `julia`). Python is the default. `create_session` verifies the selected kernel; call `prepare_language` only to switch or recheck a kernel. It never installs or downloads a language.
- Use `execute_file` for an approved local script. Local access is off by default and restricted to `allowed_local_roots`.
- Use `terminal_exec` for arbitrary Linux shell commands. It is automatic over the official Colab CLI and requires no ngrok token, public tunnel, or SSH setup. Use tmux or `start_job` for persistent commands.
- Use `upload_file` and `download_file` for single files up to 64 MiB. For larger files or folders, use `start_upload` or `start_download`, save the returned `transfer_id`, then call `transfer_status`. Omitted compression and parallelism values use the configured defaults. Managed transfers support bounded chunk retries, resume, and checksum verification. Use `cancel_transfer(confirm=true)` for cooperative cancellation and `resume_transfer` to continue completed chunks. Never broaden approved roots without explicit confirmation through `set_config`.
- Use `create_notebook`, `read_notebook`, and the cell tools to create and edit local nbformat 4 notebooks. `run_notebook_cells` runs selected cells on the session and saves outputs locally. Use `import_notebook` for a validated local copy and `export_session_notebook` to export session history.
- Use `mount_google_drive` to create the dedicated `MyDrive/codex-colab` workspace. On first use it may return `authorization_required=true` while keeping Google's official mount alive in a PTY. Ask the user only to approve the page that opened, then call `complete_google_drive_mount`; do not call `mount_google_drive` repeatedly and never ask for or handle an authorization code. Treat the returned `workspace_path` as the only Drive path agents may use. Never inspect, list, read, write, move, or delete its parent through `terminal_exec`, `execute_code`, a job, SSH, or notebook code.
- Use `list_drive_files` and `create_drive_folder` to organize the workspace. Use `save_to_drive` and `restore_from_drive` for general files or folders, and the notebook-specific save/load tools for validated notebooks. Use `move_drive_path` only within the workspace. Call `delete_drive_path` only for a specific user-requested item and set `confirm=true`; the workspace root cannot be deleted.
- Active training should normally run under fast ephemeral `/content` storage. When the user wants persistence, configure their framework or training code to write periodic checkpoints to the exact `workspace_path` returned by `mount_google_drive`, or call `save_to_drive` at requested milestones. Do not invent or force an autosave schedule.
- Every `drive_path` is relative to `MyDrive/codex-colab`; do not prefix it with `MyDrive` or `/content/drive`. The official Drive mount may require the user to complete an interactive Google authorization step. Never request, capture, or log that authorization material.
- Use `install_packages` only after reviewing package names with the user when they are untrusted or expensive to install.

## Optional SSH terminal

Do not configure SSH merely to obtain shell access; use `terminal_exec`. Continue below only when the user specifically requires the SSH protocol or SCP behavior.

1. Call `ssh_requirements`. Explain that Google may terminate SSH on free managed runtimes without a positive compute-unit balance and that ngrok creates a public TCP endpoint.
2. The user must add `NGROK_AUTHTOKEN` to Colab Secrets and enable notebook access. Never ask for or accept the token in Codex.
3. Enable `ssh_tunnel_enabled` through `set_config(..., confirm_sensitive_change=true)` only after explicit user approval.
4. Call `enable_ssh` with both acknowledgements. If Colab says Secrets are available only in the UI, call `prepare_ssh_browser` with both acknowledgements, open its `session_url`, run only its returned `bootstrap_code` in that notebook, and pass only the `CODEX_SSH_MANIFEST=` JSON to `register_ssh_manifest`. Use browser control when available; otherwise ask the user to run the cell. Never read the secret value.
5. Use `ssh_status`, `ssh_exec`, `ssh_upload`, and `ssh_download`; `/content/codex-ssh` is the writable SSH workspace. SSH is unprivileged by design, so use typed official-CLI tools for privileged setup instead of weakening SSH.
6. Call `disable_ssh(confirm=true)` when terminal access is no longer needed. `stop_session` also attempts SSH cleanup.

Do not disable host-key checking, expose a password, grant sudo/root, reveal a private key, or copy the ngrok token out of Colab Secrets.

## Long jobs

Use `start_job` for long commands. It provides tmux persistence, logs, heartbeat, exit status, optional JSON progress, and a detached local completion watcher. Programs may write JSON to the path in `CODEX_PROGRESS_FILE`.

Desktop popups use `notification_mode`: `off`, `failures_only`, or `all`. It defaults to `off`. Do not change the mode or set `notify_on_completion=true` unless the user explicitly asks for popups. Use `notification_history` for silent completion records.

Use `stop_session_on_finish=true` only when the user wants the VM released when that job ends. Otherwise keep the session available for follow-up work. Use `set_session_lifetime` for a hard upper bound that survives MCP restarts.

Automatic recovery is opt-in because it reallocates compute. Create the session with `recovery_enabled=true`, choose a bounded `max_recovery_attempts`, and set `recover_on_runtime_loss=true` only on jobs whose commands are safe to restart. Recovery recipes are saved in owner-only local state. Checkpoint important outputs to Drive or the local machine because recovery cannot restore `/content` or process memory.

Poll with `job_status` or `watch_job`. Use `job_logs` when progress stalls. A heartbeat only proves the wrapper is alive; use application progress and logs to judge real progress.

Saved watchers and lifetime timers are recovered when Codex restarts. If a host shutdown or security software stopped a watcher, call `watch_job` again. Use `recovery_status` to inspect bounded recovery state and `recover_session(confirm_reallocate=true)` for an explicitly approved manual recovery.

For expensive work, recommend checkpointing outputs to persistent storage. Colab VMs and `/content` are ephemeral.

## Finish and recover

Download or save required outputs before cleanup. Ensure managed transfers have completed or were safely cancelled. Disable SSH if active, get confirmation before `stop_job` or `stop_session`, and verify the session is absent afterward.

Read [policy-and-runtime.md](references/policy-and-runtime.md) for constraints and [recovery.md](references/recovery.md) when work fails.
