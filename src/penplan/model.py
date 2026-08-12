"""Value types shared by the planner, the renderer and the executor.

Everything in this module is plain data: no I/O, no Windows API, no Pillow.
Coordinates are integer pixels in canvas space, with the origin at the canvas
top-left corner. The mapping from canvas space to physical screen pixels is a
calibration concern and lives in :mod:`penplan.profile`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Final

type Rgb = tuple[int, int, int]
"""A colour as 8-bit sRGB components, the form Pillow and GDI both speak."""


@dataclass(frozen=True, slots=True)
class Point:
    """An integer pixel position in canvas space."""

    x: int
    y: int

    def distance_to(self, other: Point) -> float:
        """Return the Euclidean distance to ``other`` in canvas pixels."""
        return math.hypot(self.x - other.x, self.y - other.y)


@dataclass(frozen=True, slots=True)
class Stroke:
    """A pen-down polyline drawn in one palette colour with one brush size.

    A stroke with a single point is a dot: the executor presses and releases
    without moving, which is how isolated pixels are drawn.
    """

    color: int
    brush: int
    points: tuple[Point, ...]

    def __post_init__(self) -> None:
        """Reject strokes that no executor could carry out."""
        if not self.points:
            msg = "a stroke needs at least one point"
            raise ValueError(msg)
        if self.color < 0:
            msg = f"colour index must be non-negative, got {self.color}"
            raise ValueError(msg)
        if self.brush < 0:
            msg = f"brush index must be non-negative, got {self.brush}"
            raise ValueError(msg)

    @property
    def start(self) -> Point:
        """Return the point where the button goes down."""
        return self.points[0]

    @property
    def end(self) -> Point:
        """Return the point where the button comes back up."""
        return self.points[-1]

    def reverse(self) -> Stroke:
        """Return the same stroke drawn backwards.

        Reversal leaves the painted result identical, so the tour optimiser is
        free to use it to shorten travel between strokes.
        """
        return Stroke(color=self.color, brush=self.brush, points=tuple(reversed(self.points)))

    def drawn_length(self) -> float:
        """Return the pen-down path length in canvas pixels."""
        return sum(
            first.distance_to(second)
            for first, second in zip(self.points, self.points[1:], strict=False)
        )


@dataclass(frozen=True, slots=True)
class Fill:
    """A single click of the fill tool in one palette colour.

    A fill is only ever emitted after :mod:`penplan.fills` has simulated it
    against the planned outlines and proved that it cannot leak.
    """

    color: int
    seed: Point

    def __post_init__(self) -> None:
        """Reject fills that no executor could carry out."""
        if self.color < 0:
            msg = f"colour index must be non-negative, got {self.color}"
            raise ValueError(msg)


type Step = Stroke | Fill
"""One executable unit of a plan."""


class Degradation(Enum):
    """A way the planner can make a plan cheaper when it overruns the budget.

    The order of the members is the order in which the planner applies them:
    dropping specks costs the least fidelity, dropping colours costs the most.
    """

    DROP_SMALL_REGIONS = "drop_small_regions"
    SIMPLIFY_MORE = "simplify_more"
    COARSER_BRUSH = "coarser_brush"
    REDUCE_PALETTE = "reduce_palette"


@dataclass(frozen=True, slots=True)
class Sacrifice:
    """One degradation the budget forced, with the detail shown to the user."""

    kind: Degradation
    detail: str


@dataclass(frozen=True, slots=True)
class CostModel:
    """Measured execution cost of the primitives a plan is made of.

    Calibration re-measures these on the machine and canvas in use; the
    defaults below only serve until a self-timing run has happened.
    """

    seconds_per_move: float
    seconds_per_pixel: float
    seconds_per_click: float
    seconds_per_color_switch: float
    seconds_per_tool_switch: float

    def __post_init__(self) -> None:
        """Reject non-positive costs, which would make estimates meaningless."""
        for name in self.__slots__:
            value = getattr(self, name)
            if value < 0:
                msg = f"{name} must be non-negative, got {value}"
                raise ValueError(msg)


DEFAULT_COST_MODEL: Final = CostModel(
    # Placeholder figures in the range measured on a 60 Hz canvas before the
    # self-timing run replaces them; see docs/ALGORITHM.md for the model.
    seconds_per_move=0.004,
    seconds_per_pixel=0.0004,
    seconds_per_click=0.05,
    seconds_per_color_switch=0.25,
    seconds_per_tool_switch=0.20,
)


@dataclass(frozen=True, slots=True)
class PlanReport:
    """What the planner wants to tell the user before anything is drawn."""

    estimated_seconds: float
    budget_seconds: float
    tour_length: float
    greedy_tour_length: float
    sacrifices: tuple[Sacrifice, ...]

    @property
    def fits_budget(self) -> bool:
        """Return whether the estimate is inside the requested budget."""
        return self.estimated_seconds <= self.budget_seconds

    @property
    def tour_improvement(self) -> float:
        """Return the fraction of travel the optimiser saved over the greedy tour."""
        if self.greedy_tour_length <= 0:
            return 0.0
        return 1.0 - self.tour_length / self.greedy_tour_length


@dataclass(frozen=True, slots=True)
class DrawPlan:
    """An ordered, self-contained description of a drawing.

    The plan carries its own palette and brush widths so that the renderer and
    the tests can reproduce the drawing without a calibration profile.
    """

    width: int
    height: int
    palette: tuple[Rgb, ...]
    brush_widths: tuple[int, ...]
    steps: tuple[Step, ...]
    report: PlanReport

    def __post_init__(self) -> None:
        """Reject plans that reference palette or brush entries that do not exist."""
        if self.width <= 0 or self.height <= 0:
            msg = f"canvas size must be positive, got {self.width}x{self.height}"
            raise ValueError(msg)
        if not self.palette:
            msg = "a plan needs a palette"
            raise ValueError(msg)
        if not self.brush_widths:
            msg = "a plan needs at least one brush width"
            raise ValueError(msg)
        for step in self.steps:
            if step.color >= len(self.palette):
                msg = f"colour index {step.color} is outside the {len(self.palette)}-entry palette"
                raise ValueError(msg)
            if isinstance(step, Stroke) and step.brush >= len(self.brush_widths):
                msg = f"brush index {step.brush} is outside the {len(self.brush_widths)} brushes"
                raise ValueError(msg)

    @property
    def strokes(self) -> tuple[Stroke, ...]:
        """Return the stroke steps in execution order."""
        return tuple(step for step in self.steps if isinstance(step, Stroke))

    @property
    def fills(self) -> tuple[Fill, ...]:
        """Return the fill steps in execution order."""
        return tuple(step for step in self.steps if isinstance(step, Fill))

    @property
    def point_count(self) -> int:
        """Return the number of polyline points, which is what pacing costs."""
        return sum(len(stroke.points) for stroke in self.strokes)

    @property
    def color_count(self) -> int:
        """Return the number of distinct palette colours the plan uses."""
        return len({step.color for step in self.steps})
