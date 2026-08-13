"""Annotate a screenshot with the order the wizard asks for things.

Usage::

    python tools/make_calibration_figure.py SHOT docs/img/calibration.png

The markers are drawn over a real screenshot of the canvas in this repository,
so the figure shows the actual thing being pointed at rather than a diagram of
one. Positions are given on the command line as ``label:x,y`` or as
``label:x,y,x,y`` for a bracket around a group.
"""

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

ACCENT = (227, 160, 74)
INK = (18, 20, 26)
RADIUS = 15
LINE = 3


def font(size: int) -> ImageFont.ImageFont:
    """Return a bold face for the markers, falling back to whatever exists."""
    try:
        return ImageFont.truetype("segoeuib.ttf", size)
    except OSError:
        return ImageFont.load_default(size)


def marker(draw: ImageDraw.ImageDraw, label: str, x: int, y: int) -> None:
    """Draw one numbered dot."""
    draw.ellipse(
        (x - RADIUS, y - RADIUS, x + RADIUS, y + RADIUS), fill=ACCENT, outline=INK, width=2
    )
    draw.text((x, y), label, fill=INK, font=font(17), anchor="mm")


def bracket(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int]) -> None:
    """Outline a group of controls that are captured one after another."""
    draw.rectangle(box, outline=ACCENT, width=LINE)


def main() -> int:
    """Write the annotated figure and return a process exit code."""
    parser = argparse.ArgumentParser(description="Annotate a calibration screenshot")
    parser.add_argument("shot", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--point", action="append", default=[], metavar="LABEL:X,Y")
    parser.add_argument("--group", action="append", default=[], metavar="LABEL:X,Y,X,Y")
    parser.add_argument("--crop", type=int, nargs=4, default=None)
    arguments = parser.parse_args()

    with Image.open(arguments.shot) as opened:
        image = opened.convert("RGB")
    draw = ImageDraw.Draw(image)
    for item in arguments.group:
        label, numbers = item.split(":")
        left, top, right, bottom = (int(value) for value in numbers.split(","))
        bracket(draw, (left, top, right, bottom))
        marker(draw, label, left, top)
    for item in arguments.point:
        label, numbers = item.split(":")
        x, y = (int(value) for value in numbers.split(","))
        marker(draw, label, x, y)
    if arguments.crop:
        image = image.crop(tuple(arguments.crop))
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    image.save(arguments.output)
    print(f"wrote {arguments.output} at {image.width}x{image.height}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
