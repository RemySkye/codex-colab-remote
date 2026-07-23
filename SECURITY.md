# Security

Report vulnerabilities privately through this repository's GitHub Security Advisories. Do not put credentials, authorization codes, session URLs, or logs containing personal data in an issue.

## Guarantees

- The plugin forces Colab CLI OAuth2 and removes Google ADC environment variables.
- MCP tools never read or return the cached OAuth token. They inspect only file presence and permissions.
- Local API-key values are entered only through a masked terminal prompt and stored by the operating-system credential manager. MCP tools expose alias names and session grants only.
- Secret values are staged through owner-only temporary files, never MCP arguments or command-line arguments, and exact raw/common-encoded values are redacted from returned output.
- Authentication happens in the user's terminal; there is no code-handoff helper.
- Local files are inaccessible unless their parent directory is explicitly allowlisted.
- Drive tools are limited to `MyDrive/codex-colab`; they reject absolute/traversal paths, symlink escapes, and sources or destinations elsewhere in the Drive mount.
- The `codex-colab` root cannot be deleted, and deleting an item inside it requires explicit confirmation.
- Destructive cleanup and compute reallocation require confirmation.
- Common Google secrets and hardcoded user paths are blocked by validation and CI.
- SSH is disabled by default and needs separate acknowledgements for Colab policy and public-tunnel risk.
- SSH uses a fresh Ed25519 key, strict host-key pinning, key-only login, and an unprivileged account. Password, root, sudo, agent, X11, and TCP forwarding are disabled.
- The ngrok token is read only inside Colab from the user-authorized `NGROK_AUTHTOKEN` Secret. It is not passed to or returned by Codex, and the temporary ngrok config is deleted after startup.

## Boundaries

Codex and the Colab CLI run as your local OS user. Any process running as that same user could technically access files that user can access. The plugin reduces accidental exposure but cannot defend against an already-compromised host account, Windows WSL distribution, dependency, or Google account.

Remote commands intentionally have full control of the allocated Colab VM. Keep sensitive local directories out of `allowed_local_roots`, review untrusted code, and use a separate Google account for stronger isolation.

Google's Drive mount is visible to code running on the Colab VM. The typed Drive tools enforce the `MyDrive/codex-colab` boundary, and the bundled agent guidance forbids inspecting its parent. This protects normal tool use and reduces accidental damage, but it is not a security boundary against malicious code with full VM control. Use a separate Google account when adversarial code must be isolated from personal Drive data.

An enabled local secret is an environment variable available to the selected kernel or process. Arbitrary code in that execution context can read it, transform it to bypass output redaction, or transmit it over the network. The broker keeps values out of MCP configuration and ordinary responses; it is not a sandbox against untrusted code. Enable only the aliases required by a trusted workload and disable them afterward.

Optional SSH creates a public ngrok TCP endpoint and trusts Google Colab and ngrok as service providers. The SSH user is intentionally not root; use typed Colab tools for privileged work. Anyone who obtains the local private key while the tunnel is active could access that runtime. The plugin cannot protect a key from a compromised local OS account.

## Credential storage and revocation

The Colab CLI stores its OAuth token at `~/.config/colab-cli/token.json` (inside WSL on Windows); the installer changes it to mode `600`. It is excluded from this repository and must never be copied into a bug report.

To remove the local token, run this yourself on Linux/macOS:

```bash
rm -f ~/.config/colab-cli/token.json
```

On Windows:

```powershell
wsl -d Ubuntu -- sh -lc 'rm -f ~/.config/colab-cli/token.json'
```

Also revoke the application's access from your Google Account security page if a credential might be exposed. Then authenticate again from a trusted terminal.

If SSH material might be exposed, call `disable_ssh` or stop the Colab session immediately and rotate the ngrok token in the ngrok dashboard. Local retry state is deleted only after remote revocation or VM termination is confirmed. Never include the private key, ngrok token, endpoint, or complete SSH logs in a bug report.

## Release safety

Core uv and Colab CLI installers are version-pinned. Python, R, and Julia use native Colab kernels rather than external language installers. CI runs cross-platform unit/protocol tests, repository secret checks, dependency audit, CodeQL, and release checksum generation. Review version updates before merging.
