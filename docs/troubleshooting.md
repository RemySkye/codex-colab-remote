# Troubleshooting

Start by asking Codex to call `doctor`, `credential_status`, and `list_sessions`.

## Codex cannot find the plugin

Rerun the normal installer, then restart Codex or start a new task. The installer distinguishes Git and local marketplaces, repairs stale registrations, reinstalls the plugin, and verifies that Codex sees it. Do not run `marketplace upgrade` manually against a local marketplace; only Git marketplaces support that operation.

If Codex can read the Colab skill but reports that tools such as `create_session` are unavailable, first confirm that `codex mcp list` shows `colab-remote` as enabled. Version 0.6.5 and newer use plugin-relative MCP paths and test the exact shipped launcher. After installing or updating, start a new task so Codex registers the server's tools.

## Authentication is missing or expired

Rerun the OAuth command printed by `authentication_instructions`. Do not use `gcloud` ADC and do not paste the authorization code into Codex. On Windows the command runs inside WSL.

The plugin automatically changes an existing Colab CLI token to owner-only mode when needed, so users should not normally need to run `chmod 600 ~/.config/colab-cli/token.json` manually.

## A requested GPU/TPU or High-RAM mode is unavailable

Capacity and eligible combinations change by plan and region. Retry later or choose a smaller accelerator. Check `session_status` for the measured runtime and memory rather than assuming the request was honored.

## Local file access is denied

Add only the required project folder to `allowed_local_roots` using `set_config` with confirmation. Paths are native Windows paths on Windows and normal POSIX paths on Linux/macOS.

## A large transfer was interrupted

Call `transfer_status`, then `resume_transfer`. Cancel with confirmation if the transfer is no longer wanted. Failed/cancelled chunk state remains for resume; completed temporary chunks are cleaned.

## No desktop popup appears

Desktop popups are disabled by default. If you intentionally want them, set `notification_mode` to `failures_only` or `all`, request `notify_on_completion=true` for the job, then call `test_notification`. Windows notifications may be blocked by Focus Assist; macOS may require notification permission; Linux needs `notify-send` and a graphical notification service. `notification_history` works without popups.

## Terminal windows appear while background work is running

Update to version 0.6.5 or newer. Older Windows releases combined incompatible process-creation flags for job monitors, lifetime timers, managed transfers, and Drive mounting. Current releases use `pythonw.exe` when available and a shared no-window launcher. A popup whose command references an older cached plugin version came from a helper that was already running before the update; it ends with that helper, and newly started helpers use the corrected launcher.

## Runtime disappeared

Colab can reclaim VMs. Check `recovery_status`. Automatic recovery works only when it was enabled in advance and cannot restore `/content` or process memory. Reallocate manually only after acknowledging additional compute usage.

## Google Drive is not mounted

Call `mount_google_drive`. If it opens Google approval and returns `authorization_required=true`, approve the page and ask Codex to call `complete_google_drive_mount`. Do not repeatedly start new mount attempts: the completion tool resumes the original official CLI process and lets it propagate credentials into the VM. Codex must never receive an authorization code or token. All plugin-managed files live under `MyDrive/codex-colab`; paths outside it are intentionally rejected.

For training performance, read active datasets and checkpoints from `/content` and save periodic checkpoints to Drive. Avoid workloads that repeatedly open thousands of small files directly on the mounted Drive filesystem.

## SSH does not connect

Confirm SSH is enabled in configuration, the Colab Secret is named correctly and notebook access is allowed, the account can create ngrok TCP endpoints, and the session still exists. Do not weaken host-key checking. Normal terminal work does not require SSH.
