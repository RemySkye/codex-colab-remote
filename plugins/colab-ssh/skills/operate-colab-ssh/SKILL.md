---
name: operate-colab-ssh
description: Provision and operate user-authorized Google Colab runtimes from Codex. Prefer Google's official Colab CLI for CPU, T4, L4, G4, H100, A100, or TPU allocation; remote Python/notebook execution; files; logs; console access; built-in keep-alive; and cleanup. Use the secure SSH notebook bootstrap only as a fallback when the official CLI cannot satisfy a concrete requirement.
---

# Operate Google Colab

Prefer the official Google Colab CLI. On Windows, use `scripts/colab.ps1`; it discovers the configured WSL distribution, Linux user home, and CLI path and converts absolute Windows paths automatically. Read [policy-and-runtime.md](references/policy-and-runtime.md) before provisioning and [recovery.md](references/recovery.md) for long jobs or failures.

## Authenticate

Run a read-only command first:

```powershell
& '<plugin-root>\scripts\colab.ps1' sessions
```

If it is not authenticated, use the non-interactive handoff that works with Codex tools:

1. Resolve the plugin root and configured WSL distribution with `scripts/runtime.ps1`. Convert `scripts/start_colab_auth.sh` with `ConvertTo-ColabRemoteWslPath`, then run it through `wsl.exe -d <distro> -- bash <converted-path>`. It starts the CLI behind a private FIFO and prints the authorization URL.
2. Open that URL in the in-app browser and pause for the user to sign in and approve Colab access. Never ask for or handle their Google password.
3. When Google displays the one-time code, run `finish_colab_auth.ps1 -AuthorizationCode <code>`. The helper passes it over stdin to the waiting CLI, verifies the result, and cleans up temporary authorization files.
4. Run `sessions` again. The cached token remains in the user's WSL home; set its permission to `600`.

## Provision

Choose a meaningful unique session name and request exactly what the user wants:

```powershell
& '<plugin-root>\scripts\colab.ps1' new -s codex-train --gpu A100
& '<plugin-root>\scripts\colab.ps1' new -s codex-g4 --gpu G4
& '<plugin-root>\scripts\colab.ps1' new -s codex-cpu
```

Supported GPUs are `T4`, `L4`, `G4`, `H100`, and `A100`; supported TPUs are `v5e1` and `v6e1`. Availability depends on subscription, compute balance, quota, and current capacity. If the requested accelerator fails, report it and get acceptance before using a cheaper or different accelerator. Do not silently downgrade.

If the user says only "premium GPU," ask them to choose a model because it materially changes cost and capability. Summarize briefly: T4 is economical, L4 is a stronger general inference/training option, A100 is high-memory training, H100 is the fastest listed datacenter option, and G4 is the RTX Pro 6000 Blackwell class. Recommend one based on the workload, but do not allocate until accepted.

Immediately run `status -s <name>` and report the allocated hardware. Provisioning consumes compute units. Always stop sessions when finished.

## Execute and Inspect

Use the CLI commands directly:

- `exec -s <name> -f <local.py> --timeout <seconds>` runs a local Python file remotely. The default is only 30 seconds, so always set an explicit timeout based on the workload.
- `exec -s <name> -f <local.ipynb> --timeout <seconds>` runs notebook cells and writes an output notebook locally.
- `upload -s <name> <local> <remote>` and `download -s <name> <remote> <local>` move artifacts.
- `install -s <name> <packages...>` installs Python dependencies.
- `ls -s <name> <path>` lists remote files.
- `status -s <name>` shows hardware and execution state.
- `log -s <name>` shows structured history; `log -s <name> -o <file.ipynb>` archives it.
- Pipe commands into `console -s <name>` only when raw shell semantics are necessary; avoid an interactive console in an agent tool call.
- `url -s <name>` returns a browser URL for the same running session.

The official CLI launches its own supported background keep-alive daemon. Do not add notebook clickers, fake calculation loops, synthetic UI activity, or a custom idle-evasion heartbeat. The keep-alive prevents ordinary idle termination while the session is active, but it does not override maximum lifetime, compute exhaustion, quota, or backend reclamation.

The PowerShell wrapper automatically converts absolute Windows paths such as `C:\work\train.py` into WSL `/mnt/c/...` paths. Pass absolute local paths for `exec`, `upload`, `download`, image output, and log export.

## Long Jobs

Before starting expensive work:

1. Confirm the actual accelerator and estimated checkpoint destination.
2. Make the application write periodic restartable checkpoints under `/content/...`.
3. For attached execution, run `exec --timeout 86400` (or another justified bound) so output streams to Codex.
4. For disconnect-tolerant execution, create a restartable shell script that writes stdout, stderr, exit code, and application checkpoints under `/content/.codex-jobs/<job>/`; upload it; then launch it in remote tmux with a piped console command:

   ```powershell
   '<command>' | & '<plugin-root>\scripts\colab.ps1' console -s <session>
   ```

   The launch command should be `tmux new-session -d -s codex-<job> /content/<job>.sh`. Reconnect/status with a piped `tmux has-session -t codex-<job>; tail -n 200 ...` command, inspect structured CLI history with `log`, and stop with a piped `tmux send-keys -t codex-<job> C-c` followed by `tmux kill-session` if needed.
5. Use application progress such as steps, epochs, samples, or checkpoint timestamps. A keep-alive or process heartbeat is not training progress.
6. Download checkpoints periodically or, after explicit user interaction, mount Drive with `drivemount`.
7. Export the session log.

Treat all VM-local storage as ephemeral. A successful keep-alive does not make data durable.

## Recover and Clean Up

Run `sessions`, `status`, and `log` first. If the VM still exists, reconnect with the CLI and resume from its latest checkpoint. If it was reclaimed, create a new session, reinstall dependencies, restore the latest durable checkpoint, and resume.

When finished, download outputs and run:

```powershell
& '<plugin-root>\scripts\colab.ps1' log -s <name> --output <local.ipynb>
& '<plugin-root>\scripts\colab.ps1' stop -s <name>
```

Verify with `sessions` that no unintended VM remains allocated.

## SSH Fallback

Use the `colab-ssh` MCP tools only if the user specifically needs a raw SSH behavior the official CLI cannot provide. Call `prepare_session`, open/upload its generated notebook in Colab, run the cell, register the printed manifest, then use SSH tools. The bootstrap is idempotent on the same VM and preserves existing tmux jobs. It pins the host key and uses a short-lived key-only account.

The fallback requires an ngrok TCP token in Colab Secrets and may be restricted by Colab policy on free managed runtimes. Never disable host-key checking. `job_status` reports wrapper/tmux liveness, not application progress. Call `close_session` and disconnect the runtime when done.
