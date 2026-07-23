# Tools

Colab Remote provides tools for configuration/authentication, CPU/GPU/TPU sessions, native Python/R/Julia, Linux terminal commands, jobs, notifications, resumable files/folders, notebook editing, Google Drive, recovery, cleanup, and optional SSH/SCP.

The local secret broker stores values in the operating-system credential manager through a masked user-run prompt. Codex can list alias names and enable or disable them per session, but MCP cannot read, set, rename, or delete values.

Google Drive tools create and use only `MyDrive/codex-colab`. They can list and create folders; save and restore files, folders, checkpoints, datasets, models, and notebooks; move items; and perform confirmed deletion without accessing other Drive folders.

See the complete [MCP tool reference](https://github.com/RemySkye/codex-colab-remote/blob/main/docs/tools.md).
