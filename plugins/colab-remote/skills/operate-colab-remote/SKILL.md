---
name: operate-colab-remote
description: Provision and operate user-authorized Google Colab runtimes from Codex using Google's official CLI. Use for CPU, GPU, TPU, High-RAM, runtime-version, native Python, R, or Julia sessions; remote code and large files; monitored jobs; terminal access; notifications; logs; recovery; and cleanup.
---

# Operate Colab Remote

## Local secrets

When a workload needs an API key, call `list_local_secrets`. If its alias is missing, call `prepare_local_secret` and ask the user to run the returned command in their own trusted terminal; input is masked and must never be pasted into Codex or chat. Call `list_local_secrets` again after the user finishes, then `enable_local_secrets` with only the required aliases. The aliases become environment variables for kernel code, terminal commands, persistent jobs, and approved local files.

The returned user command is the cross-platform `colab-remote secrets add NAME` interface. If the user needs to inspect or change behavior, direct them to `colab-remote --help`, `colab-remote config describe NAME`, or `colab-remote config set NAME VALUE`; do not ask them to edit plugin cache files.

Never ask for, accept, print, rename, edit, or delete a secret value. The MCP tools expose names only and have no value-management operation. Code in an enabled session can technically read its environment, so enable the minimum aliases and call `disable_local_secrets` after the workload. Disabling affects future commands and the native kernel; already-running jobs retain inherited variables until stopped.

Read `require_secret_enable_approval` from `get_config`. Its default is `false`, so required aliases may be enabled automatically. When it is `true`, ask immediately before enabling and pass `acknowledge_access=true`. Disabling aliases never needs approval. Users manage values themselves with `colab-remote secrets add`, `colab-remote secrets list`, and `colab-remote secrets remove`.

The local broker is separate from Colab website Secrets. The official CLI still cannot list or toggle website Secrets. Use the attached browser page only when the user specifically chooses native Colab Secret access.

Use the `colab-remote` MCP tools. Do not read tokens, handle authorization codes, or use Google Cloud ADC. Use the typed official-CLI tools, including `terminal_exec` for arbitrary Linux commands.

Codex may defer MCP tool definitions until a relevant tool is requested. Do not claim that Colab tools are unregistered merely because an abbreviated task tool list does not show them. First attempt the harmless `doctor`, `credential_status`, or `list_sessions` tool. Report a registration problem only if that MCP call is unavailable or the MCP server returns a startup error.

## Safe startup

1. Call `doctor` and `credential_status`.
2. If authentication is missing, call `authentication_instructions`. The user must run the command and complete Google sign-in in their own trusted terminal. Never ask them to paste a code into Codex.
3. Call `get_config`. Confirm the requested accelerator, language, High-RAM setting, runtime version, maximum lifetime, and local-file roots. Prefer `runtime_version="latest"` unless the user needs a reproducible older image. A maximum lifetime of zero disables the plugin timer.
4. Read `require_cost_acknowledgement`. When it is `true`, explain the quota/compute warning, get explicit approval, and pass `acknowledge_cost=true`. When it is `false`, the user has configured standing authorization, so create sessions without a per-session prompt. Always report the allocated hardware and lifetime afterward.

Use only these accelerator values: `cpu`, `t4`, `l4`, `g4`, `h100`, `a100`, `v5e-1`, and `v6e-1`. Use only `python`, `r`, or `julia` for language. Availability depends on the user's plan and capacity. L4, G4, H100, v5e-1, and v6e-1 automatically force High-RAM even when `high_ram=false`; CPU, T4, and A100 preserve the requested High-RAM setting. Report measured memory from the result.

`create_session` returns a raw, copy-paste-only `session_url` for the exact CLI-created runtime. Show it exactly inside a fenced code block and ask the user to copy the entire URL into the browser address bar. Never format it as a Markdown link or open it through browser automation: either can encode the `datalabBackendUrl` fragment and open a disconnected scratchpad. The user must not click Colab's normal **Connect** button; if the page remains disconnected, stop and report that attachment failed. When a workload needs a known Colab Secret such as `HF_TOKEN`, use the attached page and ask the user to add the secret or enable its Notebook access toggle in the Secrets sidebar. The user must enter the value only in Colab and then tell Codex it is ready. Never ask for, display, list, copy, rename, edit, or delete secret values. The official CLI cannot list Secrets or change their toggles.

## Multiple sessions

Use a unique descriptive `session_name` for every runtime. The plugin supports up to `max_concurrent_sessions` active runtimes, eight by default. Call `list_sessions` before allocating when names or capacity are uncertain. Every execution, file, notebook, job, Drive, secret, transfer, and recovery tool takes a session name, so keep work isolated by sending each operation to its intended session. Sessions may use different hardware, languages, RAM modes, runtime versions, and lifetimes.

## Execute work

- Use `execute_code` for short Python, R, or Julia code.
- Python, R, and Julia use Colab's native kernels (`python3`, `ir`, and `julia`). Python is the default. `create_session` verifies the selected kernel; call `prepare_language` only to switch or recheck a kernel. It never installs or downloads a language.
- Use `execute_file` for an approved local script. Local access is off by default and restricted to `allowed_local_roots`.
- Use `terminal_exec` for arbitrary Linux shell commands through the official Colab CLI. Use tmux or `start_job` for persistent commands.
- Use `upload_file` and `download_file` for single files up to 64 MiB. For larger files or folders, use `start_upload` or `start_download`, save the returned `transfer_id`, then call `transfer_status`. Omitted compression and parallelism values use the configured defaults. Managed transfers support bounded chunk retries, resume, and checksum verification. Use `cancel_transfer(confirm=true)` for cooperative cancellation and `resume_transfer` to continue completed chunks. Never broaden approved roots without explicit confirmation through `set_config`.
- Use `create_notebook`, `read_notebook`, and the cell tools to create and edit local nbformat 4 notebooks. `run_notebook_cells` runs selected cells on the session and saves outputs locally. Use `import_notebook` for a validated local copy and `export_session_notebook` to export session history.
- Use `mount_google_drive` to create the dedicated `MyDrive/codex-colab` workspace. On first use it may return `authorization_required=true` while keeping Google's official mount alive in a PTY. Ask the user only to approve the page that opened, then call `complete_google_drive_mount`; do not call `mount_google_drive` repeatedly and never ask for or handle an authorization code. Treat the returned `workspace_path` as the only Drive path agents may use. Never inspect, list, read, write, move, or delete its parent through `terminal_exec`, `execute_code`, a job, or notebook code.
- Use `list_drive_files` and `create_drive_folder` to organize the workspace. Use `save_to_drive` and `restore_from_drive` for general files or folders, and the notebook-specific save/load tools for validated notebooks. Use `move_drive_path` only within the workspace. Call `delete_drive_path` only for a specific user-requested item and set `confirm=true`; the workspace root cannot be deleted.
- Active training should normally run under fast ephemeral `/content` storage. When the user wants persistence, configure their framework or training code to write periodic checkpoints to the exact `workspace_path` returned by `mount_google_drive`, or call `save_to_drive` at requested milestones. Do not invent or force an autosave schedule.
- Every `drive_path` is relative to `MyDrive/codex-colab`; do not prefix it with `MyDrive` or `/content/drive`. The official Drive mount may require the user to complete an interactive Google authorization step. Never request, capture, or log that authorization material.
- Use `install_packages` only after reviewing package names with the user when they are untrusted or expensive to install.

## Long jobs

Use `start_job` for long commands. It provides tmux persistence, logs, heartbeat, exit status, optional JSON progress, and a detached local completion watcher. Programs may write JSON to the path in `CODEX_PROGRESS_FILE`.

Desktop popups use `notification_mode`: `off`, `failures_only`, or `all`. It defaults to `off`. Do not change the mode or set `notify_on_completion=true` unless the user explicitly asks for popups. Use `notification_history` for silent completion records.

Use `stop_session_on_finish=true` only when the user wants the VM released when that job ends. Otherwise keep the session available for follow-up work. Use `set_session_lifetime` for a hard upper bound that survives MCP restarts.

Automatic recovery is opt-in because it reallocates compute. Create the session with `recovery_enabled=true`, choose a bounded `max_recovery_attempts`, and set `recover_on_runtime_loss=true` only on jobs whose commands are safe to restart. Recovery recipes are saved in owner-only local state. Checkpoint important outputs to Drive or the local machine because recovery cannot restore `/content` or process memory.

Poll with `job_status` or `watch_job`. Use `job_logs` when progress stalls. A heartbeat only proves the wrapper is alive; use application progress and logs to judge real progress.

Saved watchers and lifetime timers are recovered when Codex restarts. If a host shutdown or security software stopped a watcher, call `watch_job` again. Use `recovery_status` to inspect bounded recovery state and `recover_session(confirm_reallocate=true)` for an explicitly approved manual recovery.

For expensive work, recommend checkpointing outputs to persistent storage. Colab VMs and `/content` are ephemeral.

## Finish and recover

Download or save required outputs before cleanup. Ensure managed transfers have completed or were safely cancelled. Get confirmation before `stop_job` or `stop_session`, and verify the session is absent afterward.

Read [policy-and-runtime.md](references/policy-and-runtime.md) for constraints and [recovery.md](references/recovery.md) when work fails.
