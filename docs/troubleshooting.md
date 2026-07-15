# Troubleshooting

Start by asking Codex to call `doctor`, `credential_status`, and `list_sessions`.

## Codex cannot find the plugin

Refresh the marketplace, reinstall the plugin, then restart Codex or start a new task:

```text
codex plugin marketplace upgrade colab-remote
codex plugin add colab-remote@colab-remote
```

## Authentication is missing or expired

Rerun the OAuth command printed by `authentication_instructions`. Do not use `gcloud` ADC and do not paste the authorization code into Codex. On Windows the command runs inside WSL.

## A requested GPU/TPU or High-RAM mode is unavailable

Capacity and eligible combinations change by plan and region. Retry later or choose a smaller accelerator. Check `session_status` for the measured runtime and memory rather than assuming the request was honored.

## Local file access is denied

Add only the required project folder to `allowed_local_roots` using `set_config` with confirmation. Paths are native Windows paths on Windows and normal POSIX paths on Linux/macOS.

## A large transfer was interrupted

Call `transfer_status`, then `resume_transfer`. Cancel with confirmation if the transfer is no longer wanted. Failed/cancelled chunk state remains for resume; completed temporary chunks are cleaned.

## No desktop popup appears

Call `test_notification` and inspect its reported backend. Windows notifications may be blocked by Focus Assist; macOS may require notification permission; Linux needs `notify-send` and a graphical notification service. `notification_history` works even without a popup backend.

## Runtime disappeared

Colab can reclaim VMs. Check `recovery_status`. Automatic recovery works only when it was enabled in advance and cannot restore `/content` or process memory. Reallocate manually only after acknowledging additional compute usage.

## SSH does not connect

Confirm SSH is enabled in configuration, the Colab Secret is named correctly and notebook access is allowed, the account can create ngrok TCP endpoints, and the session still exists. Do not weaken host-key checking. Normal terminal work does not require SSH.
