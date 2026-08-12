"""Draw the icon, and write it out as both SVG and ICO.

One description of the shape, two files. Drawing the ICO from the SVG would
need an SVG renderer, which would be a dependency; describing the shape twice
would mean the two drifting apart the first time either is touched.

Usage: ``python tools/make_icon.py``.

The mark is a canvas with one planned stroke across it: a rounded frame in the
foreground colour and a four-point polyline in the accent. Nothing else fits
into sixteen pixels and still reads.
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# The whole design lives in a 32 by 32 grid, so both outputs scale from the
# same numbers.
GRID = 32
INK = "#e7eaf0"
ACCENT = "#e3a04a"

FRAME_INSET = 2.6
FRAME_RADIUS = 6.0
FRAME_WIDTH = 2.6

# A route with two turns rather than a zigzag: a symmetric zigzag reads as the
# letter N at small sizes, and a path that turns reads as a path.
STROKE = ((8.0, 23.2), (8.0, 13.6), (17.4, 13.6), (24.4, 8.2))
STROKE_WIDTH = 3.4

# Drawn at eight times and reduced, because Pillow does not antialias strokes.
SUPERSAMPLE = 8
ICO_SIZES = (16, 32, 48, 256)


def render(size: int) -> Image.Image:
    """Return the icon at one size, antialiased."""
    scale = size * SUPERSAMPLE / GRID
    canvas = Image.new("RGBA", (size * SUPERSAMPLE, size * SUPERSAMPLE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (
            FRAME_INSET * scale,
            FRAME_INSET * scale,
            (GRID - FRAME_INSET) * scale,
            (GRID - FRAME_INSET) * scale,
        ),
        radius=FRAME_RADIUS * scale,
        outline=INK,
        width=max(1, round(FRAME_WIDTH * scale)),
    )
    draw.line(
        [(x * scale, y * scale) for x, y in STROKE],
        fill=ACCENT,
        width=max(1, round(STROKE_WIDTH * scale)),
        joint="curve",
    )
    for x, y in (STROKE[0], STROKE[-1]):
        radius = STROKE_WIDTH * scale / 2
        draw.ellipse(
            (x * scale - radius, y * scale - radius, x * scale + radius, y * scale + radius),
            fill=ACCENT,
        )
    return canvas.resize((size, size), Image.Resampling.LANCZOS)


def svg() -> str:
    """Return the same mark as SVG."""
    points = " ".join(f"{x} {y}" for x, y in STROKE)
    edge = GRID - 2 * FRAME_INSET
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {GRID} {GRID}"
     width="{GRID}" height="{GRID}">
  <title>penplan</title>
  <rect x="{FRAME_INSET}" y="{FRAME_INSET}" width="{edge}" height="{edge}" rx="{FRAME_RADIUS}"
        fill="none" stroke="{INK}" stroke-width="{FRAME_WIDTH}"/>
  <polyline points="{points}" fill="none" stroke="{ACCENT}" stroke-width="{STROKE_WIDTH}"
            stroke-linecap="round" stroke-linejoin="round"/>
</svg>
"""


def main() -> int:
    """Write both files and return a process exit code."""
    root = Path(__file__).resolve().parent.parent
    icon = root / "src" / "penplan" / "penplan.ico"
    largest = render(max(ICO_SIZES))
    largest.save(icon, format="ICO", sizes=[(size, size) for size in ICO_SIZES])
    logo = root / "docs" / "img" / "logo.svg"
    logo.parent.mkdir(parents=True, exist_ok=True)
    logo.write_text(svg(), encoding="utf-8")
    preview = root / "docs" / "img" / "logo.png"
    render(256).save(preview)
    print(f"wrote {icon} at {', '.join(str(size) for size in ICO_SIZES)}")
    print(f"wrote {logo} and {preview}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
