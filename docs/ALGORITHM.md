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
