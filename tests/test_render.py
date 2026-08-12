"""Tests for the rasterizer that both the preview and the fill check use."""

from __future__ import annotations

import itertools

import pytest

from penplan.model import DrawPlan, Fill, PlanReport, Point, Stroke
from penplan.regions import Run
from penplan.render import Raster, RenderError, disc_spans, line_points, render_plan

PALETTE = ((255, 255, 255), (0, 0, 0), (255, 0, 0))
WHITE, BLACK, RED = 0, 1, 2


def painted(raster: Raster, color: int) -> set[tuple[int, int]]:
    return {
        (x, y)
        for y in range(raster.height)
        for x in range(raster.width)
        if raster.at(x, y) == color
    }


def test_a_thin_brush_paints_one_pixel() -> None:
    raster = Raster(5, 5, WHITE)
    raster.stamp(2, 2, BLACK, 1.0)
    assert painted(raster, BLACK) == {(2, 2)}


def test_a_wide_brush_paints_a_disc() -> None:
    raster = Raster(9, 9, WHITE)
    raster.stamp(4, 4, BLACK, 5.0)
    marks = painted(raster, BLACK)
    assert (4, 4) in marks
    assert (4, 2) in marks
    assert (2, 4) in marks
    # The corners of the bounding box stay clear: it is a disc, not a square.
    assert (2, 2) not in marks
    assert len(marks) == 13


@pytest.mark.parametrize(
    ("width", "painted_across"),
    [(1.0, 1), (2.0, 1), (3.0, 3), (4.0, 3), (5.0, 5), (9.0, 9)],
)
def test_odd_widths_are_exact_and_even_widths_land_one_narrow(
    width: float, painted_across: int
) -> None:
    # A disc centred on a pixel cannot be an even number of pixels across. The
    # fill check and the preview share this function, so both see the same
    # geometry and the dry run stays honest either way.
    assert 2 * disc_spans(width)[0][1] + 1 == painted_across


def test_a_zero_width_brush_is_refused() -> None:
    with pytest.raises(RenderError, match="width must be positive"):
        disc_spans(0.0)


def test_a_line_has_no_gaps() -> None:
    points = list(line_points(Point(0, 0), Point(5, 3)))
    assert points[0] == (0, 0)
    assert points[-1] == (5, 3)
    for first, second in itertools.pairwise(points):
        assert max(abs(second[0] - first[0]), abs(second[1] - first[1])) == 1


def test_a_single_point_line_is_that_point() -> None:
    assert list(line_points(Point(3, 4), Point(3, 4))) == [(3, 4)]


def test_a_stroke_paints_along_its_polyline() -> None:
    raster = Raster(10, 10, WHITE)
    raster.stroke([Point(1, 1), Point(8, 1), Point(8, 8)], BLACK, 1.0)
    assert raster.at(4, 1) == BLACK
    assert raster.at(8, 5) == BLACK
    assert raster.at(4, 5) == WHITE


def test_a_single_point_stroke_is_a_dot() -> None:
    raster = Raster(5, 5, WHITE)
    raster.stroke([Point(2, 2)], RED, 1.0)
    assert painted(raster, RED) == {(2, 2)}


def test_a_brush_at_the_edge_is_clipped_not_wrapped() -> None:
    raster = Raster(4, 4, WHITE)
    raster.stamp(0, 2, BLACK, 3.0)
    # Slice assignment on a flat buffer is how rows are painted, so a stamp
    # that runs off the left edge must not reappear on the right of the row.
    assert painted(raster, BLACK) == {(0, 1), (0, 2), (1, 2), (0, 3)}


def test_flood_stops_at_painted_pixels() -> None:
    raster = Raster(7, 7, WHITE)
    raster.stroke([Point(1, 1), Point(5, 1), Point(5, 5), Point(1, 5), Point(1, 1)], BLACK, 1.0)
    blocked = raster.blocked_mask(WHITE)
    visited = bytearray(raster.width * raster.height)
    spans = raster.flood_spans(Point(3, 3), blocked, visited)
    assert sum(span.length for span in spans) == 9
    assert spans[0] in (Run(y=2, start=2, end=4), Run(y=3, start=2, end=4))


def test_flood_escapes_through_a_gap() -> None:
    raster = Raster(7, 7, WHITE)
    # The same box with one pixel of the top edge missing.
    raster.stroke([Point(1, 1), Point(3, 1)], BLACK, 1.0)
    raster.stroke([Point(5, 1), Point(5, 5)], BLACK, 1.0)
    raster.stroke([Point(5, 5), Point(1, 5)], BLACK, 1.0)
    raster.stroke([Point(1, 5), Point(1, 1)], BLACK, 1.0)
    blocked = raster.blocked_mask(WHITE)
    visited = bytearray(raster.width * raster.height)
    spans = raster.flood_spans(Point(3, 3), blocked, visited)
    assert sum(span.length for span in spans) > 9


def test_flood_follows_a_spiral_corridor() -> None:
    # A scanline flood that trusts its span logic too much stops at the first
    # turn. The corridor here doubles back three times.
    plan = [
        "#############",
        "#...........#",
        "#.#########.#",
        "#.#.......#.#",
        "#.#.#####.#.#",
        "#.#.#...#.#.#",
        "#.#.#.#.#.#.#",
        "#.#.#.#...#.#",
        "#.#.#######.#",
        "#.#.........#",
        "#############",
    ]
    raster = Raster(len(plan[0]), len(plan), WHITE)
    for y, row in enumerate(plan):
        for x, cell in enumerate(row):
            if cell == "#":
                raster.stamp(x, y, BLACK, 1.0)
    open_pixels = sum(row.count(".") for row in plan)
    blocked = raster.blocked_mask(WHITE)
    visited = bytearray(raster.width * raster.height)
    spans = raster.flood_spans(Point(1, 1), blocked, visited)
    assert sum(span.length for span in spans) == open_pixels


def test_a_seed_outside_the_raster_is_refused() -> None:
    raster = Raster(4, 4, WHITE)
    with pytest.raises(RenderError, match="outside the 4x4 raster"):
        raster.flood_spans(Point(9, 0), bytearray(16), bytearray(16))


def test_an_empty_raster_is_refused() -> None:
    with pytest.raises(RenderError, match="raster size must be positive"):
        Raster(0, 5, WHITE)


def test_rendering_a_plan_draws_strokes_then_fills() -> None:
    plan = DrawPlan(
        width=9,
        height=9,
        palette=PALETTE,
        brush_widths=(1.0,),
        steps=(
            Stroke(
                color=BLACK,
                brush=0,
                points=(Point(1, 1), Point(7, 1), Point(7, 7), Point(1, 7), Point(1, 1)),
            ),
            Fill(color=RED, seed=Point(4, 4)),
        ),
        report=PlanReport(
            estimated_seconds=1.0,
            budget_seconds=10.0,
            tour_length=0.0,
            greedy_tour_length=0.0,
            sacrifices=(),
        ),
    )
    image = render_plan(plan, WHITE)
    assert image.size == (9, 9)
    assert image.getpixel((4, 4)) == (255, 0, 0)
    assert image.getpixel((1, 1)) == (0, 0, 0)
    assert image.getpixel((0, 0)) == (255, 255, 255)


def test_to_image_uses_the_palette() -> None:
    raster = Raster(2, 1, WHITE)
    raster.stamp(1, 0, RED, 1.0)
    image = raster.to_image(PALETTE)
    assert image.getpixel((0, 0)) == (255, 255, 255)
    assert image.getpixel((1, 0)) == (255, 0, 0)
