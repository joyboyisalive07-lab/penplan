"""Tests for mapping a source image onto a profile palette."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from penplan.palette import Palette, color_difference
from penplan.quantize import (
    QuantizedImage,
    QuantizeError,
    fit_within,
    prepare_source,
    quantize,
)

if TYPE_CHECKING:
    from penplan.model import Rgb

BLACK: Rgb = (0, 0, 0)
WHITE: Rgb = (255, 255, 255)
RED: Rgb = (220, 30, 40)
BLUE: Rgb = (40, 60, 200)

FOUR_COLORS = Palette([BLACK, WHITE, RED, BLUE])
TWO_GREYS = Palette([BLACK, WHITE])


def solid(color: Rgb, size: tuple[int, int] = (8, 8)) -> Image.Image:
    return Image.new("RGB", size, color)


def gradient(width: int, height: int) -> Image.Image:
    image = Image.new("RGB", (width, height))
    image.putdata(
        [(x * 255 // max(1, width - 1),) * 3 for y in range(height) for x in range(width)]
    )
    return image


def test_solid_image_maps_to_one_index() -> None:
    result = quantize(solid(RED), FOUR_COLORS)
    assert result.used_colors() == {2}
    assert result.at(0, 0) == 2
    assert result.colors == FOUR_COLORS.colors


def test_every_pixel_gets_its_nearest_colour() -> None:
    image = Image.new("RGB", (2, 2))
    image.putdata([BLACK, WHITE, RED, BLUE])
    result = quantize(image, FOUR_COLORS)
    assert [result.at(x, y) for y in range(2) for x in range(2)] == [0, 1, 2, 3]


def test_quantization_is_deterministic() -> None:
    image = gradient(40, 10)
    first = quantize(image, FOUR_COLORS)
    second = quantize(image, FOUR_COLORS)
    assert first.indices == second.indices
    assert quantize(image, FOUR_COLORS, dither=True).indices == (
        quantize(image, FOUR_COLORS, dither=True).indices
    )


def test_dithering_mixes_the_two_nearest_colours() -> None:
    # Mid grey has nowhere to go in a black and white palette, so undithered it
    # lands on one colour and dithered it becomes a mixture of both.
    grey = solid((128, 128, 128), (16, 16))
    plain = quantize(grey, TWO_GREYS)
    dithered = quantize(grey, TWO_GREYS, dither=True)
    assert len(plain.used_colors()) == 1
    assert dithered.used_colors() == {0, 1}


def test_dithering_holds_the_average_near_the_source() -> None:
    grey = solid((128, 128, 128), (16, 16))
    counts = Counter(quantize(grey, TWO_GREYS, dither=True).indices)
    white_share = counts[1] / sum(counts.values())
    # Mid grey sits perceptually nearer white than black, so the mixture leans
    # that way; anything outside this band would be a broken threshold.
    assert 0.4 < white_share < 0.75


def test_dithering_leaves_exact_colours_alone() -> None:
    # A pixel that is already a palette colour has zero distance to it, so no
    # Bayer threshold can ever push it to the second nearest.
    image = Image.new("RGB", (16, 16))
    image.putdata([RED if (x + y) % 2 else BLUE for y in range(16) for x in range(16)])
    dithered = quantize(image, FOUR_COLORS, dither=True)
    assert dithered.used_colors() == {2, 3}
    assert [dithered.at(x, y) for y in range(2) for x in range(2)] == [3, 2, 2, 3]


def test_dithering_costs_more_colour_changes_than_it_saves() -> None:
    # The README claim in one assertion: dithering multiplies the runs the
    # stroke planner has to draw.
    image = gradient(64, 16)
    plain = quantize(image, FOUR_COLORS)
    dithered = quantize(image, FOUR_COLORS, dither=True)
    plain_runs = sum(
        1
        for index in range(1, len(plain.indices))
        if plain.indices[index] != plain.indices[index - 1]
    )
    dithered_runs = sum(
        1
        for index in range(1, len(dithered.indices))
        if dithered.indices[index] != dithered.indices[index - 1]
    )
    assert dithered_runs > plain_runs * 2


def test_a_photograph_sized_raster_quantizes_quickly() -> None:
    # Not a benchmark, a guard: the whole design rests on matching a bounded
    # number of distinct colours rather than one per pixel.
    image = gradient(400, 300)
    assert len(quantize(image, FOUR_COLORS).indices) == 400 * 300


def test_prepare_source_letterboxes_onto_the_background() -> None:
    # A wide image into a square raster leaves background bands top and bottom.
    raster = prepare_source(solid(RED, (40, 10)), 20, 20, WHITE)
    assert raster.size == (20, 20)
    assert raster.getpixel((10, 0)) == WHITE
    assert raster.getpixel((10, 10)) == RED
    assert raster.getpixel((10, 19)) == WHITE


def test_prepare_source_flattens_transparency_onto_the_background() -> None:
    image = Image.new("RGBA", (8, 8), (255, 0, 0, 0))
    raster = prepare_source(image, 8, 8, BLUE)
    assert raster.mode == "RGB"
    assert raster.getpixel((4, 4)) == BLUE


def test_prepare_source_keeps_the_aspect_ratio() -> None:
    assert fit_within((100, 50), (40, 40)) == (40, 20)
    assert fit_within((50, 100), (40, 40)) == (20, 40)
    assert fit_within((10, 10), (40, 40)) == (40, 40)


def test_fit_never_returns_an_empty_size() -> None:
    assert fit_within((1000, 1), (10, 10)) == (10, 1)


def test_fit_refuses_an_empty_box() -> None:
    with pytest.raises(QuantizeError, match="cannot fit"):
        fit_within((10, 10), (0, 10))


def test_prepare_source_refuses_an_empty_raster() -> None:
    with pytest.raises(QuantizeError, match="raster size must be positive"):
        prepare_source(solid(RED), 0, 10, WHITE)


def test_quantize_refuses_a_non_rgb_image() -> None:
    with pytest.raises(QuantizeError, match="needs an RGB image"):
        quantize(Image.new("L", (4, 4)), FOUR_COLORS)


def test_raster_rejects_a_buffer_of_the_wrong_length() -> None:
    with pytest.raises(QuantizeError, match="needs 4 indices"):
        QuantizedImage(width=2, height=2, colors=(BLACK,), indices=b"\x00")


def test_choice_follows_perception_not_arithmetic() -> None:
    # A muted skin tone against a grey and a warmer tone: RGB distance picks the
    # grey, CIEDE2000 picks the skin tone, and the drawing shows the difference.
    skin: Rgb = (222, 170, 135)
    grey: Rgb = (170, 170, 170)
    sample: Rgb = (205, 160, 130)
    palette = Palette([grey, skin])
    assert quantize(solid(sample), palette).used_colors() == {1}
    assert color_difference(sample, skin) < color_difference(sample, grey)
