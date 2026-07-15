#!/usr/bin/env bash
set -euo pipefail

REPOSITORY="RemySkye/codex-colab-remote"
MARKETPLACE="colab-remote"
PLUGIN="colab-remote"
UV_VERSION="0.11.28"
COLAB_CLI_VERSION="0.6.0"
UV_INSTALLER_SHA256="b7b3fe80cad1142a2a5794050b7db7b3291d1bac1423b0732571dd9366e8ca8b"

DEFAULT_ACCELERATOR="cpu"
DEFAULT_LANGUAGE="python"
DEFAULT_RUNTIME_VERSION="latest"
DEFAULT_MAX_LIFETIME="0"
PREFER_HIGH_RAM="false"
NOTIFICATIONS_ENABLED="true"
SSH_TUNNEL_ENABLED="false"
SSH_SECRET_NAME="NGROK_AUTHTOKEN"
SKIP_AUTHENTICATION="false"
RUN_SMOKE_TEST="false"
STATE_ROOT="${HOME}/.codex/colab-remote"
ALLOWED_ROOTS=()

usage() {
  cat <<'EOF'
Usage: ./install.sh [options]

Options:
  --default-accelerator VALUE   cpu, t4, l4, g4, h100, a100, v5e1, or v6e1
  --default-language VALUE      python, r, or julia
  --runtime-version VALUE       latest or YYYY.MM
  --max-lifetime MINUTES        0-1440; 0 disables the plugin timer
  --high-ram                    Prefer High-RAM allocations
  --allowed-root PATH           Allow local file access under PATH; repeatable
  --disable-notifications       Store notification history without desktop popups
  --enable-ssh                  Enable the optional ngrok SSH feature
  --ssh-secret-name NAME        Colab Secret name; default NGROK_AUTHTOKEN
  --skip-authentication         Install now and authenticate later
  --run-smoke-test              Briefly allocate a CPU runtime and verify cleanup
  --state-root PATH             Override ~/.codex/colab-remote
  -h, --help                    Show this help
EOF
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

step() {
  printf '\n==> %s\n' "$*"
}

require_value() {
  [ "$#" -ge 2 ] || fail "Missing value for $1"
}

lowercase() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --default-accelerator)
      require_value "$@"; DEFAULT_ACCELERATOR="$(lowercase "$2")"; shift 2 ;;
    --default-language)
      require_value "$@"; DEFAULT_LANGUAGE="$(lowercase "$2")"; shift 2 ;;
    --runtime-version)
      require_value "$@"; DEFAULT_RUNTIME_VERSION="$(lowercase "$2")"; shift 2 ;;
    --max-lifetime)
      require_value "$@"; DEFAULT_MAX_LIFETIME="$2"; shift 2 ;;
    --high-ram)
      PREFER_HIGH_RAM="true"; shift ;;
    --allowed-root)
      require_value "$@"; ALLOWED_ROOTS+=("$2"); shift 2 ;;
    --disable-notifications)
      NOTIFICATIONS_ENABLED="false"; shift ;;
    --enable-ssh)
      SSH_TUNNEL_ENABLED="true"; shift ;;
    --ssh-secret-name)
      require_value "$@"; SSH_SECRET_NAME="$2"; shift 2 ;;
    --skip-authentication)
      SKIP_AUTHENTICATION="true"; shift ;;
    --run-smoke-test)
      RUN_SMOKE_TEST="true"; shift ;;
    --state-root)
      require_value "$@"; STATE_ROOT="$2"; shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      fail "Unknown option: $1" ;;
  esac
done

case "$DEFAULT_ACCELERATOR" in
  cpu|t4|l4|g4|h100|a100|v5e1|v6e1) ;;
  *) fail "Unsupported default accelerator: $DEFAULT_ACCELERATOR" ;;
esac
case "$DEFAULT_LANGUAGE" in
  python|r|julia) ;;
  *) fail "Unsupported default language: $DEFAULT_LANGUAGE" ;;
esac
[[ "$DEFAULT_RUNTIME_VERSION" =~ ^(latest|20[0-9]{2}\.[0-9]{2})$ ]] ||
  fail "Runtime version must be latest or YYYY.MM"
[[ "$DEFAULT_MAX_LIFETIME" =~ ^[0-9]+$ ]] || fail "Max lifetime must be a number"
[ "$DEFAULT_MAX_LIFETIME" -le 1440 ] || fail "Max lifetime cannot exceed 1440 minutes"
[[ "$SSH_SECRET_NAME" =~ ^[A-Za-z][A-Za-z0-9_]{2,63}$ ]] ||
  fail "SSH secret name must contain only letters, numbers, and underscores"

case "$(uname -s)" in
  Linux|Darwin) ;;
  *) fail "Use install.ps1 on Windows; install.sh supports Linux and macOS" ;;
esac

command -v curl >/dev/null 2>&1 || fail "curl is required"
command -v codex >/dev/null 2>&1 ||
  fail "Codex CLI was not found. Install Codex, reopen the terminal, and rerun this installer."

TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/colab-remote.XXXXXX")"
cleanup() {
  rm -rf -- "$TEMP_DIR"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

UV_BIN="$(command -v uv 2>/dev/null || true)"
if [ -z "$UV_BIN" ] && [ -x "$HOME/.local/bin/uv" ]; then
  UV_BIN="$HOME/.local/bin/uv"
fi
if [ -z "$UV_BIN" ]; then
  step "Installing pinned uv ${UV_VERSION}"
  UV_INSTALLER="$TEMP_DIR/uv-install.sh"
  curl --proto '=https' --tlsv1.2 -fsSLo "$UV_INSTALLER" \
    "https://astral.sh/uv/${UV_VERSION}/install.sh"
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s  %s\n' "$UV_INSTALLER_SHA256" "$UV_INSTALLER" | sha256sum -c -
  else
    [ "$(shasum -a 256 "$UV_INSTALLER" | awk '{print $1}')" = "$UV_INSTALLER_SHA256" ] ||
      fail "The downloaded uv installer checksum did not match"
  fi
  sh "$UV_INSTALLER"
  UV_BIN="$HOME/.local/bin/uv"
fi
[ -x "$UV_BIN" ] || fail "uv installation did not produce an executable"

step "Installing Google's official Colab CLI ${COLAB_CLI_VERSION}"
"$UV_BIN" tool install --force "google-colab-cli==${COLAB_CLI_VERSION}"
COLAB_BIN="$HOME/.local/bin/colab"
[ -x "$COLAB_BIN" ] || fail "Google Colab CLI installation failed"
"$COLAB_BIN" version

step "Adding or refreshing the Codex plugin marketplace"
if ! codex plugin marketplace add "$REPOSITORY"; then
  codex plugin marketplace upgrade "$MARKETPLACE" ||
    fail "Could not add or refresh the ${MARKETPLACE} marketplace"
fi

step "Installing or updating Colab Remote"
codex plugin add "${PLUGIN}@${MARKETPLACE}" || fail "Codex could not install Colab Remote"

ROOTS_FILE="$TEMP_DIR/allowed-roots.txt"
: > "$ROOTS_FILE"
for root in "${ALLOWED_ROOTS[@]}"; do
  [ -d "$root" ] || fail "Allowed root must be a directory: $root"
  case "$root" in *$'\n'*) fail "Allowed roots cannot contain newlines" ;; esac
  (cd "$root" && pwd -P) >> "$ROOTS_FILE"
done

step "Saving owner-only Colab Remote defaults"
mkdir -p -- "$STATE_ROOT"
chmod 700 "$STATE_ROOT"
export COLAB_REMOTE_STATE_ROOT="$STATE_ROOT"
export COLAB_REMOTE_DEFAULT_ACCELERATOR="$DEFAULT_ACCELERATOR"
export COLAB_REMOTE_DEFAULT_LANGUAGE="$DEFAULT_LANGUAGE"
export COLAB_REMOTE_RUNTIME_VERSION="$DEFAULT_RUNTIME_VERSION"
export COLAB_REMOTE_MAX_LIFETIME="$DEFAULT_MAX_LIFETIME"
export COLAB_REMOTE_PREFER_HIGH_RAM="$PREFER_HIGH_RAM"
export COLAB_REMOTE_NOTIFICATIONS="$NOTIFICATIONS_ENABLED"
export COLAB_REMOTE_SSH_ENABLED="$SSH_TUNNEL_ENABLED"
export COLAB_REMOTE_SSH_SECRET_NAME="$SSH_SECRET_NAME"
export COLAB_REMOTE_ROOTS_FILE="$ROOTS_FILE"
"$UV_BIN" run --no-project --python 3.12 python - <<'PY'
import json
import os
from pathlib import Path

roots_file = Path(os.environ["COLAB_REMOTE_ROOTS_FILE"])
roots = sorted(set(filter(None, roots_file.read_text(encoding="utf-8").splitlines())))
config = {
    "distro": "Ubuntu",
    "default_accelerator": os.environ["COLAB_REMOTE_DEFAULT_ACCELERATOR"],
    "default_language": os.environ["COLAB_REMOTE_DEFAULT_LANGUAGE"],
    "default_runtime_version": os.environ["COLAB_REMOTE_RUNTIME_VERSION"],
    "prefer_high_ram": os.environ["COLAB_REMOTE_PREFER_HIGH_RAM"] == "true",
    "default_timeout_seconds": 3600,
    "compute_warning_minutes": 60,
    "default_max_lifetime_minutes": int(os.environ["COLAB_REMOTE_MAX_LIFETIME"]),
    "notifications_enabled": os.environ["COLAB_REMOTE_NOTIFICATIONS"] == "true",
    "require_cost_acknowledgement": True,
    "allowed_local_roots": roots,
    "ssh_tunnel_enabled": os.environ["COLAB_REMOTE_SSH_ENABLED"] == "true",
    "ssh_secret_name": os.environ["COLAB_REMOTE_SSH_SECRET_NAME"],
}
path = Path(os.environ["COLAB_REMOTE_STATE_ROOT"]) / "config.json"
temporary = path.with_suffix(".tmp")
temporary.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
temporary.chmod(0o600)
temporary.replace(path)
path.chmod(0o600)
PY

if [ "$SKIP_AUTHENTICATION" = "false" ]; then
  step "Authenticating directly with Google Colab"
  printf '%s\n' "Follow the Google sign-in link. Paste a one-time code only into this terminal."
  if [ -r /dev/tty ]; then
    env -u GOOGLE_APPLICATION_CREDENTIALS -u CLOUDSDK_CONFIG \
      "$COLAB_BIN" --auth oauth2 sessions </dev/tty
  else
    env -u GOOGLE_APPLICATION_CREDENTIALS -u CLOUDSDK_CONFIG \
      "$COLAB_BIN" --auth oauth2 sessions
  fi
  TOKEN_PATH="$HOME/.config/colab-cli/token.json"
  [ ! -f "$TOKEN_PATH" ] || chmod 600 "$TOKEN_PATH"
fi

if [ "$RUN_SMOKE_TEST" = "true" ]; then
  step "Creating a temporary CPU runtime for verification"
  SESSION="codex-install-smoke-${RANDOM}-${RANDOM}"
  CREATED="false"
  smoke_cleanup() {
    if [ "$CREATED" = "true" ]; then
      env -u GOOGLE_APPLICATION_CREDENTIALS -u CLOUDSDK_CONFIG \
        "$COLAB_BIN" --auth oauth2 stop -s "$SESSION" || return 1
      LISTING="$(env -u GOOGLE_APPLICATION_CREDENTIALS -u CLOUDSDK_CONFIG \
        "$COLAB_BIN" --auth oauth2 sessions 2>&1)" || return 1
      case "$LISTING" in *"$SESSION"*) return 1 ;; esac
    fi
  }
  env -u GOOGLE_APPLICATION_CREDENTIALS -u CLOUDSDK_CONFIG \
    "$COLAB_BIN" --auth oauth2 new -s "$SESSION"
  CREATED="true"
  if ! printf '%s\n' 'print("COLAB_REMOTE_INSTALL_OK")' | \
    env -u GOOGLE_APPLICATION_CREDENTIALS -u CLOUDSDK_CONFIG \
      "$COLAB_BIN" --auth oauth2 exec -s "$SESSION" --timeout 120; then
    smoke_cleanup || true
    fail "Remote smoke-test execution failed"
  fi
  smoke_cleanup || fail "Smoke-test cleanup could not be verified; stop session ${SESSION} manually"
fi

printf '\nColab Remote is installed. Restart Codex or start a new task.\n'
if [ "$SSH_TUNNEL_ENABLED" = "true" ]; then
  printf '%s\n' "SSH is optional. Add NGROK_AUTHTOKEN to Colab Secrets before enabling a tunnel."
fi
if [ "$SKIP_AUTHENTICATION" = "true" ]; then
  printf '%s\n' "Authenticate later with: umask 077; env -u GOOGLE_APPLICATION_CREDENTIALS -u CLOUDSDK_CONFIG ~/.local/bin/colab --auth oauth2 sessions; chmod 600 ~/.config/colab-cli/token.json"
fi
