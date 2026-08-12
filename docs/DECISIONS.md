# Decisions

Every non-obvious choice, with the reason it was made. New entries go at the
bottom, so the file reads as a history.

## Pillow is the only runtime dependency

Decoding PNG, JPEG and GIF correctly, and resampling with a decent filter, is
weeks of work with no upside. Everything above that line is planning, and
planning is what this tool is. Adding a second runtime dependency requires an
entry in this file explaining what it buys that stdlib cannot.

Consequences accepted deliberately:

- Colour science (sRGB to CIE Lab, CIEDE2000) is implemented in `palette.py`
  rather than pulled from `colormath` or `scikit-image`. It is roughly a
  hundred lines of arithmetic against a published formula and it is testable
  against the Sharma reference pairs.
- Mouse input, screen pixel reads and hotkeys use `ctypes` against `user32`
  and `gdi32` instead of `pyautogui` or `keyboard`. `pyautogui` cannot express
  a paced drag reliably and pulls in a dependency tree of its own; `SendInput`
  is one struct.
- The interface is `tkinter`, which ships with CPython on Windows.

## No numpy

The raster work is connected components, morphological erosion and flood fill
on images bounded by the canvas size, which is a few hundred pixels on the
targeted canvases. Flat `bytearray` buffers with explicit index arithmetic are
fast enough at that scale, keep the executable small, and keep the planner
readable as plain Python. If profiling on a 2000x2000 canvas ever shows the
raster passes dominating the planning budget, this decision is the one to
revisit.

## Coverage is measured with a stdlib tool, not pytest-cov

The dev dependency list is deliberately three names. `sys.monitoring`, added in
Python 3.12, reports line events cheaply enough to measure statement coverage
of the planning modules from `tools/coverage.py`, so no fourth package is
needed to enforce the coverage bar.

## Plan coordinates are integer canvas pixels

A plan is expressed in the canvas raster's own pixel grid, not in screen
coordinates, and carries its own palette and brush widths. That keeps every
module above `input_win.py` testable with no screen attached, makes the
renderer and the executor consume exactly the same object, and means a plan
stays meaningful if the browser window moves between planning and execution.
Conversion to physical screen pixels happens once, in the profile.

## `dist/` is not committed

The executable is a build output. It is produced by `release.yml` on a `v*`
tag and attached to the GitHub release, and can be rebuilt locally with one
PyInstaller command. A 20 MB binary in git history would dwarf the source it
was built from.

## Ruff runs `select = ["ALL"]`

Starting from everything and subtracting is auditable; starting from a curated
list is a slow drift towards nothing. The subtractions are listed in
`pyproject.toml`, each with the reason it was subtracted, and are limited to
rules that contradict the formatter, contradict each other, or contradict a
rule stated in this project's own conventions.
