#!/usr/bin/env bash
set -euo pipefail

rm -f /tmp/colab-auth-in /tmp/colab-auth-out /tmp/colab-auth-rc /tmp/colab-auth-pid
mkfifo /tmp/colab-auth-in
nohup bash -c '
  export PATH="$HOME/.local/bin:$PATH"
  exec 3<>/tmp/colab-auth-in
  colab sessions <&3 > /tmp/colab-auth-out 2>&1
  echo $? > /tmp/colab-auth-rc
' >/dev/null 2>&1 &
echo $! > /tmp/colab-auth-pid
sleep 2
cat /tmp/colab-auth-out
