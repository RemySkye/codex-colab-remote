# Changelog

## Unreleased

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
