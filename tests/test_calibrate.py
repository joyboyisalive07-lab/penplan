"""Tests for the calibration wizard.

The wizard is driven end to end by a fake surface: scripted key presses,
scripted cursor positions, a pixel map for the palette, and a canvas that
remembers the strokes drawn on it so brush measurement can be exercised without
a browser.
"""

from __future__ import annotations

import pytest

from penplan.calibrate import (
    ABORT_KEY,
    CAPTURE_KEY,
    NEXT_KEY,
    SCRIPT,
    CalibrationRequest,
    CalibrationStep,
    StepKind,
    calibrate,
    canvas_from_corners,
    fallback_brush_widths,
    measure_ink_width,
)
from penplan.input_win import AbortedError
from penplan.model import Rgb, ScreenRect
from penplan.profile import ProfileError

SCREEN = ScreenRect(left=0, top=0, width=1920, height=1080)
BACKGROUND: Rgb = (255, 255, 255)
INK: Rgb = (0, 0, 0)

CANVAS_TOP_LEFT = (400, 200)
CANVAS_BOTTOM_RIGHT = (1199, 799)
SWATCHES = {(20, 100): (0, 0, 0), (20, 130): (255, 255, 255), (20, 160): (220, 40, 60)}
BRUSH_TOOL = (20, 40)
FILL_TOOL = (20, 70)
BRUSH_CONTROLS = ((60, 40), (60, 70), (60, 100))
BRUSH_PAINTED_WIDTHS = {(60, 40): 3, (60, 70): 9, (60, 100): 21}


class FakeSurface:
    """A scripted stand-in for the screen, the mouse and the hotkeys."""

    def __init__(
        self,
        events: list[tuple[int, tuple[int, int]]],
        *,
        paints: dict[tuple[int, int], int] | None = None,
    ) -> None:
        self._events = list(events)
        self._cursor = (0, 0)
        self._paints = paints or {}
        self._selected_width = 0
        self._bands: list[tuple[int, int, int, int]] = []
        self.clicks: list[tuple[int, int]] = []
        self.parked: tuple[int, int] | None = None
        self.drags: list[list[tuple[int, int]]] = []

    def cursor(self) -> tuple[int, int]:
        return self._cursor

    def pixel(self, x: int, y: int) -> Rgb:
        if (x, y) in SWATCHES:
            return SWATCHES[(x, y)]
        for left, right, row, width in self._bands:
            if left <= x <= right and abs(y - row) <= width // 2:
                return INK
        return BACKGROUND

    def park(self, x: int, y: int) -> None:
        self.parked = (x, y)

    def click(self, x: int, y: int) -> None:
        self.clicks.append((x, y))
        if (x, y) in self._paints:
            self._selected_width = self._paints[(x, y)]

    def drag(self, points: list[tuple[int, int]]) -> None:
        self.drags.append(list(points))
        left = min(point[0] for point in points)
        right = max(point[0] for point in points)
        self._bands.append((left, right, points[0][1], self._selected_width))

    def wait_key(self) -> int:
        key, position = self._events.pop(0)
        self._cursor = position
        return key


def full_script() -> list[tuple[int, tuple[int, int]]]:
    events: list[tuple[int, tuple[int, int]]] = [
        (CAPTURE_KEY, CANVAS_TOP_LEFT),
        (CAPTURE_KEY, CANVAS_BOTTOM_RIGHT),
    ]
    events += [(CAPTURE_KEY, position) for position in SWATCHES]
    events.append((NEXT_KEY, (0, 0)))
    events.append((CAPTURE_KEY, BRUSH_TOOL))
    events.append((CAPTURE_KEY, FILL_TOOL))
    events += [(CAPTURE_KEY, position) for position in BRUSH_CONTROLS]
    events.append((NEXT_KEY, (0, 0)))
    return events


def request(*, measure_brushes: bool = False) -> CalibrationRequest:
    return CalibrationRequest(
        name="fake", screen=SCREEN, dpi_scale=1.0, measure_brushes=measure_brushes
    )


def silent(_message: str) -> None:
    return


def test_script_collects_everything_a_profile_needs() -> None:
    keys = [step.key for step in SCRIPT]
    assert keys == [
        "canvas_top_left",
        "canvas_bottom_right",
        "palette",
        "brush_tool",
        "fill_tool",
        "brushes",
    ]
    assert [step.kind for step in SCRIPT].count(StepKind.LIST) == 2


def test_wizard_builds_a_profile_from_the_captures() -> None:
    surface = FakeSurface(full_script())
    result = calibrate(request(), surface, silent)
    assert result.canvas == ScreenRect(left=400, top=200, width=800, height=600)
    assert result.colors == tuple(SWATCHES.values())
    assert [(swatch.x, swatch.y) for swatch in result.palette] == list(SWATCHES)
    assert (result.brush_tool.x, result.brush_tool.y) == BRUSH_TOOL
    assert (result.fill_tool.x, result.fill_tool.y) == FILL_TOOL
    assert len(result.brushes) == len(BRUSH_CONTROLS)
    assert not any(brush.measured for brush in result.brushes)
    assert result.brush_widths == fallback_brush_widths(len(BRUSH_CONTROLS))


def test_wizard_reads_palette_colours_with_the_cursor_parked_away() -> None:
    surface = FakeSurface(full_script())
    calibrate(request(), surface, silent)
    assert surface.parked == (800, 500)
    assert surface.clicks == []


def test_wizard_measures_brush_widths_when_asked() -> None:
    surface = FakeSurface(full_script(), paints=BRUSH_PAINTED_WIDTHS)
    result = calibrate(request(measure_brushes=True), surface, silent)
    assert all(brush.measured for brush in result.brushes)
    assert result.brush_widths == (3.0, 9.0, 21.0)
    assert len(surface.drags) == len(BRUSH_CONTROLS)


def test_measured_widths_keep_each_control_with_its_own_width() -> None:
    surface = FakeSurface(full_script(), paints=BRUSH_PAINTED_WIDTHS)
    result = calibrate(request(measure_brushes=True), surface, silent)
    positions = {(brush.x, brush.y): brush.width for brush in result.brushes}
    assert positions == {
        control: float(BRUSH_PAINTED_WIDTHS[control]) for control in BRUSH_CONTROLS
    }


def test_failed_measurement_falls_back_instead_of_inventing_a_width() -> None:
    # No control paints anything, which is what a mis-captured brush control
    # looks like from here.
    surface = FakeSurface(full_script(), paints={})
    result = calibrate(request(measure_brushes=True), surface, silent)
    assert not any(brush.measured for brush in result.brushes)
    assert result.brush_widths == fallback_brush_widths(len(BRUSH_CONTROLS))


@pytest.mark.parametrize("stop_after", range(9))
def test_abort_at_any_point_raises_and_writes_nothing(stop_after: int) -> None:
    events = full_script()[:stop_after]
    events.append((ABORT_KEY, (0, 0)))
    surface = FakeSurface(events)
    with pytest.raises(AbortedError, match="aborted by the user"):
        calibrate(request(), surface, silent)


def test_corners_may_be_captured_in_either_order() -> None:
    forward = canvas_from_corners((100, 200), (399, 499))
    backward = canvas_from_corners((399, 499), (100, 200))
    assert forward == backward
    assert forward == ScreenRect(left=100, top=200, width=300, height=300)


def test_corners_too_close_together_are_refused() -> None:
    with pytest.raises(ProfileError, match="not a canvas"):
        canvas_from_corners((100, 200), (105, 260))


def test_ink_width_of_a_clean_band() -> None:
    column = [BACKGROUND] * 10 + [INK] * 5 + [BACKGROUND] * 10
    assert measure_ink_width(column, BACKGROUND) == 5.0


def test_ink_width_uses_half_coverage_as_the_edge() -> None:
    # An anti-aliased band: two faint edge pixels at a quarter coverage that
    # must not be counted, and one at three quarters that must be.
    faint = (200, 200, 200)
    strong = (60, 60, 60)
    column = [BACKGROUND, faint, strong, INK, INK, INK, strong, faint, BACKGROUND]
    assert measure_ink_width(column, BACKGROUND) == 5.0


def test_ink_width_is_zero_when_nothing_was_drawn() -> None:
    assert measure_ink_width([BACKGROUND] * 20, BACKGROUND) == 0.0
    assert measure_ink_width([], BACKGROUND) == 0.0


def test_ink_width_ignores_a_second_band_in_the_scan() -> None:
    column = [INK] * 3 + [BACKGROUND] * 8 + [INK] * 9
    assert measure_ink_width(column, BACKGROUND) == 3.0


def test_fallback_widths_increase_and_start_thin() -> None:
    widths = fallback_brush_widths(4)
    assert len(widths) == 4
    assert widths == tuple(sorted(widths))
    assert widths[0] < widths[-1]


def test_calibration_step_carries_its_prompt() -> None:
    step = CalibrationStep("thing", "Point at the thing", StepKind.SINGLE)
    assert step.prompt.startswith("Point at")
