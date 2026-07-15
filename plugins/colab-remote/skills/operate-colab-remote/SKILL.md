---
name: operate-colab-remote
description: Provision and operate user-authorized Google Colab runtimes from Codex using Google's official CLI, with optional direct key-only SSH. Use for CPU, GPU, TPU, Python or best-effort Julia sessions; remote code and files; monitored jobs; terminal access; notifications; logs; recovery; and cleanup.
---

# Operate Colab Remote

Use the `colab-remote` MCP tools. Do not read tokens, handle authorization codes, or use Google Cloud ADC. Prefer the typed official-CLI tools; use optional SSH only when the user explicitly requests direct terminal access.

## Safe startup

1. Call `doctor` and `credential_status`.
2. If authentication is missing, call `authentication_instructions`. The user must run the command and complete Google sign-in in their own trusted terminal. Never ask them to paste a code into Codex.
3. Call `get_config`. Confirm the requested accelerator, language, high-RAM preference, and local-file roots.
4. Explain the quota/compute warning and get explicit approval before `create_session(..., acknowledge_cost=true)`.

Supported accelerators are CPU, T4, L4, G4, H100, A100, TPU v5e-1, and TPU v6e-1. Availability depends on the user's Colab plan and capacity. High RAM is not a Colab CLI allocation flag; report the measured RAM and clearly label the preference as unfulfilled when applicable.

## Execute work

- Use `execute_code` for short Python or Julia code.
- Python is native. Before Julia, call `prepare_language`; installation uses Julia's official bootstrap and needs explicit user approval.
- Use `execute_file` for an approved local script. Local access is off by default and restricted to `allowed_local_roots`.
- Use `upload_file`, `download_file`, and `list_files` for data. Never broaden approved roots without explicit confirmation through `set_config`.
- Use `install_packages` only after reviewing package names with the user when they are untrusted or expensive to install.

## Optional SSH terminal

1. Call `ssh_requirements`. Explain that Google may terminate SSH on free managed runtimes without a positive compute-unit balance and that ngrok creates a public TCP endpoint.
2. The user must add `NGROK_AUTHTOKEN` to Colab Secrets and enable notebook access. Never ask for or accept the token in Codex.
3. Enable `ssh_tunnel_enabled` through `set_config(..., confirm_sensitive_change=true)` only after explicit user approval.
4. Call `enable_ssh` with both `acknowledge_colab_policy=true` and `acknowledge_public_tunnel=true` only after the user confirms both warnings.
5. Use `ssh_status`, `ssh_exec`, `ssh_upload`, and `ssh_download`; `/content/codex-ssh` is the writable SSH workspace. SSH is unprivileged by design, so use typed official-CLI tools for privileged setup instead of weakening SSH.
6. Call `disable_ssh(confirm=true)` when terminal access is no longer needed. `stop_session` also attempts SSH cleanup.

Do not disable host-key checking, expose a password, grant sudo/root, reveal a private key, or copy the ngrok token out of Colab Secrets.

## Long jobs

Use `start_job` for long commands. It provides tmux persistence, logs, heartbeat, exit status, optional JSON progress, and a detached local completion watcher. Programs may write JSON to the path in `CODEX_PROGRESS_FILE`.

Poll with `job_status` or `watch_job`. Use `job_logs` when progress stalls. A heartbeat only proves the wrapper is alive; use application progress and logs to judge real progress.

Saved watchers are recovered when Codex restarts. If Windows shutdown or security software stopped a watcher, call `watch_job` again.

For expensive work, recommend checkpointing outputs to persistent storage. Colab VMs and `/content` are ephemeral.

## Finish and recover

Download required outputs before cleanup. Disable SSH if active, get confirmation before `stop_job` or `stop_session`, and verify the session is absent afterward.

Read [policy-and-runtime.md](references/policy-and-runtime.md) for constraints and [recovery.md](references/recovery.md) when work fails.
