#!/usr/bin/env bash
set -euo pipefail

if [[ ! -p /tmp/colab-auth-in ]]; then
  echo "No waiting Colab authentication process. Run start_colab_auth.sh first." >&2
  exit 2
fi

IFS= read -r authorization_code
printf '%s\n' "$authorization_code" > /tmp/colab-auth-in
unset authorization_code

for _ in $(seq 1 30); do
  [[ -f /tmp/colab-auth-rc ]] && break
  sleep 1
done
if [[ ! -f /tmp/colab-auth-rc ]]; then
  echo "Timed out waiting for the Colab CLI authentication process." >&2
  exit 3
fi

cat /tmp/colab-auth-out
rc=$(cat /tmp/colab-auth-rc)
rm -f /tmp/colab-auth-in /tmp/colab-auth-out /tmp/colab-auth-rc /tmp/colab-auth-pid
exit "$rc"
