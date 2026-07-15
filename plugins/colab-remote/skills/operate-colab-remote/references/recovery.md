# Recovery

1. Call `doctor`, `credential_status`, and `list_sessions`.
2. If OAuth is missing or expired, give `authentication_instructions`; the user reauthenticates in their terminal.
3. If allocation failed, report the exact CLI error and offer CPU or a smaller accelerator. Do not silently allocate a different tier.
4. If a job stalled, inspect `job_status` and `job_logs`. Distinguish stale heartbeat, application error, and lost runtime.
5. If automatic recovery was pre-enabled, call `recovery_status`. The monitor may recreate the same requested runtime and restart only jobs that opted in with `recover_on_runtime_loss=true`, up to the configured attempt limit.
6. For manual recovery, explain that reallocation can consume compute units and call `recover_session(confirm_reallocate=true)` only after approval. Commands must be safe to run again; duplicate external side effects remain possible.
7. Restore outputs from a local notebook, resumable transfer, or a checkpoint saved under `MyDrive/codex-colab`. VM memory and uncheckpointed ephemeral files cannot be recovered.
8. If a native Python, R, or Julia kernel fails after a restart, rerun `prepare_language`. If Colab still reports the kernel unavailable, stop the unusable session and report the image/capacity error; do not install a replacement runtime silently.
9. Before retrying an expensive command, explain expected quota/compute impact.
10. For a failed or cancelled managed transfer, inspect `transfer_status` and use `resume_transfer`; checksum verification protects the final result. Do not discard completed chunks unless the user no longer needs recovery.
11. Stop abandoned sessions after user confirmation and verify cleanup.
12. If SSH fails, call `ssh_status`. Confirm the paid-plan/positive-balance requirement, Colab Secret existence and notebook access, ngrok TCP availability, and local OpenSSH tools without requesting the token. When the CLI cannot read Secrets, use `prepare_ssh_browser` and `register_ssh_manifest` through the attached Colab notebook.
13. If host-key verification changes, do not bypass it. Call `disable_ssh`, revoke the old state, and create a fresh tunnel.
14. If an SSH key or endpoint may be exposed, disable SSH immediately and tell the user to rotate the ngrok token.
