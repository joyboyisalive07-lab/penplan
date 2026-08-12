"""Tests for stroke ordering."""

from __future__ import annotations

import random

import pytest

from penplan.model import Fill, Point, Stroke
from penplan.tour import (
    Leg,
    Switching,
    link_cost,
    nearest_neighbour,
    order,
    path_cost,
    plan_tour,
)

# A colour switch costs about as much as crossing a canvas, which is what makes
# the problem clustered rather than plain.
SWITCH = 600.0
COSTS = Switching(color=SWITCH)


def segment(color: int, start: tuple[int, int], end: tuple[int, int]) -> Stroke:
    return Stroke(color=color, brush=0, points=(Point(*start), Point(*end)))


def legs_of(steps: list[Stroke | Fill]) -> list[Leg]:
    return [Leg(index=index, step=step, flipped=False) for index, step in enumerate(steps)]


def test_a_leg_reports_the_ends_it_is_drawn_between() -> None:
    stroke = segment(0, (0, 0), (10, 5))
    leg = Leg(index=0, step=stroke, flipped=False)
    assert (leg.start, leg.end) == (Point(0, 0), Point(10, 5))
    assert (leg.flip().start, leg.flip().end) == (Point(10, 5), Point(0, 0))


def test_a_flipped_leg_executes_as_a_reversed_stroke() -> None:
    stroke = segment(0, (0, 0), (10, 5))
    assert Leg(index=0, step=stroke, flipped=True).oriented() == stroke.reverse()
    assert Leg(index=0, step=stroke, flipped=False).oriented() == stroke


def test_a_fill_reads_the_same_either_way_round() -> None:
    fill = Fill(color=1, seed=Point(4, 4))
    leg = Leg(index=0, step=fill, flipped=True)
    assert leg.start == leg.end == Point(4, 4)
    assert leg.oriented() == fill


def test_a_colour_change_costs_the_switch() -> None:
    first = Leg(index=0, step=segment(0, (0, 0), (0, 0)), flipped=False)
    same = Leg(index=1, step=segment(0, (3, 4), (3, 4)), flipped=False)
    other = Leg(index=2, step=segment(1, (3, 4), (3, 4)), flipped=False)
    assert link_cost(first, same, COSTS) == pytest.approx(5.0)
    assert link_cost(first, other, COSTS) == pytest.approx(5.0 + SWITCH)


def test_greedy_finishes_a_colour_before_moving_on() -> None:
    steps: list[Stroke | Fill] = [
        segment(0, (0, 0), (5, 0)),
        segment(1, (6, 0), (10, 0)),
        segment(0, (11, 0), (15, 0)),
        segment(1, (16, 0), (20, 0)),
    ]
    colors = [leg.color for leg in nearest_neighbour(steps)]
    assert colors in ([0, 0, 1, 1], [1, 1, 0, 0])


def test_greedy_picks_the_near_end_of_a_stroke() -> None:
    steps: list[Stroke | Fill] = [
        segment(0, (0, 0), (1, 0)),
        # Its far end is nearer, so it should be drawn backwards.
        segment(0, (40, 0), (3, 0)),
    ]
    legs = nearest_neighbour(steps)
    assert legs[1].flipped
    assert legs[1].start == Point(3, 0)


def ends(step: Stroke) -> tuple[tuple[int, int], ...]:
    """Return a stroke's endpoints in a form that ignores which way it is drawn."""
    return tuple(sorted((point.x, point.y) for point in (step.points[0], step.points[-1])))


def test_ordering_keeps_every_step_exactly_once() -> None:
    random.seed(11)
    steps: list[Stroke | Fill] = [
        segment(
            index % 3,
            (random.randrange(500), random.randrange(500)),
            (random.randrange(500), random.randrange(500)),
        )
        for index in range(60)
    ]
    result = order(steps, color_switch_cost=SWITCH, time_limit=0.5)
    assert len(result.steps) == len(steps)
    assert sorted(ends(step) for step in result.steps) == sorted(ends(step) for step in steps)


def test_ordering_beats_the_order_the_planner_produced() -> None:
    random.seed(13)
    steps: list[Stroke | Fill] = [
        segment(index % 4, (random.randrange(600), random.randrange(400)), (10, 10))
        for index in range(150)
    ]
    result = order(steps, color_switch_cost=SWITCH, time_limit=1.0)
    assert result.length < result.arrival_length
    assert result.total_improvement > 0.3


def test_total_improvement_is_zero_without_a_baseline() -> None:
    steps: list[Stroke | Fill] = [segment(0, (0, 0), (1, 1))]
    assert order(steps, color_switch_cost=SWITCH, time_limit=0.1).total_improvement == 0.0


def test_long_edges_get_a_partner_the_neighbour_lists_would_miss() -> None:
    # Two tight clusters far apart, interleaved on arrival. The improving move
    # pairs steps that are nowhere near each other, which is exactly what a
    # neighbour list cannot propose.
    steps: list[Stroke | Fill] = []
    for index in range(12):
        steps.append(segment(0, (index * 3, 0), (index * 3 + 2, 0)))
        steps.append(segment(0, (900 + index * 3, 400), (902 + index * 3, 400)))
    result = order(steps, color_switch_cost=SWITCH, time_limit=1.0)
    crossings = sum(
        1
        for first, second in zip(result.steps, result.steps[1:], strict=False)
        if abs(first.points[-1].x - second.points[0].x) > 400
    )
    assert crossings <= 2


def test_improvement_beats_the_greedy_tour() -> None:
    random.seed(5)
    steps: list[Stroke | Fill] = []
    for index in range(120):
        x, y = random.randrange(800), random.randrange(600)
        steps.append(segment(index % 4, (x, y), (x + random.randrange(-20, 20), y + 5)))
    result = order(steps, color_switch_cost=SWITCH, time_limit=1.0)
    assert result.length < result.greedy_length
    assert result.improvement > 0.02


def test_improvement_is_reported_as_a_fraction() -> None:
    steps: list[Stroke | Fill] = [segment(0, (0, 0), (1, 1))]
    result = order(steps, color_switch_cost=SWITCH, time_limit=0.1)
    assert result.improvement == 0.0


def test_a_deadline_that_has_passed_still_returns_a_tour() -> None:
    random.seed(3)
    steps: list[Stroke | Fill] = [
        segment(0, (random.randrange(200), random.randrange(200)), (10, 10)) for _ in range(40)
    ]
    result = order(steps, color_switch_cost=SWITCH, time_limit=0.0)
    assert len(result.steps) == len(steps)
    assert result.length == pytest.approx(result.greedy_length)


def test_ordering_is_deterministic() -> None:
    random.seed(9)
    steps: list[Stroke | Fill] = [
        segment(index % 3, (random.randrange(300), random.randrange(300)), (5, 5))
        for index in range(50)
    ]
    first = order(steps, color_switch_cost=SWITCH, time_limit=0.3)
    second = order(steps, color_switch_cost=SWITCH, time_limit=0.3)
    assert first.steps == second.steps


def test_phases_are_never_reordered_against_each_other() -> None:
    outlines: list[Stroke | Fill] = [segment(0, (0, 0), (5, 0)), segment(1, (100, 100), (105, 100))]
    fills: list[Stroke | Fill] = [
        Fill(color=0, seed=Point(2, 2)),
        Fill(color=1, seed=Point(102, 102)),
    ]
    interior: list[Stroke | Fill] = [segment(0, (1, 1), (4, 1))]
    result = plan_tour([outlines, fills, interior], color_switch_cost=SWITCH, time_limit=0.2)
    kinds = [isinstance(step, Fill) for step in result.steps]
    assert kinds == [False, False, True, True, False]


def test_an_empty_plan_orders_to_nothing() -> None:
    result = plan_tour([[], []], color_switch_cost=SWITCH, time_limit=0.1)
    assert result.steps == ()
    assert result.improvement == 0.0


def test_path_cost_adds_up_the_links() -> None:
    steps: list[Stroke | Fill] = [
        segment(0, (0, 0), (10, 0)),
        segment(0, (20, 0), (30, 0)),
        segment(1, (40, 0), (50, 0)),
    ]
    assert path_cost(legs_of(steps), COSTS) == pytest.approx(10.0 + 10.0 + SWITCH)


def test_a_single_step_has_no_travel() -> None:
    assert path_cost(legs_of([segment(0, (0, 0), (9, 9))]), COSTS) == 0.0
    assert path_cost([], COSTS) == 0.0


def test_ordering_lowers_the_cost_of_a_shuffled_grid() -> None:
    # Strokes laid out on a grid and then shuffled: any sensible ordering has
    # to beat the order they arrived in by a wide margin.
    random.seed(2)
    steps: list[Stroke | Fill] = [
        segment(0, (x * 40, y * 40), (x * 40 + 20, y * 40)) for x in range(8) for y in range(8)
    ]
    random.shuffle(steps)
    arrival = path_cost(legs_of(steps), COSTS)
    result = order(steps, color_switch_cost=SWITCH, time_limit=1.0)
    assert result.length < arrival / 3
