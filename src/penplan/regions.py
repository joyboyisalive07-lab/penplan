"""Split a quantized raster into connected regions of one colour each.

Regions are held as horizontal runs rather than as sets of pixels. That is the
shape the rest of the planner wants: a run is already most of a stroke, a
region's area and bounding box fall out of its runs, and a picture with half a
million pixels stays a few thousand objects instead of half a million.

Connectivity is four-way, the same rule the flood fill in every paint program
uses. Eight-way connectivity would join regions across a diagonal touch that a
fill would not cross, and the fill planner has to be able to trust that a
region is exactly what one fill click would reach.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Collection, Iterator

    from penplan.quantize import QuantizedImage

MIN_AREA_FLOOR: Final = 1
"""The smallest useful threshold: keep every region, including single pixels."""

_CHANGE: Final = 1
_CHANGE_MARKS: Final = bytes(_CHANGE if value else 0 for value in range(256))
"""Flattens any difference between neighbouring pixels to a single marker byte."""


class RegionError(ValueError):
    """A region was built from runs that cannot describe a shape."""


@dataclass(frozen=True, slots=True, order=True)
class Run:
    """A horizontal stretch of same-coloured pixels, both ends inclusive."""

    y: int
    start: int
    end: int

    def __post_init__(self) -> None:
        """Reject a run that ends before it starts."""
        if self.end < self.start:
            msg = f"run on row {self.y} ends at {self.end}, before its start {self.start}"
            raise RegionError(msg)

    @property
    def length(self) -> int:
        """Return the number of pixels the run covers."""
        return self.end - self.start + 1

    def overlaps(self, other: Run) -> bool:
        """Return whether the two runs share at least one column."""
        return self.start <= other.end and other.start <= self.end


@dataclass(frozen=True, slots=True)
class Region:
    """One connected area of a single palette colour."""

    color: int
    runs: tuple[Run, ...]

    def __post_init__(self) -> None:
        """Reject an empty region, which nothing downstream could draw."""
        if not self.runs:
            msg = "a region needs at least one run"
            raise RegionError(msg)

    @property
    def area(self) -> int:
        """Return the number of pixels in the region."""
        return sum(run.length for run in self.runs)

    @property
    def min_x(self) -> int:
        """Return the leftmost column the region touches."""
        return min(run.start for run in self.runs)

    @property
    def max_x(self) -> int:
        """Return the rightmost column the region touches."""
        return max(run.end for run in self.runs)

    @property
    def min_y(self) -> int:
        """Return the topmost row the region touches."""
        return self.runs[0].y

    @property
    def max_y(self) -> int:
        """Return the bottommost row the region touches."""
        return self.runs[-1].y

    @property
    def width(self) -> int:
        """Return the width of the bounding box."""
        return self.max_x - self.min_x + 1

    @property
    def height(self) -> int:
        """Return the height of the bounding box."""
        return self.max_y - self.min_y + 1

    def pixels(self) -> Iterator[tuple[int, int]]:
        """Yield every pixel in the region, row by row."""
        for run in self.runs:
            for x in range(run.start, run.end + 1):
                yield x, run.y


@dataclass(frozen=True, slots=True)
class Decomposition:
    """Every region worth drawing, and what raising the threshold cost."""

    regions: tuple[Region, ...]
    dropped_regions: int
    dropped_pixels: int

    @property
    def total_area(self) -> int:
        """Return the number of pixels the kept regions cover."""
        return sum(region.area for region in self.regions)


def row_runs(row: bytes) -> Iterator[tuple[int, int, int]]:
    """Yield colour, start and inclusive end for each run of equal bytes.

    The row is compared against itself shifted one byte, as a single big
    integer, so the per-pixel comparison happens in C and only one step of
    Python is spent per run rather than per pixel. On a 900 pixel row that is
    the difference between counting to 900 and counting to the handful of runs
    the row actually contains.
    """
    if not row:
        return
    value = int.from_bytes(row, "big")
    changes = (value ^ (value >> 8)).to_bytes(len(row), "big").translate(_CHANGE_MARKS)
    start = 0
    while True:
        following = changes.find(_CHANGE, start + 1)
        if following < 0:
            yield row[start], start, len(row) - 1
            return
        yield row[start], start, following - 1
        start = following


class _Union:
    """Union-find over runs, keyed by their position in the run list."""

    def __init__(self) -> None:
        self._parent: list[int] = []

    def add(self) -> int:
        """Add a new singleton and return its identifier."""
        self._parent.append(len(self._parent))
        return len(self._parent) - 1

    def find(self, item: int) -> int:
        """Return the representative of an item's set, compressing the path."""
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, first: int, second: int) -> None:
        """Merge the sets containing two items."""
        first_root, second_root = self.find(first), self.find(second)
        if first_root != second_root:
            self._parent[max(first_root, second_root)] = min(first_root, second_root)


@dataclass(slots=True)
class _RunTable:
    """Runs as parallel lists, to keep the merge pass free of object churn.

    A noisy raster produces hundreds of thousands of runs, and building a
    validated dataclass for each one costs more than everything else in this
    module put together. Objects are built at the end, for the regions that
    survive the area threshold.
    """

    y: list[int]
    start: list[int]
    end: list[int]
    color: list[int]

    def add(self, y: int, start: int, end: int, color: int) -> int:
        """Append a run and return its index."""
        self.y.append(y)
        self.start.append(start)
        self.end.append(end)
        self.color.append(color)
        return len(self.y) - 1


def _merge_adjacent_rows(
    previous: list[int],
    current: list[int],
    table: _RunTable,
    groups: _Union,
) -> None:
    """Join runs in two neighbouring rows that overlap and share a colour."""
    above = below = 0
    while above < len(previous) and below < len(current):
        upper, lower = previous[above], current[below]
        if (
            table.color[upper] == table.color[lower]
            and table.start[upper] <= table.end[lower]
            and table.start[lower] <= table.end[upper]
        ):
            groups.union(upper, lower)
        if table.end[upper] < table.end[lower]:
            above += 1
        else:
            below += 1


def decompose(
    image: QuantizedImage,
    *,
    ignore: Collection[int] = (),
    min_area: int = MIN_AREA_FLOOR,
) -> Decomposition:
    """Split the raster into connected single-colour regions.

    Colours in ``ignore`` are skipped entirely; that is how the canvas
    background, which is already on screen, costs nothing. Regions smaller than
    ``min_area`` are dropped and counted, which is the lever the time budget
    pulls first when a plan overruns.
    """
    if min_area < MIN_AREA_FLOOR:
        msg = f"minimum area must be at least {MIN_AREA_FLOOR}, got {min_area}"
        raise RegionError(msg)
    skipped = frozenset(ignore)
    groups = _Union()
    table = _RunTable(y=[], start=[], end=[], color=[])
    previous: list[int] = []

    for y in range(image.height):
        row = image.indices[y * image.width : (y + 1) * image.width]
        current: list[int] = []
        for color, start, end in row_runs(row):
            if color in skipped:
                continue
            # The union-find identifier of a run is its index in the table.
            current.append(table.add(y, start, end, color))
            groups.add()
        _merge_adjacent_rows(previous, current, table, groups)
        previous = current

    members: dict[int, list[int]] = defaultdict(list)
    for index in range(len(table.y)):
        members[groups.find(index)].append(index)

    kept: list[Region] = []
    dropped_regions = 0
    dropped_pixels = 0
    for root, indices in members.items():
        area = sum(table.end[index] - table.start[index] + 1 for index in indices)
        if area < min_area:
            dropped_regions += 1
            dropped_pixels += area
            continue
        kept.append(
            Region(
                color=table.color[root],
                runs=tuple(
                    Run(y=table.y[index], start=table.start[index], end=table.end[index])
                    for index in indices
                ),
            )
        )

    kept.sort(key=lambda region: (region.color, region.min_y, region.min_x))
    return Decomposition(
        regions=tuple(kept), dropped_regions=dropped_regions, dropped_pixels=dropped_pixels
    )
