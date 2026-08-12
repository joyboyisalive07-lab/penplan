"""The calibration wizard: teaching the tool a canvas it has never seen.

The user hovers over each target and presses a key; nothing is clicked while
positions are being captured, because a click on the canvas corner would leave
a dot on the drawing and a click on a palette swatch would change the tool
state mid-calibration.

Palette colours are read from the screen afterwards rather than at the moment
of capture, with the cursor parked in the middle of the canvas. A swatch under
the pointer is usually drawn in a hover state, and a hover highlight recorded
as the palette colour would poison every quantization decision made with the
profile.

The wizard talks to the screen through :class:`CalibrationSurface`, so the
whole flow can be driven by a fake in the tests.
"""

from __future__ import annotations

import itertools
import math
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Final, Protocol, Self

from penplan.input_win import (
    VK_ESCAPE,
    VK_F8,
    VK_F9,
    AbortedError,
    HotkeyListener,
    Pointer,
    ScreenPixels,
    cursor_position,
    enable_dpi_awareness,
    virtual_screen,
)
from penplan.model import DEFAULT_COST_MODEL, CostModel, Rgb, ScreenRect
from penplan.profile import (
    MIN_CANVAS_SIDE,
    BrushControl,
    Control,
    Profile,
    ProfileError,
    Swatch,
    timestamp,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

CAPTURE_KEY: Final = VK_F8
NEXT_KEY: Final = VK_F9
ABORT_KEY: Final = VK_ESCAPE

# Where the cursor is parked while palette colours are read: the middle of the
# canvas is guaranteed to be away from the palette and has no hot corner.
# The canvas background is sampled near its top-left corner, far enough from
# the parked cursor that a brush-preview ring cannot reach the sample.
_BACKGROUND_SAMPLE_INSET: Final = 4

# Test strokes for brush measurement span this fraction of the canvas width,
# long enough to reach full width and short enough to stay clear of the edges.
_TEST_STROKE_FRACTION: Final = 0.25
# Half the height of the column scanned across a test stroke. Nothing sensible
# paints wider than this, and scanning further only risks meeting another row.
_SCAN_HALF_HEIGHT: Final = 48
# A stroke edge is where coverage falls to half the peak, which is the standard
# way to state the width of an anti-aliased line.
_HALF_MAXIMUM: Final = 0.5
# Below this much colour distance from the background, nothing was drawn at
# all, and reporting a width would be inventing one. The scale is 0 to 441.
_MIN_INK_CONTRAST: Final = 24.0

# Used only when the user declines to measure: a plausible progression rather
# than a measurement, and the profile records it as unmeasured.
_FALLBACK_BASE_WIDTH: Final = 4.0
_FALLBACK_WIDTH_RATIO: Final = 2.2

# A page needs a frame or two to drop a hover highlight after the pointer
# leaves a swatch, and reading during that frame records the highlight.
_HOVER_SETTLE_SECONDS: Final = 0.15
# Test strokes are drawn in short hops rather than one jump, because a canvas
# that samples pointer position rather than interpolating would otherwise see
# a single move and paint a dot at each end.
_TEST_STROKE_HOP_PIXELS: Final = 4
_TEST_STROKE_HOP_SECONDS: Final = 0.008

# The self-timing run. Enough repetitions to average out a scheduling hiccup,
# few enough that the whole thing is over in a couple of seconds.
_COST_SAMPLES: Final = 12
_COST_SHORT_HOP: Final = 8
_COST_SAMPLE_INSET: Final = 4
# A measured cost of zero would let the planner believe a drawing is free.
_MIN_COST_SECONDS: Final = 1e-5


class StepKind(Enum):
    """Whether a calibration step captures one point or a list of them."""

    SINGLE = "single"
    LIST = "list"


@dataclass(frozen=True, slots=True)
class CalibrationStep:
    """One instruction shown to the user, and what it collects."""

    key: str
    prompt: str
    kind: StepKind


SCRIPT: Final = (
    CalibrationStep(
        "canvas_top_left", "Point at the top-left corner of the canvas", StepKind.SINGLE
    ),
    CalibrationStep(
        "canvas_bottom_right", "Point at the bottom-right corner of the canvas", StepKind.SINGLE
    ),
    CalibrationStep(
        "palette", "Point at each palette colour in turn, then finish the list", StepKind.LIST
    ),
    CalibrationStep("brush_tool", "Point at the brush tool", StepKind.SINGLE),
    CalibrationStep("fill_tool", "Point at the fill tool", StepKind.SINGLE),
    CalibrationStep(
        "brushes",
        "Point at each brush size control, thinnest first, then finish the list",
        StepKind.LIST,
    ),
)


class CalibrationSurface(Protocol):
    """Everything the wizard needs from the outside world."""

    def cursor(self) -> tuple[int, int]:
        """Return the current cursor position in physical screen pixels."""
        ...

    def pixel(self, x: int, y: int) -> Rgb:
        """Return the colour of one physical screen pixel."""
        ...

    def park(self, x: int, y: int) -> None:
        """Move the cursor somewhere harmless without clicking."""
        ...

    def click(self, x: int, y: int) -> None:
        """Click once at a physical screen pixel."""
        ...

    def drag(self, points: Sequence[tuple[int, int]]) -> None:
        """Draw one pen-down polyline through the given screen pixels."""
        ...

    def wait_key(self) -> int:
        """Block until the user presses one of the wizard's hotkeys."""
        ...


@dataclass(frozen=True, slots=True)
class CalibrationRequest:
    """What the caller knows before the wizard starts."""

    name: str
    screen: ScreenRect
    dpi_scale: float
    measure_brushes: bool


def canvas_from_corners(first: tuple[int, int], second: tuple[int, int]) -> ScreenRect:
    """Build the canvas rectangle from two captured corners, in any order.

    The bottom-right corner is inclusive, because that is the pixel the user
    pointed at, so the rectangle is one pixel wider and taller than the
    difference between the two captures.
    """
    left, right = sorted((first[0], second[0]))
    top, bottom = sorted((first[1], second[1]))
    width = right - left + 1
    height = bottom - top + 1
    if width < MIN_CANVAS_SIDE or height < MIN_CANVAS_SIDE:
        msg = (
            f"the two corners are only {width}x{height} apart, which is not a canvas; "
            "capture opposite corners"
        )
        raise ProfileError(msg)
    return ScreenRect(left=left, top=top, width=width, height=height)


def color_distance(first: Rgb, second: Rgb) -> float:
    """Return the plain RGB distance between two colours.

    Perceptual distance is the right tool for matching a palette and the wrong
    one here: this measures how far a pixel is from a known background, where
    what matters is how much ink covers it, not how the difference looks.
    """
    return math.dist(first, second)


def measure_ink_width(column: Sequence[Rgb], background: Rgb) -> float:
    """Return the width of the painted band in a column of pixels.

    Zero means the test stroke did not land, which the caller must treat as a
    failed measurement rather than as a zero-width brush.
    """
    distances = [color_distance(pixel, background) for pixel in column]
    peak = max(distances, default=0.0)
    if peak < _MIN_INK_CONTRAST:
        return 0.0
    threshold = peak * _HALF_MAXIMUM
    centre = distances.index(peak)
    first = centre
    while first > 0 and distances[first - 1] >= threshold:
        first -= 1
    last = centre
    while last + 1 < len(distances) and distances[last + 1] >= threshold:
        last += 1
    return float(last - first + 1)


def fallback_brush_widths(count: int) -> tuple[float, ...]:
    """Return plausible brush widths for a user who declined the measurement."""
    return tuple(_FALLBACK_BASE_WIDTH * _FALLBACK_WIDTH_RATIO**index for index in range(count))


def _capture_single(surface: CalibrationSurface) -> tuple[int, int]:
    while True:
        key = surface.wait_key()
        if key == ABORT_KEY:
            msg = "calibration aborted by the user"
            raise AbortedError(msg)
        if key == CAPTURE_KEY:
            return surface.cursor()


def _capture_list(
    surface: CalibrationSurface, announce: Callable[[str], None]
) -> list[tuple[int, int]]:
    captured: list[tuple[int, int]] = []
    while True:
        key = surface.wait_key()
        if key == ABORT_KEY:
            msg = "calibration aborted by the user"
            raise AbortedError(msg)
        if key == CAPTURE_KEY:
            captured.append(surface.cursor())
            announce(f"captured {len(captured)}")
        elif key == NEXT_KEY and captured:
            return captured


def _read_palette(
    surface: CalibrationSurface, positions: Sequence[tuple[int, int]]
) -> tuple[Swatch, ...]:
    return tuple(Swatch(x=x, y=y, color=surface.pixel(x, y)) for x, y in positions)


def _test_stroke_rows(canvas: ScreenRect, count: int) -> list[int]:
    spacing = canvas.height / (count + 1)
    return [canvas.top + int(spacing * (index + 1)) for index in range(count)]


def read_background(surface: CalibrationSurface, canvas: ScreenRect) -> Rgb:
    """Sample the blank canvas colour, near the corner and far from the cursor."""
    return surface.pixel(
        canvas.left + _BACKGROUND_SAMPLE_INSET, canvas.top + _BACKGROUND_SAMPLE_INSET
    )


def measure_brush_widths(
    surface: CalibrationSurface,
    canvas: ScreenRect,
    brush_tool: Control,
    ink: Swatch,
    controls: Sequence[tuple[int, int]],
) -> tuple[float, ...]:
    """Draw one test stroke per brush size and measure what each one paints.

    This writes on the canvas. The caller asks the user first and tells them to
    clear the canvas afterwards.
    """
    background = read_background(surface, canvas)
    centre_x = canvas.left + canvas.width // 2
    half_length = max(2, int(canvas.width * _TEST_STROKE_FRACTION / 2))
    widths: list[float] = []
    for row, control in zip(_test_stroke_rows(canvas, len(controls)), controls, strict=True):
        surface.click(brush_tool.x, brush_tool.y)
        surface.click(control[0], control[1])
        surface.click(ink.x, ink.y)
        surface.drag([(centre_x - half_length, row), (centre_x + half_length, row)])
        span = min(_SCAN_HALF_HEIGHT, canvas.height // (2 * len(controls)) or 1)
        column = [
            surface.pixel(centre_x, y)
            for y in range(max(canvas.top, row - span), min(canvas.bottom, row + span + 1))
        ]
        widths.append(measure_ink_width(column, background))
    return tuple(widths)


def measure_costs(surface: CalibrationSurface, profile: Profile) -> CostModel:
    """Time the primitives a plan is built from, on the machine that will run it.

    The run is deliberately short and deliberately harmless: it moves the
    cursor over the canvas and clicks palette swatches and tool buttons, which
    change the selected colour and tool and nothing else. It never presses on
    the canvas, so nothing is drawn.

    Two move distances are timed rather than one, because the two parts of the
    cost have to be separated: the fixed price of sending an event, and
    whatever extra a long jump costs. On synthetic input the second is usually
    close to nothing, and the estimate should say so rather than assume it.
    """
    canvas = profile.canvas
    near = (canvas.left + canvas.width // 2, canvas.top + canvas.height // 2)
    far = (canvas.left + _COST_SAMPLE_INSET, canvas.top + _COST_SAMPLE_INSET)
    short_hop = ((near[0] + _COST_SHORT_HOP, near[1]), near)
    long_hop = (far, near)

    short_seconds = _time_moves(surface, short_hop)
    long_seconds = _time_moves(surface, long_hop)
    short_distance = math.dist(*short_hop)
    long_distance = math.dist(*long_hop)
    per_pixel = max(0.0, (long_seconds - short_seconds) / max(1.0, long_distance - short_distance))
    per_move = max(_MIN_COST_SECONDS, short_seconds - per_pixel * short_distance)

    swatch = profile.palette[0]
    per_click = max(_MIN_COST_SECONDS, _time_clicks(surface, [(swatch.x, swatch.y)]) - per_move)
    per_color = max(
        per_click,
        _time_clicks(surface, [(other.x, other.y) for other in profile.palette[:2]], park=near),
    )
    per_tool = max(
        per_click,
        _time_clicks(
            surface,
            [
                (profile.fill_tool.x, profile.fill_tool.y),
                (profile.brush_tool.x, profile.brush_tool.y),
            ],
            park=near,
        ),
    )
    return CostModel(
        seconds_per_move=per_move,
        seconds_per_pixel=per_pixel,
        seconds_per_click=per_click,
        seconds_per_color_switch=per_color,
        seconds_per_tool_switch=per_tool,
    )


def _time_moves(surface: CalibrationSurface, points: Sequence[tuple[int, int]]) -> float:
    start = time.perf_counter()
    for _ in range(_COST_SAMPLES):
        for point in points:
            surface.park(*point)
    return (time.perf_counter() - start) / (_COST_SAMPLES * len(points))


def _time_clicks(
    surface: CalibrationSurface,
    points: Sequence[tuple[int, int]],
    park: tuple[int, int] | None = None,
) -> float:
    start = time.perf_counter()
    for _ in range(_COST_SAMPLES):
        for point in points:
            surface.click(*point)
            if park is not None:
                surface.park(*park)
    return (time.perf_counter() - start) / (_COST_SAMPLES * len(points))


def _pick_ink(palette: Sequence[Swatch], background: Rgb) -> Swatch:
    return max(palette, key=lambda swatch: color_distance(swatch.color, background))


def _resolve_widths(measured: Sequence[float], count: int) -> tuple[tuple[float, ...], bool]:
    if any(width <= 0 for width in measured) or len(set(measured)) != len(measured):
        return fallback_brush_widths(count), False
    return tuple(measured), True


def calibrate(
    request: CalibrationRequest,
    surface: CalibrationSurface,
    announce: Callable[[str], None],
) -> Profile:
    """Run the wizard and return the finished profile.

    Raises :class:`penplan.input_win.AbortedError` if the user presses the abort
    key at any point, leaving nothing written.
    """
    captures: dict[str, list[tuple[int, int]]] = {}
    for step in SCRIPT:
        announce(step.prompt)
        if step.kind is StepKind.SINGLE:
            captures[step.key] = [_capture_single(surface)]
        else:
            captures[step.key] = _capture_list(surface, announce)

    canvas = canvas_from_corners(captures["canvas_top_left"][0], captures["canvas_bottom_right"][0])
    announce("Reading palette colours")
    surface.park(canvas.left + canvas.width // 2, canvas.top + canvas.height // 2)
    palette = _read_palette(surface, captures["palette"])
    # Sampled before anything is drawn, so this is the blank canvas.
    background = read_background(surface, canvas)
    brush_tool = Control(*captures["brush_tool"][0])
    fill_tool = Control(*captures["fill_tool"][0])
    controls = captures["brushes"]

    measured = False
    if request.measure_brushes:
        announce("Measuring brush widths, the canvas will need clearing afterwards")
        raw = measure_brush_widths(
            surface, canvas, brush_tool, _pick_ink(palette, background), controls
        )
        widths, measured = _resolve_widths(raw, len(controls))
    else:
        widths = fallback_brush_widths(len(controls))

    brushes = tuple(
        BrushControl(x=x, y=y, width=width, measured=measured)
        for (x, y), width in sorted(zip(controls, widths, strict=True), key=lambda pair: pair[1])
    )
    return Profile(
        name=request.name,
        canvas=canvas,
        screen=request.screen,
        background=background,
        palette=palette,
        brush_tool=brush_tool,
        fill_tool=fill_tool,
        brushes=brushes,
        dpi_scale=request.dpi_scale,
        cost=DEFAULT_COST_MODEL,
        created=timestamp(),
    )


class WindowsSurface:
    """The calibration surface backed by the real screen and the real mouse."""

    def __init__(self) -> None:
        enable_dpi_awareness()
        self.screen = virtual_screen()
        self._pointer = Pointer(self.screen)
        self._pixels = ScreenPixels()
        self._listener = HotkeyListener([ABORT_KEY, CAPTURE_KEY, NEXT_KEY])

    def __enter__(self) -> Self:
        """Register the wizard's hotkeys and open the screen device context."""
        self._listener.__enter__()
        self._pixels.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        """Release the hotkeys and the device context."""
        self._pixels.__exit__(*exc)
        self._listener.__exit__(*exc)

    def cursor(self) -> tuple[int, int]:
        """Return the current cursor position in physical screen pixels."""
        return cursor_position()

    def pixel(self, x: int, y: int) -> Rgb:
        """Return the colour of one physical screen pixel."""
        return self._pixels.at(x, y)

    def park(self, x: int, y: int) -> None:
        """Move the cursor somewhere harmless and let the page settle."""
        self._pointer.move_to(x, y)
        time.sleep(_HOVER_SETTLE_SECONDS)

    def click(self, x: int, y: int) -> None:
        """Click once at a physical screen pixel."""
        self._pointer.click(x, y)

    def drag(self, points: Sequence[tuple[int, int]]) -> None:
        """Draw one pen-down polyline through the given screen pixels."""
        if not points:
            return
        with self._pointer:
            self._pointer.move_to(*points[0])
            self._pointer.press()
            for start, end in itertools.pairwise(points):
                for x, y in _hops(start, end):
                    self._pointer.move_to(x, y)
                    time.sleep(_TEST_STROKE_HOP_SECONDS)

    def wait_key(self) -> int:
        """Block until the user presses one of the wizard's hotkeys."""
        while True:
            key = self._listener.wait(timeout=None)
            if key is not None:
                return key


def _hops(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    """Split a segment into short steps, ending exactly on the endpoint."""
    distance = math.dist(start, end)
    steps = max(1, math.ceil(distance / _TEST_STROKE_HOP_PIXELS))
    return [
        (
            round(start[0] + (end[0] - start[0]) * step / steps),
            round(start[1] + (end[1] - start[1]) * step / steps),
        )
        for step in range(1, steps + 1)
    ]
