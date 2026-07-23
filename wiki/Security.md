# Security

Local API keys can be stored in Windows Credential Manager, macOS Keychain, or Linux Secret Service. MCP sees names only. Enabled runtime code can read its environment, so use secrets only with trusted workloads and disable them afterward.

Colab OAuth credentials stay in the official CLI's storage and are never parsed or returned by the plugin. Local files are denied unless their folder is explicitly approved.

Report vulnerabilities privately through GitHub Security Advisories. Read the full [security policy](https://github.com/RemySkye/codex-colab-remote/blob/main/SECURITY.md).
