# Development and testing

Install `uv` and run:

```bash
python plugins/colab-remote/scripts/validate_repo.py
uv sync --project plugins/colab-remote
uv run --project plugins/colab-remote ruff check plugins/colab-remote
uv run --project plugins/colab-remote python -m unittest discover -s plugins/colab-remote/tests -v
```

On Windows, also run the mocked installer test:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tests\installer-smoke.ps1
```

On Linux/macOS:

```bash
bash -n install.sh
bash install.sh --help
```

GitHub Actions repeats repository, lint, unit, MCP protocol, and installer checks on `windows-latest`, `ubuntu-latest`, and `macos-latest`. Linux/macOS jobs install and invoke the real official Colab CLI without authenticating or allocating compute. Security CI runs Gitleaks, dependency audit, and CodeQL.

Live tests are intentionally explicit because they use an account and may consume quota. Use the installer's smoke-test flag or `python tests/live-colab-smoke.py --acknowledge-cost` for a temporary CPU runtime; never put a personal OAuth token in a repository or normal CI variable. Both paths verify that the session disappears after cleanup.

Before release, update the version/changelog, validate the Codex plugin and skill, scan the full Git history for secrets, verify all hosted jobs, and confirm no Colab sessions remain.
