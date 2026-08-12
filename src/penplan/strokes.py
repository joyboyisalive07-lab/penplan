"""Turn pixels into strokes: outlines, then whatever the fills did not cover.

Three ideas carry this module.

Outlines are traced, not scanned. Following a region's boundary produces one
closed polyline, and a closed polyline is what a fill needs in order to be
provable. Scanning the boundary into horizontal pieces would leave diagonal
gaps, and every one of them is a leak.

Brush size comes from morphological erosion. A brush may only be used where it
fits entirely inside the area still to be painted, so the thick brush covers
the interior in a few passes and the thin brush is left with the boundary band.
The cascade runs from thickest to thinnest, and after each pass the simulated
canvas says what is left.

Points cost time. Runs are merged into polylines, and every polyline is
simplified with Ramer-Douglas-Peucker at a tolerance the planner controls,
because each point is a mouse event and the time budget pays for all of them.
"""

from __future__ import annotations

import bisect
from typing import TYPE_CHECKING, Final

from penplan.model import Point, Stroke
from penplan.regions import row_runs
from penplan.render import disc_spans

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Sequence

    from penplan.quantize import QuantizedImage
    from penplan.regions import Region
    from penplan.render import Raster

type Interval = tuple[int, int]
"""An inclusive stretch of columns on one row."""

type Rows = list[list[Interval]]
"""One sorted, disjoint interval list per raster row."""

_MIN_SIMPLIFIABLE_POINTS: Final = 3

# Clockwise from due east. Moore tracing walks this ring to find the next
# boundary pixel.
_RING: Final = ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))
_RING_SIZE: Final = len(_RING)

# A traced contour cannot be longer than this, and a trace that exceeds it has
# found a shape no region decomposition could have produced.
_TRACE_LIMIT_FACTOR: Final = 4
_TRACE_LIMIT_MARGIN: Final = 8

_MARKED: Final = 1
_DIFFERENCE_MARKS: Final = bytes(_MARKED if value else 0 for value in range(256))


def simplify(points: Sequence[Point], tolerance: float) -> tuple[Point, ...]:
    """Drop the points of a polyline that no one would miss.

    Ramer-Douglas-Peucker, iteratively rather than recursively, because a
    thousand-point contour would otherwise be a thousand stack frames deep.
    A tolerance of zero returns the polyline unchanged.
    """
    if len(points) < _MIN_SIMPLIFIABLE_POINTS or tolerance <= 0:
        return tuple(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        furthest = -1
        distance = tolerance
        for index in range(first + 1, last):
            candidate = _point_line_distance(points[index], points[first], points[last])
            if candidate > distance:
                furthest, distance = index, candidate
        if furthest < 0:
            continue
        keep[furthest] = True
        stack.append((first, furthest))
        stack.append((furthest, last))
    return tuple(point for point, kept in zip(points, keep, strict=True) if kept)


def _point_line_distance(point: Point, start: Point, end: Point) -> float:
    span_x = end.x - start.x
    span_y = end.y - start.y
    if span_x == 0 and span_y == 0:
        return point.distance_to(start)
    # Twice the triangle area over the base length, which is the perpendicular
    # distance without a square root on the numerator.
    area = abs(span_x * (start.y - point.y) - span_y * (start.x - point.x))
    return area / (span_x * span_x + span_y * span_y) ** 0.5


def region_rows(region: Region, height: int) -> Rows:
    """Return the region's pixels as one interval list per raster row."""
    rows: Rows = [[] for _ in range(height)]
    for run in region.runs:
        rows[run.y].append((run.start, run.end))
    return rows


def _contains(rows: Rows, x: int, y: int) -> bool:
    if not 0 <= y < len(rows):
        return False
    intervals = rows[y]
    # The last interval that starts at or before x. Comparing against (x + 1, 0)
    # rather than (x, something) keeps the answer independent of the interval
    # ends, which are unbounded by anything this function knows.
    index = bisect.bisect_right(intervals, (x + 1, 0)) - 1
    return index >= 0 and intervals[index][0] <= x <= intervals[index][1]


def trace_contour(region: Region, height: int) -> tuple[Point, ...]:
    """Return the region's outer boundary as a closed polyline.

    Moore-neighbour tracing, walking clockwise. The result comes back to its
    starting pixel, so that drawing it leaves no seam for a fill to escape
    through. Holes are not traced: a hole is a region in its own right and gets
    its own outline, which is what stops a fill from running into it.
    """
    rows = region_rows(region, height)
    start = Point(x=region.runs[0].start, y=region.runs[0].y)
    limit = _TRACE_LIMIT_FACTOR * region.area + _TRACE_LIMIT_MARGIN
    contour = [start]
    current = start
    # The pixel we arrived from. Nothing is to the left of the topmost-leftmost
    # pixel of a region, so west is a safe place to have come from.
    entry = 4
    for _ in range(limit):
        found = False
        for step in range(1, _RING_SIZE + 1):
            direction = (entry + step) % _RING_SIZE
            offset = _RING[direction]
            candidate = Point(x=current.x + offset[0], y=current.y + offset[1])
            if _contains(rows, candidate.x, candidate.y):
                # Seen from the pixel just stepped onto, the pixel just left is
                # half a turn away, and the next search resumes one step after
                # it. Resuming any later skips a neighbour, and a skipped
                # neighbour is how a trace walks off along the wrong edge.
                entry = (direction + _RING_SIZE // 2) % _RING_SIZE
                current = candidate
                found = True
                break
        if not found:
            break
        if current == start:
            break
        contour.append(current)
    if len(contour) > 1:
        contour.append(start)
    return tuple(contour)


def outline_strokes(
    regions: Iterable[Region], height: int, brush: int, tolerance: float
) -> list[Stroke]:
    """Trace every region and return its outline as one stroke.

    The thinnest brush is used, because an outline is the one place where the
    drawn line has to follow the shape rather than fill it.
    """
    strokes: list[Stroke] = []
    for region in regions:
        contour = simplify(trace_contour(region, height), tolerance)
        strokes.append(Stroke(color=region.color, brush=brush, points=contour))
    return strokes


def remaining_rows(target: QuantizedImage, canvas: Raster, color: int, difference: bytes) -> Rows:
    """Return the pixels of one colour that the canvas does not have yet."""
    selected = target.indices.translate(
        bytes(_MARKED if value == color else 0 for value in range(256))
    )
    combined = (int.from_bytes(selected, "big") & int.from_bytes(difference, "big")).to_bytes(
        len(selected), "big"
    )
    rows: Rows = []
    for y in range(canvas.height):
        row = combined[y * canvas.width : (y + 1) * canvas.width]
        rows.append([(start, end) for value, start, end in row_runs(row) if value == _MARKED])
    return rows


def difference_mask(target: QuantizedImage, canvas: Raster) -> bytes:
    """Return a byte per pixel: 1 where the canvas does not match the target.

    Comparing two half-megabyte buffers as one big integer keeps the work in C.
    A Python loop over the pixels would cost more than everything else in the
    stroke planner put together.
    """
    difference = int.from_bytes(target.indices, "big") ^ int.from_bytes(
        bytes(canvas.indices), "big"
    )
    return difference.to_bytes(len(target.indices), "big").translate(_DIFFERENCE_MARKS)


def _shrink(intervals: Sequence[Interval], amount: int) -> list[Interval]:
    if amount == 0:
        return list(intervals)
    return [(start + amount, end - amount) for start, end in intervals if end - start >= 2 * amount]


def _intersect(first: Sequence[Interval], second: Sequence[Interval]) -> list[Interval]:
    result: list[Interval] = []
    left = right = 0
    while left < len(first) and right < len(second):
        start = max(first[left][0], second[right][0])
        end = min(first[left][1], second[right][1])
        if start <= end:
            result.append((start, end))
        if first[left][1] < second[right][1]:
            left += 1
        else:
            right += 1
    return result


def erode(rows: Rows, brush_width: float) -> Rows:
    """Return where the brush fits entirely inside the mask.

    This is a morphological erosion by the brush disc, done on intervals rather
    than pixels: shrinking a row by the disc's half-width on that row, then
    intersecting the shrunk rows the disc spans.
    """
    spans = disc_spans(brush_width)
    height = len(rows)
    shrunk = {span: [_shrink(row, span) for row in rows] for _, span in spans}
    eroded: Rows = []
    for y in range(height):
        current: list[Interval] | None = None
        for offset, span in spans:
            for row in {y - offset, y + offset}:
                if not 0 <= row < height:
                    current = []
                    break
                candidate = shrunk[span][row]
                current = list(candidate) if current is None else _intersect(current, candidate)
                if not current:
                    break
            if not current:
                break
        eroded.append(current or [])
    return eroded


def _brush_footprint(brush_width: float) -> int:
    """Return how many rows one brush mark covers."""
    return 2 * disc_spans(brush_width)[-1][0] + 1


def hatch(rows: Rows, color: int, brush: int, step: int) -> list[Stroke]:
    """Cover a mask with horizontal strokes, one every ``step`` rows.

    The spacing is the brush's own height, so consecutive passes meet. What
    they miss at the edges is left to the thinner brushes, which is the whole
    point of running the cascade against a simulated canvas.
    """
    first = next((y for y, row in enumerate(rows) if row), None)
    if first is None:
        return []
    return [
        Stroke(color=color, brush=brush, points=(Point(start, y), Point(end, y)))
        for y, row in enumerate(rows)
        if (y - first) % step == 0
        for start, end in row
    ]


def chain(rows: Rows, color: int, brush: int, tolerance: float, limit: int) -> list[Stroke]:
    """Merge narrow runs into polylines and emit wide runs as they are.

    A diagonal line one pixel wide arrives here as one run per row. Emitted
    separately those are a click each; chained they are a single stroke, and
    after simplification a single stroke with two points.

    ``limit`` is the number of pixels the brush covers across, so a chained run
    is one the brush paints in full from its centre. A wider run is drawn as
    the horizontal stroke it already is.
    """
    strokes: list[Stroke] = []
    previous: list[tuple[Interval, list[Point]]] = []
    for y, row in enumerate(rows):
        current: list[tuple[Interval, list[Point]]] = []
        used: set[int] = set()
        for start, end in row:
            if end - start + 1 > limit:
                strokes.append(
                    Stroke(color=color, brush=brush, points=(Point(start, y), Point(end, y)))
                )
                continue
            centre = Point((start + end) // 2, y)
            index = _adjacent(previous, start, end, used)
            if index is None:
                current.append(((start, end), [centre]))
            else:
                used.add(index)
                points = previous[index][1]
                points.append(centre)
                current.append(((start, end), points))
        strokes.extend(
            Stroke(color=color, brush=brush, points=simplify(points, tolerance))
            for index, (_, points) in enumerate(previous)
            if index not in used
        )
        previous = current
    strokes.extend(
        Stroke(color=color, brush=brush, points=simplify(points, tolerance))
        for _, points in previous
    )
    return strokes


def _adjacent(
    previous: Sequence[tuple[Interval, list[Point]]], start: int, end: int, used: Collection[int]
) -> int | None:
    for index, ((other_start, other_end), _) in enumerate(previous):
        if index in used:
            continue
        if other_start <= end + 1 and start <= other_end + 1:
            return index
    return None


def cover(
    canvas: Raster,
    target: QuantizedImage,
    brush_widths: Sequence[float],
    *,
    ignore: Collection[int] = (),
    tolerance: float = 1.0,
) -> list[Stroke]:
    """Paint everything the canvas is still missing, thickest brush first.

    The canvas is painted as the strokes are chosen, so each pass sees exactly
    what the one before it left behind, and the strokes returned are exactly
    what the executor will draw.
    """
    skipped = frozenset(ignore)
    strokes: list[Stroke] = []
    colors = sorted(set(target.indices) - skipped)
    for brush in range(len(brush_widths) - 1, -1, -1):
        width = brush_widths[brush]
        finest = brush == 0
        for color in colors:
            rows = remaining_rows(target, canvas, color, difference_mask(target, canvas))
            usable = rows if finest else erode(rows, width)
            if not any(usable):
                continue
            footprint = _brush_footprint(width)
            fresh = (
                chain(usable, color, brush, tolerance, footprint)
                if finest
                else hatch(usable, color, brush, footprint)
            )
            for stroke in fresh:
                canvas.stroke(stroke.points, stroke.color, width)
            strokes.extend(fresh)
    return strokes
