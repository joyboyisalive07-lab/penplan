"""Order the plan so the mouse stops wandering.

Drawing order is a travelling-salesman problem with a twist that changes the
answer: switching colour costs a trip to the palette and a click, not just the
distance between two strokes. That makes it a clustered problem, where visiting
everything of one colour before moving on is nearly always right, and where the
penalty has to sit inside the cost function rather than beside it.

The second difference from a textbook tour is that a stroke can be drawn either
way round. Reversing one costs nothing and changes which end the next stroke
starts from, so orientation is part of what is being optimised. That falls out
of 2-opt for free: reversing a stretch of the tour reverses the strokes in it,
and because distance is symmetric, only the two edges at the ends of the
stretch change cost.

Costs here are in canvas pixels. A colour switch is expressed as the distance
the mouse could have travelled in the same time, so one number covers both.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from penplan.model import Fill, Stroke

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

    from penplan.model import Point, Step

# How long the improvement phase may run before the plan has to be delivered.
# The user is waiting, and 2-opt returns most of its gain in the first pass.
DEFAULT_TIME_LIMIT: Final = 1.0

# Each stroke considers moves involving only this many nearby strokes. Full
# 2-opt is quadratic in the number of strokes and spends nearly all of that on
# pairs at opposite ends of the canvas, which never improve anything.
NEIGHBOUR_COUNT: Final = 12

# Or-opt relocates runs of up to this many strokes. Longer runs are what 2-opt
# already handles by reversal.
MAX_SEGMENT: Final = 3

# How many of the tour's worst edges get a full search for a partner rather
# than a neighbour-list one. Long edges are where the neighbour lists fail, and
# on a scattered plan they hold most of the improvement left.
LONG_EDGE_COUNT: Final = 48

# Target number of endpoints per grid cell when searching for nearby strokes.
_CELL_OCCUPANCY: Final = 2.0
# Below this many steps there is nothing for 2-opt to exchange.
_MIN_TOUR_FOR_IMPROVEMENT: Final = 4
# Gains smaller than this are floating-point noise, and accepting them would
# let the improvement loop cycle for as long as the deadline allows.
_MIN_GAIN: Final = 1e-9


@dataclass(frozen=True, slots=True)
class Leg:
    """One step in the tour, with the direction it is drawn in.

    ``index`` is the step's position in the input and survives flipping, so the
    improvement passes can find where a step currently sits in the tour.
    """

    index: int
    step: Step
    flipped: bool

    @property
    def color(self) -> int:
        """Return the palette index this step draws with."""
        return self.step.color

    @property
    def brush(self) -> int:
        """Return the brush index, or -1 for a fill, which uses none."""
        return -1 if isinstance(self.step, Fill) else self.step.brush

    @property
    def start(self) -> Point:
        """Return the point where the mouse arrives."""
        if isinstance(self.step, Stroke):
            return self.step.end if self.flipped else self.step.start
        return self.step.seed

    @property
    def end(self) -> Point:
        """Return the point where the mouse leaves."""
        if isinstance(self.step, Stroke):
            return self.step.start if self.flipped else self.step.end
        return self.step.seed

    def flip(self) -> Leg:
        """Return the same step drawn the other way round."""
        return Leg(index=self.index, step=self.step, flipped=not self.flipped)

    def oriented(self) -> Step:
        """Return the step as it will be executed."""
        if self.flipped and isinstance(self.step, Stroke):
            return self.step.reverse()
        return self.step


@dataclass(frozen=True, slots=True)
class TourResult:
    """The ordered plan, and what the ordering was worth.

    Two families of number, because they answer different questions. The costs
    are what the optimiser minimised, travel and switching together, and only
    they can say whether it did its job. The travels are plain canvas pixels,
    and only they mean anything to a reader, because a cost moves when the
    measured cost model moves and a distance does not.

    Each family covers the three stages: the order the planner produced, what
    the greedy construction made of it, and what the improvement passes made of
    that.
    """

    steps: tuple[Step, ...]
    cost: float
    greedy_cost: float
    arrival_cost: float
    travel: float
    greedy_travel: float
    arrival_travel: float

    @property
    def improvement(self) -> float:
        """Return the fraction of cost the improvement passes saved over greedy."""
        if self.greedy_cost <= 0:
            return 0.0
        return 1.0 - self.cost / self.greedy_cost

    @property
    def travel_improvement(self) -> float:
        """Return the fraction of travel saved against the order planned."""
        if self.arrival_travel <= 0:
            return 0.0
        return 1.0 - self.travel / self.arrival_travel


@dataclass(frozen=True, slots=True)
class Switching:
    """What changing colour or brush costs, as an equivalent travel distance.

    Expressing both as distances is what lets the optimiser trade them against
    travel and against each other with one number.
    """

    color: float
    brush: float = 0.0


_TRAVEL_ONLY: Final = Switching(color=0.0, brush=0.0)
"""Measures pure geometry: what the mouse covers with the switching set aside."""


def link_cost(first: Leg, second: Leg, switch: Switching) -> float:
    """Return the cost of drawing ``second`` immediately after ``first``."""
    cost = first.end.distance_to(second.start)
    if first.color != second.color:
        cost += switch.color
    if first.brush != second.brush:
        cost += switch.brush
    return cost


def path_cost(legs: Sequence[Leg], switch: Switching) -> float:
    """Return the total cost of a tour, travel and colour switches together."""
    return sum(link_cost(legs[index], legs[index + 1], switch) for index in range(len(legs) - 1))


def _cell_size(points: Sequence[Point]) -> float:
    if len(points) <= 1:
        return 1.0
    width = max(point.x for point in points) - min(point.x for point in points) + 1
    height = max(point.y for point in points) - min(point.y for point in points) + 1
    return max(1.0, math.sqrt(width * height * _CELL_OCCUPANCY / len(points)))


def _ring(centre: tuple[int, int], radius: int) -> Iterator[tuple[int, int]]:
    """Yield the cells exactly ``radius`` cells away, Chebyshev style."""
    if radius == 0:
        yield centre
        return
    for offset in range(-radius, radius + 1):
        yield centre[0] + offset, centre[1] - radius
        yield centre[0] + offset, centre[1] + radius
    for offset in range(-radius + 1, radius):
        yield centre[0] - radius, centre[1] + offset
        yield centre[0] + radius, centre[1] + offset


class _Grid:
    """Buckets points by cell so the nearest one can be found without a scan."""

    def __init__(self, cell: float) -> None:
        self._cell = cell
        self._buckets: dict[tuple[int, int], list[tuple[int, Point]]] = {}
        self._taken: set[int] = set()

    def add(self, key: int, point: Point) -> None:
        """File a point under its cell."""
        self._buckets.setdefault(self._index(point), []).append((key, point))

    def discard(self, key: int) -> None:
        """Forget every entry filed under a key."""
        self._taken.add(key)

    def _index(self, point: Point) -> tuple[int, int]:
        return int(point.x // self._cell), int(point.y // self._cell)

    def nearest(self, origin: Point) -> int | None:
        """Return the key of the nearest live point, or None if there is none."""
        centre = self._index(origin)
        best_key: int | None = None
        best_distance = math.inf
        radius = 0
        limit = self._search_limit(centre)
        while radius <= limit:
            for cell in _ring(centre, radius):
                best_key, best_distance = self._scan_cell(cell, origin, best_key, best_distance)
            # Anything not yet examined lies at least this far away, so a
            # candidate closer than that cannot be beaten by a further ring.
            if best_key is not None and (radius * self._cell) ** 2 >= best_distance:
                break
            radius += 1
        return best_key

    def _scan_cell(
        self, cell: tuple[int, int], origin: Point, best_key: int | None, best_distance: float
    ) -> tuple[int | None, float]:
        bucket = self._buckets.get(cell)
        if not bucket:
            return best_key, best_distance
        live = [entry for entry in bucket if entry[0] not in self._taken]
        if len(live) != len(bucket):
            self._buckets[cell] = live
        for key, point in live:
            distance = (point.x - origin.x) ** 2 + (point.y - origin.y) ** 2
            if distance < best_distance:
                best_key, best_distance = key, distance
        return best_key, best_distance

    def _search_limit(self, centre: tuple[int, int]) -> int:
        if not self._buckets:
            return 0
        return (
            max(max(abs(cell[0] - centre[0]), abs(cell[1] - centre[1])) for cell in self._buckets)
            + 1
        )


def _build_grid(legs: Iterable[Leg]) -> _Grid:
    entries = [(leg.index, leg.start, leg.end) for leg in legs]
    points = [point for _, start, end in entries for point in (start, end)]
    grid = _Grid(_cell_size(points))
    for index, start, end in entries:
        grid.add(index * 2, start)
        grid.add(index * 2 + 1, end)
    return grid


def nearest_neighbour(steps: Sequence[Step]) -> list[Leg]:
    """Build a tour greedily: finish a colour, then jump to the nearest next one.

    Both ends of every stroke go into the grid, so the nearest end decides
    which way round the stroke is drawn. Colours are exhausted one at a time
    because a switch costs more than crossing the canvas, and the tour that
    comes out is the baseline the improvement phase is measured against.
    """
    if not steps:
        return []
    legs = [Leg(index=index, step=step, flipped=False) for index, step in enumerate(steps)]
    by_color: dict[int, list[Leg]] = {}
    for leg in legs:
        by_color.setdefault(leg.color, []).append(leg)

    remaining = {color: _build_grid(members) for color, members in by_color.items()}
    entry_grid = _build_grid(legs)
    ordered: list[Leg] = []
    position = legs[0].start
    while remaining:
        color = _nearest_color(entry_grid, legs, remaining, position)
        grid = remaining.pop(color)
        while True:
            key = grid.nearest(position)
            if key is None:
                break
            index, flipped = divmod(key, 2)
            leg = Leg(index=index, step=steps[index], flipped=bool(flipped))
            ordered.append(leg)
            position = leg.end
            for entry in (index * 2, index * 2 + 1):
                grid.discard(entry)
                entry_grid.discard(entry)
    return ordered


def _nearest_color(
    entry_grid: _Grid, legs: Sequence[Leg], remaining: dict[int, _Grid], position: Point
) -> int:
    key = entry_grid.nearest(position)
    if key is not None:
        color = legs[key // 2].color
        if color in remaining:
            return color
    return next(iter(remaining))


def _endpoints(legs: Sequence[Leg]) -> list[tuple[Point, Point]]:
    ends: list[tuple[Point, Point]] = [(legs[0].start, legs[0].end)] * len(legs)
    for leg in legs:
        step = leg.step
        ends[leg.index] = (step.start, step.end) if isinstance(step, Stroke) else (step.seed,) * 2
    return ends


def _separation(first: tuple[Point, Point], second: tuple[Point, Point]) -> float:
    """Return the closest either end of one step comes to either end of another."""
    return min((one.x - other.x) ** 2 + (one.y - other.y) ** 2 for one in first for other in second)


def _candidate_lists(legs: Sequence[Leg], count: int) -> list[list[int]]:
    """Return, for each step, a handful of steps that lie near it.

    Restricting the improvement passes to these turns two quadratic searches
    into linear ones. Both ends of a step are filed and both are searched from,
    because a stroke's useful neighbour is as often the one near the end it
    finishes at as the one near where it starts. Ranking by only one end costs
    around a third of the improvement 2-opt would otherwise find.

    The lists are built once and never rebuilt: they are a hint about what is
    worth trying, not a fact about the tour.
    """
    ends = _endpoints(legs)
    cell = _cell_size([point for pair in ends for point in pair])
    buckets: dict[tuple[int, int], set[int]] = {}
    for index, pair in enumerate(ends):
        for point in pair:
            buckets.setdefault((int(point.x // cell), int(point.y // cell)), set()).add(index)
    candidates: list[list[int]] = []
    for index, pair in enumerate(ends):
        nearby: set[int] = set()
        for point in pair:
            centre = (int(point.x // cell), int(point.y // cell))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    nearby.update(buckets.get((centre[0] + dx, centre[1] + dy), ()))
        nearby.discard(index)
        ranked = sorted(nearby, key=lambda other: _separation(pair, ends[other]))
        candidates.append(ranked[:count])
    return candidates


def _two_opt_pass(
    legs: list[Leg],
    position: list[int],
    candidates: Sequence[Sequence[int]],
    switch: Switching,
    deadline: float,
) -> bool:
    """Reverse stretches of the tour where that shortens it."""
    improved = False
    last = len(legs) - 1
    for first in range(last):
        if time.monotonic() > deadline:
            return improved
        head = legs[first]
        for other in candidates[head.index]:
            second = position[other]
            if second <= first + 1:
                continue
            gain = link_cost(head, legs[first + 1], switch) - link_cost(
                head, legs[second].flip(), switch
            )
            if second < last:
                gain += link_cost(legs[second], legs[second + 1], switch) - link_cost(
                    legs[first + 1].flip(), legs[second + 1], switch
                )
            if gain > _MIN_GAIN:
                legs[first + 1 : second + 1] = [
                    leg.flip() for leg in reversed(legs[first + 1 : second + 1])
                ]
                for index in range(first + 1, second + 1):
                    position[legs[index].index] = index
                improved = True
                break
    return improved


def _long_edge_pass(
    legs: list[Leg], position: list[int], switch: Switching, deadline: float
) -> bool:
    """Give the worst edges in the tour a full search for a partner.

    A neighbour list only proposes partners that are geometrically close, and
    for a short edge that is where every improvement lives. A long edge is
    different: the move that removes it can pair it with a step anywhere in the
    tour, because the new edge only has to be shorter than the long one it
    replaces. Those are exactly the moves the neighbour lists miss, and on a
    scattered plan they are most of the remaining gain.

    Only the worst few edges get this treatment, so the pass stays linear in
    the number of steps rather than quadratic.
    """
    improved = False
    last = len(legs) - 1
    ranked = sorted(range(last), key=lambda index: -link_cost(legs[index], legs[index + 1], switch))
    for first in ranked[:LONG_EDGE_COUNT]:
        if time.monotonic() > deadline:
            return improved
        head = legs[first]
        for second in range(first + 2, len(legs)):
            gain = link_cost(head, legs[first + 1], switch) - link_cost(
                head, legs[second].flip(), switch
            )
            if second < last:
                gain += link_cost(legs[second], legs[second + 1], switch) - link_cost(
                    legs[first + 1].flip(), legs[second + 1], switch
                )
            if gain > _MIN_GAIN:
                legs[first + 1 : second + 1] = [
                    leg.flip() for leg in reversed(legs[first + 1 : second + 1])
                ]
                for index in range(first + 1, second + 1):
                    position[legs[index].index] = index
                improved = True
                break
    return improved


def _or_opt_pass(
    legs: list[Leg],
    position: list[int],
    candidates: Sequence[Sequence[int]],
    switch: Switching,
    deadline: float,
) -> bool:
    """Move short runs of steps elsewhere in the tour, either way round."""
    improved = False
    for length in range(1, MAX_SEGMENT + 1):
        start = 0
        while start + length <= len(legs):
            if time.monotonic() > deadline:
                return improved
            if _try_relocate(legs, position, candidates, switch, (start, length)):
                improved = True
            start += 1
    return improved


def _removal_gain(legs: Sequence[Leg], start: int, stop: int, switch: Switching) -> float:
    before = legs[start - 1] if start > 0 else None
    after = legs[stop + 1] if stop + 1 < len(legs) else None
    gain = 0.0
    if before is not None:
        gain += link_cost(before, legs[start], switch)
    if after is not None:
        gain += link_cost(legs[stop], after, switch)
    if before is not None and after is not None:
        gain -= link_cost(before, after, switch)
    return gain


def _try_relocate(
    legs: list[Leg],
    position: list[int],
    candidates: Sequence[Sequence[int]],
    switch: Switching,
    segment: tuple[int, int],
) -> bool:
    """Try every placement of one run of steps, and take the first that helps."""
    start, length = segment
    stop = start + length - 1
    removal = _removal_gain(legs, start, stop, switch)
    if removal <= _MIN_GAIN:
        return False
    run = legs[start : stop + 1]
    reversed_run = [leg.flip() for leg in reversed(run)]
    for other in candidates[legs[start].index]:
        target = position[other]
        if start - 1 <= target <= stop:
            continue
        destination = legs[target]
        follow = legs[target + 1] if target + 1 < len(legs) else None
        for oriented in (run, reversed_run):
            added = link_cost(destination, oriented[0], switch)
            if follow is not None:
                added += link_cost(oriented[-1], follow, switch) - link_cost(
                    destination, follow, switch
                )
            if removal - added > _MIN_GAIN:
                _relocate(legs, position, (start, stop), target, oriented)
                return True
    return False


def _relocate(
    legs: list[Leg],
    position: list[int],
    segment: tuple[int, int],
    target: int,
    oriented: Sequence[Leg],
) -> None:
    """Cut a run out of the tour and splice it back in after ``target``."""
    start, stop = segment
    del legs[start : stop + 1]
    insert = target + 1 if target < start else target + 1 - (stop - start + 1)
    legs[insert:insert] = oriented
    # Cheaper to renumber the whole tour than to reason about which side of the
    # cut every step ended up on, and relocations become rare within a pass.
    for index, leg in enumerate(legs):
        position[leg.index] = index


def improve(legs: list[Leg], switch: Switching, deadline: float) -> list[Leg]:
    """Run 2-opt and Or-opt alternately until they stop helping or time runs out."""
    if len(legs) < _MIN_TOUR_FOR_IMPROVEMENT:
        return legs
    candidates = _candidate_lists(legs, NEIGHBOUR_COUNT)
    position = [0] * len(legs)
    for index, leg in enumerate(legs):
        position[leg.index] = index
    while time.monotonic() < deadline:
        two_opt = _two_opt_pass(legs, position, candidates, switch, deadline)
        long_edges = _long_edge_pass(legs, position, switch, deadline)
        or_opt = _or_opt_pass(legs, position, candidates, switch, deadline)
        if not (two_opt or long_edges or or_opt):
            break
    return legs


def order(
    steps: Sequence[Step],
    *,
    color_switch_cost: float,
    brush_switch_cost: float = 0.0,
    time_limit: float = DEFAULT_TIME_LIMIT,
) -> TourResult:
    """Order one group of steps, greedily and then by improvement."""
    switch = Switching(color=color_switch_cost, brush=brush_switch_cost)
    as_planned = [Leg(index=index, step=step, flipped=False) for index, step in enumerate(steps)]
    legs = nearest_neighbour(steps)
    greedy_cost = path_cost(legs, switch)
    greedy_travel = path_cost(legs, _TRAVEL_ONLY)
    improved = improve(legs, switch, time.monotonic() + time_limit)
    return TourResult(
        steps=tuple(leg.oriented() for leg in improved),
        cost=path_cost(improved, switch),
        greedy_cost=greedy_cost,
        arrival_cost=path_cost(as_planned, switch),
        travel=path_cost(improved, _TRAVEL_ONLY),
        greedy_travel=greedy_travel,
        arrival_travel=path_cost(as_planned, _TRAVEL_ONLY),
    )


def plan_tour(
    phases: Sequence[Sequence[Step]],
    *,
    color_switch_cost: float,
    brush_switch_cost: float = 0.0,
    time_limit: float = DEFAULT_TIME_LIMIT,
) -> TourResult:
    """Order every phase in turn and join them into one plan.

    The phases are ordered but never reordered: every outline is drawn before
    any fill, because that is the canvas state the fills were proved against,
    and the interior strokes come last because they were planned against a
    canvas that already had the fills on it.
    """
    total = sum(len(phase) for phase in phases) or 1
    steps: list[Step] = []
    totals = [0.0] * 6
    for phase in phases:
        if not phase:
            continue
        share = time_limit * len(phase) / total
        result = order(
            phase,
            color_switch_cost=color_switch_cost,
            brush_switch_cost=brush_switch_cost,
            time_limit=share,
        )
        steps.extend(result.steps)
        for index, value in enumerate(
            (
                result.cost,
                result.greedy_cost,
                result.arrival_cost,
                result.travel,
                result.greedy_travel,
                result.arrival_travel,
            )
        ):
            totals[index] += value
    cost, greedy_cost, arrival_cost, travel, greedy_travel, arrival_travel = totals
    return TourResult(
        steps=tuple(steps),
        cost=cost,
        greedy_cost=greedy_cost,
        arrival_cost=arrival_cost,
        travel=travel,
        greedy_travel=greedy_travel,
        arrival_travel=arrival_travel,
    )
