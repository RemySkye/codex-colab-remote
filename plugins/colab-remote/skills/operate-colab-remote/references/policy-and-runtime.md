# Policy and runtime constraints

- Use Google's official Colab CLI with OAuth2 only. Never use Google Cloud ADC or service-account credentials.
- Authentication is user-driven. Codex may show the command but must not receive the authorization code or token.
- Colab capacity, runtime duration, idle behavior, accelerator access, and usage limits are controlled by Google and the user's plan. Do not promise a particular GPU or uninterrupted runtime.
- A heartbeat keeps the monitored process observable; it does not bypass Colab idle or usage policies.
- The current CLI has no high-RAM request flag. Measure allocated memory and report it honestly.
- Julia runs best-effort inside the Python-based VM after an explicit, user-approved Juliaup LTS installation.
- `/content` is ephemeral. Checkpoint valuable work to approved persistent storage and download results before stopping.
- Local file access is disabled unless a directory is explicitly allowlisted. Keep the allowlist narrow.
- GPU and TPU work can consume paid compute units or limited quota. Always obtain cost acknowledgement before allocation.
- Google says SSH shells are disallowed on free managed runtimes without a positive compute-unit balance and may be terminated. Optional SSH therefore requires the user to confirm a paid plan with positive compute units.
- Optional SSH creates a public ngrok TCP endpoint. It must stay disabled unless explicitly requested and acknowledged, use only the pinned host key and short-lived key pair, and be closed when work finishes.
- The ngrok token stays in a user-authorized Colab Secret. Never ask the user to paste it into Codex, return it, or store it in plugin configuration.
- SSH is intentionally unprivileged. Use the typed official-CLI tools for privileged setup; never add sudo/root access or weaken password, host-key, or forwarding restrictions.
