"""Tests for the time budget: the estimate, the ladder, and the honesty."""

from __future__ import annotations

from dataclasses import replace

import pytest
from PIL import Image, ImageDraw

from penplan.budget import (
    LADDER,
    PlanRequest,
    build_plan,
    estimate_seconds,
    initial_settings,
    plan_within_budget,
    raster_size,
)
from penplan.model import CostModel, Degradation, Fill, Point, ScreenRect, Stroke
from penplan.profile import BrushControl, Control, Profile, Swatch

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
    seconds_per_pixel=0.001,
    seconds_per_click=0.1,
    seconds_per_color_switch=0.5,
    seconds_per_tool_switch=0.2,
)


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


def test_estimate_counts_events_not_guesses() -> None:
    steps = [
        Stroke(color=0, brush=0, points=(Point(0, 0), Point(10, 0), Point(10, 10))),
        Stroke(color=1, brush=0, points=(Point(10, 10), Point(20, 10))),
    ]
    # First stroke: arrive 0.01, colour switch 0.5, tool switch 0.2, click 0.1,
    # two further points 0.02, twenty pixels drawn 0.02, which is 0.85.
    # Second: no travel, arrive 0.01, colour switch 0.5, click 0.1, one further
    # point 0.01, ten pixels drawn 0.01, which is 0.63.
    assert estimate_seconds(steps, COST) == pytest.approx(1.48)


def test_a_fill_costs_a_click_and_the_tool_switches_around_it() -> None:
    steps = [
        Stroke(color=0, brush=0, points=(Point(0, 0), Point(1, 0))),
        Fill(color=0, seed=Point(5, 5)),
        Stroke(color=0, brush=0, points=(Point(5, 5), Point(6, 5))),
    ]
    with_fill = estimate_seconds(steps, COST)
    without_fill = estimate_seconds([steps[0], steps[2]], COST)
    # The fill itself, plus switching to it and back again.
    assert with_fill - without_fill > 2 * COST.seconds_per_tool_switch


def test_an_empty_plan_takes_no_time() -> None:
    assert estimate_seconds([], COST) == 0.0


def test_the_raster_follows_the_detail_setting() -> None:
    profile = make_profile()
    coarse = raster_size(profile, 0.2)
    fine = raster_size(profile, 1.0)
    assert coarse[0] < fine[0]
    assert fine == (300, 225)


def test_the_raster_never_collapses() -> None:
    assert raster_size(make_profile(), 0.0)[0] >= 32


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


def test_dithering_costs_more_time_than_it_saves() -> None:
    plain, plain_estimate = build_plan(request(600.0), initial_settings(request(600.0)))
    dithered_request = request(600.0, dither=True)
    dithered, dithered_estimate = build_plan(dithered_request, initial_settings(dithered_request))
    assert dithered_estimate > plain_estimate
    assert dithered.point_count > plain.point_count
