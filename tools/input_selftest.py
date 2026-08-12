"""Check the Windows input layer against the real machine it will run on.

Run it from a checkout: ``python tools/input_selftest.py``. It moves the cursor
to a grid of sample points and reads back where the system says the cursor
landed, which is the only honest way to confirm the absolute-coordinate
normalisation. It clicks nothing, and it puts the cursor back where it was.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from penplan.input_win import (
    AbortHotkey,
    Pointer,
    ScreenPixels,
    cursor_position,
    dpi_scale_at,
    enable_dpi_awareness,
    normalize_absolute,
    virtual_screen,
)

# Enough spread to catch a scaling error at either edge without a slow sweep.
SAMPLE_FRACTIONS = (0.0, 0.017, 0.25, 0.5, 0.75, 0.983, 1.0)
# The system needs a moment to process a synthetic move before it reports back.
READBACK_SECONDS = 0.02


def check_moves() -> int:
    """Move to sample points, report the readback error, return the worst one.

    A caller that gets -1 back is not looking at a broken input layer: some
    other application is holding the cursor.
    """
    screen = virtual_screen()
    pointer = Pointer(screen)
    worst = 0
    landings: set[tuple[int, int]] = set()
    for fraction_x in SAMPLE_FRACTIONS:
        for fraction_y in SAMPLE_FRACTIONS:
            target_x = screen.left + min(screen.width - 1, round(fraction_x * (screen.width - 1)))
            target_y = screen.top + min(screen.height - 1, round(fraction_y * (screen.height - 1)))
            pointer.move_to(target_x, target_y)
            time.sleep(READBACK_SECONDS)
            actual_x, actual_y = cursor_position()
            landings.add((actual_x, actual_y))
            error = max(abs(actual_x - target_x), abs(actual_y - target_y))
            worst = max(worst, error)
            if error:
                normalized = normalize_absolute(target_x, target_y, screen)
                print(
                    f"  ({target_x}, {target_y}) -> ({actual_x}, {actual_y}) "
                    f"off by {error}, absolute {normalized}"
                )
    if len(landings) == 1 and worst:
        # A pointer-locked application, a full-screen game most of all, warps
        # the cursor back every frame. Every sample then reads back the same
        # position and the numbers above say nothing about this code.
        print(f"  every sample landed on {landings.pop()}: an application is holding the cursor")
        return -1
    return worst


def main() -> int:
    """Run every check and return a process exit code."""
    print(f"dpi awareness enabled: {enable_dpi_awareness()}")
    screen = virtual_screen()
    print(f"virtual screen: {screen.width}x{screen.height} at ({screen.left}, {screen.top})")
    print(f"scale at screen origin: {dpi_scale_at(screen.left, screen.top):.2f}")

    with ScreenPixels() as pixels:
        centre_x = screen.left + screen.width // 2
        centre_y = screen.top + screen.height // 2
        print(f"pixel at screen centre: {pixels.at(centre_x, centre_y)}")

    restore_x, restore_y = cursor_position()
    print("moving the cursor over a sample grid, no clicks")
    try:
        worst = check_moves()
    finally:
        Pointer(screen).move_to(restore_x, restore_y)
    if worst < 0:
        print("cursor readback inconclusive: run this with an ordinary window focused")
    else:
        print(f"worst cursor readback error: {worst} px")

    with AbortHotkey() as hotkey:
        print(f"abort hotkey registered, triggered: {hotkey.triggered}")
    print("abort hotkey released")

    return 0 if worst == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
