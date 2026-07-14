#!/usr/bin/env bash
set -euo pipefail
umask 077

auth_dir="${XDG_RUNTIME_DIR:-$HOME/.cache}/codex-colab-remote/auth"
input="$auth_dir/input"
output="$auth_dir/output"
result="$auth_dir/rc"

if [[ ! -p "$input" ]]; then
  echo "No waiting Colab authentication process. Run start_colab_auth.sh first." >&2
  exit 2
fi

IFS= read -r authorization_code
printf '%s\n' "$authorization_code" > "$input"
unset authorization_code

for _ in $(seq 1 30); do
  [[ -f "$result" ]] && break
  sleep 1
done
if [[ ! -f "$result" ]]; then
  echo "Timed out waiting for the Colab CLI authentication process." >&2
  exit 3
fi

cat "$output"
rc=$(cat "$result")
if [[ -f "$HOME/.config/colab-cli/token.json" ]]; then
  chmod 600 "$HOME/.config/colab-cli/token.json"
fi
rm -f "$input" "$output" "$result" "$auth_dir/pid"
rmdir "$auth_dir" 2>/dev/null || true
exit "$rc"
