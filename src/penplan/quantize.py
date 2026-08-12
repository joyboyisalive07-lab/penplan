"""Map a source image onto the colours a profile can actually draw.

Two things make this more than a loop over pixels. The first is cost: matching
with CIEDE2000 takes microseconds, so the source is reduced to a bounded number
of distinct colours first and only those are matched, after which every pixel is
mapped through a byte table at C speed.

The second is dithering. With a dozen swatches, a gradient has nowhere to go and
lands as bands. Ordered Bayer dithering trades those bands for a fixed
checkerboard between the two nearest colours. It is off by default because it
does not just change how the drawing looks: alternating pixels destroy the runs
the stroke planner merges into polylines, so the same picture costs several
times as many strokes and the time budget pays for all of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from PIL import Image

if TYPE_CHECKING:
    from penplan.model import Rgb
    from penplan.palette import Palette

# Enough distinct colours that a further reduction to a dozen swatches loses
# nothing, and few enough that matching them all costs a few tens of
# milliseconds rather than tens of seconds.
MAX_SOURCE_COLORS: Final = 256
_INDEX_LIMIT: Final = 256

# The classic 4x4 ordered dither threshold matrix. Four by four gives sixteen
# levels, which is as fine as a palette this coarse can use, and its short
# period keeps the runs the stroke planner merges longer than an 8x8 would.
_BAYER: Final = (
    (0, 8, 2, 10),
    (12, 4, 14, 6),
    (3, 11, 1, 9),
    (15, 7, 13, 5),
)
_BAYER_SIDE: Final = 4
_BAYER_LEVELS: Final = _BAYER_SIDE * _BAYER_SIDE


class QuantizeError(ValueError):
    """The image or the palette cannot be quantized as asked."""


@dataclass(frozen=True, slots=True)
class QuantizedImage:
    """A raster of palette indices, the planner's input.

    ``colors`` is the profile's full palette, so an index here is the same
    index the plan and the executor use.
    """

    width: int
    height: int
    colors: tuple[Rgb, ...]
    indices: bytes

    def __post_init__(self) -> None:
        """Reject a raster whose size and buffer disagree."""
        if self.width <= 0 or self.height <= 0:
            msg = f"raster size must be positive, got {self.width}x{self.height}"
            raise QuantizeError(msg)
        if len(self.indices) != self.width * self.height:
            msg = (
                f"raster of {self.width}x{self.height} needs "
                f"{self.width * self.height} indices, got {len(self.indices)}"
            )
            raise QuantizeError(msg)

    def at(self, x: int, y: int) -> int:
        """Return the palette index at one raster position."""
        return self.indices[y * self.width + x]

    def used_colors(self) -> frozenset[int]:
        """Return the palette indices that actually appear."""
        return frozenset(self.indices)


def fit_within(size: tuple[int, int], box: tuple[int, int]) -> tuple[int, int]:
    """Return the largest size with the original aspect ratio that fits the box."""
    width, height = size
    box_width, box_height = box
    if width <= 0 or height <= 0 or box_width <= 0 or box_height <= 0:
        msg = f"cannot fit {size} into {box}"
        raise QuantizeError(msg)
    scale = min(box_width / width, box_height / height)
    return max(1, round(width * scale)), max(1, round(height * scale))


def prepare_source(image: Image.Image, width: int, height: int, background: Rgb) -> Image.Image:
    """Fit the image into the raster, centred on the canvas background colour.

    The margins are filled with the canvas background rather than with white or
    black, so that the letterbox quantizes to whatever the blank canvas already
    is and the planner leaves it alone instead of painting it.
    """
    if width <= 0 or height <= 0:
        msg = f"raster size must be positive, got {width}x{height}"
        raise QuantizeError(msg)
    with_alpha = image.convert("RGBA")
    flattened = Image.new("RGB", image.size, background)
    flattened.paste(with_alpha, mask=with_alpha)
    fitted_width, fitted_height = fit_within(image.size, (width, height))
    fitted = flattened.resize((fitted_width, fitted_height), Image.Resampling.LANCZOS)
    raster = Image.new("RGB", (width, height), background)
    raster.paste(fitted, ((width - fitted_width) // 2, (height - fitted_height) // 2))
    return raster


def _reduce_colors(image: Image.Image) -> tuple[bytes, list[Rgb]]:
    """Reduce to at most MAX_SOURCE_COLORS and return indices plus their colours."""
    reduced = image.quantize(
        colors=MAX_SOURCE_COLORS, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE
    )
    flat = reduced.getpalette() or []
    colors: list[Rgb] = [
        (flat[offset], flat[offset + 1], flat[offset + 2]) for offset in range(0, len(flat), 3)
    ]
    # translate() needs a full 256-entry table, and unused slots are never read.
    while len(colors) < _INDEX_LIMIT:
        colors.append((0, 0, 0))
    return reduced.tobytes(), colors


def _plain_table(palette: Palette, colors: list[Rgb]) -> bytes:
    return bytes(palette.nearest(color) for color in colors)


def _dither_tables(palette: Palette, colors: list[Rgb]) -> list[list[bytes]]:
    """Build one lookup table per cell of the Bayer matrix.

    A table maps a source colour to the nearer palette colour, or to the second
    nearest where the cell's threshold falls below the distance ratio. Every
    pixel of the raster is then one table lookup, with no colour maths left.
    """
    pairs = [palette.match_pair(color) for color in colors]
    tables: list[list[bytes]] = []
    for row in _BAYER:
        row_tables: list[bytes] = []
        for cell in row:
            threshold = (cell + 0.5) / _BAYER_LEVELS
            row_tables.append(
                bytes(second if threshold < ratio else first for first, second, ratio in pairs)
            )
        tables.append(row_tables)
    return tables


def quantize(image: Image.Image, palette: Palette, *, dither: bool = False) -> QuantizedImage:
    """Map an RGB image onto the palette, optionally with ordered dithering."""
    if len(palette) > _INDEX_LIMIT:
        msg = f"a palette of {len(palette)} colours does not fit in one byte per pixel"
        raise QuantizeError(msg)
    if image.mode != "RGB":
        msg = f"quantize needs an RGB image, got {image.mode}"
        raise QuantizeError(msg)
    width, height = image.size
    source, colors = _reduce_colors(image)

    if not dither:
        return QuantizedImage(
            width=width,
            height=height,
            colors=palette.colors,
            indices=source.translate(_plain_table(palette, colors)),
        )

    tables = _dither_tables(palette, colors)
    output = bytearray(width * height)
    for y in range(height):
        start = y * width
        row = source[start : start + width]
        row_tables = tables[y % _BAYER_SIDE]
        # Every fourth pixel shares a Bayer cell, so a whole row is four
        # C-speed translations and four strided copies rather than a Python
        # loop over its pixels.
        for phase in range(_BAYER_SIDE):
            output[start + phase : start + width : _BAYER_SIDE] = row.translate(row_tables[phase])[
                phase::_BAYER_SIDE
            ]
    return QuantizedImage(width=width, height=height, colors=palette.colors, indices=bytes(output))
