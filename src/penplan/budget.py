"""Fit the drawing into the seconds the user has, and say what that cost.

This is the module the whole tool exists for. Everything else decides how to
draw well; this decides how to draw well enough in the time available, and it
refuses to lie about whether it managed.

The estimate comes from a cost model measured on the machine that will do the
drawing, not from constants. Synthetic input teleports the cursor, so time goes
on events rather than on distance: every polyline point, every click, every trip
to the palette. That is why simplification is the sharpest lever the budget has.

When a plan overruns, it is degraded in a stated order, one rung at a time,
and each rung that gets applied is reported as a sacrifice:

1. drop the smallest regions, which costs specks nobody will miss
2. simplify harder, which costs the accuracy of outlines
3. withhold the thinnest brushes, which costs fine detail
4. cut the palette, which costs colour

If the last rung still does not fit, the plan is returned anyway with its
estimate and a report saying so. The one thing that never happens is a plan
presented as fitting when it does not.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final

from penplan.fills import plan_fills
from penplan.model import (
    CostModel,
    Degradation,
    DrawPlan,
    Fill,
    PlanReport,
    Sacrifice,
    Stroke,
)
from penplan.palette import Palette
from penplan.quantize import prepare_source, quantize
from penplan.regions import decompose
from penplan.render import Raster
from penplan.strokes import cover, outline_strokes
from penplan.tour import plan_tour

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PIL import Image

    from penplan.model import Step
    from penplan.profile import Profile
    from penplan.quantize import QuantizedImage

# The plan raster at full detail, as a fraction of the canvas. Drawing one plan
# pixel per canvas pixel is finer than any mouse can execute inside a budget,
# and it makes every planning pass cost several times more for nothing.
MAX_DETAIL_FRACTION: Final = 0.5
MIN_RASTER_SIDE: Final = 32

DEFAULT_TOLERANCE: Final = 1.0
DEFAULT_MIN_REGION_AREA: Final = 2

# How long the tour optimiser may run. Beyond this the user is waiting on a
# plan rather than saving time in it.
DEFAULT_TOUR_SECONDS: Final = 1.0


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything the budget is allowed to turn down."""

    raster_width: int
    raster_height: int
    min_region_area: int
    tolerance: float
    lowest_brush: int
    palette_size: int
    dither: bool
    use_fills: bool


@dataclass(frozen=True, slots=True)
class PlanRequest:
    """What the user asked for."""

    image: Image.Image
    profile: Profile
    budget_seconds: float
    detail: float = 1.0
    dither: bool = False
    use_fills: bool = True
    tour_seconds: float = DEFAULT_TOUR_SECONDS


@dataclass(frozen=True, slots=True)
class _Rung:
    """One step down the degradation ladder."""

    kind: Degradation
    field: str
    value: object
    detail: str


LADDER: Final = (
    _Rung(Degradation.DROP_SMALL_REGIONS, "min_region_area", 6, "dropped regions under 6 pixels"),
    _Rung(Degradation.DROP_SMALL_REGIONS, "min_region_area", 16, "dropped regions under 16 pixels"),
    _Rung(Degradation.DROP_SMALL_REGIONS, "min_region_area", 40, "dropped regions under 40 pixels"),
    _Rung(Degradation.SIMPLIFY_MORE, "tolerance", 1.8, "simplified outlines to 1.8 pixels"),
    _Rung(Degradation.SIMPLIFY_MORE, "tolerance", 3.0, "simplified outlines to 3.0 pixels"),
    _Rung(Degradation.COARSER_BRUSH, "lowest_brush", 1, "gave up the thinnest brush"),
    _Rung(Degradation.COARSER_BRUSH, "lowest_brush", 2, "gave up the two thinnest brushes"),
    _Rung(Degradation.REDUCE_PALETTE, "palette_size", 8, "cut the palette to 8 colours"),
    _Rung(Degradation.REDUCE_PALETTE, "palette_size", 6, "cut the palette to 6 colours"),
    _Rung(Degradation.REDUCE_PALETTE, "palette_size", 4, "cut the palette to 4 colours"),
)


def raster_size(profile: Profile, detail: float) -> tuple[int, int]:
    """Return the plan raster size for a detail setting between 0 and 1."""
    fraction = max(0.05, min(1.0, detail)) * MAX_DETAIL_FRACTION
    return (
        max(MIN_RASTER_SIDE, round(profile.canvas.width * fraction)),
        max(MIN_RASTER_SIDE, round(profile.canvas.height * fraction)),
    )


def initial_settings(request: PlanRequest) -> Settings:
    """Return the settings a plan starts from, before any degradation."""
    width, height = raster_size(request.profile, request.detail)
    return Settings(
        raster_width=width,
        raster_height=height,
        min_region_area=DEFAULT_MIN_REGION_AREA,
        tolerance=DEFAULT_TOLERANCE,
        lowest_brush=0,
        palette_size=len(request.profile.palette),
        dither=request.dither,
        use_fills=request.use_fills,
    )


def estimate_seconds(steps: Sequence[Step], cost: CostModel) -> float:
    """Estimate how long executing these steps will take.

    Time goes on events. A stroke costs one event per point plus the press and
    release; a fill costs a click and the tool switches around it; a colour
    change costs the trip to the palette. Distance is charged too, because a
    long jump needs the canvas to settle before the next press, but on
    synthetic input that term is small and the measured model says how small.
    """
    total = 0.0
    position = None
    color: int | None = None
    filling: bool | None = None
    for step in steps:
        start = step.points[0] if isinstance(step, Stroke) else step.seed
        if position is not None:
            total += position.distance_to(start) * cost.seconds_per_pixel
        total += cost.seconds_per_move
        if color != step.color:
            total += cost.seconds_per_color_switch
            color = step.color
        is_fill = isinstance(step, Fill)
        if filling is not is_fill:
            total += cost.seconds_per_tool_switch
            filling = is_fill
        total += cost.seconds_per_click
        if isinstance(step, Stroke):
            total += (len(step.points) - 1) * cost.seconds_per_move
            total += step.drawn_length() * cost.seconds_per_pixel
            position = step.points[-1]
        else:
            position = step.seed
    return total


def _kept_colors(image: QuantizedImage, keep: int, background: int) -> list[int]:
    """Return the most used palette indices, with the background always kept."""
    counts: dict[int, int] = {}
    for index in image.indices:
        counts[index] = counts.get(index, 0) + 1
    ranked = sorted(counts, key=lambda index: (-counts[index], index))
    chosen = [index for index in ranked if index != background][: max(1, keep - 1)]
    return sorted({background, *chosen})


def _quantize_to_profile(request: PlanRequest, settings: Settings) -> tuple[QuantizedImage, int]:
    """Quantize onto the profile palette, honouring any palette reduction.

    The result always speaks in the profile's own palette indices, because the
    executor clicks swatches by where they sit on screen.
    """
    profile = request.profile
    full = Palette(profile.colors)
    source = prepare_source(
        request.image, settings.raster_width, settings.raster_height, profile.background
    )
    target = quantize(source, full, dither=settings.dither)
    background = full.nearest(profile.background)
    if settings.palette_size >= len(profile.palette):
        return target, background

    kept = _kept_colors(target, settings.palette_size, background)
    reduced = full.subset(kept)
    narrowed = quantize(source, reduced, dither=settings.dither)
    table = bytes(kept[value] if value < len(kept) else kept[0] for value in range(256))
    return (
        replace(narrowed, colors=profile.colors, indices=narrowed.indices.translate(table)),
        background,
    )


def build_plan(request: PlanRequest, settings: Settings) -> tuple[DrawPlan, float]:
    """Build one plan at the given settings, and return it with its estimate.

    The order is fixed by what each stage needs from the one before: outlines
    are drawn onto a simulated canvas, the fills are proved against that canvas,
    and the strokes cover whatever the fills left. Only then is the whole thing
    ordered.
    """
    profile = request.profile
    target, background = _quantize_to_profile(request, settings)
    regions = decompose(target, ignore=[background], min_area=settings.min_region_area).regions
    canvas = Raster(target.width, target.height, background)
    outlines = outline_strokes(regions, target.height, settings.lowest_brush, settings.tolerance)
    thinnest = profile.brush_widths[settings.lowest_brush]
    for stroke in outlines:
        canvas.stroke(stroke.points, stroke.color, thinnest)
    fills = plan_fills(canvas, regions, background=background).fills if settings.use_fills else ()
    interior = cover(
        canvas,
        target,
        profile.brush_widths,
        ignore=[background],
        tolerance=settings.tolerance,
        lowest_brush=settings.lowest_brush,
    )
    switch_pixels = profile.cost.seconds_per_color_switch / max(
        profile.cost.seconds_per_pixel, 1e-9
    )
    tour = plan_tour(
        [list(outlines), list(fills), interior],
        color_switch_cost=switch_pixels,
        time_limit=request.tour_seconds,
    )
    estimate = estimate_seconds(tour.steps, profile.cost)
    plan = DrawPlan(
        width=target.width,
        height=target.height,
        palette=profile.colors,
        brush_widths=profile.brush_widths,
        steps=tour.steps,
        report=PlanReport(
            estimated_seconds=estimate,
            budget_seconds=request.budget_seconds,
            tour_length=tour.length,
            greedy_tour_length=tour.greedy_length,
            arrival_tour_length=tour.arrival_length,
            sacrifices=(),
        ),
    )
    return plan, estimate


def plan_within_budget(request: PlanRequest) -> DrawPlan:
    """Return the best plan that fits the budget, and what fitting it cost.

    Rungs are applied one at a time and only while the plan overruns, so a
    drawing that fits comfortably is never degraded at all. A plan that still
    overruns after the last rung comes back with ``fits_budget`` false rather
    than quietly.
    """
    settings = initial_settings(request)
    sacrifices: list[Sacrifice] = []
    plan, estimate = build_plan(request, settings)
    for rung in LADDER:
        if estimate <= request.budget_seconds:
            break
        candidate = replace(settings, **{rung.field: rung.value})
        candidate_plan, candidate_estimate = build_plan(request, candidate)
        if candidate_estimate >= estimate:
            # This rung bought nothing. Keeping it would mean reporting a
            # sacrifice the user paid for and did not get anything back for.
            continue
        saved = estimate - candidate_estimate
        settings, plan, estimate = candidate, candidate_plan, candidate_estimate
        sacrifices.append(
            Sacrifice(kind=rung.kind, detail=f"{rung.detail}, saving {saved:.1f} seconds")
        )
    return replace(plan, report=replace(plan.report, sacrifices=tuple(sacrifices)))
