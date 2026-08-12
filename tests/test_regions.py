"""Tests for splitting a quantized raster into connected regions."""

from __future__ import annotations

import pytest

from penplan.quantize import QuantizedImage
from penplan.regions import Region, RegionError, Run, decompose, row_runs

PALETTE = ((0, 0, 0), (255, 255, 255), (255, 0, 0), (0, 0, 255))


def raster(*rows: str) -> QuantizedImage:
    """Build a raster from rows of digits, each digit a palette index."""
    width = len(rows[0])
    indices = bytes(int(char) for row in rows for char in row)
    return QuantizedImage(width=width, height=len(rows), colors=PALETTE, indices=indices)


def test_a_solid_raster_is_one_region() -> None:
    result = decompose(raster("222", "222"))
    assert len(result.regions) == 1
    assert result.regions[0].color == 2
    assert result.regions[0].area == 6


def test_separated_shapes_of_one_colour_are_separate_regions() -> None:
    result = decompose(raster("20002", "20002"), ignore=[0])
    assert len(result.regions) == 2
    assert [region.area for region in result.regions] == [2, 2]


def test_a_diagonal_touch_does_not_connect() -> None:
    # Four-way connectivity, because that is what a flood fill would reach.
    result = decompose(raster("20", "02"), ignore=[0])
    assert len(result.regions) == 2


def test_a_concave_shape_stays_one_region() -> None:
    result = decompose(raster("2002", "2002", "2222"), ignore=[0])
    assert len(result.regions) == 1
    assert result.regions[0].area == 8


def test_regions_of_different_colours_never_merge() -> None:
    result = decompose(raster("2233", "2233"))
    assert sorted(region.color for region in result.regions) == [2, 3]


def test_ignored_colours_produce_no_regions() -> None:
    result = decompose(raster("0120", "0120"), ignore=[0])
    assert sorted(region.color for region in result.regions) == [1, 2]


def test_runs_describe_the_shape_row_by_row() -> None:
    result = decompose(raster("0220", "2222"), ignore=[0])
    region = result.regions[0]
    assert region.runs == (Run(y=0, start=1, end=2), Run(y=1, start=0, end=3))
    assert region.area == 6


def test_bounding_box_covers_the_region() -> None:
    region = decompose(raster("0220", "2222", "0000"), ignore=[0]).regions[0]
    assert (region.min_x, region.max_x) == (0, 3)
    assert (region.min_y, region.max_y) == (0, 1)
    assert (region.width, region.height) == (4, 2)


def test_small_regions_are_dropped_and_counted() -> None:
    # One block of four pixels and three single strays.
    result = decompose(raster("22020", "22000", "00002"), ignore=[0], min_area=2)
    assert [region.area for region in result.regions] == [4]
    assert result.dropped_regions == 2
    assert result.dropped_pixels == 2


def test_nothing_is_dropped_at_the_default_threshold() -> None:
    result = decompose(raster("20202", "02020"), ignore=[0])
    assert result.dropped_regions == 0
    assert result.dropped_pixels == 0
    assert result.total_area == 5


def test_a_checkerboard_is_every_pixel_its_own_region() -> None:
    result = decompose(raster("2323", "3232", "2323"))
    assert len(result.regions) == 12


def test_order_is_deterministic_and_by_colour_then_position() -> None:
    image = raster("13", "31")
    first = decompose(image)
    second = decompose(image)
    assert first == second
    assert [(region.color, region.min_y, region.min_x) for region in first.regions] == [
        (1, 0, 0),
        (1, 1, 1),
        (3, 0, 1),
        (3, 1, 0),
    ]


def test_pixels_lists_every_pixel_once() -> None:
    region = decompose(raster("022", "220"), ignore=[0]).regions[0]
    assert sorted(region.pixels()) == [(0, 1), (1, 0), (1, 1), (2, 0)]


def test_row_runs_splits_a_row_into_maximal_stretches() -> None:
    assert list(row_runs(bytes([2, 2, 3, 3, 3, 0]))) == [(2, 0, 1), (3, 2, 4), (0, 5, 5)]


def test_row_runs_of_a_single_pixel() -> None:
    assert list(row_runs(bytes([7]))) == [(7, 0, 0)]


def test_a_raster_of_only_ignored_colour_yields_nothing() -> None:
    result = decompose(raster("000", "000"), ignore=[0])
    assert result.regions == ()
    assert result.total_area == 0


def test_threshold_below_one_is_refused() -> None:
    with pytest.raises(RegionError, match="minimum area must be at least 1"):
        decompose(raster("22"), min_area=0)


def test_a_backwards_run_is_refused() -> None:
    with pytest.raises(RegionError, match="ends at 1, before its start 3"):
        Run(y=0, start=3, end=1)


def test_an_empty_region_is_refused() -> None:
    with pytest.raises(RegionError, match="at least one run"):
        Region(color=0, runs=())


def test_run_overlap_needs_a_shared_column() -> None:
    assert Run(y=0, start=0, end=3).overlaps(Run(y=1, start=3, end=6))
    assert not Run(y=0, start=0, end=3).overlaps(Run(y=1, start=4, end=6))


def test_two_labels_merge_when_an_arm_rejoins_the_body() -> None:
    # The stub at row 2 starts life as its own label and only turns out to be
    # part of the border two rows later. Single-pass labelling without a merge
    # step reports two regions here.
    result = decompose(
        raster(
            "22222",
            "20002",
            "20202",
            "20222",
            "20000",
            "22222",
        ),
        ignore=[0],
    )
    assert len(result.regions) == 1
    assert result.regions[0].area == 20
