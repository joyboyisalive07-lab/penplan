"""Colour distance done properly: sRGB to CIE Lab, and CIEDE2000 between them.

Matching a photograph to a dozen palette colours with plain RGB distance
produces choices that are arithmetically closest and visibly wrong: skin tones
go green, skies go purple, because equal steps in RGB are not equal steps in
perception. Lab spreads colours the way the eye does, and CIEDE2000 corrects
what is left, most of all in the blue region and for near-neutral colours where
Lab still overstates hue differences.

The formulas are the published ones: IEC 61966-2-1 for the sRGB transfer
function, CIE 15 for Lab under D65, and Sharma, Wu and Dalal (2005) for the
CIEDE2000 implementation notes, whose worked test pairs the tests use because
they were chosen to catch exactly the hue-wraparound mistakes this formula
invites.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Sequence

    from penplan.model import Rgb

type Lab = tuple[float, float, float]
"""Lightness, green-red and blue-yellow, under the D65 white point."""

_CHANNEL_MAX: Final = 255.0

# sRGB transfer function, IEC 61966-2-1: a short linear segment near black
# where the power function would have infinite slope, then a 2.4 power curve.
_SRGB_LINEAR_CUTOFF: Final = 0.04045
_SRGB_LINEAR_DIVISOR: Final = 12.92
_SRGB_GAMMA: Final = 2.4
_SRGB_OFFSET: Final = 0.055

# sRGB primaries to CIE XYZ under D65, the matrix that accompanies the standard.
_RGB_TO_XYZ: Final = (
    (0.4124564, 0.3575761, 0.1804375),
    (0.2126729, 0.7151522, 0.0721750),
    (0.0193339, 0.1191920, 0.9503041),
)
# D65, the white point sRGB is defined against, normalised to Y = 1.
_WHITE_POINT: Final = (0.95047, 1.0, 1.08883)

# CIE 15 replaces the cube root below this value with a linear segment, for the
# same reason as the sRGB curve: the cube root's slope runs away near zero.
_LAB_DELTA: Final = 6.0 / 29.0
_LAB_DELTA_CUBED: Final = _LAB_DELTA**3
_LAB_LINEAR_SLOPE: Final = 1.0 / (3.0 * _LAB_DELTA**2)
_LAB_LINEAR_OFFSET: Final = 4.0 / 29.0

# CIEDE2000 constants. The weights are the graphic-arts defaults of 1, and 25^7
# appears in the chroma corrections that pull blue and near-neutral colours into
# line with the observed data.
_WEIGHT_LIGHTNESS: Final = 1.0
_WEIGHT_CHROMA: Final = 1.0
_WEIGHT_HUE: Final = 1.0
_POW25_7: Final = 25.0**7
_NEUTRAL_LIGHTNESS: Final = 50.0
_STRAIGHT_ANGLE: Final = 180.0
_FULL_TURN: Final = 360.0


def srgb_to_linear(channel: int) -> float:
    """Undo the sRGB transfer function for one 8-bit channel."""
    value = channel / _CHANNEL_MAX
    if value <= _SRGB_LINEAR_CUTOFF:
        return value / _SRGB_LINEAR_DIVISOR
    return ((value + _SRGB_OFFSET) / (1.0 + _SRGB_OFFSET)) ** _SRGB_GAMMA


def srgb_to_xyz(color: Rgb) -> tuple[float, float, float]:
    """Convert an 8-bit sRGB colour to CIE XYZ under D65."""
    linear = [srgb_to_linear(channel) for channel in color]
    return tuple(sum(row[index] * linear[index] for index in range(3)) for row in _RGB_TO_XYZ)  # type: ignore[return-value]


def _lab_transfer(ratio: float) -> float:
    if ratio > _LAB_DELTA_CUBED:
        return ratio ** (1.0 / 3.0)
    return _LAB_LINEAR_SLOPE * ratio + _LAB_LINEAR_OFFSET


def xyz_to_lab(xyz: tuple[float, float, float]) -> Lab:
    """Convert CIE XYZ to CIE Lab under D65."""
    fx, fy, fz = (
        _lab_transfer(value / white) for value, white in zip(xyz, _WHITE_POINT, strict=True)
    )
    return (116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz))


def srgb_to_lab(color: Rgb) -> Lab:
    """Convert an 8-bit sRGB colour to CIE Lab under D65."""
    return xyz_to_lab(srgb_to_xyz(color))


def _hue_degrees(a: float, b: float) -> float:
    if a == 0.0 and b == 0.0:
        return 0.0
    return math.degrees(math.atan2(b, a)) % _FULL_TURN


def _hue_difference(first: float, second: float, chroma_product: float) -> float:
    if chroma_product == 0.0:
        return 0.0
    delta = second - first
    if abs(delta) <= _STRAIGHT_ANGLE:
        return delta
    return delta - math.copysign(_FULL_TURN, delta)


def _mean_hue(first: float, second: float, chroma_product: float) -> float:
    if chroma_product == 0.0:
        return first + second
    if abs(first - second) <= _STRAIGHT_ANGLE:
        return (first + second) / 2.0
    if first + second < _FULL_TURN:
        return (first + second + _FULL_TURN) / 2.0
    return (first + second - _FULL_TURN) / 2.0


def ciede2000(first: Lab, second: Lab) -> float:
    """Return the CIEDE2000 difference between two Lab colours.

    The value is symmetric, zero for identical colours, and scaled so that a
    difference of about 1 is the threshold a trained observer notices.
    """
    lightness_1, a_1, b_1 = first
    lightness_2, a_2, b_2 = second

    chroma_1 = math.hypot(a_1, b_1)
    chroma_2 = math.hypot(a_2, b_2)
    mean_chroma = (chroma_1 + chroma_2) / 2.0
    # Stretches the a axis for low-chroma colours, which is what stops two
    # near-neutral greys from reading as further apart than they look.
    stretch = 0.5 * (1.0 - math.sqrt(mean_chroma**7 / (mean_chroma**7 + _POW25_7)))
    a_prime_1 = (1.0 + stretch) * a_1
    a_prime_2 = (1.0 + stretch) * a_2
    chroma_prime_1 = math.hypot(a_prime_1, b_1)
    chroma_prime_2 = math.hypot(a_prime_2, b_2)
    chroma_product = chroma_prime_1 * chroma_prime_2

    hue_1 = _hue_degrees(a_prime_1, b_1)
    hue_2 = _hue_degrees(a_prime_2, b_2)
    delta_lightness = lightness_2 - lightness_1
    delta_chroma = chroma_prime_2 - chroma_prime_1
    delta_hue_angle = _hue_difference(hue_1, hue_2, chroma_product)
    delta_hue = 2.0 * math.sqrt(chroma_product) * math.sin(math.radians(delta_hue_angle) / 2.0)

    mean_lightness = (lightness_1 + lightness_2) / 2.0
    mean_chroma_prime = (chroma_prime_1 + chroma_prime_2) / 2.0
    mean_hue = _mean_hue(hue_1, hue_2, chroma_product)

    hue_weight = (
        1.0
        - 0.17 * math.cos(math.radians(mean_hue - 30.0))
        + 0.24 * math.cos(math.radians(2.0 * mean_hue))
        + 0.32 * math.cos(math.radians(3.0 * mean_hue + 6.0))
        - 0.20 * math.cos(math.radians(4.0 * mean_hue - 63.0))
    )
    lightness_scale = 1.0 + (
        0.015
        * (mean_lightness - _NEUTRAL_LIGHTNESS) ** 2
        / math.sqrt(20.0 + (mean_lightness - _NEUTRAL_LIGHTNESS) ** 2)
    )
    chroma_scale = 1.0 + 0.045 * mean_chroma_prime
    hue_scale = 1.0 + 0.015 * mean_chroma_prime * hue_weight

    # The rotation term, which is what keeps saturated blues from being ranked
    # as similar to purples; it only bites near a hue of 275 degrees.
    rotation_angle = 30.0 * math.exp(-(((mean_hue - 275.0) / 25.0) ** 2))
    chroma_correction = 2.0 * math.sqrt(mean_chroma_prime**7 / (mean_chroma_prime**7 + _POW25_7))
    rotation = -math.sin(math.radians(2.0 * rotation_angle)) * chroma_correction

    lightness_term = delta_lightness / (_WEIGHT_LIGHTNESS * lightness_scale)
    chroma_term = delta_chroma / (_WEIGHT_CHROMA * chroma_scale)
    hue_term = delta_hue / (_WEIGHT_HUE * hue_scale)
    return math.sqrt(
        lightness_term**2 + chroma_term**2 + hue_term**2 + rotation * chroma_term * hue_term
    )


def color_difference(first: Rgb, second: Rgb) -> float:
    """Return the perceptual difference between two 8-bit sRGB colours."""
    return ciede2000(srgb_to_lab(first), srgb_to_lab(second))


class Palette:
    """The colours one profile can actually draw with, and how to match them.

    Matching is cached per source colour. An image reduced to a few thousand
    distinct colours would otherwise pay for the same CIEDE2000 comparisons
    hundreds of times over.
    """

    def __init__(self, colors: Sequence[Rgb]) -> None:
        if not colors:
            msg = "a palette needs at least one colour"
            raise ValueError(msg)
        self.colors: Final = tuple(colors)
        self.labs: Final = tuple(srgb_to_lab(color) for color in self.colors)
        self._matches: dict[Rgb, tuple[int, float]] = {}

    def __len__(self) -> int:
        """Return the number of colours in the palette."""
        return len(self.colors)

    def match(self, color: Rgb) -> tuple[int, float]:
        """Return the index of the closest palette colour and its distance."""
        cached = self._matches.get(color)
        if cached is not None:
            return cached
        lab = srgb_to_lab(color)
        best_index = 0
        best_distance = math.inf
        for index, candidate in enumerate(self.labs):
            distance = ciede2000(lab, candidate)
            if distance < best_distance:
                best_index = index
                best_distance = distance
        result = (best_index, best_distance)
        self._matches[color] = result
        return result

    def nearest(self, color: Rgb) -> int:
        """Return the index of the closest palette colour."""
        return self.match(color)[0]
