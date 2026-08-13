<img src="docs/img/logo.png" width="72" align="left" alt="penplan">

# penplan

[![ci](https://github.com/joyboyisalive07-lab/penplan/actions/workflows/ci.yml/badge.svg)](https://github.com/joyboyisalive07-lab/penplan/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

Reproduces an image on a browser drawing canvas by moving the real mouse, and
plans the strokes so the drawing finishes inside a time budget you set.

<br>

![Source and dry run](docs/img/dry-run.png)

The tool knows nothing about any particular site. It learns a canvas from a
calibration profile: where the canvas is, what colours the palette actually
contains, read off the screen rather than declared, where the brush and fill
tools are, and which brush sizes exist. Gartic Phone, skribbl.io,
drawaria.online and Windows Paint are the same problem to it.

It sees the screen and moves the mouse. It injects no scripts, touches no
network traffic, automates no account, and makes no network requests at all.

Russian: [README.ru.md](README.ru.md).

## How the planner works

    image -> quantize -> regions -> fills -> strokes -> tour -> budget -> plan
                                                                          |
                                                       render (PNG)   +-> schedule -> execute

**Quantization** maps the image onto the profile's palette in CIE Lab with
CIEDE2000, not with RGB distance, because equal steps in RGB are not equal
steps in perception and the difference shows on skin tones and skies. Ordered
Bayer dithering is available and off by default: it more than doubles the
number of colour changes along a row, and the budget pays for every one.

**Regions** are connected areas of one colour, four-way connected, held as
horizontal runs.

**Fills** are the best trade in the planner, one click instead of thousands of
pixels, and the only step that can destroy a drawing. So none is issued on
trust: the outlines are painted onto a simulated canvas, the fill is flooded
from the seed that would actually be clicked, and one pixel outside the region
is a refusal. A refused fill costs nothing, because whatever it did not cover is
still unpainted and the stroke planner picks it up.

**Strokes** get their brush size from morphological erosion: a brush is used
only where it fits entirely inside what is left to paint, so the thick brush
takes the interior and the thin one inherits the boundary. Runs are chained into
polylines and simplified with Ramer-Douglas-Peucker.

**The tour** is a clustered travelling-salesman problem: travel costs distance,
and changing colour costs a trip to the palette. Nearest-neighbour construction,
then 2-opt with neighbour lists, a full search for the worst edges, and Or-opt,
all under a time cap, with strokes free to be drawn either way round.

**The budget** estimates the duration from a cost model measured on your machine
and, when a plan overruns, degrades it in a stated order: drop the smallest
regions, simplify harder, withhold the thinnest brushes, cut the palette. Each
rung is kept only if it actually shortens the plan, and each one is reported
with the seconds it bought. A plan that cannot fit says so rather than pretending.

The details are in [docs/ALGORITHM.md](docs/ALGORITHM.md), and the reason behind
every non-obvious choice is in [docs/DECISIONS.md](docs/DECISIONS.md).

## Numbers

All measured, all reproducible from the tests and the tools in this repository.

| | |
| --- | --- |
| CIEDE2000 against the 29 published Sharma pairs | agrees to 1e-4 |
| Planning a 600x450 drawing of five shapes | 0.12 s, 5 fills, 65 strokes, 161 points |
| That plan rendered against the quantized target | 0.24 per cent of pixels differ |
| Region decomposition, 900x700, ten colours | 59 ms, or 2 s on worst-case noise |
| Mouse travel, ordered against as-planned | 52 to 87 per cent shorter |
| Improvement passes against full 2-opt, 200 strokes | 16955 in 0.26 s against 16754 in 0.64 s |
| A 20.5 s drawing given a 15 s budget | 9.0 s after four sacrifices |
| Statement coverage of the planning modules | 95.1 per cent |

## Calibrating a canvas

Arrange the window first, so that the canvas, the whole palette, both tools and
every brush size are visible at once. Then run the wizard:

```bash
python tools/calibrate.py gartic-phone --measure
```

Hover over each target and press **F8** to capture it, **F9** to finish a list,
**Escape** to abort. In order: the canvas top-left corner, the canvas
bottom-right corner, every palette colour, the brush tool, the fill tool, and
every brush size from thinnest to widest.

Nothing is clicked while positions are being captured. A click on a canvas
corner would leave a dot on the drawing, and a click on a swatch would change
the tool halfway through. Palette colours are read afterwards, with the cursor
parked in the middle of the canvas, because a swatch under the pointer is drawn
in a hover state and that highlight would be recorded as the colour.

`--measure` draws a short test stroke per brush size and a zigzag, which
measures what each brush actually paints and how fast the canvas can be fed.
It writes on the canvas, so clear the canvas afterwards. Without it the widths
are a plausible guess, and the profile records which of the two you have.

The profile lands in `%APPDATA%\penplan\profiles\`, as readable JSON you can
edit by hand.

Before a single mouse event is sent, the palette is read back off the screen and
compared with the profile. If the window has moved, or the profile came from
another machine, the tool says which swatch does not match and refuses rather
than clicking on whatever is there now.

## Using it

Run `penplan.exe`, or `python -m penplan` from a checkout. Choose a profile,
drop an image on the window, enter the seconds you have, and press Draw. Before
anything happens you get the estimated duration, the stroke, fill and colour
counts, a dry run of exactly what will be drawn, and a list of what the budget
had to give up.

Draw starts a three-second countdown so you can focus the canvas, and the window
steps out of the way while it draws. **Escape stops it at any moment**, releases
the mouse button and gives the mouse back.

## Building from source

```bash
git clone https://github.com/joyboyisalive07-lab/penplan
cd penplan
pip install -e ".[dev]"
pytest
pyinstaller penplan.spec --noconfirm
```

The result is `dist/penplan.exe`, one file, no installer, about 18 MB. It runs
from any path, including one with spaces in it and one on a flash drive.

Python 3.12 or newer, Windows. The only runtime dependency is Pillow; the rest
is the standard library and ctypes.

## Windows Defender

PyInstaller executables are regularly flagged by Defender and by other scanners.
This is what a self-extracting Python interpreter looks like from the outside,
and it happens to every one-file build; a tool that moves the mouse and reads
the screen fits the pattern twice over. Nothing can be done about it from this
side beyond saying so. Build it yourself with the command above if you would
rather not take the binary on trust, or check the file against the source you
can read here.

## About using this in games

This automates mouse input in games that generally do not allow automation.
It is meant for private lobbies with people who know, and for offline canvases.
Pointing it at strangers in a public round takes the game away from them, which
is the entire point of the game. That is not a legal position, it is just what
happens.

## License

MIT, copyright joyboyisalive07-lab. See [LICENSE](LICENSE).
