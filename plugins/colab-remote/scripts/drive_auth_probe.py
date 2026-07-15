"""Check Colab Drive authorization without mounting or exposing credentials."""

from __future__ import annotations

import argparse
import json
from urllib.parse import urlparse

from colab_cli.auth import AuthProvider, get_credentials
from colab_cli.common import state
from colab_cli.utils import get_status_code


RESULT_MARKER = "CODEX_DRIVE_AUTH="


def _response_json(response) -> dict:
    """Decode Google's XSSI-prefixed JSON response."""
    payload = response.text.split("\n", 1)[-1]
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise RuntimeError("Google returned an invalid Drive authorization response")
    return value


def _authorization_url(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (
        hostname == "google.com" or hostname.endswith(".google.com")
    ):
        raise RuntimeError("Google returned an unexpected Drive authorization URL")
    return value


def probe(session_name: str) -> dict[str, object]:
    """Run the official CLI's Drive credential dry-run without its TTY wait."""
    state.auth_provider = AuthProvider.OAUTH2
    session = state.store.get(session_name)
    if session is None:
        raise RuntimeError(f"Unknown Colab session: {session_name}")

    url = (
        f"{state.client.colab_domain}/tun/m/credentials-propagation/"
        f"{session.endpoint}"
    )
    params = {
        "authuser": "0",
        "authtype": "dfs_ephemeral",
        "version": "2",
        "dryrun": "true",
        "propagate": "true",
        "record": "false",
    }
    credentials = get_credentials(
        state.client_oauth_config, provider=state.auth_provider
    )
    token_response = credentials.request("GET", url, params=params)
    if get_status_code(token_response) != 200:
        raise RuntimeError("Google Drive authorization preflight failed")
    token = _response_json(token_response).get("token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("Google Drive authorization preflight returned no token")

    dry_run = credentials.request(
        "POST",
        url,
        params=params,
        headers={"x-goog-colab-token": token},
        files={"file_id": (None, "empty.ipynb")},
    )
    if get_status_code(dry_run) != 200:
        raise RuntimeError("Google Drive authorization check failed")
    data = _response_json(dry_run)
    authorized = bool(data.get("success"))
    return {
        "authorized": authorized,
        "authorization_url": (
            None
            if authorized
            else _authorization_url(data.get("unauthorized_redirect_uri"))
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True)
    arguments = parser.parse_args()
    result = probe(arguments.session)
    print(RESULT_MARKER + json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
