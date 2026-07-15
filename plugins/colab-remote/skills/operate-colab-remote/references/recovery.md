# Recovery

1. Call `doctor`, `credential_status`, and `list_sessions`.
2. If OAuth is missing or expired, give `authentication_instructions`; the user reauthenticates in their terminal.
3. If allocation failed, report the exact CLI error and offer CPU or a smaller accelerator. Do not silently allocate a different tier.
4. If a job stalled, inspect `job_status` and `job_logs`. Distinguish stale heartbeat, application error, and lost runtime.
5. If the runtime vanished, create a new approved session and restore the latest checkpoint. Ephemeral files cannot be recovered.
6. If Julia is missing after a restart, rerun `prepare_language` with approval.
7. Before retrying an expensive command, explain expected quota/compute impact.
8. Stop abandoned sessions after user confirmation and verify cleanup.
