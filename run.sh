#!/usr/bin/env bash
# Quick launcher for the LinkedIn Automation web app.
# Usage:
#   ./run.sh                # start on 127.0.0.1:8000
#   ./run.sh --port 9000    # override port
#   HOST=0.0.0.0 ./run.sh   # override host via env
#
# Any extra arguments are forwarded to uvicorn.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ -f ".venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
else
  echo "warning: .venv not found at $SCRIPT_DIR/.venv — using system python" >&2
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

exec python -m uvicorn app.main:app --host "$HOST" --port "$PORT" "$@"
