#!/usr/bin/env sh
set -eu

command -v python3 >/dev/null 2>&1 || {
  printf '%s\n' 'ERROR: Python 3.11 or newer is required.' >&2
  exit 1
}
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' || {
  printf '%s\n' 'ERROR: Python 3.11 or newer is required.' >&2
  exit 1
}

installer=''
temporary=''
if [ -f "$0" ]; then
  candidate=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/install.py
  [ ! -f "$candidate" ] || installer=$candidate
fi

cleanup() {
  [ -z "$temporary" ] || rm -f -- "$temporary"
}
trap cleanup EXIT HUP INT TERM

if [ -z "$installer" ]; then
  command -v curl >/dev/null 2>&1 || {
    printf '%s\n' 'ERROR: curl is required to download install.py.' >&2
    exit 1
  }
  temporary=$(mktemp "${TMPDIR:-/tmp}/colab-remote-install.XXXXXX.py")
  curl --proto '=https' --tlsv1.2 -fsSLo "$temporary" \
    'https://raw.githubusercontent.com/RemySkye/codex-colab-remote/main/install.py'
  installer=$temporary
fi

python3 "$installer" "$@"
