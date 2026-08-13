"""Tests for the time budget: the estimate, the ladder, and the honesty."""

from __future__ import annotations

from dataclasses import replace

import pytest
from PIL import Image, ImageDraw

from penplan.budget import (
    LADDER,
    PlanRequest,
    build_plan,
    initial_settings,
    plan_within_budget,
    raster_brush_widths,
    raster_size,
    schedule,
    schedule_seconds,
    select_color,
)
from penplan.model import (
    Action,
    ActionKind,
    CostModel,
    Degradation,
    DrawPlan,
    Fill,
    Pacing,
    PlanReport,
    Point,
    Rgb,
    ScreenRect,
    Stroke,
)
from penplan.palette import Palette
from penplan.profile import BrushControl, ColorPicker, Control, Profile, Swatch
from penplan.quantize import prepare_source, quantize
from penplan.render import Raster, apply_step

COLORS = (
    (255, 255, 255),
    (0, 0, 0),
    (220, 30, 40),
    (40, 60, 200),
    (40, 180, 90),
    (240, 200, 60),
    (150, 90, 200),
    (120, 80, 40),
    (255, 150, 180),
    (90, 90, 90),
)

# Round numbers, so an estimate can be checked by hand rather than by rerunning
# the function under test.
COST = CostModel(
    seconds_per_move=0.01,
    seconds_per_pixel=0.0,
    seconds_per_click=0.1,
    seconds_per_color_switch=0.5,
    seconds_per_tool_switch=0.2,
)
PACING = Pacing(point_seconds=0.02, settle_seconds=0.01, hold_seconds=0.01)


def make_profile() -> Profile:
    return Profile(
        name="test",
        canvas=ScreenRect(left=100, top=100, width=600, height=450),
        screen=ScreenRect(left=0, top=0, width=1920, height=1080),
        background=(255, 255, 255),
        palette=tuple(
            Swatch(x=10, y=40 + index * 20, color=color) for index, color in enumerate(COLORS)
        ),
        brush_tool=Control(x=10, y=10),
        fill_tool=Control(x=30, y=10),
        brushes=(
            BrushControl(x=50, y=10, width=1.0, measured=True),
            BrushControl(x=70, y=10, width=5.0, measured=True),
            BrushControl(x=90, y=10, width=13.0, measured=True),
        ),
        dpi_scale=1.0,
        cost=COST,
        pacing=PACING,
        created="2026-08-12T00:00:00+00:00",
    )


def shapes(size: tuple[int, int] = (600, 450)) -> Image.Image:
    image = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse([60, 60, 300, 300], fill=(220, 30, 40))
    draw.rectangle([260, 180, 520, 380], fill=(40, 60, 200))
    draw.polygon([(400, 40), (560, 150), (340, 150)], fill=(40, 180, 90))
    draw.ellipse([120, 300, 260, 420], fill=(240, 200, 60))
    return image


def make_plan(steps: list[Stroke | Fill], palette: tuple[Rgb, ...] = COLORS) -> DrawPlan:
    """Wrap steps in the smallest plan the schedule will accept."""
    return DrawPlan(
        width=32,
        height=32,
        palette=palette,
        background=0,
        brush_widths=(1.0, 5.0, 13.0),
        steps=tuple(steps),
        report=PlanReport(
            estimated_seconds=0.0,
            budget_seconds=60.0,
            travel=0.0,
            greedy_travel=0.0,
            arrival_travel=0.0,
            sacrifices=(),
        ),
    )


def request(budget: float, **overrides: object) -> PlanRequest:
    fields: dict[str, object] = {
        "image": shapes(),
        "profile": make_profile(),
        "budget_seconds": budget,
        "detail": 0.4,
        "tour_seconds": 0.2,
    }
    fields.update(overrides)
    return PlanRequest(**fields)  # type: ignore[arg-type]


def test_a_schedule_selects_the_tool_the_colour_and_the_brush_first() -> None:
    steps = [Stroke(color=2, brush=1, points=(Point(0, 0), Point(4, 0)))]
    actions = schedule(make_plan(steps), make_profile(), PACING)
    moves = [action for action in actions if action.kind is ActionKind.MOVE]
    profile = make_profile()
    assert (moves[0].x, moves[0].y) == (profile.brush_tool.x, profile.brush_tool.y)
    assert (moves[1].x, moves[1].y) == (profile.palette[2].x, profile.palette[2].y)
    assert (moves[2].x, moves[2].y) == (profile.brushes[1].x, profile.brushes[1].y)


def test_a_schedule_only_reselects_what_changed() -> None:
    same = [
        Stroke(color=2, brush=1, points=(Point(0, 0), Point(4, 0))),
        Stroke(color=2, brush=1, points=(Point(8, 8), Point(9, 9))),
    ]
    different = [
        same[0],
        Stroke(color=3, brush=1, points=(Point(8, 8), Point(9, 9))),
    ]
    assert len(schedule(make_plan(different), make_profile(), PACING)) > len(
        schedule(make_plan(same), make_profile(), PACING)
    )


def test_a_stroke_costs_a_point_at_a_time() -> None:
    short = [Stroke(color=0, brush=0, points=(Point(0, 0), Point(4, 0)))]
    long_stroke = [
        Stroke(color=0, brush=0, points=(Point(0, 0), Point(4, 0), Point(8, 0), Point(12, 0)))
    ]
    extra = schedule_seconds(
        schedule(make_plan(long_stroke), make_profile(), PACING), COST
    ) - schedule_seconds(schedule(make_plan(short), make_profile(), PACING), COST)
    # Two further points, each a move and a pacing wait.
    assert extra == pytest.approx(2 * (COST.seconds_per_move + PACING.point_seconds))


def test_a_fill_costs_the_tool_switches_around_it() -> None:
    strokes = [
        Stroke(color=0, brush=0, points=(Point(0, 0), Point(1, 0))),
        Stroke(color=0, brush=0, points=(Point(5, 5), Point(6, 5))),
    ]
    with_fill = [strokes[0], Fill(color=0, seed=Point(5, 5)), strokes[1]]
    profile = make_profile()
    added = schedule_seconds(
        schedule(make_plan(with_fill), profile, PACING), COST
    ) - schedule_seconds(schedule(make_plan(strokes), profile, PACING), COST)
    # Two strokes alone need the brush tool, the colour, the brush size and one
    # press each: five. Putting a fill between them adds the switch to the fill
    # tool, the fill click itself, the switch back, and the brush size that a
    # tool change is assumed to have lost: nine.
    clicks = [
        action
        for action in schedule(make_plan(with_fill), profile, PACING)
        if action.kind is ActionKind.PRESS
    ]
    assert len(clicks) == 9
    assert added > 3 * (COST.seconds_per_click + COST.seconds_per_move)


def test_an_empty_plan_takes_no_time() -> None:
    assert schedule_seconds([], COST) == 0.0
    assert schedule(make_plan([]), make_profile(), PACING) == []


def test_the_raster_follows_the_detail_setting() -> None:
    profile = make_profile()
    coarse = raster_size(profile, 0.2)
    fine = raster_size(profile, 1.0)
    assert coarse[0] < fine[0]
    assert fine == (300, 225)


def test_the_raster_never_collapses() -> None:
    assert raster_size(make_profile(), 0.0)[0] >= 32


def test_brush_widths_are_converted_into_plan_pixels() -> None:
    # The canvas is 600 wide, so a 300-wide plan gets half the screen width.
    profile = make_profile()
    assert raster_brush_widths(profile, 300) == (1.0, 2.5, 6.5)


def test_a_brush_is_never_thinner_than_the_pixel_it_aims_at() -> None:
    profile = make_profile()
    assert min(raster_brush_widths(profile, 32)) >= 1.0


def test_the_plan_carries_the_widths_it_was_planned_with() -> None:
    settings = initial_settings(request(60.0))
    plan, _ = build_plan(request(60.0), settings)
    assert plan.brush_widths == raster_brush_widths(make_profile(), settings.raster_width)


def test_strokes_cover_the_picture_rather_than_hatching_it() -> None:
    # Widths in the wrong unit make the coverage pass believe each stroke paints
    # several plan pixels when it paints one, and the drawing comes out as
    # hatching with the paper showing through between the lines.
    plan = plan_within_budget(request(600.0))
    drawn = Raster(plan.width, plan.height, plan.background)
    for step in plan.steps:
        apply_step(drawn, step, plan.brush_widths)
    source = prepare_source(shapes(), plan.width, plan.height, plan.palette[plan.background])
    wanted = quantize(source, Palette(plan.palette)).indices
    painted = sum(1 for want in wanted if want != plan.background)
    missed = sum(
        1
        for want, got in zip(wanted, drawn.indices, strict=True)
        if want != plan.background and got == plan.background
    )
    assert missed / painted < 0.02


def test_a_generous_budget_sacrifices_nothing() -> None:
    plan = plan_within_budget(request(600.0))
    assert plan.report.sacrifices == ()
    assert plan.report.fits_budget
    assert plan.steps


def test_a_tight_budget_degrades_in_the_stated_order() -> None:
    plan = plan_within_budget(request(6.0))
    kinds = [sacrifice.kind for sacrifice in plan.report.sacrifices]
    assert kinds
    assert kinds == sorted(kinds, key=list(Degradation).index)
    assert kinds[0] is Degradation.DROP_SMALL_REGIONS


def test_degrading_actually_shortens_the_plan() -> None:
    generous = plan_within_budget(request(600.0))
    tight = plan_within_budget(request(6.0))
    assert tight.report.estimated_seconds < generous.report.estimated_seconds
    assert tight.point_count < generous.point_count


def test_an_impossible_budget_is_reported_not_hidden() -> None:
    plan = plan_within_budget(request(0.05))
    assert not plan.report.fits_budget
    assert plan.report.sacrifices
    assert plan.report.estimated_seconds > plan.report.budget_seconds


def test_a_sacrifice_that_buys_nothing_is_not_reported() -> None:
    # Every rung that gets applied has to pay for itself, otherwise the user is
    # told they lost something and got nothing back.
    plan = plan_within_budget(request(1.0))
    assert len(plan.report.sacrifices) < len(LADDER)
    for sacrifice in plan.report.sacrifices:
        assert "saving" in sacrifice.detail


def test_every_sacrifice_says_what_it_cost() -> None:
    plan = plan_within_budget(request(1.0))
    for sacrifice in plan.report.sacrifices:
        assert sacrifice.detail
        assert sacrifice.detail[0].islower()


def test_the_ladder_visits_each_degradation_in_order() -> None:
    kinds = [rung.kind for rung in LADDER]
    assert kinds == sorted(kinds, key=list(Degradation).index)
    assert set(kinds) == set(Degradation)


def test_planning_is_deterministic() -> None:
    first = plan_within_budget(request(30.0))
    second = plan_within_budget(request(30.0))
    assert first.steps == second.steps
    assert first.report.estimated_seconds == second.report.estimated_seconds


def test_the_plan_stays_inside_its_raster() -> None:
    plan = plan_within_budget(request(60.0))
    for stroke in plan.strokes:
        for point in stroke.points:
            assert 0 <= point.x < plan.width
            assert 0 <= point.y < plan.height
    for fill in plan.fills:
        assert 0 <= fill.seed.x < plan.width
        assert 0 <= fill.seed.y < plan.height


def test_a_reduced_palette_still_speaks_profile_indices() -> None:
    settings = initial_settings(request(60.0))
    narrow = replace(settings, palette_size=4)
    plan, _ = build_plan(request(60.0), narrow)
    assert plan.palette == COLORS
    assert plan.color_count <= 4
    for step in plan.steps:
        assert 0 <= step.color < len(COLORS)


def test_withholding_the_thin_brushes_leaves_the_thick_ones() -> None:
    settings = initial_settings(request(60.0))
    coarse = replace(settings, lowest_brush=1)
    plan, _ = build_plan(request(60.0), coarse)
    assert plan.strokes
    assert min(stroke.brush for stroke in plan.strokes) >= 1


def test_turning_fills_off_leaves_only_strokes() -> None:
    plan, _ = build_plan(
        request(600.0, use_fills=False), initial_settings(request(600.0, use_fills=False))
    )
    assert plan.fills == ()
    assert plan.strokes


def picker_profile() -> Profile:
    return replace(
        make_profile(),
        picker=ColorPicker(
            open=Control(x=10, y=900),
            red=Control(x=10, y=940),
            green=Control(x=40, y=940),
            blue=Control(x=70, y=940),
            preview=Control(x=10, y=900),
        ),
    )


def test_a_colour_off_the_palette_needs_a_picker() -> None:
    with pytest.raises(ValueError, match="no colour picker"):
        select_color(make_profile(), PACING, (1, 2, 3))


def test_a_colour_off_the_palette_is_typed_when_there_is_one() -> None:
    actions = select_color(picker_profile(), PACING, (1, 2, 3))
    typed = [action.text for action in actions if action.kind is ActionKind.TYPE]
    assert typed == ["1", "2", "3"]


def test_a_swatch_is_clicked_even_when_a_picker_exists() -> None:
    # Clicking a swatch is one trip; typing a colour is four clicks and three
    # numbers. A colour that is already on offer is not worth typing.
    actions = select_color(picker_profile(), PACING, COLORS[2])
    assert not [action for action in actions if action.kind is ActionKind.TYPE]


def test_typing_and_chords_are_charged_for() -> None:
    typing = [Action.type_text("255"), Action.chord(1, 2)]
    assert schedule_seconds(typing, COST) == pytest.approx(4 * COST.seconds_per_move)


def test_a_picker_frees_the_planner_from_the_swatches() -> None:
    profile = picker_profile()
    plan, _ = build_plan(
        request(600.0, profile=profile, use_picker=True, colors=8),
        initial_settings(request(600.0, profile=profile, use_picker=True, colors=8)),
    )
    # The plan speaks the picture's colours, which are not the profile's.
    assert plan.palette != COLORS
    assert len(plan.palette) <= 8


def test_without_a_bound_picker_the_swatches_are_all_there_is() -> None:
    settings = initial_settings(request(600.0, use_picker=True))
    plan, _ = build_plan(request(600.0, use_picker=True), settings)
    assert plan.palette == COLORS


def test_dithering_costs_more_time_than_it_saves() -> None:
    plain, plain_estimate = build_plan(request(600.0), initial_settings(request(600.0)))
    dithered_request = request(600.0, dither=True)
    dithered, dithered_estimate = build_plan(dithered_request, initial_settings(dithered_request))
    assert dithered_estimate > plain_estimate
    assert dithered.point_count > plain.point_count
