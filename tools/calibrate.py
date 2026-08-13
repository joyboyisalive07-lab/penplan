"""Run the calibration wizard and save the profile.

Usage: ``python tools/calibrate.py gartic-phone [--measure]``.

Arrange the browser first, so the canvas, the whole palette, both tools and
every brush size are visible at once. Then follow the prompt: hover over each
target and press F8 to capture it, F9 to finish a list, Escape to abort. The
wizard never clicks while capturing, so nothing is drawn and no tool changes.

The prompt is shown in a strip across the top of the screen, above every other
window, because the thing you are pointing at is a browser and a prompt printed
into a terminal behind it is a prompt nobody can read.
"""

import argparse
import sys
import threading
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from penplan.calibrate import CalibrationRequest, WindowsSurface, calibrate
from penplan.input_win import AbortedError, cursor_position, dpi_scale_at, enable_dpi_awareness
from penplan.profile import Profile, ProfileError, user_profiles_dir
from penplan.ui import PromptStrip

PROMPT_KEYS = "F8 captures what the cursor is on    F9 finishes a list    Escape aborts"


def run(
    name: str, *, measure: bool, picker: bool, prompt: PromptStrip
) -> tuple[Profile | None, str]:
    """Run the wizard on this thread and return the profile or the reason there is none."""
    with WindowsSurface() as surface:
        request = CalibrationRequest(
            name=name,
            screen=surface.screen,
            dpi_scale=dpi_scale_at(*cursor_position()),
            measure_by_drawing=measure,
            bind_picker=picker,
        )
        try:
            return calibrate(request, surface, lambda message: prompt.say(message, PROMPT_KEYS)), ""
        except AbortedError:
            return None, "aborted, nothing was written"
        except ProfileError as error:
            return None, f"calibration failed: {error}"


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
        "--picker",
        action="store_true",
        help="also bind a colour picker, for canvases with R, G and B fields",
    )
    parser.add_argument(
        "--output", type=Path, default=None, help="where to write, defaults to the user profile dir"
    )
    arguments = parser.parse_args()

    enable_dpi_awareness()
    root = tk.Tk()
    root.withdraw()
    prompt = PromptStrip(root)
    prompt.say("Starting", PROMPT_KEYS)
    outcome: list[tuple[Profile | None, str]] = []

    def worker() -> None:
        outcome.append(
            run(
                arguments.name,
                measure=arguments.measure,
                picker=arguments.picker,
                prompt=prompt,
            )
        )
        root.after(0, root.destroy)

    threading.Thread(target=worker, daemon=True).start()
    root.mainloop()

    profile, problem = outcome[0] if outcome else (None, "the prompt was closed")
    if profile is None:
        print(problem)
        return 1

    path = arguments.output or user_profiles_dir() / f"{arguments.name}.json"
    profile.save(path)
    print(f"wrote {path}")
    print(f"canvas {profile.canvas.width}x{profile.canvas.height}, {len(profile.palette)} colours")
    widths = ", ".join(f"{width:.1f}" for width in profile.brush_widths)
    measured = "measured" if profile.brushes[0].measured else "estimated"
    print(f"brush widths {widths} ({measured})")
    print(f"pacing {profile.pacing.point_seconds * 1000:.0f} ms between stroke points")
    if profile.picker is not None:
        print("colour picker bound and tested")
    if arguments.measure:
        print("clear the canvas: the test strokes are still on it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
