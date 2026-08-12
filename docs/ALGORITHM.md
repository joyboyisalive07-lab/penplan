# Algorithm

The planner turns a raster image into an ordered list of mouse actions that
fits a time budget. It runs as a pipeline, and every stage below is filled in
as it is built.

```
image -> quantize -> regions -> fills -> strokes -> tour -> budget -> plan
                                                                       |
                                                        render (PNG)   +-> execute (SendInput)
```

Two properties hold across the whole pipeline:

- **A plan is data.** It is integer canvas coordinates, palette indices and
  brush indices, with no reference to a screen. The renderer and the executor
  consume the same object, so what the dry run shows is what the mouse draws.
- **A plan is honest about time.** The estimate comes from a measured cost
  model, and a plan that does not fit the budget is degraded in a stated order
  and reported, never silently shipped.

## Colour distance

Every palette decision rests on one question: which of the profile's colours
looks most like this pixel. Answering it in RGB answers a different question,
because equal steps in RGB are not equal steps in perception. The classic
failures are skin tones, which drift green, and skies, which drift purple.

So colours go through the published chain: undo the sRGB transfer function,
multiply by the sRGB primaries matrix into CIE XYZ under D65, and convert to
CIE Lab, where distance is roughly perceptual. Roughly is not enough on its
own, and the remaining error is largest exactly where a drawing shows it, so
matching uses CIEDE2000 rather than the plain Lab distance. CIEDE2000 adds
three corrections: it stretches the a axis for near-neutral colours, weights
lightness, chroma and hue separately by where in the space they sit, and adds a
rotation term that stops saturated blues from being ranked as similar to
purples.

The implementation is about a hundred lines in `palette.py` and is checked
against 29 of the worked pairs published with Sharma, Wu and Dalal (2005),
agreeing with all of them to within 1e-4. Those pairs exist because the formula
has three traps: hue angles either side of zero, the mean of two hue angles
that straddle 360 degrees, and pairs where one colour has no chroma at all.

CIEDE2000 costs about 6 microseconds per comparison in plain Python, which is
why quantization bounds the number of distinct source colours before matching
rather than calling it once per pixel.

## Quantization

The source image is fitted into the plan raster with its aspect ratio intact
and the margins filled with the canvas background colour, which calibration
sampled from the blank canvas. Filling the letterbox with the background rather
than with white means the planner recognises it as already drawn and spends no
time on it. Transparency is flattened onto the same background.

Matching then happens in two stages, for cost. A CIEDE2000 comparison costs
about 6 microseconds, so matching every pixel of a 900x700 raster against a
dozen swatches would take minutes. Instead the raster is reduced to at most 256
distinct colours by median cut, those 256 are matched once each, and every
pixel is mapped through a 256-byte table by `bytes.translate`, which runs in C.
The whole pass takes about half a second on a 900x700 raster, most of it inside
Pillow's median cut.

### Dithering

A palette of a dozen colours cannot express a gradient, so gradients land as
bands. Ordered Bayer dithering replaces the bands with a fixed 4x4 pattern that
alternates between the two nearest palette colours. For each source colour the
planner already knows both nearest colours and the ratio of their distances;
the Bayer cell's threshold is compared against that ratio, so a pixel sitting
a tenth of the way towards the second colour takes it in a tenth of the cells.
A pixel that is exactly a palette colour has a ratio of zero and is never
dithered.

The pattern is built into sixteen lookup tables, one per Bayer cell, so
dithering costs no colour maths at all: a row is four `translate` calls and
four strided copies.

Dithering is off by default, and the interface says why. Alternating pixels
destroy the horizontal runs that the stroke planner merges into polylines. On a
gradient test image, dithering more than doubles the number of colour changes
along a row, and every one of those is a stroke the time budget has to pay for.

## Region decomposition

The quantized raster is split into connected areas of one colour each, held as
horizontal runs rather than as sets of pixels. A run is already most of a
stroke, area and bounding box fall out of the runs, and a picture with half a
million pixels stays a few thousand objects.

Connectivity is four-way, matching the flood fill in every paint program.
Eight-way connectivity would join two areas across a diagonal touch that a fill
would not cross, and the fill planner has to be able to trust that a region is
exactly what one fill click would reach.

The algorithm is one pass of run-length labelling with union-find: each row is
split into runs, each run gets a label, and runs that overlap a run of the same
colour in the row above are merged. A shape whose arm rejoins its body several
rows later comes out as one region, which single-pass labelling without the
merge step gets wrong.

Two details keep it fast enough to sit in front of a waiting user. Rows are
split by comparing the row against itself shifted one byte, as a single big
integer, so the per-pixel comparison happens in C and Python takes one step per
run instead of one per pixel. And runs live in parallel lists during the merge,
with `Run` and `Region` objects built only for the regions that survive the
area threshold.

Measured on a 900x700 raster with ten colours: an image made of shapes, which
is what this tool is for, decomposes in 59 ms. A worst case of high-frequency
noise, which quantizes into 190,000 regions, takes 2 seconds. The lever against
the worst case is the raster size, which the detail slider controls: a plan
raster is normally a fraction of the canvas resolution, because a mouse cannot
draw one plan pixel per canvas pixel inside any usable time budget.

Colours listed as ignored produce no regions at all. That is how the canvas
background costs nothing: it is already on screen.

The minimum area threshold is the first lever the time budget pulls. Regions
below it are dropped and counted, so the interface can say how many specks were
sacrificed rather than quietly losing them.

## Fill verification

To be written in the fill phase.

## Brush sizing and stroke generation

To be written in the stroke phase.

## Tour optimization

To be written in the tour phase.

## Cost model and time budget

To be written in the budget phase.
