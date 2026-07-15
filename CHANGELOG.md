# Changelog

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
