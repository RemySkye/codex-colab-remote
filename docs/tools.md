# MCP tool reference

Codex calls these tools automatically; users normally describe the desired outcome in plain language.

Every parameter has a machine-readable description and range. Choice fields are constrained in the MCP schema: accelerators are `cpu`, `t4`, `l4`, `g4`, `h100`, `a100`, `v5e-1`, or `v6e-1`; languages are `python`, `r`, or `julia`. `runtime_version` accepts `latest` or `YYYY.MM`.

## Setup and sessions

- `doctor`, `credential_status`, `authentication_instructions`
- `get_config`, `set_config`
- `list_sessions`, `create_session`, `session_status`, `set_session_lifetime`, `stop_session`
- `recovery_status`, `recover_session`

`create_session` uses `high_ram=true` or `false`. `set_config` uses `default_high_ram`. L4, G4, H100, v5e-1, and v6e-1 automatically override `false` because those accelerators require High-RAM; CPU, T4, and A100 keep the selected value. Creating or reallocating a runtime requires explicit cost acknowledgement.

Every successful `create_session` result includes a validated raw `session_url` for the attached Colab webpage. It is copy-paste-only: show it exactly in a fenced code block and have the user paste the entire URL into the browser address bar. Do not make it a Markdown link or open it through browser automation because encoding its fragment opens a disconnected scratchpad. Do not click Colab's normal **Connect** button; report an attachment failure instead. Use the attached page when the user needs to add a Colab Secret or enable its Notebook access toggle. Secret values remain user-entered in Colab and are not returned by the tool. The official CLI cannot list or toggle Secrets directly.

## Local secret broker

- `prepare_local_secret`
- `list_local_secrets`
- `enable_local_secrets`, `disable_local_secrets`

`prepare_local_secret` returns a command the user runs in their own trusted terminal. The value is entered twice through a masked prompt and stored in Windows Credential Manager, macOS Keychain, or a Linux Secret Service keyring. MCP arguments, responses, metadata, and session-grant files contain names only.

`list_local_secrets` refreshes the configured alias names without returning values. `enable_local_secrets` exposes selected aliases as environment variables to `execute_code`, `execute_file`, `terminal_exec`, `start_job`, and `ssh_exec`. `disable_local_secrets` removes future access and clears the native kernel environment when possible; already-running jobs retain their inherited environment until stopped.

This broker is separate from Colab's website Secrets. The official CLI cannot import, list, or toggle those website entries. Code running in an enabled session can read its environment, so enable only the aliases the workload needs and disable them afterward. There is deliberately no MCP tool to read, set, replace, rename, or delete a value.

## Code and terminal

- `prepare_language`, `execute_code`, `execute_file`, `terminal_exec`
- `install_packages`, `restart_kernel`, `get_logs`, `session_url`

Use `execute_code` for native kernel code and `terminal_exec` for arbitrary Linux commands. `prepare_language` only switches to and verifies a native kernel; it never downloads a runtime.

## Jobs and notifications

- `start_job`, `job_status`, `job_logs`, `watch_job`, `stop_job`
- `notification_history`, `test_notification`

Jobs run under `tmux`, expose logs/exit status/heartbeat, may write JSON progress, and can stop the session at completion. Completion history is silent by default; desktop popups require both global and per-job opt-in. Recovery restarts only explicitly opted-in, restart-safe jobs.

## Files and transfers

- `upload_file`, `download_file`, `list_files`
- `start_upload`, `start_download`, `transfer_status`, `list_transfers`, `cancel_transfer`, `resume_transfer`

Managed transfers support folders, compression, 1–8 parallel chunks, checksums, bounded chunk retries, cancellation, and interruption-safe resume. Omitted compression and parallelism values use configuration defaults. Every local path must be within an approved root.

## Notebooks and Drive

- `create_notebook`, `read_notebook`
- `add_notebook_cell`, `edit_notebook_cell`, `delete_notebook_cell`, `move_notebook_cell`
- `run_notebook_cells`, `import_notebook`, `export_session_notebook`
- `mount_google_drive`, `complete_google_drive_mount`
- `list_drive_files`, `create_drive_folder`
- `save_to_drive`, `restore_from_drive`
- `move_drive_path`, `delete_drive_path`
- `save_notebook_to_drive`, `load_notebook_from_drive`

`mount_google_drive` creates `MyDrive/codex-colab` when it is missing. If it returns `authorization_required=true`, approve Google in the opened browser and then call `complete_google_drive_mount`; the plugin keeps that same official CLI process alive so it can finish credential propagation. Every `drive_path` is relative to the protected folder; absolute paths, traversal, symlink escapes, and access to other mounted Drive folders are rejected. Deletion requires `confirm=true`. The save and restore tools copy complete files or folders directly between the Colab VM and Drive, without routing data through the local PC.

Use fast `/content` storage for active training and periodically checkpoint important outputs into the returned Drive workspace. A save with no `drive_path` uses the configured checkpoint folder. The plugin does not force an autosave policy: Codex may configure framework checkpoint code when the user requests it. Drive mounting may require the user to complete an interactive Google authorization step in Colab. Never provide that authorization material to Codex.

## Optional SSH

- `ssh_requirements`, `enable_ssh`, `prepare_ssh_browser`, `register_ssh_manifest`
- `ssh_status`, `ssh_exec`, `ssh_upload`, `ssh_download`, `disable_ssh`

SSH requires separate acknowledgements and an ngrok token stored in Colab Secrets. Prefer `terminal_exec` and managed transfers unless the SSH/SCP protocol itself is necessary.
