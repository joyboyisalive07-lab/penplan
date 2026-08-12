"""Tests for the shared value types."""

from __future__ import annotations

import math

import pytest

from penplan.model import (
    DEFAULT_COST_MODEL,
    CostModel,
    Degradation,
    DrawPlan,
    Fill,
    PlanReport,
    Point,
    Sacrifice,
    ScreenRect,
    Stroke,
)

PALETTE = ((0, 0, 0), (255, 255, 255), (255, 0, 0))
BRUSHES = (1, 4, 12)


def make_report(**overrides: object) -> PlanReport:
    fields: dict[str, object] = {
        "estimated_seconds": 10.0,
        "budget_seconds": 60.0,
        "tour_length": 800.0,
        "greedy_tour_length": 1000.0,
        "arrival_tour_length": 2000.0,
        "sacrifices": (),
    }
    fields.update(overrides)
    return PlanReport(**fields)  # type: ignore[arg-type]


def make_plan(steps: tuple[Stroke | Fill, ...]) -> DrawPlan:
    return DrawPlan(
        width=64,
        height=64,
        palette=PALETTE,
        brush_widths=BRUSHES,
        steps=steps,
        report=make_report(),
    )


def test_point_distance_is_euclidean() -> None:
    assert Point(0, 0).distance_to(Point(3, 4)) == pytest.approx(5.0)


def test_stroke_length_sums_segments() -> None:
    stroke = Stroke(color=0, brush=0, points=(Point(0, 0), Point(0, 3), Point(4, 3)))
    assert stroke.drawn_length() == pytest.approx(7.0)


def test_single_point_stroke_has_zero_length() -> None:
    assert Stroke(color=0, brush=0, points=(Point(5, 5),)).drawn_length() == 0.0


def test_stroke_reverse_swaps_ends_and_keeps_length() -> None:
    stroke = Stroke(color=1, brush=2, points=(Point(0, 0), Point(10, 0), Point(10, 10)))
    reversed_stroke = stroke.reverse()
    assert reversed_stroke.start == stroke.end
    assert reversed_stroke.end == stroke.start
    assert reversed_stroke.drawn_length() == pytest.approx(stroke.drawn_length())
    assert reversed_stroke.reverse() == stroke


def test_empty_stroke_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one point"):
        Stroke(color=0, brush=0, points=())


def test_negative_indices_are_rejected() -> None:
    with pytest.raises(ValueError, match="colour index"):
        Stroke(color=-1, brush=0, points=(Point(0, 0),))
    with pytest.raises(ValueError, match="brush index"):
        Stroke(color=0, brush=-1, points=(Point(0, 0),))
    with pytest.raises(ValueError, match="colour index"):
        Fill(color=-1, seed=Point(0, 0))


def test_plan_counts_split_strokes_and_fills() -> None:
    plan = make_plan(
        (
            Stroke(color=0, brush=0, points=(Point(0, 0), Point(1, 1))),
            Fill(color=2, seed=Point(20, 20)),
            Stroke(color=2, brush=1, points=(Point(4, 4), Point(5, 4), Point(6, 4))),
        )
    )
    assert len(plan.strokes) == 2
    assert len(plan.fills) == 1
    assert plan.point_count == 5
    assert plan.color_count == 2


def test_plan_rejects_out_of_range_palette_index() -> None:
    with pytest.raises(ValueError, match="outside the 3-entry palette"):
        make_plan((Fill(color=7, seed=Point(1, 1)),))


def test_plan_rejects_out_of_range_brush_index() -> None:
    with pytest.raises(ValueError, match="outside the 3 brushes"):
        make_plan((Stroke(color=0, brush=9, points=(Point(1, 1),)),))


def test_plan_rejects_empty_palette() -> None:
    with pytest.raises(ValueError, match="needs a palette"):
        DrawPlan(
            width=8,
            height=8,
            palette=(),
            brush_widths=BRUSHES,
            steps=(),
            report=make_report(),
        )


def test_report_tour_improvement_is_a_fraction() -> None:
    report = make_report(tour_length=750.0, greedy_tour_length=1000.0)
    assert report.tour_improvement == pytest.approx(0.25)
    assert report.fits_budget


def test_report_improvement_is_zero_without_a_baseline() -> None:
    assert make_report(greedy_tour_length=0.0).tour_improvement == 0.0


def test_report_knows_when_it_overruns() -> None:
    assert not make_report(estimated_seconds=90.0, budget_seconds=60.0).fits_budget


def test_sacrifice_carries_its_kind() -> None:
    sacrifice = Sacrifice(kind=Degradation.DROP_SMALL_REGIONS, detail="dropped 12 regions")
    assert sacrifice.kind is Degradation.DROP_SMALL_REGIONS


def test_default_cost_model_is_finite_and_positive() -> None:
    for name in CostModel.__slots__:
        value = getattr(DEFAULT_COST_MODEL, name)
        assert math.isfinite(value)
        assert value > 0


def test_screen_rect_edges_are_exclusive() -> None:
    rect = ScreenRect(left=100, top=50, width=800, height=600)
    assert rect.right == 900
    assert rect.bottom == 650
    assert rect.contains(100, 50)
    assert rect.contains(899, 649)
    assert not rect.contains(900, 649)
    assert not rect.contains(899, 650)


def test_screen_rect_accepts_a_negative_origin() -> None:
    rect = ScreenRect(left=-1920, top=-200, width=3840, height=1280)
    assert rect.contains(-1920, -200)
    assert not rect.contains(-1921, -200)


def test_screen_rect_rejects_zero_size() -> None:
    with pytest.raises(ValueError, match="size must be positive"):
        ScreenRect(left=0, top=0, width=0, height=100)


def test_negative_cost_is_rejected() -> None:
    with pytest.raises(ValueError, match="seconds_per_click"):
        CostModel(
            seconds_per_move=0.01,
            seconds_per_pixel=0.001,
            seconds_per_click=-1.0,
            seconds_per_color_switch=0.2,
            seconds_per_tool_switch=0.2,
        )
