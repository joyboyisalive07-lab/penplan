"""Capture the screen to a PNG, for the images in the documentation.

Usage: ``python tools/screenshot.py docs/img/interface.png [--delay 3]``.

Pillow can already grab the screen on Windows, so this is a thin wrapper that
exists to keep the documentation images reproducible rather than hand-made.
"""

import argparse
import sys
import time
from pathlib import Path

from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def main() -> int:
    """Grab the screen and return a process exit code."""
    parser = argparse.ArgumentParser(description="Save a screenshot")
    parser.add_argument("output", type=Path)
    parser.add_argument("--delay", type=float, default=0.0, help="seconds to wait before grabbing")
    parser.add_argument(
        "--box",
        type=int,
        nargs=4,
        default=None,
        metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
        help="grab only this rectangle",
    )
    arguments = parser.parse_args()
    if arguments.delay:
        time.sleep(arguments.delay)
    image = ImageGrab.grab(bbox=tuple(arguments.box) if arguments.box else None)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    image.save(arguments.output)
    print(f"wrote {arguments.output} at {image.width}x{image.height}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
