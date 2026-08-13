"""Turn a plan into pixels, exactly as executing it would.

This module is the reason the dry run can be trusted. The fill planner uses it
to simulate a flood fill against the outlines it has planned, and the preview
uses it to show the user what the mouse is about to draw. Both go through the
same brush geometry and the same flood fill, so a preview that looks right and
an execution that goes wrong cannot disagree about geometry.

Rasters hold palette indices, one byte per pixel, in the plan's own coordinate
space.
"""

from __future__ import annotations

import functools
import itertools
from typing import TYPE_CHECKING, Final

from PIL import Image

from penplan.model import Fill, Point, Stroke
from penplan.regions import Run

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from penplan.model import DrawPlan, Rgb

_BLOCKED: Final = 1
_CLEAR: Final = 0


class RenderError(ValueError):
    """A plan cannot be rendered onto a raster."""


@functools.cache
def disc_spans(width: float) -> tuple[tuple[int, int], ...]:
    """Return the half-width of a round brush on each row, centre row first.

    The result is indexed by the distance from the centre row, and each entry
    is the inclusive column offset the brush covers on that row.

    A brush of width w is stamped as a disc of radius (w - 1) / 2 measured
    between pixel centres, so an odd width lands on exactly that many pixels.
    An even width lands one pixel narrow, because a disc centred on a pixel
    cannot be an even number of pixels across. The verifier and the preview use
    this same function, so the dry run stays honest either way.
    """
    if width <= 0:
        msg = f"brush width must be positive, got {width}"
        raise RenderError(msg)
    radius = (width - 1.0) / 2.0
    rows: list[tuple[int, int]] = []
    for dy in range(int(radius) + 1):
        span = int((radius * radius - dy * dy) ** 0.5)
        rows.append((dy, span))
    return tuple(rows)


def line_points(start: Point, end: Point) -> Iterator[tuple[int, int]]:
    """Yield every pixel along a segment, endpoints included.

    Stepping by the larger of the two deltas is what guarantees the trail has
    no gaps, which matters because a gap in an outline is what lets a fill
    escape.
    """
    delta_x = end.x - start.x
    delta_y = end.y - start.y
    steps = max(abs(delta_x), abs(delta_y))
    if steps == 0:
        yield start.x, start.y
        return
    for step in range(steps + 1):
        yield (
            start.x + round(delta_x * step / steps),
            start.y + round(delta_y * step / steps),
        )


class Raster:
    """A canvas of palette indices that can be painted like the real one."""

    def __init__(self, width: int, height: int, background: int) -> None:
        if width <= 0 or height <= 0:
            msg = f"raster size must be positive, got {width}x{height}"
            raise RenderError(msg)
        self.width: Final = width
        self.height: Final = height
        self.indices = bytearray([background]) * (width * height)

    def at(self, x: int, y: int) -> int:
        """Return the palette index at one pixel."""
        return self.indices[y * self.width + x]

    def stamp(self, x: int, y: int, color: int, width: float) -> None:
        """Paint one brush mark centred on a pixel."""
        for dy, span in disc_spans(width):
            for row in {y - dy, y + dy}:
                if not 0 <= row < self.height:
                    continue
                left = max(0, x - span)
                right = min(self.width - 1, x + span)
                if left > right:
                    continue
                offset = row * self.width
                self.indices[offset + left : offset + right + 1] = bytes([color]) * (
                    right - left + 1
                )

    def stroke(self, points: Sequence[Point], color: int, width: float) -> None:
        """Paint a pen-down polyline."""
        if not points:
            return
        if len(points) == 1:
            self.stamp(points[0].x, points[0].y, color, width)
            return
        for start, end in itertools.pairwise(points):
            for x, y in line_points(start, end):
                self.stamp(x, y, color, width)

    def blocked_mask(self, background: int) -> bytearray:
        """Return a byte per pixel: 1 where a flood fill would stop.

        Kept as a separate buffer so that the flood fill can find the end of a
        span with one C-level search instead of a Python loop over pixels.
        """
        table = bytes(_CLEAR if value == background else _BLOCKED for value in range(256))
        return bytearray(self.indices.translate(table))

    def flood_spans(self, seed: Point, blocked: bytearray, visited: bytearray) -> list[Run]:
        """Return the spans a fill click at ``seed`` would paint.

        Four-way scanline flood, the same rule the region decomposition uses.
        Nothing is painted: the caller decides whether the result is safe.
        """
        if not 0 <= seed.x < self.width or not 0 <= seed.y < self.height:
            msg = f"fill seed {seed} is outside the {self.width}x{self.height} raster"
            raise RenderError(msg)
        return _Flood(self, blocked, visited).run(seed)

    def paint_spans(self, spans: Sequence[Run], color: int) -> None:
        """Paint whole rows of pixels at once."""
        for span in spans:
            offset = span.y * self.width
            self.indices[offset + span.start : offset + span.end + 1] = bytes([color]) * span.length

    def to_image(self, colors: Sequence[Rgb]) -> Image.Image:
        """Convert the raster to an RGB image using a palette."""
        image = Image.frombytes("P", (self.width, self.height), bytes(self.indices))
        flat: list[int] = []
        for color in colors:
            flat.extend(color)
        image.putpalette(flat + [0] * (768 - len(flat)))
        return image.convert("RGB")


class _Flood:
    """One scanline flood fill in progress.

    Spans are found with C-level searches over a byte per pixel rather than a
    Python loop, so the cost is one step per row of the filled area instead of
    one per pixel. On a fill covering a hundred thousand pixels that is the
    difference between milliseconds and seconds.
    """

    def __init__(self, raster: Raster, blocked: bytearray, visited: bytearray) -> None:
        self._raster = raster
        self._blocked = blocked
        self._visited = visited
        self._stack: list[tuple[int, int]] = []

    def run(self, seed: Point) -> list[Run]:
        """Return every span the fill reaches, marking them as visited."""
        width = self._raster.width
        spans: list[Run] = []
        self._stack.append((seed.x, seed.y))
        while self._stack:
            x, y = self._stack.pop()
            offset = y * width
            if self._blocked[offset + x] or self._visited[offset + x]:
                continue
            end = self._blocked.find(_BLOCKED, offset + x, offset + width)
            right = width - 1 if end < 0 else end - offset - 1
            found = self._blocked.rfind(_BLOCKED, offset, offset + x)
            left = 0 if found < 0 else found - offset + 1
            self._visited[offset + left : offset + right + 1] = b"\x01" * (right - left + 1)
            spans.append(Run(y=y, start=left, end=right))
            for row in (y - 1, y + 1):
                if 0 <= row < self._raster.height:
                    self._push_row(row, left, right)
        return spans

    def _push_row(self, row: int, left: int, right: int) -> None:
        """Queue one seed per unblocked stretch of a neighbouring row."""
        offset = row * self._raster.width
        x = left
        while x <= right:
            if self._blocked[offset + x]:
                found = self._blocked.find(_CLEAR, offset + x, offset + right + 1)
                if found < 0:
                    return
                x = found - offset
            if not self._visited[offset + x]:
                self._stack.append((x, row))
            end = self._blocked.find(_BLOCKED, offset + x, offset + right + 1)
            x = right + 1 if end < 0 else end - offset + 1


def apply_step(raster: Raster, step: Stroke | Fill, brush_widths: Sequence[float]) -> None:
    """Apply one plan step to a raster."""
    if isinstance(step, Stroke):
        raster.stroke(step.points, step.color, brush_widths[step.brush])
        return
    blocked = raster.blocked_mask(raster.at(step.seed.x, step.seed.y))
    visited = bytearray(raster.width * raster.height)
    raster.paint_spans(raster.flood_spans(step.seed, blocked, visited), step.color)


def render_plan(plan: DrawPlan) -> Image.Image:
    """Render a plan exactly as executing it would draw it."""
    raster = Raster(plan.width, plan.height, plan.background)
    for step in plan.steps:
        apply_step(raster, step, plan.brush_widths)
    return raster.to_image(plan.palette)
