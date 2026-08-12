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

## Absolute mouse coordinates aim at the centre of a pixel

`SendInput` takes absolute coordinates normalised over 0..65535, and the mouse
driver converts them back to a pixel with a truncating divide. Normalising a
pixel's leading edge therefore lands one pixel short whenever the rounding goes
the wrong way, and a systematic one-pixel drift is visible in a drawing.
`normalize_absolute` adds half a pixel before scaling, which puts the value in
the middle of the slot that maps back to the intended pixel. The test asserts
the round trip for every pixel column of four common resolutions, and for a
virtual desktop whose origin is negative.

`MOUSEEVENTF_MOVE_NOCOALESCE` is set on every move. Without it Windows is free
to merge consecutive moves, and a merged pair of moves is a straight line
across a curve the planner intended to draw.

## The process declares itself per-monitor DPI aware

An unaware process is lied to in both directions on a scaled display: cursor
positions come back in logical pixels and GDI hands out a stretched copy of the
screen, so a canvas calibrated at 150 per cent would be off by half again on
every stroke. `enable_dpi_awareness` asks for per-monitor v2 and falls back
through the two older APIs. The cost is that tkinter then renders unscaled and
the interface has to apply the scale factor itself, which `ui.py` does.

## The abort hotkey runs on its own thread and fails loudly

`RegisterHotKey` delivers `WM_HOTKEY` to the thread that registered it, and the
thread sending input is busy, so the hotkey gets a dedicated thread with its own
message loop. If registration fails, because another application already holds
the key, `AbortHotkey` raises instead of continuing: a user watching their mouse
draw with no way to stop it is the worst outcome this tool can produce.

## Calibration captures with a hotkey, not with a click

Clicking each target would be the obvious interaction and it is the wrong one.
A click on the canvas corner leaves a dot on the drawing, and a click on a
palette swatch or a brush control changes the tool state halfway through
calibration. The user hovers and presses F8 instead, so capturing is
observation only. F9 ends a list of unknown length, Escape aborts everywhere.

## Palette colours are read after every position is captured

A swatch under the pointer is usually drawn in a hover state: lighter, ringed,
or scaled up. Reading its colour at the moment of capture would record the
hover highlight as the palette colour and poison every quantization decision
made with the profile. The wizard captures all positions first, parks the
cursor in the middle of the canvas, waits for the page to drop the highlight,
and only then reads the swatches.

## Brush widths are measured, and the profile says whether they were

The dry run promises to show what will be drawn, and it cannot keep that
promise without knowing what a brush actually paints. So calibration can draw
one test stroke per brush size and measure the painted band, taking its width
at half the peak contrast, which is the standard way to state the width of an
anti-aliased line. This writes on the canvas, so it is opt-in, and a user who
declines gets a plausible progression instead. `BrushControl.measured` records
which of the two happened, because a rendered preview built on guessed widths
should not look as trustworthy as one built on measurements.

## A malformed profile raises instead of disappearing

Profiles are JSON on purpose: they are meant to be re-edited by hand. A file
that is broken by that editing must say so. Skipping it would silently drop the
user's calibration from the list and leave them wondering where it went.

## Rescaling a profile is an aim, not a substitute for recalibration

`Profile.rescaled` moves every recorded position by the ratio of display
scales, which is how Windows lays a page out, but a browser reflows text at the
new size and a canvas sized by the surrounding layout can end up a pixel or two
away from where the arithmetic puts it. The interface says so when it applies
the conversion. The alternative, refusing to load a profile at a different
scale, would be worse for a user who only changed their monitor.

## CIEDE2000, not the plain Lab distance

Converting to Lab and taking the Euclidean distance is most of the benefit for
a tenth of the code, and it is still wrong in the places a drawing shows.
CIEDE2000 was fitted to the datasets where Lab misjudges: near-neutral colours,
where it overstates hue differences, and the blue region, where it ranks blues
as close to purples. A palette of a dozen swatches makes every match a coarse
one, and coarse matches are exactly where the difference between the two
metrics changes which swatch gets picked.

The cost is measured: about 6 microseconds per comparison in plain Python. At
that price a 480,000 pixel canvas cannot be matched pixel by pixel, so
quantization reduces the source to a bounded number of distinct colours first
and matches those. The alternative, a faster metric, would trade the one thing
this module exists to provide.

## Ruff runs `select = ["ALL"]`

Starting from everything and subtracting is auditable; starting from a curated
list is a slow drift towards nothing. The subtractions are listed in
`pyproject.toml`, each with the reason it was subtracted, and are limited to
rules that contradict the formatter, contradict each other, or contradict a
rule stated in this project's own conventions.
