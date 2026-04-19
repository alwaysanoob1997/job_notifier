#!/usr/bin/env python3
"""Rasterize packaging/calendar-clock.svg to packaging/calendar-clock-1024.png (1024×1024).

Requires: pip install cairosvg (listed in requirements-build.txt).
"""
from pathlib import Path

try:
    import cairosvg
except ImportError as e:
    raise SystemExit(
        "cairosvg is required. Install build deps: pip install -r requirements-build.txt"
    ) from e

ROOT = Path(__file__).resolve().parents[1]
svg = ROOT / "packaging" / "calendar-clock.svg"
png = ROOT / "packaging" / "calendar-clock-1024.png"
if not svg.is_file():
    raise SystemExit(f"Missing {svg}")

cairosvg.svg2png(url=str(svg), write_to=str(png), output_width=1024, output_height=1024)
print(f"Wrote {png}")
