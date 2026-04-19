#!/usr/bin/env bash
# Build packaging/LinkedInJobs.icns from packaging/calendar-clock-1024.png (macOS only).
# Uses sips + iconutil; no extra dependencies beyond the OS.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PNG="${ROOT}/packaging/calendar-clock-1024.png"
OUT_ICNS="${ROOT}/packaging/LinkedInJobs.icns"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "error: This script must run on macOS." >&2
  exit 1
fi

if [[ ! -f "$PNG" ]]; then
  echo "error: Missing master PNG: $PNG" >&2
  echo "  Regenerate from SVG: python scripts/render_app_icon_png.py" >&2
  exit 1
fi

ICONSET="$(mktemp -d "${TMPDIR:-/tmp}/linkedinjobs.iconset.XXXXXX")"
cleanup() { rm -rf "$ICONSET"; }
trap cleanup EXIT

sips -z 16 16     "$PNG" --out "${ICONSET}/icon_16x16.png"       >/dev/null
sips -z 32 32     "$PNG" --out "${ICONSET}/icon_16x16@2x.png"   >/dev/null
sips -z 32 32     "$PNG" --out "${ICONSET}/icon_32x32.png"      >/dev/null
sips -z 64 64     "$PNG" --out "${ICONSET}/icon_32x32@2x.png"  >/dev/null
sips -z 128 128   "$PNG" --out "${ICONSET}/icon_128x128.png"    >/dev/null
sips -z 256 256   "$PNG" --out "${ICONSET}/icon_128x128@2x.png" >/dev/null
sips -z 256 256   "$PNG" --out "${ICONSET}/icon_256x256.png"    >/dev/null
sips -z 512 512   "$PNG" --out "${ICONSET}/icon_256x256@2x.png" >/dev/null
sips -z 512 512   "$PNG" --out "${ICONSET}/icon_512x512.png"    >/dev/null
sips -z 1024 1024 "$PNG" --out "${ICONSET}/icon_512x512@2x.png" >/dev/null

iconutil -c icns "$ICONSET" -o "$OUT_ICNS"
echo "Wrote ${OUT_ICNS}"
