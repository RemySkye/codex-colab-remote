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
