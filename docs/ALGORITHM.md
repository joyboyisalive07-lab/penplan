# Algorithm

The planner turns a raster image into an ordered list of mouse actions that
fits a time budget. It runs as a pipeline, and each stage below is one of its
steps, with the measurements that decided how it works.

```
image -> quantize -> regions -> fills -> strokes -> tour -> budget -> plan
                                                                       |
                                                        render (PNG)   +-> schedule -> execute
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

A fill is the best trade in the planner: one click instead of thousands of
pixels. It is also the only step that can destroy a drawing. A fill leaks
through a one-pixel gap in the outline around it and runs until it meets the
next painted pixel, which on a blank canvas can be most of the picture.

So no fill is issued on trust. The planner:

1. Paints every outline it intends to draw onto a simulated canvas, using the
   same rasterizer the preview uses.
2. Picks a seed: the middle of the region's longest run, then the next longest,
   up to eight attempts, stopping at the first that is not already covered by
   the outline. A region whose candidate seeds are all covered is a region its
   own outline already painted, and it needs no fill.
3. Floods the simulated canvas from that seed, four-way, exactly as the paint
   bucket would.
4. Counts how many flooded pixels fall outside the intended region.

**One pixel outside is a refusal.** There is no tolerance, because a leak is
never small: it stops only where the next outline happens to be, so the
difference between a safe fill and a catastrophic one is not a matter of
degree. A refused fill costs nothing but time. Whatever it would have covered
is still unpainted on the simulated canvas, and the stroke planner picks it up
from there without being told.

A fill is also refused when it would paint fewer pixels than
`MIN_FILL_AREA`, because selecting the fill tool, the colour, and then the
brush again costs more than the few strokes it would replace.

Accepted fills are painted onto the simulated canvas before the next region is
considered, so each check sees the canvas the executor will actually face. This
is what makes the ordering contract real: every outline is drawn before any
fill, and the fills run in plan order.

The cost of the check is one step per row of the flooded area rather than one
per pixel. Spans are found with C-level searches over a byte-per-pixel blocked
mask, and leakage is counted with `bytes.count` over each span.

The test suite builds outlines with deliberate gaps, one pixel wide, on each of
the four edges, and asserts both halves of the guarantee: the fill is refused,
and the simulated canvas is left untouched.

## Brush sizing and stroke generation

### Outlines are traced, not scanned

Each region's boundary is followed with Moore-neighbour tracing, walking
clockwise, and comes back to the pixel it started from. The result is one
closed polyline per region. Closed is the point: scanning a boundary into
horizontal pieces leaves diagonal gaps, and every gap is a fill leak.

Holes are not traced. A hole is a region in its own right, with its own
outline, and that outline is what stops the surrounding fill from running into
it.

Simplifying an outline can cut a corner and open the shape. That is allowed to
happen, because the fill check runs afterwards against the rasterized outline
and refuses any fill the simplification broke. Raising the tolerance therefore
costs fills, and the planner can spend that trade deliberately.

### Brush size comes from erosion

A brush may be used only where it fits entirely inside the area still to be
painted. That set is a morphological erosion of the remaining mask by the brush
disc, computed on intervals rather than pixels: shrink each row by the disc's
half-width on that row, then intersect the rows the disc spans.

The cascade runs from the thickest brush to the thinnest. Each pass hatches the
eroded area with horizontal strokes spaced by the brush's own height, paints
them onto the simulated canvas, and hands whatever is left to the next brush
down. The thick brush therefore covers the interior in a few passes and the
thin brush inherits the boundary band, which is exactly the division of labour
the erosion is for.

The thinnest brush does not erode. There is nothing narrower to fall back on,
so it covers what remains and accepts that a brush wider than a one-pixel
detail paints over its neighbours, which is what happens on a real canvas too.

### Points cost time

Every point in a polyline is a mouse event. Two steps reduce them.

Runs no wider than the brush are chained across rows into a single polyline
through their centres, so a one-pixel diagonal arrives as one stroke instead of
one click per row. Wider runs stay the horizontal strokes they already are.

Then every polyline, outline and chain alike, is simplified with
Ramer-Douglas-Peucker at a tolerance the planner controls. The implementation
is iterative rather than recursive, because a thousand-point contour would
otherwise be a thousand stack frames deep.

### Measured end to end

A 600x450 drawing of five shapes, quantized to a ten-colour palette, plans in
0.12 seconds into 5 outlines, 5 fills and 65 interior strokes: 161 points in
total. The rendered plan differs from the quantized target on 0.24 per cent of
pixels. The same picture planned at 300x225, where anti-aliasing leaves more
small regions, gives 33 outlines, 4 fills, 147 interior strokes and 0.02 per
cent mismatch. The refused fills in that run are refusals of economy, not
safety: regions too small to be worth a tool switch.

## Tour optimization

Drawing order is a travelling-salesman problem with two differences from the
textbook one.

The first is that switching colour costs a trip to the palette and a click, not
just the distance between two strokes. That makes it a clustered problem. The
penalty lives inside the cost function, expressed as the distance the mouse
could have covered in the same time, so travel and switching are one number. At
the default cost model a switch is worth about 625 canvas pixels, which is
roughly a diagonal of the canvas, and that is why finishing a colour before
moving on is nearly always right.

The second is that a stroke can be drawn either way round. Reversing one costs
nothing and changes which end the next stroke starts from, so orientation is
part of what is being optimised. It falls out of 2-opt for free: reversing a
stretch of the tour reverses the strokes inside it, and because distance is
symmetric, only the two edges at the ends of the stretch change cost.

### Construction

Nearest neighbour, colour by colour. Both ends of every stroke go into a
spatial grid, so the nearest end decides which way round the stroke is drawn,
and the search expands ring by ring until the nearest candidate cannot be
beaten by a further ring. When a colour is exhausted, the next colour is the
one whose nearest entry point is closest.

### Improvement

Three passes run in turn until none of them finds anything or the time cap
expires.

**2-opt** on neighbour lists. Each step keeps a dozen nearby steps, ranked by
how close either of its ends comes to either of theirs. Ranking by only one end
costs about a third of the improvement, because a stroke's useful partner is as
often near the end it finishes at.

**Long edges.** A neighbour list only proposes partners that are close, and for
a short edge that is where every improvement lives. A long edge is different:
the move that removes it can pair it with a step anywhere, because the new edge
only has to beat the long one it replaces. So the worst few dozen edges get a
full scan. Without this pass the optimiser stalls at 5 per cent on a scattered
plan where full 2-opt reaches 14; with it, it reaches 12.7 per cent in a third
of the time.

**Or-opt.** Runs of up to three steps are relocated elsewhere in the tour,
either way round.

### Measured

Against a full quadratic 2-opt run to convergence, on scattered random strokes:

| strokes | greedy | penplan | full 2-opt |
| --- | --- | --- | --- |
| 200 | 19430 | 16955 in 0.26 s | 16754 in 0.64 s |
| 500 | 27848 | 25186 in 0.86 s | 25117 in 3.50 s |

On real plans the picture is different, because the greedy construction is
already close to right: hatch rows and contours arrive in sensible positions.
Ordering is worth far more than the improvement passes are. These are canvas
pixels of mouse travel, with the switching costs set aside:

| plan | steps | as planned | after greedy | optimised | travel saved |
| --- | --- | --- | --- | --- | --- |
| shapes | 184 | 10470 | 2918 | 2672 | 74.5% |
| scattered circles | 1007 | 55446 | 19177 | 18141 | 67.3% |
| gradient | 48 | 3968 | 2116 | 1907 | 51.9% |
| shapes, dithered | 1407 | 62305 | 6980 | 7933 | 87.3% |

The dithered row is the interesting one: the optimiser ended up with more
travel than the greedy tour, and was right to. It minimises cost, not distance,
and on a plan with fourteen hundred tiny steps it paid a little extra travel to
avoid colour and brush switches worth 625 and 500 pixels each. In cost terms
that run improved by 56 per cent.

That is also why the result reports both families of number. A cost moves when
the measured cost model moves; a distance does not, so a distance is what the
interface shows.

### Ordering is not free to rearrange

Phases are ordered but never reordered. Every outline is drawn before any fill,
because that is the canvas state the fills were proved against, and the interior
strokes come last because they were planned against a canvas that already had
the fills on it. The optimiser works inside each phase.

## Cost model and time budget

### What costs time

Synthetic input teleports the cursor: a `SendInput` move takes the same time
whether it crosses ten pixels or a thousand. Time therefore goes on events, not
on distance. Every polyline point is an event, every press and release is a
click, every colour change is a trip to the palette, and every switch between
brush and fill is a tool click.

That is why simplification is the sharpest lever the budget has, and why the
tour's saving is large in pixels and small in seconds. Both facts are reported
rather than smoothed over.

The model still carries a per-pixel term, because a long jump can need the
canvas to settle before the next press. Calibration measures it rather than
assuming it: the self-timing run times a short hop and a long one, and the
difference between them separates the fixed cost of an event from whatever
distance adds. It also times clicks on a palette swatch and on the tool
buttons, which changes the selected colour and tool and draws nothing.

### The estimate

    for each step:
        travel from the last position       distance x seconds_per_pixel
        arriving                            seconds_per_move
        colour change, if any               seconds_per_color_switch
        tool change, if any                 seconds_per_tool_switch
        press and release                   seconds_per_click
        each further point of a stroke      seconds_per_move
        the length drawn                    distance x seconds_per_pixel

### The ladder

When a plan overruns, it is degraded one rung at a time, in a stated order:

1. **Drop the smallest regions** at 6, then 16, then 40 pixels. Costs specks.
2. **Simplify harder**, to 1.8 then 3.0 pixels. Costs outline accuracy, and
   incidentally costs fills, because a simplified outline that cuts a corner
   fails the fill check.
3. **Withhold the thinnest brushes**, one then two. Costs fine detail.
4. **Cut the palette** to 8, then 6, then 4 colours. Costs colour.

A rung is only kept if it actually shortens the plan. A rung that buys nothing
is skipped rather than reported, because telling a user they gave up their
thinnest brush and got nothing back is worse than not trying it.

On a 600x450 drawing of four shapes with a ten-colour palette, at half detail:

| budget | estimate | outcome |
| --- | --- | --- |
| 300 s | 20.5 s | fits, nothing sacrificed |
| 30 s | 20.5 s | fits, nothing sacrificed |
| 15 s | 9.0 s | fits after four rungs |
| 4 s | 9.0 s | reported as not fitting |

The four rungs at 15 seconds, with what each one bought: dropping regions under
6 pixels saved 1.9 s, giving up the thinnest brush saved 2.0 s, cutting the
palette to 6 colours saved 1.0 s, and cutting it to 4 saved 6.5 s. Planning all
of that took 1.1 seconds.

A plan that cannot fit even at the bottom of the ladder is returned with its
estimate and `fits_budget` false. It is never presented as fitting.

## Execution

### One description, not two

A plan becomes a list of actions: move, press, release, wait. The list is built
without touching the screen, and it is the only description of an execution
there is. The estimate is the duration of that list, and the executor performs
that list. There is no second model of what execution will cost that could
agree with the first until the day it does not.

The schedule reselects a colour, a tool or a brush only when it changes, and
always for the first step, because nothing is known about what the canvas had
selected before. A tool change is assumed to lose the brush size, since some
canvases keep one per tool; that costs one extra click per fill and never draws
with the wrong brush.

### Pacing

A canvas samples the pointer on its own schedule and draws a straight line
between whatever it received, so a stroke fed faster than the canvas samples
arrives as a straight line with its corners missing. Calibration measures the
pace the canvas needs: it draws a zigzag with corners too sharp to confuse with
a straight line, checks whether every corner got painted, and if any is missing
raises the delay and tries again, in a lower band of the canvas. The measured
delay is stored in the profile.

The measurement runs during calibration rather than during a drawing, because
the test has to be drawn somewhere, and calibration is already the step that
warns the canvas will need clearing.

### Checking the profile first

A plan is coordinates, and coordinates are only meaningful while the window
they were calibrated from is where it was. Before the countdown, every palette
swatch is read off the screen and compared with the colour the profile
recorded, perceptually, with a tolerance of five CIEDE2000 units: enough for
scaling and subpixel rendering, nowhere near enough to accept a browser tab.
Anything that does not match is named, and the drawing does not start.

### Abort

The abort key is checked before every single action, so pressing it takes
effect within one input event rather than at the end of a stroke. The pointer
is a context manager, so the button comes back up on any exit, including an
abort or an exception. The tests assert both: that a run aborted mid-stroke
reports the button as still held at the moment the run ended, and that the last
thing the pointer did was release it.
