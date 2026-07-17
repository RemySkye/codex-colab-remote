# Architecture

```text
Codex
  └─ MCP stdio → portable Python server
                  ├─ official Colab CLI → Google Colab runtime
                  ├─ local owner-only state and approved files
                  ├─ desktop notification backend
                  └─ optional OpenSSH client → ngrok TCP → Colab sshd
```

One Python installer contains the shared Windows, Linux, and macOS setup logic; the PowerShell and shell files only launch it. On Linux/macOS the installed MCP server invokes the Colab CLI directly. On Windows the server runs natively but invokes the CLI inside WSL2 because Google does not provide a native Windows Colab CLI.

Normal code, terminal commands, packages, files, logs, notebooks, and jobs travel through Google's official CLI. The compatibility wrapper only fills gaps in CLI 0.6.0 for native kernel, runtime-version, and High-RAM selection.

Google Drive is mounted through the official CLI at `/content/drive`. Drive-facing MCP tools create and resolve every path beneath `/content/drive/MyDrive/codex-colab`, reject traversal and symlink escapes, and refuse sources or destinations in sibling Drive folders. General files and folders move directly between `/content` and this persistent workspace. Because user code runs with control of the Colab VM, the skill also instructs agents never to inspect the parent Drive mount through arbitrary code or terminal commands.

Local state lives in `~/.codex/colab-remote` by default. It contains configuration, job/lease metadata, notifications, transfer checkpoints, and optional short-lived SSH keys. The directory is restricted to the current user. Colab OAuth credentials remain in the CLI's separate configuration directory and are never parsed by the server.

## Main dependencies

- Codex plugin and MCP support
- Python 3.11 or newer for installation; Python 3.12 through `uv` for the MCP environment
- `google-colab-cli==0.6.0`
- `mcp>=1.20,<2`
- WSL2/Ubuntu on Windows only
- Optional: OpenSSH client and ngrok TCP service for true SSH

Google Cloud Storage (GCS) is not a current dependency; bucket support remains a future roadmap item.

Opt-in desktop notifications use one Windows toast, `osascript` on macOS, or `notify-send` on Linux when available. Popups are disabled by default, while notification history remains available.
