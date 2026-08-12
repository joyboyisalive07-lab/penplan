"""Decide which regions may be painted with the fill tool, and prove it first.

A fill is the best trade in the whole planner: one click instead of thousands
of pixels. It is also the only step that can destroy a drawing, because a fill
leaks through a single-pixel gap in the outline around it and floods everything
it can reach.

So no fill is ever issued on trust. The caller paints the outlines it plans to
draw onto a simulated canvas, and this module floods that canvas from the seed
it would actually click. The fill is accepted only if the simulated flood stops
inside the region. One pixel outside is a rejection: a leak does not stay small,
it runs until it meets the next outline, so there is no tolerance worth
allowing. A refused fill costs nothing but time, because whatever the fill did
not cover is still unpainted on the simulated canvas and the stroke planner
picks it up from there.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from penplan.model import Fill, Point

if TYPE_CHECKING:
    from collections.abc import Sequence

    from penplan.regions import Region, Run
    from penplan.render import Raster

# A fill costs a tool click, a colour click and a click on the canvas, and then
# the brush has to be selected again. Below this many pixels the strokes it
# would replace are cheaper than the switching it costs.
MIN_FILL_AREA: Final = 64

# A leak escapes into whatever the neighbouring outline lets it reach, which is
# never a few pixels. Nothing above zero is a tolerance, it is a gamble.
LEAK_TOLERANCE: Final = 0

# Seeds are tried from the middle of the region's longest runs. A full distance
# transform would find a better point and cost more than the fill saves; the
# simulation catches a bad seed anyway.
MAX_SEED_ATTEMPTS: Final = 8


@dataclass(frozen=True, slots=True)
class FillCheck:
    """What the simulation found when it tried to fill one region."""

    color: int
    area: int
    accepted: bool
    seed: Point | None
    reached: int
    leaked: int
    reason: str


@dataclass(frozen=True, slots=True)
class FillPlanning:
    """The fills that were proved safe, and the record of every attempt."""

    fills: tuple[Fill, ...]
    checks: tuple[FillCheck, ...]

    @property
    def refused(self) -> tuple[FillCheck, ...]:
        """Return the attempts that were rejected, with their reasons."""
        return tuple(check for check in self.checks if not check.accepted)

    @property
    def painted_pixels(self) -> int:
        """Return how many pixels the accepted fills paint."""
        return sum(check.reached for check in self.checks if check.accepted)


def seed_candidates(region: Region) -> list[Point]:
    """Return likely fill seeds, most promising first.

    The middle of a long run is nearly always well inside the shape, and a
    region whose longest runs are all covered by its own outline did not need a
    fill in the first place.
    """
    runs = sorted(region.runs, key=lambda run: run.length, reverse=True)
    return [Point(x=(run.start + run.end) // 2, y=run.y) for run in runs[:MAX_SEED_ATTEMPTS]]


@dataclass(slots=True)
class _Scratch:
    """The three per-pixel buffers the planner reuses across every region.

    Allocating them once matters: a canvas-sized buffer per region would cost
    more than the flood fills themselves.
    """

    blocked: bytearray
    visited: bytearray
    region_mask: bytearray


def _mark_region(mask: bytearray, region: Region, width: int, value: int) -> None:
    marker = bytes([value])
    for run in region.runs:
        offset = run.y * width
        mask[offset + run.start : offset + run.end + 1] = marker * run.length


def _count_outside(mask: bytearray, spans: Sequence[Run], width: int) -> tuple[int, int]:
    """Return how many pixels the flood reached, and how many fell outside the mask.

    Counting happens a span at a time with ``bytes.count``, so a fill covering
    a hundred thousand pixels is measured in as many steps as it has rows.
    """
    reached = 0
    outside = 0
    for span in spans:
        offset = span.y * width
        reached += span.length
        outside += span.length - mask[offset + span.start : offset + span.end + 1].count(1)
    return reached, outside


def plan_fills(
    canvas: Raster,
    regions: Sequence[Region],
    *,
    background: int,
    min_fill_area: int = MIN_FILL_AREA,
) -> FillPlanning:
    """Choose the fills that can be proved safe, and apply them to the canvas.

    ``canvas`` must already carry every outline the plan will draw before its
    first fill, because that is the state the real canvas will be in. Accepted
    fills are painted onto it, so the caller can see what is left to stroke and
    later fills see the same canvas the executor will.
    """
    pixels = canvas.width * canvas.height
    scratch = _Scratch(
        blocked=canvas.blocked_mask(background),
        visited=bytearray(pixels),
        region_mask=bytearray(pixels),
    )
    fills: list[Fill] = []
    checks: list[FillCheck] = []

    for region in regions:
        _mark_region(scratch.region_mask, region, canvas.width, 1)
        checks.append(_try_region(canvas, region, scratch, fills, min_fill_area=min_fill_area))
        _mark_region(scratch.region_mask, region, canvas.width, 0)

    return FillPlanning(fills=tuple(fills), checks=tuple(checks))


def _try_region(
    canvas: Raster,
    region: Region,
    scratch: _Scratch,
    fills: list[Fill],
    *,
    min_fill_area: int,
) -> FillCheck:
    """Simulate a fill of one region and apply it if the simulation is clean."""
    seed = _first_open_seed(canvas, region, scratch.blocked)
    if seed is None:
        return FillCheck(
            color=region.color,
            area=region.area,
            accepted=False,
            seed=None,
            reached=0,
            leaked=0,
            reason="the outline already covers the region, so there is nothing to fill",
        )

    spans = canvas.flood_spans(seed, scratch.blocked, scratch.visited)
    reached, leaked = _count_outside(scratch.region_mask, spans, canvas.width)
    for span in spans:
        offset = span.y * canvas.width
        scratch.visited[offset + span.start : offset + span.end + 1] = bytes(span.length)

    if leaked > LEAK_TOLERANCE:
        return FillCheck(
            color=region.color,
            area=region.area,
            accepted=False,
            seed=seed,
            reached=reached,
            leaked=leaked,
            reason=f"the outline leaks: the fill would escape onto {leaked} pixels outside it",
        )
    if reached < min_fill_area:
        return FillCheck(
            color=region.color,
            area=region.area,
            accepted=False,
            seed=seed,
            reached=reached,
            leaked=0,
            reason=f"only {reached} pixels, cheaper to stroke than to switch tools",
        )

    canvas.paint_spans(spans, region.color)
    for span in spans:
        offset = span.y * canvas.width
        scratch.blocked[offset + span.start : offset + span.end + 1] = b"\x01" * span.length
    fills.append(Fill(color=region.color, seed=seed))
    return FillCheck(
        color=region.color,
        area=region.area,
        accepted=True,
        seed=seed,
        reached=reached,
        leaked=0,
        reason="",
    )


def _first_open_seed(canvas: Raster, region: Region, blocked: bytearray) -> Point | None:
    for seed in seed_candidates(region):
        if not blocked[seed.y * canvas.width + seed.x]:
            return seed
    return None
