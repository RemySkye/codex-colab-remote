---
name: operate-colab-remote
description: Provision and operate user-authorized Google Colab runtimes from Codex using Google's official CLI. Use for CPU, GPU, TPU, Python or best-effort Julia sessions; remote code and files; monitored jobs; notifications; logs; recovery; and cleanup.
---

# Operate Colab Remote

Use the `colab-remote` MCP tools. Do not read tokens, handle authorization codes, use Google Cloud ADC, or build an SSH/tunnel fallback.

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

## Long jobs

Use `start_job` for long commands. It provides tmux persistence, logs, heartbeat, exit status, optional JSON progress, and a detached local completion watcher. Programs may write JSON to the path in `CODEX_PROGRESS_FILE`.

Poll with `job_status` or `watch_job`. Use `job_logs` when progress stalls. A heartbeat only proves the wrapper is alive; use application progress and logs to judge real progress.

Saved watchers are recovered when Codex restarts. If Windows shutdown or security software stopped a watcher, call `watch_job` again.

For expensive work, recommend checkpointing outputs to persistent storage. Colab VMs and `/content` are ephemeral.

## Finish and recover

Download required outputs before cleanup. Get confirmation before `stop_job` or `stop_session`. Verify the session is absent afterward.

Read [policy-and-runtime.md](references/policy-and-runtime.md) for constraints and [recovery.md](references/recovery.md) when work fails.
