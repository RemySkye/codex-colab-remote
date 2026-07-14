# Recovery playbook

## Official CLI session

1. Run `colab sessions`, `colab status -s NAME`, and `colab log -s NAME`.
2. If the VM exists, reconnect and resume from the latest application checkpoint.
3. If the kernel is wedged, use `colab restart-kernel -s NAME`; this resets Python state but retains the VM.
4. If the VM is gone, create a new named session, reinstall dependencies, restore a durable checkpoint, and resume.
5. Stop abandoned or completed sessions so they do not keep consuming compute units.

## Tunnel failed, VM still present

1. Inspect the bootstrap cell output and `/tmp/codex-ngrok.log`.
2. Rerun the same generated bootstrap cell. It preserves the existing `codex` home, tmux jobs, logs, and checkpoints.
3. Register the newly printed endpoint. Do not reuse an unverified endpoint.

## VM was replaced or reclaimed

1. Prepare a new session, which creates a new SSH key and nonce.
2. Select and connect a new Colab runtime.
3. Run the new notebook bootstrap and register its manifest.
4. Restore code and the most recent durable checkpoint.
5. Restart using a resumable command.

## Job diagnosis

- `job_status` shows tmux state, exit status, and the age of the real process heartbeat.
- `job_logs` separates stdout and stderr.
- A stale heartbeat is evidence to inspect the process, not evidence that Colab itself will remain allocated.

## Checkpoint rule

Treat `/content` and `/home/codex` as ephemeral. Copy valuable results off the VM throughout the run, not just at the end.
