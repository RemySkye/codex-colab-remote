# MCP tool reference

Codex calls these tools automatically; users normally describe the desired outcome in plain language.

## Setup and sessions

- `doctor`, `credential_status`, `authentication_instructions`
- `get_config`, `set_config`
- `list_sessions`, `create_session`, `session_status`, `set_session_lifetime`, `stop_session`
- `recovery_status`, `recover_session`

## Code and terminal

- `prepare_language`, `execute_code`, `execute_file`, `terminal_exec`
- `install_packages`, `restart_kernel`, `get_logs`, `session_url`

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
- `mount_google_drive`, `save_notebook_to_drive`, `load_notebook_from_drive`

Drive mounting may require the user to complete an interactive Google authorization step in Colab.

## Optional SSH

- `ssh_requirements`, `enable_ssh`, `prepare_ssh_browser`, `register_ssh_manifest`
- `ssh_status`, `ssh_exec`, `ssh_upload`, `ssh_download`, `disable_ssh`

SSH requires separate acknowledgements and an ngrok token stored in Colab Secrets. Prefer `terminal_exec` and managed transfers unless the SSH/SCP protocol itself is necessary.
