# Calibrating a canvas

A profile is what makes this tool work on a site it has never seen. It is a set
of screen coordinates and the colours that were read at them, and it is worth
one minute of your time per canvas.

Everything below applies to any drawing canvas. Gartic Phone is used as the
worked example because that is what most people arrive here for.

![What the wizard asks for, in order](img/calibration.png)

## Before you start

Four things decide whether a profile keeps working, and all four are about the
window staying put:

- **Set the browser zoom to 100 per cent** with `Ctrl+0`. A profile calibrated
  at 110 per cent is wrong at 100, and the tool will refuse to use it.
- **Do not scroll the page afterwards.** Scrolling moves the canvas out from
  under every coordinate in the profile.
- **Maximise the window, or at least leave it alone.** A moved or resized
  window invalidates the profile in exactly the same way.
- **Arrange the layout so everything is visible at once**: the canvas, the
  colours you want to use, the brush tool, the fill tool and the brush sizes.

None of these are fatal if you forget. The tool reads the palette back off the
screen before it draws and refuses when it does not match. But recalibrating
takes a minute, and being told to recalibrate takes a second.

## Running the wizard

```bash
python tools/calibrate.py gartic-phone --measure
```

The name is the file name the profile is saved under, so pick something you
will recognise in the profile list.

The wizard prints one instruction at a time and waits. For each one:

- hover the mouse over the target and press **F8** to capture it,
- press **F9** when a list is finished,
- press **Escape** to abort, at any point, leaving nothing written.

The order is fixed, and matches the numbers in the picture above:

1. **The top-left corner of the canvas.** The first pixel that belongs to the
   drawing area, not the border around it.
2. **The bottom-right corner of the canvas.** The last pixel that belongs to it.
3. **Every palette colour**, one after another, then F9. Capture only the
   colours you actually want used: each colour the planner uses costs a trip to
   the palette, so a smaller palette draws faster.
4. **The brush tool.**
5. **The fill tool**, the paint bucket.
6. **Every brush size, thinnest first**, then F9.

Nothing is clicked while positions are being captured. A click on a canvas
corner would leave a dot on the drawing, and a click on a swatch would change
the tool halfway through the calibration.

The colours are read afterwards, with the cursor parked in the middle of the
canvas. A swatch under the pointer is drawn in a hover state on most sites, and
that highlight would go into the profile instead of the colour.

## What `--measure` does

With the flag, the wizard draws on the canvas: a short test stroke for each
brush size, and a zigzag.

The test strokes are measured across, at half the peak contrast, which is what
tells the planner how wide each brush actually paints. Without this the widths
are a plausible guess, and the dry run is a guess with them. On the canvas in
this repository the measurement returns 2.0, 6.0, 14.0 and 28.0 against a page
that defines 2, 6, 14 and 28.

The zigzag measures how fast the canvas can be fed. A canvas samples the
pointer on its own schedule and draws a straight line between whatever it
received, so a stroke sent too fast arrives with its corners missing. The
wizard raises the delay until every corner lands.

**Clear the canvas afterwards.** The test marks are still on it.

The mouse is also timed, with or without the flag: a few dozen moves and taps
on the palette, which draw nothing. That is where the time estimates come from,
and it is measured on your machine rather than assumed.

## Gartic Phone in particular

- **Calibrate in a private lobby, in your own round.** The drawing screen only
  exists while a round is running, and calibrating during someone else's game
  is both rude and rushed.
- **The brush size is a slider on some layouts.** A click on a slider jumps to
  the position clicked, so capture two to four points along it as if they were
  buttons, thinnest first. Use `--measure` so the planner learns what each of
  those positions actually paints instead of assuming.
- **Set the time budget to the seconds left in the round, minus five.** The
  estimate is accurate to about one per cent, but a round that ends mid-stroke
  submits a half-finished drawing.
- **The palette is larger than you need.** Twelve colours is plenty for most
  images, and each colour you leave out is a palette trip the planner never
  has to make.

The same applies to skribbl.io and drawaria.online. There is nothing about
Gartic Phone in the code, and there is nothing about it in the profile beyond
the coordinates you captured.

## Practising without anybody watching

Open `tools/testcanvas.html` from this repository in a browser. It is a plain
HTML canvas with a palette, two tools and four brush sizes, and it belongs to
nobody. Calibrate against it, draw on it, get a feel for what the detail slider
and the budget do, and only then point the tool at a real round.

The bundled `test-canvas` profile was made against that page, and the picture at
the top of the readme was drawn on it.

## When it refuses to draw

Before a single mouse event is sent, every palette swatch is read back off the
screen and compared with the colour the profile recorded. A mismatch means the
window has moved, the zoom or the display scaling changed, the page scrolled,
or the profile came from another machine. The tool names the swatch and stops.

That refusal is the feature, not the fault. Without it a stale profile does not
fail, it clicks: on browser tabs, on menus, on the button that submits the
drawing.

Pressing the button a second time draws anyway, for the case where the check is
wrong and you can see that it is wrong.

## Other things that go wrong

| What you see | What it is |
| --- | --- |
| The drawing lands beside the canvas | The canvas corners were captured on the border or the page around it. Recapture them. |
| Everything is offset by the same amount | The display scaling changed since calibration. Recalibrate. |
| Fewer fills than you expected | A fill is only issued when a simulation proves it cannot leak. The dry run shows exactly which ones survived. |
| The result is coarser than the dry run promised | It should not be. Check the profile records measured brush widths and not guessed ones. |
| The drawing is too coarse | Raise the detail slider, or give it more seconds. The sacrifice list says what the budget cut. |
| Escape does nothing | Another application is holding the Escape hotkey. The tool refuses to start drawing in that case and says so. |

## Editing a profile by hand

Profiles are JSON in `%APPDATA%\penplan\profiles\`. Every field is readable and
every coordinate is a physical screen pixel. Nudging a swatch by a few pixels or
deleting a colour you do not want used is a reasonable thing to do, and the
format is versioned so a file that is broken by an edit says so rather than
disappearing from the list.
