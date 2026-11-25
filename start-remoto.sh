#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if ! command -v cloudflared >/dev/null 2>&1; then
  printf '%s\n' 'cloudflared não encontrado. Consulte o README para instalar.' >&2
  exit 1
fi
exec python3 app.py --tunnel "$@"
