# MCP tool reference

Codex calls these tools automatically; users normally describe the desired outcome in plain language.

Every parameter has a machine-readable description and range. Choice fields are constrained in the MCP schema: accelerators are `cpu`, `t4`, `l4`, `g4`, `h100`, `a100`, `v5e-1`, or `v6e-1`; languages are `python`, `r`, or `julia`. `runtime_version` accepts `latest` or `YYYY.MM`.

## Setup and sessions

- `doctor`, `credential_status`, `authentication_instructions`
- `get_config`, `set_config`
- `list_sessions`, `create_session`, `session_status`, `set_session_lifetime`, `stop_session`
- `recovery_status`, `recover_session`

`create_session` uses `high_ram=true` or `false`. `set_config` uses `default_high_ram`. Creating or reallocating a runtime requires explicit cost acknowledgement.

## Code and terminal

- `prepare_language`, `execute_code`, `execute_file`, `terminal_exec`
- `install_packages`, `restart_kernel`, `get_logs`, `session_url`

Use `execute_code` for native kernel code and `terminal_exec` for arbitrary Linux commands. `prepare_language` only switches to and verifies a native kernel; it never downloads a runtime.

## Jobs and notifications

- `start_job`, `job_status`, `job_logs`, `watch_job`, `stop_job`
- `notification_history`, `test_notification`

Jobs run under `tmux`, expose logs/exit status/heartbeat, may write JSON progress, and can notify or stop the session at completion. Recovery restarts only explicitly opted-in, restart-safe jobs.

## Files and transfers

- `upload_file`, `download_file`, `list_files`
- `start_upload`, `start_download`, `transfer_status`, `list_transfers`, `cancel_transfer`, `resume_transfer`

Managed transfers support folders, compression, 1–8 parallel chunks, checksums, cancellation, and interruption-safe resume. Every local path must be within an approved root.

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

Use fast `/content` storage for active training and periodically checkpoint important outputs into the returned Drive workspace. The plugin does not force an autosave policy: Codex may configure framework checkpoint code when the user requests it. Drive mounting may require the user to complete an interactive Google authorization step in Colab. Never provide that authorization material to Codex.

## Optional SSH

- `ssh_requirements`, `enable_ssh`, `prepare_ssh_browser`, `register_ssh_manifest`
- `ssh_status`, `ssh_exec`, `ssh_upload`, `ssh_download`, `disable_ssh`

SSH requires separate acknowledgements and an ngrok token stored in Colab Secrets. Prefer `terminal_exec` and managed transfers unless the SSH/SCP protocol itself is necessary.
