"""Tests for stroke generation: contours, erosion, chaining and simplification."""

from __future__ import annotations

import pytest

from penplan.model import Point
from penplan.quantize import QuantizedImage
from penplan.regions import Region, Run, decompose
from penplan.render import Raster
from penplan.strokes import (
    chain,
    cover,
    erode,
    hatch,
    outline_strokes,
    simplify,
    trace_contour,
)

PALETTE = ((255, 255, 255), (0, 0, 0), (255, 0, 0), (0, 0, 255))
WHITE, BLACK, RED, BLUE = 0, 1, 2, 3
BRUSHES = (1.0, 3.0, 9.0)


def raster(*rows: str) -> QuantizedImage:
    """Build a target raster from rows of digits, each digit a palette index."""
    width = len(rows[0])
    return QuantizedImage(
        width=width,
        height=len(rows),
        colors=PALETTE,
        indices=bytes(int(char) for row in rows for char in row),
    )


def box(color: int, left: int, top: int, right: int, bottom: int) -> Region:
    return Region(
        color=color, runs=tuple(Run(y=y, start=left, end=right) for y in range(top, bottom + 1))
    )


def test_simplify_collapses_a_straight_line() -> None:
    points = [Point(x, 0) for x in range(10)]
    assert simplify(points, 0.5) == (Point(0, 0), Point(9, 0))


def test_simplify_keeps_a_corner() -> None:
    points = (Point(0, 0), Point(5, 0), Point(5, 5))
    assert simplify(points, 0.5) == points


def test_simplify_drops_a_bump_below_the_tolerance() -> None:
    points = (Point(0, 0), Point(5, 1), Point(10, 0))
    assert simplify(points, 2.0) == (Point(0, 0), Point(10, 0))
    assert simplify(points, 0.5) == points


def test_simplify_at_zero_tolerance_changes_nothing() -> None:
    points = tuple(Point(x, x % 3) for x in range(20))
    assert simplify(points, 0.0) == points


def test_simplify_leaves_short_polylines_alone() -> None:
    assert simplify((Point(0, 0), Point(4, 4)), 10.0) == (Point(0, 0), Point(4, 4))


def test_a_traced_contour_is_closed() -> None:
    contour = trace_contour(box(RED, 2, 2, 6, 6), 10)
    assert contour[0] == contour[-1]
    assert contour[0] == Point(2, 2)
    # The border of a five by five square is sixteen pixels, plus the repeat.
    assert len(contour) == 17


def test_a_single_pixel_region_traces_to_one_point() -> None:
    assert trace_contour(Region(color=RED, runs=(Run(y=3, start=4, end=4),)), 8) == (Point(4, 3),)


def test_a_traced_contour_holds_a_flood_in() -> None:
    # The property the fill planner depends on: a traced outline has no gap.
    region = box(RED, 2, 2, 20, 14)
    canvas = Raster(24, 18, WHITE)
    canvas.stroke(trace_contour(region, 18), RED, 1.0)
    blocked = canvas.blocked_mask(WHITE)
    visited = bytearray(canvas.width * canvas.height)
    spans = canvas.flood_spans(Point(10, 8), blocked, visited)
    assert sum(span.length for span in spans) == 17 * 11


def test_a_traced_l_shape_follows_the_concave_corner() -> None:
    region = Region(
        color=RED,
        runs=(
            Run(y=0, start=0, end=5),
            Run(y=1, start=0, end=5),
            Run(y=2, start=0, end=1),
            Run(y=3, start=0, end=1),
        ),
    )
    contour = trace_contour(region, 4)
    assert Point(5, 0) in contour
    assert Point(1, 3) in contour
    assert contour[0] == contour[-1]


def test_outlines_use_the_thinnest_brush() -> None:
    image = raster("00000", "02220", "02220", "00000")
    strokes = outline_strokes(decompose(image, ignore=[0]).regions, image.height, 0, 0.5)
    assert len(strokes) == 1
    assert strokes[0].brush == 0
    assert strokes[0].color == 2


def test_erosion_shrinks_a_block_by_the_brush_radius() -> None:
    rows = [[(0, 8)] if 0 <= y <= 8 else [] for y in range(9)]
    eroded = erode(rows, 3.0)
    assert eroded[0] == []
    assert eroded[4] == [(1, 7)]
    assert eroded[8] == []


def test_a_thin_line_erodes_to_nothing() -> None:
    rows = [[(0, 20)], [], []]
    assert erode(rows, 3.0) == [[], [], []]


def test_erosion_of_an_empty_mask_is_empty() -> None:
    assert erode([[] for _ in range(5)], 3.0) == [[] for _ in range(5)]


def test_a_one_pixel_brush_erodes_nothing_away() -> None:
    rows = [[(2, 5)], [(2, 5)], []]
    assert erode(rows, 1.0) == rows


def test_hatch_spaces_strokes_by_the_brush_height() -> None:
    rows = [[(0, 9)] for _ in range(10)]
    strokes = hatch(rows, RED, 1, 3)
    assert [stroke.points[0].y for stroke in strokes] == [0, 3, 6, 9]
    assert strokes[0].points == (Point(0, 0), Point(9, 0))


def test_hatch_of_an_empty_mask_is_empty() -> None:
    assert hatch([[] for _ in range(4)], RED, 0, 2) == []


def test_a_diagonal_line_becomes_one_stroke() -> None:
    rows = [[(y, y)] for y in range(8)]
    strokes = chain(rows, RED, 0, 0.5, 1)
    assert len(strokes) == 1
    assert strokes[0].points == (Point(0, 0), Point(7, 7))


def test_separate_marks_stay_separate_strokes() -> None:
    rows = [[(0, 0)], [], [(6, 6)]]
    strokes = chain(rows, RED, 0, 0.5, 1)
    assert len(strokes) == 2
    assert {stroke.points[0] for stroke in strokes} == {Point(0, 0), Point(6, 2)}


def test_wide_runs_stay_horizontal_strokes() -> None:
    rows = [[(0, 9)], [(0, 9)]]
    strokes = chain(rows, RED, 0, 0.5, 1)
    assert len(strokes) == 2
    assert all(stroke.points[0].y == stroke.points[1].y for stroke in strokes)


def test_cover_paints_the_whole_target() -> None:
    image = raster(
        "0000000000",
        "0222222000",
        "0222222000",
        "0222222000",
        "0000333000",
        "0000333000",
    )
    canvas = Raster(image.width, image.height, WHITE)
    strokes = cover(canvas, image, BRUSHES, ignore=[WHITE], tolerance=0.0)
    assert strokes
    assert bytes(canvas.indices) == image.indices


def test_cover_uses_a_thick_brush_where_it_fits() -> None:
    image = raster(*["0" + "2" * 14 + "0" for _ in range(12)])
    canvas = Raster(image.width, image.height, WHITE)
    strokes = cover(canvas, image, BRUSHES, ignore=[WHITE], tolerance=0.0)
    assert max(stroke.brush for stroke in strokes) == 2
    assert bytes(canvas.indices) == image.indices


def test_cover_is_deterministic() -> None:
    image = raster(
        "0000000000",
        "0223333000",
        "0223333000",
        "0000330000",
    )
    first = Raster(image.width, image.height, WHITE)
    second = Raster(image.width, image.height, WHITE)
    assert cover(first, image, BRUSHES, ignore=[WHITE], tolerance=0.0) == cover(
        second, image, BRUSHES, ignore=[WHITE], tolerance=0.0
    )


def test_cover_leaves_the_ignored_colour_alone() -> None:
    image = raster("0000", "0220", "0000")
    canvas = Raster(image.width, image.height, WHITE)
    cover(canvas, image, BRUSHES, ignore=[WHITE], tolerance=0.0)
    assert canvas.at(0, 0) == WHITE


def test_cover_of_an_empty_target_draws_nothing() -> None:
    image = raster("000", "000")
    canvas = Raster(image.width, image.height, WHITE)
    assert cover(canvas, image, BRUSHES, ignore=[WHITE], tolerance=0.0) == []


def test_simplification_trades_points_for_time() -> None:
    # The claim the tolerance exists for: a coarser tolerance costs fewer mouse
    # events for the same drawing.
    image = raster(*[("0" + "2" * 18 + "0") if 2 <= y <= 18 else "0" * 20 for y in range(21)])
    regions = decompose(image, ignore=[WHITE]).regions
    exact = outline_strokes(regions, image.height, 0, 0.0)
    coarse = outline_strokes(regions, image.height, 0, 2.0)
    assert sum(len(stroke.points) for stroke in coarse) < sum(
        len(stroke.points) for stroke in exact
    )


@pytest.mark.parametrize("tolerance", [0.0, 0.5, 2.0])
def test_cover_never_leaves_the_raster(tolerance: float) -> None:
    image = raster(*["2" * 12 for _ in range(12)])
    canvas = Raster(image.width, image.height, WHITE)
    for stroke in cover(canvas, image, BRUSHES, ignore=[WHITE], tolerance=tolerance):
        for point in stroke.points:
            assert 0 <= point.x < image.width
            assert 0 <= point.y < image.height
