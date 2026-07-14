#!/usr/bin/env bash
set -euo pipefail
umask 077

auth_dir="${XDG_RUNTIME_DIR:-$HOME/.cache}/codex-colab-remote/auth"
mkdir -p "$auth_dir"
chmod 700 "$auth_dir"
export CODEX_COLAB_AUTH_DIR="$auth_dir"

rm -f "$auth_dir/input" "$auth_dir/output" "$auth_dir/rc" "$auth_dir/pid"
mkfifo "$auth_dir/input"
chmod 600 "$auth_dir/input"
nohup bash -c '
  set -euo pipefail
  umask 077
  export PATH="$HOME/.local/bin:$PATH"
  exec 3<>"$CODEX_COLAB_AUTH_DIR/input"
  set +e
  colab sessions <&3 > "$CODEX_COLAB_AUTH_DIR/output" 2>&1
  rc=$?
  set -e
  if [[ -f "$HOME/.config/colab-cli/token.json" ]]; then
    chmod 600 "$HOME/.config/colab-cli/token.json"
  fi
  echo "$rc" > "$CODEX_COLAB_AUTH_DIR/rc"
' >/dev/null 2>&1 &
echo $! > "$auth_dir/pid"
sleep 2
cat "$auth_dir/output"
