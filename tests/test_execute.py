"""Tests for the executor and for the honesty of the estimate.

The executor is driven by a fake pointer and a fake clock, so the abort path,
the button-release guarantee and the schedule's timing are all checked without
a screen and without a second of real waiting.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

import pytest

from penplan.budget import PlanRequest, plan_within_budget, schedule, schedule_seconds
from penplan.input_win import AbortHotkey, Executor
from penplan.model import Action, ActionKind, Point, Stroke
from test_budget import COST, PACING, make_profile, shapes

if TYPE_CHECKING:
    from penplan.model import Step

# The executor's own overhead is not modelled, so the simulated run is compared
# against the estimate within this fraction. Anything wider would mean the two
# had drifted apart in substance rather than in rounding.
HONESTY_MARGIN = 1e-6


class FakePointer:
    """Records what it was told to do, and whether it was left holding down."""

    def __init__(self) -> None:
        self.log: list[tuple[str, int, int]] = []
        self.is_down = False
        self.left_down_at_exit: bool | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.left_down_at_exit = self.is_down
        self.release()

    def move_to(self, x: int, y: int) -> None:
        self.log.append(("move", x, y))

    def press(self) -> None:
        self.log.append(("press", 0, 0))
        self.is_down = True

    def release(self) -> None:
        if self.is_down:
            self.log.append(("release", 0, 0))
        self.is_down = False


class FakeClock:
    """A clock that only moves when the executor sleeps."""

    def __init__(self) -> None:
        self.now = 0.0

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def time(self) -> float:
        return self.now


def quiet_hotkey() -> AbortHotkey:
    """Return an abort hotkey that is not registered and never triggers."""
    return AbortHotkey()


def two_strokes() -> list[Step]:
    return [
        Stroke(color=1, brush=0, points=(Point(2, 2), Point(8, 2), Point(8, 8))),
        Stroke(color=2, brush=1, points=(Point(10, 10), Point(20, 20))),
    ]


def test_the_executor_performs_every_action_in_order() -> None:
    actions = schedule(two_strokes(), (32, 32), make_profile(), PACING)
    pointer = FakePointer()
    clock = FakeClock()
    result = Executor(pointer, quiet_hotkey(), clock.sleep, clock.time).run(actions)
    assert result.actions_done == len(actions)
    assert not result.aborted
    expected = [
        (action.kind.value, action.x, action.y)
        for action in actions
        if action.kind is not ActionKind.WAIT
    ]
    assert pointer.log == expected


def test_the_simulated_run_takes_as_long_as_the_estimate() -> None:
    actions = schedule(two_strokes(), (32, 32), make_profile(), PACING)
    clock = FakeClock()
    Executor(FakePointer(), quiet_hotkey(), clock.sleep, clock.time).run(actions)
    # The fake clock only advances on waits, so what it accumulated is the
    # waiting half of the estimate; the rest is the measured cost of the events.
    waits = sum(action.seconds for action in actions if action.kind is ActionKind.WAIT)
    events = schedule_seconds(actions, COST) - waits
    assert clock.now == pytest.approx(waits)
    assert schedule_seconds(actions, COST) == pytest.approx(clock.now + events)


@pytest.mark.parametrize("stop_after", [1, 5, 17])
def test_an_abort_stops_within_one_action(stop_after: int) -> None:
    actions = schedule(two_strokes(), (32, 32), make_profile(), PACING)
    hotkey = quiet_hotkey()
    pointer = FakePointer()
    clock = FakeClock()

    def trip(done: int, _total: int) -> None:
        if done == stop_after:
            hotkey.trigger()

    result = Executor(pointer, hotkey, clock.sleep, clock.time).run(actions, trip)
    assert result.aborted
    assert result.actions_done == stop_after
    assert not pointer.is_down


def test_an_abort_mid_stroke_releases_the_button() -> None:
    actions = schedule(two_strokes(), (32, 32), make_profile(), PACING)
    first_press = next(
        index for index, action in enumerate(actions) if action.kind is ActionKind.PRESS
    )
    hotkey = quiet_hotkey()
    pointer = FakePointer()
    clock = FakeClock()

    def trip(done: int, _total: int) -> None:
        if done == first_press + 1:
            hotkey.trigger()

    result = Executor(pointer, hotkey, clock.sleep, clock.time).run(actions, trip)
    assert result.aborted
    # The button was still down when the run ended, and the pointer put it back
    # up on the way out rather than leaving the user dragging.
    assert pointer.left_down_at_exit
    assert pointer.log[-1][0] == "release"
    assert not pointer.is_down


def test_an_abort_before_the_first_action_draws_nothing() -> None:
    hotkey = quiet_hotkey()
    hotkey.trigger()
    pointer = FakePointer()
    clock = FakeClock()
    actions = schedule(two_strokes(), (32, 32), make_profile(), PACING)
    result = Executor(pointer, hotkey, clock.sleep, clock.time).run(actions)
    assert result.aborted
    assert result.actions_done == 0
    assert pointer.log == []


def test_an_empty_schedule_runs_to_completion() -> None:
    clock = FakeClock()
    result = Executor(FakePointer(), quiet_hotkey(), clock.sleep, clock.time).run([])
    assert result.actions_done == 0
    assert not result.aborted
    assert result.seconds == 0.0


def test_progress_is_reported_for_every_action() -> None:
    actions = [Action.move(1, 1), Action.press(), Action.release()]
    seen: list[tuple[int, int]] = []
    clock = FakeClock()
    Executor(FakePointer(), quiet_hotkey(), clock.sleep, clock.time).run(
        actions, lambda done, total: seen.append((done, total))
    )
    assert seen == [(1, 3), (2, 3), (3, 3)]


@pytest.mark.parametrize("budget", [8.0, 20.0, 60.0, 600.0])
def test_a_plan_is_never_longer_than_its_estimate_says(budget: float) -> None:
    profile = make_profile()
    plan = plan_within_budget(
        PlanRequest(
            image=shapes(),
            profile=profile,
            budget_seconds=budget,
            detail=0.3,
            tour_seconds=0.2,
        )
    )
    actions = schedule(plan.steps, (plan.width, plan.height), profile, profile.pacing)
    simulated = schedule_seconds(actions, profile.cost)
    assert simulated == pytest.approx(plan.report.estimated_seconds, rel=HONESTY_MARGIN)
    if plan.report.fits_budget:
        assert simulated <= budget
