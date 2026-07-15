# Architecture

```text
Codex
  └─ MCP stdio → portable Python server
                  ├─ official Colab CLI → Google Colab runtime
                  ├─ local owner-only state and approved files
                  ├─ desktop notification backend
                  └─ optional OpenSSH client → ngrok TCP → Colab sshd
```

The MCP server and tests run on Windows, Linux, and macOS. On Linux/macOS it invokes the Colab CLI directly. On Windows the server runs natively but invokes the CLI inside WSL2 because Google does not provide a native Windows Colab CLI.

Normal code, terminal commands, packages, files, logs, notebooks, and jobs travel through Google's official CLI. The compatibility wrapper only fills gaps in CLI 0.6.0 for native kernel, runtime-version, and High-RAM selection.

Local state lives in `~/.codex/colab-remote` by default. It contains configuration, job/lease metadata, notifications, transfer checkpoints, and optional short-lived SSH keys. The directory is restricted to the current user. Colab OAuth credentials remain in the CLI's separate configuration directory and are never parsed by the server.

## Main dependencies

- Codex plugin and MCP support
- Python 3.12 through `uv`
- `google-colab-cli==0.6.0`
- `mcp>=1.20,<2`
- WSL2/Ubuntu on Windows only
- Optional: OpenSSH client and ngrok TCP service for true SSH

Desktop notifications use Windows PowerShell toast APIs, `osascript` on macOS, or `notify-send` on Linux when available. Notification history remains available when a desktop backend cannot display a popup.
