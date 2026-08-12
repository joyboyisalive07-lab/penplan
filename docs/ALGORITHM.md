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

To be written in the quantization phase.

## Region decomposition

To be written in the region phase.

## Fill verification

To be written in the fill phase.

## Brush sizing and stroke generation

To be written in the stroke phase.

## Tour optimization

To be written in the tour phase.

## Cost model and time budget

To be written in the budget phase.
