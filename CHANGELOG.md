# Changelog

## 0.6.7

- Automatically force High-RAM for L4, G4, H100, v5e-1, and v6e-1 allocations, even when a caller supplies `high_ram=false`.
- Replaced the notification boolean with `off`, `failures_only`, and `all` modes while safely migrating existing configuration.
- Added an enforced default limit of eight concurrent Colab sessions.
- Added managed-transfer compression, parallelism, and bounded retry defaults.
- Added a default Drive checkpoint folder used when save tools omit `drive_path`.
- Added a real `config.jsonc` with comments directly above every setting, safe comment/trailing-comma parsing, canonical comment regeneration, and automatic migration from `config.json`.

## 0.6.6

- Disabled desktop notification popups by default while retaining silent notification history.
- Made job completion popups require explicit per-job and global opt-in.
- Removed the duplicate legacy Windows tray balloon so an enabled notification produces only one toast.
- Updated diagnostics, installer flags, documentation, and tests for the quieter behavior.

## 0.6.5

- Fixed Codex MCP registration by replacing an unsupported `${__dirname}` placeholder with plugin-relative paths and an explicit plugin working directory.
- Made protocol tests execute the exact shipped `.mcp.json` launcher and added validation for unresolved placeholders, escaped paths, and missing manifest or marketplace targets.
- Updated agent guidance to probe a harmless deferred MCP tool before incorrectly reporting that Colab tools are unregistered.
- Prevented Windows Terminal popups from job monitors, session-lifetime timers, managed transfers, and the Drive mount launcher.
- Replaced the conflicting `CREATE_NO_WINDOW | DETACHED_PROCESS` combination with one shared cross-platform background-process launcher.
- Prefer `pythonw.exe` for Windows Python helpers, hide fallback processes explicitly, and keep all helper standard streams disconnected from MCP.
- Added unit coverage for every background helper plus a real Windows test that verifies the spawned process has no console window.

## 0.6.4

- Replaced the incomplete Drive authorization preflight with a persistent PTY worker that keeps Google's original `drivemount` process alive through user approval and VM credential propagation.
- Added `complete_google_drive_mount` so agents resume the exact pending mount instead of creating repeated authorization attempts.
- Blocked conflicting session commands while Drive authorization is pending and cancel the worker during session shutdown.
- Automatically repair an existing Colab CLI token to owner-only mode without reading its contents, eliminating the usual manual `chmod 600` repair.
- Added native POSIX PTY regression coverage plus expanded Drive, credential-permission, protocol, and cleanup tests.

## 0.6.3

- Fixed Google Drive mounting so first-use authorization returns immediately, opens Google's approval page, and never exposes authorization URLs, codes, or tokens to the agent.
- Prevented headless `colab drivemount` calls from blocking while waiting for `/dev/tty`; the mount now starts only after an authorization preflight succeeds.
- Verified the protected workspace itself instead of treating a partial `/content/drive` directory as a successful mount.
- Made terminal and Drive helpers reliable for long commands by staging them through the official Colab upload and execution commands.
- Added regression coverage for Drive authorization, protected workspace bootstrapping, and staged remote execution.

## 0.6.2

- Replaced duplicated PowerShell and shell installation logic with one cross-platform Python installer and two thin launchers.
- Expanded Google Drive support to general files, folders, checkpoints, models, datasets, and notebooks under the dedicated `MyDrive/codex-colab` workspace.
- Added Drive path traversal, sibling-folder, symlink, root-deletion, and confirmation protections without imposing an automatic-save policy.
- Made the shared installer safely update existing installations by handling Git/local marketplace differences, repairing stale registrations, verifying replacement installs, and preserving authentication and configuration.

## 0.6.1

- Added machine-readable descriptions and limits for every MCP tool parameter.
- Constrained accelerator, language, runtime-version, cell-type, path, timeout, and parallelism inputs.
- Standardized public accelerator values and renamed High-RAM inputs to `high_ram` and `default_high_ram`.
- Removed the obsolete external-download acknowledgement from native language switching.
- Improved MCP and skill guidance so agents choose the shortest safe workflow.
- Added protocol tests that prevent ambiguous or undocumented tool schemas from returning.
- Simplified the Linux/macOS one-line installer while preserving interactive OAuth input.
- Fixed the SSH Colab Secret name to `NGROK_AUTHTOKEN` instead of storing a configurable label.

## 0.6.0

- Added native Linux and macOS operation plus a portable `uv` MCP launcher and POSIX installer.
- Added GitHub-hosted Windows, Ubuntu, and macOS validation and real CLI installation checks.
- Added native Python, R, and Julia selection, High-RAM/runtime-version controls, and session lifetimes.
- Added notebook/Drive workflows, resumable parallel folder transfers, safe cancellation, and recovery.
- Added complete public documentation, troubleshooting, Wiki sources, and full-history secret scanning.

## 0.5.0

- Added an optional direct SSH terminal and SCP workflow through an ngrok TCP endpoint.
- Added explicit policy/public-tunnel acknowledgements, short-lived Ed25519 keys, host-key pinning, and automatic cleanup.
- Restricted SSH to an unprivileged key-only account with password, root, sudo, and forwarding disabled.
- Kept the ngrok token inside a user-authorized Colab Secret so Codex never receives it.
- Serialized per-session CLI operations so progress monitoring and interactive tools cannot race, and forced UTF-8-safe subprocess decoding on Windows.

## 0.4.0

- Replaced the SSH/tunnel design with typed tools around Google's official Colab CLI.
- Added accelerator and language defaults, measured high-RAM reporting, cost approval, and elapsed-compute warnings.
- Added persistent monitored jobs, JSON progress, logs, heartbeats, and Windows completion notifications.
- Disabled local file access by default and added OAuth2-only credential protections.
- Added a pinned installer, protocol/unit/security checks, dependency auditing, CodeQL, SBOM, and checksummed releases.
