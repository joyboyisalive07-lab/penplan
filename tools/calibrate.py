"""Run the calibration wizard from a terminal and save the profile.

Usage: ``python tools/calibrate.py gartic-phone [--measure-brushes]``.

Arrange the browser first, so the canvas, the whole palette, both tools and
every brush size are visible at once. Then follow the prompts: hover over each
target and press F8 to capture it, F9 to finish a list, Escape to abort. The
wizard never clicks while capturing, so nothing is drawn and no tool changes.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from penplan.calibrate import CalibrationRequest, WindowsSurface, calibrate
from penplan.input_win import AbortedError, cursor_position, dpi_scale_at, enable_dpi_awareness
from penplan.profile import ProfileError, user_profiles_dir


def main() -> int:
    """Run the wizard and return a process exit code."""
    parser = argparse.ArgumentParser(description="Calibrate a drawing canvas")
    parser.add_argument("name", help="profile name, used as the file name")
    parser.add_argument(
        "--measure",
        action="store_true",
        help="draw test strokes to measure brush widths and pacing; this writes on the canvas",
    )
    parser.add_argument(
        "--output", type=Path, default=None, help="where to write, defaults to the user profile dir"
    )
    arguments = parser.parse_args()

    enable_dpi_awareness()
    print("F8 captures the point under the cursor, F9 finishes a list, Escape aborts")
    with WindowsSurface() as surface:
        request = CalibrationRequest(
            name=arguments.name,
            screen=surface.screen,
            dpi_scale=dpi_scale_at(*cursor_position()),
            measure_by_drawing=arguments.measure,
        )
        try:
            profile = calibrate(request, surface, print)
        except AbortedError:
            print("aborted, nothing was written")
            return 1
        except ProfileError as error:
            print(f"calibration failed: {error}")
            return 1

    path = arguments.output or user_profiles_dir() / f"{arguments.name}.json"
    profile.save(path)
    print(f"wrote {path}")
    print(f"canvas {profile.canvas.width}x{profile.canvas.height}, {len(profile.palette)} colours")
    if arguments.measure:
        measured = "measured" if profile.brushes[0].measured else "estimated, measurement failed"
        widths = ", ".join(f"{width:.1f}" for width in profile.brush_widths)
        print(f"brush widths {widths} ({measured})")
        print(f"pacing {profile.pacing.point_seconds * 1000:.0f} ms between stroke points")
        print("clear the canvas: the test strokes are still on it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
