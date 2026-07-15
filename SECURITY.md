# Security

Report vulnerabilities privately through this repository's GitHub Security Advisories. Do not put credentials, authorization codes, session URLs, or logs containing personal data in an issue.

## Guarantees

- The plugin forces Colab CLI OAuth2 and removes Google ADC environment variables.
- MCP tools never read or return the cached OAuth token. They inspect only file presence and permissions.
- Authentication happens in the user's terminal; there is no code-handoff helper.
- Local files are inaccessible unless their parent directory is explicitly allowlisted.
- Destructive cleanup and external Julia installation require confirmation.
- Common Google secrets, private keys, old tunnel code, and hardcoded user paths are blocked by validation and CI.

## Boundaries

Codex and the Colab CLI run as your local OS user. Any process running as that same user could technically access files that user can access. The plugin reduces accidental exposure but cannot defend against an already-compromised Windows account, WSL distribution, dependency, or Google account.

Remote commands intentionally have full control of the allocated Colab VM. Keep sensitive local directories out of `allowed_local_roots`, review untrusted code, and use a separate Google account for stronger isolation.

## Credential storage and revocation

The Colab CLI stores its OAuth token inside WSL at `~/.config/colab-cli/token.json`; the installer changes it to mode `600`. It is excluded from this repository and must never be copied into a bug report.

To remove the local token, run this yourself:

```powershell
wsl -d Ubuntu -- sh -lc 'rm -f ~/.config/colab-cli/token.json'
```

Also revoke the application's access from your Google Account security page if a credential might be exposed. Then authenticate again from a trusted terminal.

## Release safety

Core uv and Colab CLI installers are version-pinned. Julia's official bootstrap is optional, user-approved, and follows its LTS channel rather than a fixed artifact. CI runs unit/protocol tests, repository secret checks, dependency audit, CodeQL, and release checksum generation. Review version updates before merging.
