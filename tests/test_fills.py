"""Tests for fill safety.

The rule this file exists to enforce: a fill is issued only when a simulation
against the planned outline proves it stays inside its region. Every test with
a deliberately broken outline asserts both halves of that, the refusal and the
absence of a leak in what actually gets drawn.
"""

from __future__ import annotations

import pytest

from penplan.fills import MIN_FILL_AREA, plan_fills, seed_candidates
from penplan.model import Point
from penplan.quantize import QuantizedImage
from penplan.regions import Region, Run, decompose
from penplan.render import Raster

PALETTE = ((255, 255, 255), (0, 0, 0), (255, 0, 0), (0, 0, 255))
WHITE, BLACK, RED, BLUE = 0, 1, 2, 3
SIZE = 40


def box_region(color: int, box: tuple[int, int, int, int]) -> Region:
    """Return a solid rectangular region, both corners inclusive."""
    left, top, right, bottom = box
    return Region(
        color=color,
        runs=tuple(Run(y=y, start=left, end=right) for y in range(top, bottom + 1)),
    )


def closed_outline(raster: Raster, color: int, box: tuple[int, int, int, int]) -> None:
    left, top, right, bottom = box
    raster.stroke(
        [
            Point(left, top),
            Point(right, top),
            Point(right, bottom),
            Point(left, bottom),
            Point(left, top),
        ],
        color,
        1.0,
    )


def test_a_closed_outline_is_filled() -> None:
    raster = Raster(SIZE, SIZE, WHITE)
    closed_outline(raster, RED, (5, 5, 30, 30))
    planning = plan_fills(raster, [box_region(RED, (5, 5, 30, 30))], background=WHITE)
    assert len(planning.fills) == 1
    assert planning.fills[0].color == RED
    assert planning.checks[0].leaked == 0
    assert raster.at(18, 18) == RED


def test_a_gap_in_the_outline_refuses_the_fill() -> None:
    raster = Raster(SIZE, SIZE, WHITE)
    # The same box with two pixels missing from the top edge.
    raster.stroke([Point(5, 5), Point(15, 5)], RED, 1.0)
    raster.stroke([Point(18, 5), Point(30, 5)], RED, 1.0)
    raster.stroke([Point(30, 5), Point(30, 30)], RED, 1.0)
    raster.stroke([Point(30, 30), Point(5, 30)], RED, 1.0)
    raster.stroke([Point(5, 30), Point(5, 5)], RED, 1.0)
    planning = plan_fills(raster, [box_region(RED, (5, 5, 30, 30))], background=WHITE)
    assert planning.fills == ()
    assert "leaks" in planning.refused[0].reason
    assert planning.refused[0].leaked > 0
    # And nothing was painted: the canvas is exactly as the outline left it.
    assert raster.at(18, 18) == WHITE
    assert raster.at(35, 35) == WHITE


@pytest.mark.parametrize("gap_at", [Point(12, 5), Point(30, 17), Point(20, 30), Point(5, 22)])
def test_a_single_pixel_gap_on_any_edge_refuses_the_fill(gap_at: Point) -> None:
    raster = Raster(SIZE, SIZE, WHITE)
    closed_outline(raster, RED, (5, 5, 30, 30))
    # Reopen exactly one pixel of the outline.
    raster.indices[gap_at.y * SIZE + gap_at.x] = WHITE
    planning = plan_fills(raster, [box_region(RED, (5, 5, 30, 30))], background=WHITE)
    assert planning.fills == ()
    assert planning.refused[0].leaked > 0
    assert raster.at(18, 18) == WHITE


def test_a_leak_that_is_stopped_by_a_neighbour_is_still_refused() -> None:
    # The region's own outline has a gap, but the neighbouring shape's outline
    # happens to be next to it. The fill would still paint pixels that are not
    # this region, so it is refused.
    raster = Raster(SIZE, SIZE, WHITE)
    closed_outline(raster, RED, (5, 5, 20, 30))
    closed_outline(raster, BLUE, (22, 5, 34, 30))
    raster.indices[17 * SIZE + 20] = WHITE
    raster.indices[17 * SIZE + 21] = WHITE
    raster.indices[17 * SIZE + 22] = WHITE
    planning = plan_fills(raster, [box_region(RED, (5, 5, 20, 30))], background=WHITE)
    assert planning.fills == ()
    assert planning.refused[0].leaked > 0


def test_a_thin_region_covered_by_its_own_outline_needs_no_fill() -> None:
    raster = Raster(SIZE, SIZE, WHITE)
    raster.stroke([Point(5, 5), Point(30, 5)], RED, 3.0)
    region = box_region(RED, (5, 4, 30, 6))
    planning = plan_fills(raster, [region], background=WHITE)
    assert planning.fills == ()
    assert "already covers" in planning.refused[0].reason


def test_a_small_region_is_not_worth_a_fill() -> None:
    raster = Raster(SIZE, SIZE, WHITE)
    closed_outline(raster, RED, (5, 5, 11, 11))
    planning = plan_fills(raster, [box_region(RED, (5, 5, 11, 11))], background=WHITE)
    assert planning.fills == ()
    assert "cheaper to stroke" in planning.refused[0].reason
    assert planning.refused[0].reached < MIN_FILL_AREA


def test_two_neighbouring_regions_both_fill_without_touching() -> None:
    raster = Raster(SIZE, SIZE, WHITE)
    closed_outline(raster, RED, (2, 2, 18, 30))
    closed_outline(raster, BLUE, (20, 2, 36, 30))
    planning = plan_fills(
        raster,
        [box_region(RED, (2, 2, 18, 30)), box_region(BLUE, (20, 2, 36, 30))],
        background=WHITE,
    )
    assert len(planning.fills) == 2
    assert raster.at(10, 16) == RED
    assert raster.at(28, 16) == BLUE
    assert raster.at(19, 16) == WHITE


def test_an_accepted_fill_blocks_a_later_leak() -> None:
    # The blue box is open towards the red one. Because the red fill runs
    # first and paints up to the shared edge, blue would escape into painted
    # pixels rather than blank ones, and is refused all the same.
    raster = Raster(SIZE, SIZE, WHITE)
    closed_outline(raster, RED, (2, 2, 18, 30))
    raster.stroke([Point(20, 2), Point(36, 2)], BLUE, 1.0)
    raster.stroke([Point(36, 2), Point(36, 30)], BLUE, 1.0)
    raster.stroke([Point(36, 30), Point(20, 30)], BLUE, 1.0)
    planning = plan_fills(
        raster,
        [box_region(RED, (2, 2, 18, 30)), box_region(BLUE, (20, 2, 36, 30))],
        background=WHITE,
    )
    assert [fill.color for fill in planning.fills] == [RED]
    assert planning.refused[0].leaked > 0


def test_the_planning_reports_what_it_painted() -> None:
    raster = Raster(SIZE, SIZE, WHITE)
    closed_outline(raster, RED, (5, 5, 30, 30))
    planning = plan_fills(raster, [box_region(RED, (5, 5, 30, 30))], background=WHITE)
    assert planning.painted_pixels == 24 * 24
    assert planning.refused == ()


def test_seeds_start_from_the_longest_run() -> None:
    region = Region(
        color=RED,
        runs=(Run(y=0, start=0, end=1), Run(y=1, start=0, end=9), Run(y=2, start=4, end=6)),
    )
    assert seed_candidates(region)[0] == Point(x=4, y=1)


def test_a_real_decomposition_fills_its_large_regions() -> None:
    # Two solid blocks straight from the region decomposition, outlined and
    # then filled, with nothing to hand-build.
    width, height = 24, 12
    indices = bytes(RED if x < 12 else BLUE for y in range(height) for x in range(width))
    image = QuantizedImage(width=width, height=height, colors=PALETTE, indices=indices)
    regions = decompose(image).regions
    raster = Raster(width, height, WHITE)
    for region in regions:
        raster.stroke(
            [
                Point(region.min_x, region.min_y),
                Point(region.max_x, region.min_y),
                Point(region.max_x, region.max_y),
                Point(region.min_x, region.max_y),
                Point(region.min_x, region.min_y),
            ],
            region.color,
            1.0,
        )
    planning = plan_fills(raster, regions, background=WHITE, min_fill_area=10)
    assert len(planning.fills) == 2
    assert raster.at(6, 6) == RED
    assert raster.at(18, 6) == BLUE
