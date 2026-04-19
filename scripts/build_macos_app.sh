#!/usr/bin/env bash
# Build LinkedInJobs.app for macOS. Requires Darwin (run on a Mac, not WSL/Linux).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "error: This script must run on macOS (Darwin)." >&2
  echo "  PyInstaller produces a native .app for the host OS; cross-building macOS bundles from WSL is not supported here." >&2
  exit 1
fi

if [[ -d "${ROOT}/.venv" ]]; then
  # shellcheck source=/dev/null
  source "${ROOT}/.venv/bin/activate"
fi

python -m pip install -r "${ROOT}/requirements.txt" -r "${ROOT}/requirements-build.txt"

bash "${ROOT}/scripts/macos_app_icon_png_to_icns.sh"

python -m PyInstaller --noconfirm "${ROOT}/packaging/LinkedInJobs.spec"

echo ""
if [[ -d "${ROOT}/dist/LinkedInJobs.app" ]]; then
  echo "Built: ${ROOT}/dist/LinkedInJobs.app"
  echo "If Gatekeeper blocks the app, right-click it → Open (first launch only)."
else
  echo "warning: expected ${ROOT}/dist/LinkedInJobs.app — onedir output is ${ROOT}/dist/LinkedInJobs" >&2
fi
