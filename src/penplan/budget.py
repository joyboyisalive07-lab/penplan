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

import math
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final

from PIL import Image

from penplan.fills import plan_fills
from penplan.input_win import VK_A, VK_CONTROL, VK_TAB
from penplan.model import (
    Action,
    ActionKind,
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

    from penplan.model import CostModel, Pacing, Rgb
    from penplan.profile import ColorPicker, Profile
    from penplan.quantize import QuantizedImage

# The plan raster at full detail, as a fraction of the canvas. Drawing one plan
# pixel per canvas pixel is finer than any mouse can execute inside a budget,
# and it makes every planning pass cost several times more for nothing.
MAX_DETAIL_FRACTION: Final = 0.5
MIN_RASTER_SIDE: Final = 32

# A brush thinner than one plan pixel would let the coverage pass leave gaps it
# can never close, so the thinnest brush is worth at least the pixel it aims at.
MIN_RASTER_BRUSH_WIDTH: Final = 1.0

DEFAULT_TOLERANCE: Final = 1.0
DEFAULT_MIN_REGION_AREA: Final = 2

# How long the tour optimiser may run. Beyond this the user is waiting on a
# plan rather than saving time in it.
DEFAULT_TOUR_SECONDS: Final = 1.0

# Guards the division that converts a switching cost into an equivalent
# distance, for a machine where a move measured as costing nothing per pixel.
_MIN_PIXEL_SECONDS: Final = 1e-9


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
    use_picker: bool = False
    """Choose colours from the image and type them, rather than using swatches."""

    colors: int = 12
    """How many colours to choose from the image, when the picker is used."""

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


def raster_brush_widths(profile: Profile, raster_width: int) -> tuple[float, ...]:
    """Return the profile's brush widths measured in plan pixels.

    The profile measures a brush in screen pixels, because that is what the
    calibration saw. The plan is a small raster that gets stretched over the
    canvas, so a 2-pixel brush on an 800-pixel canvas covers half a pixel of a
    200-pixel plan, not two. Handing the screen numbers straight to the coverage
    pass makes it believe each stroke paints four times the width it really
    does, and the drawing comes out as hatching with the paper showing through.
    """
    scale = raster_width / profile.canvas.width
    return tuple(max(MIN_RASTER_BRUSH_WIDTH, width * scale) for width in profile.brush_widths)


def initial_settings(request: PlanRequest) -> Settings:
    """Return the settings a plan starts from, before any degradation."""
    width, height = raster_size(request.profile, request.detail)
    return Settings(
        raster_width=width,
        raster_height=height,
        min_region_area=DEFAULT_MIN_REGION_AREA,
        tolerance=DEFAULT_TOLERANCE,
        lowest_brush=0,
        palette_size=request.colors
        if request.use_picker and request.profile.picker is not None
        else len(request.profile.palette),
        dither=request.dither,
        use_fills=request.use_fills,
    )


def _click_at(x: int, y: int, pacing: Pacing) -> list[Action]:
    """Return the actions that click one control."""
    return [
        Action.move(x, y),
        Action.wait(pacing.settle_seconds),
        Action.press(),
        Action.wait(pacing.hold_seconds),
        Action.release(),
    ]


def picker_actions(picker: ColorPicker, pacing: Pacing, color: Rgb) -> list[Action]:
    """Return the actions that type one colour into a picker.

    Opened, filled in, and closed again. Closing matters: a picker panel left
    open can sit over the canvas, and a stroke drawn onto a panel is a stroke
    that never reaches the picture. It costs one click and removes the question.

    Each field is clicked, selected whole, and overwritten, rather than trusting
    that it was empty or that the caret landed anywhere in particular.
    """
    actions = _click_at(picker.open.x, picker.open.y, pacing)
    for control, value in zip((picker.red, picker.green, picker.blue), color, strict=True):
        actions.extend(_click_at(control.x, control.y, pacing))
        actions.append(Action.chord(VK_CONTROL, VK_A))
        actions.append(Action.wait(pacing.settle_seconds))
        actions.append(Action.type_text(str(value)))
        actions.append(Action.wait(pacing.settle_seconds))
    # Leaving the last field commits it on canvases that only apply on blur.
    actions.append(Action.chord(VK_TAB))
    actions.append(Action.wait(pacing.settle_seconds))
    actions.extend(_click_at(picker.open.x, picker.open.y, pacing))
    return actions


def select_color(profile: Profile, pacing: Pacing, color: Rgb) -> list[Action]:
    """Return the actions that make ``color`` the one being drawn with.

    A colour the palette already has is one click. Anything else is typed into
    the picker, which is the only way to draw a colour a canvas does not offer.
    """
    for swatch in profile.palette:
        if swatch.color == color:
            return _click_at(swatch.x, swatch.y, pacing)
    if profile.picker is None:
        msg = f"{color} is not in the palette and this profile has no colour picker"
        raise ValueError(msg)
    return picker_actions(profile.picker, pacing, color)


def schedule(plan: DrawPlan, profile: Profile, pacing: Pacing) -> list[Action]:
    """Turn a plan into the exact sequence of things the mouse will do.

    This is the one description of an execution. The estimate is the duration
    of this schedule, and the executor performs this schedule, so the two
    cannot drift apart into models that agree until they do not.

    Selections are emitted only when they change, and always for the first
    step, because nothing is known about what the canvas had selected before.

    It takes the whole plan rather than its parts on purpose. An earlier version
    took the steps and let the palette default to the profile's, and a caller
    that forgot to pass the plan's palette got a drawing in whatever colours
    happened to sit at those indices on the site. It looked like a bug in the
    colour picker for an hour.
    """
    width, height = plan.width, plan.height
    colors = plan.palette
    actions: list[Action] = []
    color: int | None = None
    brush: int | None = None
    filling: bool | None = None
    for step in plan.steps:
        is_fill = isinstance(step, Fill)
        if filling is not is_fill:
            control = profile.fill_tool if is_fill else profile.brush_tool
            actions.extend(_click_at(control.x, control.y, pacing))
            filling = is_fill
            # A tool change loses the brush size on canvases that keep one per
            # tool, so the next stroke reselects it.
            brush = None
        if color != step.color:
            actions.extend(select_color(profile, pacing, colors[step.color]))
            color = step.color
        if isinstance(step, Stroke):
            if brush != step.brush:
                control = profile.brushes[step.brush]
                actions.extend(_click_at(control.x, control.y, pacing))
                brush = step.brush
            actions.extend(_stroke_actions(step, width, height, profile, pacing))
        else:
            actions.extend(_click_at(*profile.canvas_to_screen(step.seed, width, height), pacing))
    return actions


def _stroke_actions(
    stroke: Stroke, width: int, height: int, profile: Profile, pacing: Pacing
) -> list[Action]:
    points = [profile.canvas_to_screen(point, width, height) for point in stroke.points]
    actions = [
        Action.move(*points[0]),
        Action.wait(pacing.settle_seconds),
        Action.press(),
        Action.wait(pacing.hold_seconds),
    ]
    for point in points[1:]:
        actions.append(Action.move(*point))
        actions.append(Action.wait(pacing.point_seconds))
    actions.append(Action.release())
    return actions


def schedule_seconds(actions: Sequence[Action], cost: CostModel) -> float:
    """Return how long a schedule takes, from the measured cost of its parts.

    Waits are what the executor sleeps. Everything else is the price of getting
    an event to the system, which on synthetic input barely depends on how far
    the cursor moves; the per-pixel term is there because calibration measures
    it rather than assuming it away.
    """
    total = 0.0
    position: tuple[int, int] | None = None
    for action in actions:
        if action.kind is ActionKind.WAIT:
            total += action.seconds
        elif action.kind is ActionKind.MOVE:
            total += cost.seconds_per_move
            if position is not None:
                total += math.dist(position, (action.x, action.y)) * cost.seconds_per_pixel
            position = (action.x, action.y)
        elif action.kind is ActionKind.TYPE:
            # A keystroke is an event like any other, and there is one of them
            # per character, twice over for the press and the release.
            total += len(action.text) * cost.seconds_per_move
        elif action.kind is ActionKind.KEYS:
            total += cost.seconds_per_move
        else:
            total += cost.seconds_per_click / 2.0
    return total


def _kept_colors(image: QuantizedImage, keep: int, background: int) -> list[int]:
    """Return the most used palette indices, with the background always kept."""
    counts: dict[int, int] = {}
    for index in image.indices:
        counts[index] = counts.get(index, 0) + 1
    ranked = sorted(counts, key=lambda index: (-counts[index], index))
    chosen = [index for index in ranked if index != background][: max(1, keep - 1)]
    return sorted({background, *chosen})


def _image_palette(source: Image.Image, size: int, background: Rgb) -> tuple[Rgb, ...]:
    """Choose the colours of the image itself, with the background kept.

    A canvas with a colour picker is not limited to its swatches, so neither is
    the planner: median cut over the image gives colours that belong to the
    picture rather than the nearest crayon to them. The background is forced in
    because everything already that colour is work the planner gets to skip.
    """
    reduced = source.quantize(
        colors=max(2, size), method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE
    )
    flat = reduced.getpalette() or []
    entries = [
        (flat[offset], flat[offset + 1], flat[offset + 2]) for offset in range(0, len(flat), 3)
    ]
    chosen = [entries[index] for index in sorted(set(reduced.tobytes()))]
    return tuple(dict.fromkeys([background, *chosen]))[:size]


def _prepare_target(request: PlanRequest, settings: Settings) -> tuple[QuantizedImage, int]:
    """Quantize the image onto the colours this plan is allowed to use.

    Without a picker those are the profile's swatches, and the result speaks in
    the profile's own indices because the executor clicks swatches by position.
    With one, they are the image's own colours and the executor types them.
    """
    profile = request.profile
    source = prepare_source(
        request.image, settings.raster_width, settings.raster_height, profile.background
    )
    if request.use_picker and profile.picker is not None:
        palette = Palette(_image_palette(source, settings.palette_size, profile.background))
        target = quantize(source, palette, dither=settings.dither)
        return target, palette.nearest(profile.background)

    full = Palette(profile.colors)
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


def _color_switch_seconds(profile: Profile, colors: Sequence[Rgb]) -> float:
    """Return what changing colour costs, measured on the actual selections.

    A swatch is a click. A typed colour is a click, three fields, three
    selections, three numbers and a click to close, which is an order of
    magnitude more, and the tour has to know that or it will happily switch
    back and forth between colours it should have finished with.
    """
    worst = profile.cost.seconds_per_color_switch
    for color in colors:
        actions = select_color(profile, profile.pacing, color)
        worst = max(worst, schedule_seconds(actions, profile.cost))
    return worst


def build_plan(request: PlanRequest, settings: Settings) -> tuple[DrawPlan, float]:
    """Build one plan at the given settings, and return it with its estimate.

    The order is fixed by what each stage needs from the one before: outlines
    are drawn onto a simulated canvas, the fills are proved against that canvas,
    and the strokes cover whatever the fills left. Only then is the whole thing
    ordered.
    """
    profile = request.profile
    target, background = _prepare_target(request, settings)
    widths = raster_brush_widths(profile, target.width)
    regions = decompose(target, ignore=[background], min_area=settings.min_region_area).regions
    canvas = Raster(target.width, target.height, background)
    outlines = outline_strokes(regions, target.height, settings.lowest_brush, settings.tolerance)
    thinnest = widths[settings.lowest_brush]
    for stroke in outlines:
        canvas.stroke(stroke.points, stroke.color, thinnest)
    fills = plan_fills(canvas, regions, background=background).fills if settings.use_fills else ()
    interior = cover(
        canvas,
        target,
        widths,
        ignore=[background],
        tolerance=settings.tolerance,
        lowest_brush=settings.lowest_brush,
    )
    # The tour works in canvas pixels, so the switching costs are expressed as
    # the distance the mouse could have covered in the same time.
    per_pixel = max(profile.cost.seconds_per_pixel, _MIN_PIXEL_SECONDS)
    switch_seconds = _color_switch_seconds(profile, target.colors)
    tour = plan_tour(
        [list(outlines), list(fills), interior],
        color_switch_cost=switch_seconds / per_pixel,
        brush_switch_cost=profile.cost.seconds_per_tool_switch / per_pixel,
        time_limit=request.tour_seconds,
    )
    plan = DrawPlan(
        width=target.width,
        height=target.height,
        palette=target.colors,
        background=background,
        brush_widths=widths,
        steps=tour.steps,
        report=PlanReport(
            estimated_seconds=0.0,
            budget_seconds=request.budget_seconds,
            travel=tour.travel,
            greedy_travel=tour.greedy_travel,
            arrival_travel=tour.arrival_travel,
            sacrifices=(),
        ),
    )
    # The estimate is what the plan's own schedule costs, so the number quoted to
    # the user and the actions performed for them can never come from different
    # palettes.
    estimate = schedule_seconds(schedule(plan, profile, profile.pacing), profile.cost)
    return replace(plan, report=replace(plan.report, estimated_seconds=estimate)), estimate


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
