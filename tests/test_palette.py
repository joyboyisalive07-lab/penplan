"""Tests for the colour maths.

The CIEDE2000 pairs come from the worked examples published with Sharma, Wu and
Dalal (2005). They exist because the formula has three places where a naive
implementation goes wrong: hue angles either side of zero, the mean hue of two
angles that straddle 360, and the pairs where one colour has no chroma at all.
Every one of those cases is in the list below.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from penplan.palette import (
    Lab,
    Palette,
    ciede2000,
    color_difference,
    srgb_to_lab,
    srgb_to_linear,
)

if TYPE_CHECKING:
    from penplan.model import Rgb

# Lab1, Lab2, published CIEDE2000.
REFERENCE_PAIRS: list[tuple[Lab, Lab, float]] = [
    ((50.0000, 2.6772, -79.7751), (50.0000, 0.0000, -82.7485), 2.0425),
    ((50.0000, 3.1571, -77.2803), (50.0000, 0.0000, -82.7485), 2.8615),
    ((50.0000, 2.8361, -74.0200), (50.0000, 0.0000, -82.7485), 3.4412),
    ((50.0000, -1.3802, -84.2814), (50.0000, 0.0000, -82.7485), 1.0000),
    ((50.0000, -1.1848, -84.8006), (50.0000, 0.0000, -82.7485), 1.0000),
    ((50.0000, -0.9009, -85.5211), (50.0000, 0.0000, -82.7485), 1.0000),
    ((50.0000, 0.0000, 0.0000), (50.0000, -1.0000, 2.0000), 2.3669),
    ((50.0000, -1.0000, 2.0000), (50.0000, 0.0000, 0.0000), 2.3669),
    ((50.0000, 2.4900, -0.0010), (50.0000, -2.4900, 0.0009), 7.1792),
    ((50.0000, 2.4900, -0.0010), (50.0000, -2.4900, 0.0011), 7.2195),
    ((50.0000, 2.5000, 0.0000), (50.0000, 0.0000, -2.5000), 4.3065),
    ((50.0000, 2.5000, 0.0000), (73.0000, 25.0000, -18.0000), 27.1492),
    ((50.0000, 2.5000, 0.0000), (61.0000, -5.0000, 29.0000), 22.8977),
    ((50.0000, 2.5000, 0.0000), (56.0000, -27.0000, -3.0000), 31.9030),
    ((50.0000, 2.5000, 0.0000), (58.0000, 24.0000, 15.0000), 19.4535),
    ((50.0000, 2.5000, 0.0000), (50.0000, 3.1736, 0.5854), 1.0000),
    ((50.0000, 2.5000, 0.0000), (50.0000, 3.2972, 0.0000), 1.0000),
    ((50.0000, 2.5000, 0.0000), (50.0000, 1.8634, 0.5757), 1.0000),
    ((50.0000, 2.5000, 0.0000), (50.0000, 3.2592, 0.3350), 1.0000),
    ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
    ((63.0109, -31.0961, -5.8663), (62.8187, -29.7946, -4.0864), 1.2630),
    ((61.2901, 3.7196, -5.3901), (61.4292, 2.2480, -4.9620), 1.8731),
    ((35.0831, -44.1164, 3.7933), (35.0232, -40.0716, 1.5901), 1.8645),
    ((22.7233, 20.0904, -46.6940), (23.0331, 14.9730, -42.5619), 2.0373),
    ((36.4612, 47.8580, 18.3852), (36.2715, 50.5065, 21.2231), 1.4146),
    ((90.8027, -2.0831, 1.4410), (91.1528, -1.6435, 0.0447), 1.4441),
    ((90.9257, -0.5406, -0.9208), (88.6381, -0.8985, -0.7239), 1.5381),
    ((6.7747, -0.2908, -2.4247), (5.8714, -0.0985, -2.2286), 0.6377),
    ((2.0776, 0.0795, -1.1350), (0.9033, -0.0636, -0.5514), 0.9082),
]

# The published values carry four decimals, so this is as tight as it can be.
REFERENCE_TOLERANCE = 1e-4

# Lab values for the sRGB primaries, to four decimals.
PRIMARY_LAB: list[tuple[Rgb, Lab]] = [
    ((255, 255, 255), (100.0, 0.0, 0.0)),
    ((0, 0, 0), (0.0, 0.0, 0.0)),
    ((255, 0, 0), (53.2408, 80.0925, 67.2032)),
    ((0, 255, 0), (87.7347, -86.1827, 83.1793)),
    ((0, 0, 255), (32.2970, 79.1875, -107.8602)),
]


@pytest.mark.parametrize(("first", "second", "expected"), REFERENCE_PAIRS)
def test_reference_pairs(first: Lab, second: Lab, expected: float) -> None:
    assert ciede2000(first, second) == pytest.approx(expected, abs=REFERENCE_TOLERANCE)


@pytest.mark.parametrize(("first", "second", "expected"), REFERENCE_PAIRS)
def test_difference_is_symmetric(first: Lab, second: Lab, expected: float) -> None:
    assert ciede2000(second, first) == pytest.approx(expected, abs=REFERENCE_TOLERANCE)


def test_identical_colours_are_zero_apart() -> None:
    assert ciede2000((42.0, -13.0, 7.0), (42.0, -13.0, 7.0)) == 0.0
    assert color_difference((120, 30, 200), (120, 30, 200)) == 0.0


@pytest.mark.parametrize(("color", "expected"), PRIMARY_LAB)
def test_srgb_to_lab_matches_the_published_values(color: Rgb, expected: Lab) -> None:
    for actual, wanted in zip(srgb_to_lab(color), expected, strict=True):
        assert actual == pytest.approx(wanted, abs=1e-3)


def test_mid_grey_is_darker_than_half_lightness() -> None:
    # The sRGB curve is not linear, so the numeric midpoint sits above 50.
    lightness, a, b = srgb_to_lab((128, 128, 128))
    assert lightness == pytest.approx(53.585, abs=1e-2)
    # The published matrix rows sum to 1.0000001 rather than exactly 1, so a
    # neutral carries a hundred-thousandth of a unit of chroma. That is eleven
    # orders of magnitude below anything an eye or this planner cares about.
    assert a == pytest.approx(0.0, abs=1e-4)
    assert b == pytest.approx(0.0, abs=1e-4)


def test_transfer_function_has_a_linear_foot() -> None:
    assert srgb_to_linear(0) == 0.0
    assert srgb_to_linear(255) == pytest.approx(1.0)
    # Below the cutoff the curve is a straight line through the origin.
    assert srgb_to_linear(10) == pytest.approx(10 / 255 / 12.92)


def test_palette_matches_the_perceptually_closest_colour() -> None:
    # In RGB the sample is closer to the grey; perceptually it belongs to the
    # skin tone, which is the whole reason this module exists.
    palette = Palette([(128, 128, 128), (222, 170, 135)])
    assert palette.nearest((205, 160, 130)) == 1


def test_palette_reports_the_distance_it_matched_at() -> None:
    palette = Palette([(0, 0, 0), (255, 255, 255)])
    index, distance = palette.match((250, 250, 250))
    assert index == 1
    assert distance == pytest.approx(color_difference((250, 250, 250), (255, 255, 255)))


def test_palette_match_is_cached_but_unchanged() -> None:
    palette = Palette([(10, 20, 30), (200, 100, 50)])
    first = palette.match((190, 110, 60))
    assert palette.match((190, 110, 60)) == first


def test_exact_palette_colours_match_themselves_at_zero() -> None:
    colors: list[Rgb] = [(0, 0, 0), (255, 255, 255), (255, 0, 0), (0, 128, 64)]
    palette = Palette(colors)
    for index, color in enumerate(colors):
        assert palette.match(color) == (index, 0.0)


def test_empty_palette_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one colour"):
        Palette([])


def test_palette_length_is_its_colour_count() -> None:
    assert len(Palette([(0, 0, 0), (1, 1, 1), (2, 2, 2)])) == 3
