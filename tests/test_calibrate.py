"""Tests for the calibration wizard.

The wizard is driven end to end by a fake surface: scripted key presses,
scripted cursor positions, a pixel map for the palette, and a canvas that
remembers the strokes drawn on it so brush measurement can be exercised without
a browser.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from penplan.budget import picker_actions
from penplan.calibrate import (
    ABORT_KEY,
    CAPTURE_KEY,
    NEXT_KEY,
    PACING_LADDER,
    PICKER_TEST_COLOR,
    SCRIPT,
    CalibrationRequest,
    CalibrationStep,
    StepKind,
    apply_color,
    calibrate,
    canvas_from_corners,
    fallback_brush_widths,
    measure_ink_width,
    measure_pacing,
    verify_against_screen,
    zigzag,
)
from penplan.input_win import AbortedError
from penplan.model import DEFAULT_COST_MODEL, DEFAULT_PACING, ActionKind, Rgb, ScreenRect
from penplan.profile import BrushControl, ColorPicker, Control, Profile, ProfileError, Swatch

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

PICKER_OPEN = (20, 200)
PICKER_RED, PICKER_GREEN, PICKER_BLUE = (20, 230), (50, 230), (80, 230)
PICKER_PREVIEW = (110, 230)
PICKER_FIELDS = {PICKER_RED: 0, PICKER_GREEN: 1, PICKER_BLUE: 2}


class FakeSurface:
    """A scripted stand-in for the screen, the mouse and the hotkeys."""

    def __init__(
        self,
        events: list[tuple[int, tuple[int, int]]],
        *,
        paints: dict[tuple[int, int], int] | None = None,
        keeps_up: bool = True,
    ) -> None:
        self._keeps_up = keeps_up
        self._events = list(events)
        self._cursor = (0, 0)
        self._paints = paints or {}
        self._selected_width = 0
        self._bands: list[tuple[int, int, int, int]] = []
        self._marks: set[tuple[int, int]] = set()
        self.paces: list[float] = []
        self.clicks: list[tuple[int, int]] = []
        self.moves: list[tuple[int, int]] = []
        self.taps: list[tuple[int, int]] = []
        self.typed: list[str] = []
        self.chords: list[tuple[int, ...]] = []
        self._focus: int | None = None
        self._fields: dict[int, str] = {}
        self.parked: tuple[int, int] | None = None
        self.drags: list[list[tuple[int, int]]] = []

    def cursor(self) -> tuple[int, int]:
        return self._cursor

    def pixel(self, x: int, y: int) -> Rgb:
        if (x, y) == PICKER_PREVIEW:
            channels = [int(self._fields.get(index, "0") or 0) for index in range(3)]
            return (channels[0], channels[1], channels[2])
        if (x, y) in SWATCHES:
            return SWATCHES[(x, y)]
        if (x, y) in self._marks:
            return INK
        for left, right, row, width in self._bands:
            if left <= x <= right and abs(y - row) <= width // 2:
                return INK
        return BACKGROUND

    def park(self, x: int, y: int) -> None:
        self.parked = (x, y)

    def move(self, x: int, y: int) -> None:
        self.moves.append((x, y))

    def tap(self, x: int, y: int) -> None:
        self.taps.append((x, y))

    def type_text(self, text: str) -> None:
        self.typed.append(text)
        if self._focus is not None:
            self._fields[self._focus] = text

    def chord(self, keys: tuple[int, ...]) -> None:
        self.chords.append(tuple(keys))

    def click(self, x: int, y: int) -> None:
        self.clicks.append((x, y))
        if (x, y) in self._paints:
            self._selected_width = self._paints[(x, y)]
        self._focus = PICKER_FIELDS.get((x, y))

    def drag(self, points: list[tuple[int, int]], seconds_between: float) -> None:
        self.drags.append(list(points))
        self.paces.append(seconds_between)
        rows = {point[1] for point in points}
        if len(rows) > 1:
            # A zigzag. A canvas that keeps up receives every corner; one that
            # does not receives the ends and draws straight between them.
            if self._keeps_up:
                self._marks.update(points)
            return
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


def request(*, measure: bool = False) -> CalibrationRequest:
    return CalibrationRequest(name="fake", screen=SCREEN, dpi_scale=1.0, measure_by_drawing=measure)


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
    assert surface.parked is not None


def test_calibration_never_clicks_inside_the_canvas() -> None:
    # A click inside the canvas would leave a mark on the user's drawing. Moves
    # over it are fine, and are how the palette is read without a hover state.
    surface = FakeSurface(full_script())
    result = calibrate(request(), surface, silent)
    for x, y in surface.clicks + surface.taps:
        assert not result.canvas.contains(x, y)


def test_timing_measures_bare_moves_not_settled_ones() -> None:
    # The parked move waits for hover states to settle, on purpose. Timing that
    # instead of a bare move reported 150 ms per mouse move on a real canvas,
    # which would have made every estimate five times too long.
    surface = FakeSurface(full_script())
    calibrate(request(), surface, silent)
    assert surface.moves
    assert len(surface.moves) > len(surface.clicks)


def test_switching_costs_follow_from_the_primitives() -> None:
    surface = FakeSurface(full_script())
    cost = calibrate(request(), surface, silent).cost
    settle = DEFAULT_PACING.settle_seconds + DEFAULT_PACING.hold_seconds
    expected = 2 * cost.seconds_per_move + cost.seconds_per_click + settle
    assert cost.seconds_per_color_switch == pytest.approx(expected)
    # A tool change also loses the brush size, so it costs the trip twice.
    assert cost.seconds_per_tool_switch == pytest.approx(2 * expected)


def test_the_wizard_always_times_the_mouse() -> None:
    # The cost model decides every estimate, so it is measured on the machine
    # that will draw rather than carried over from the defaults.
    surface = FakeSurface(full_script())
    result = calibrate(request(), surface, silent)
    assert result.cost != DEFAULT_COST_MODEL
    for name in type(result.cost).__slots__:
        assert getattr(result.cost, name) >= 0


def test_timing_the_mouse_draws_nothing() -> None:
    surface = FakeSurface(full_script())
    calibrate(request(), surface, silent)
    assert surface.drags == []


def test_wizard_records_the_blank_canvas_colour() -> None:
    surface = FakeSurface(full_script())
    assert calibrate(request(), surface, silent).background == BACKGROUND


def test_wizard_measures_the_pace_the_canvas_keeps_up_with() -> None:
    surface = FakeSurface(full_script(), paints=BRUSH_PAINTED_WIDTHS)
    result = calibrate(request(measure=True), surface, silent)
    assert result.pacing.point_seconds == PACING_LADDER[0]


def test_a_canvas_that_never_keeps_up_gets_the_slowest_pace() -> None:
    canvas = ScreenRect(left=400, top=200, width=800, height=600)
    ink = Swatch(x=20, y=100, color=(0, 0, 0))
    surface = FakeSurface([], paints={}, keeps_up=False)
    assert measure_pacing(surface, canvas, ink).point_seconds == PACING_LADDER[-1]


def test_the_zigzag_has_corners_a_straight_line_would_miss() -> None:
    corners = zigzag(100, 200)
    assert len({y for _, y in corners}) == 2
    assert len(corners) >= 6


def test_wizard_measures_brush_widths_when_asked() -> None:
    surface = FakeSurface(full_script(), paints=BRUSH_PAINTED_WIDTHS)
    result = calibrate(request(measure=True), surface, silent)
    assert all(brush.measured for brush in result.brushes)
    assert result.brush_widths == (3.0, 9.0, 21.0)
    # One test stroke per brush size, and the zigzag that measures the pace.
    assert len(surface.drags) == len(BRUSH_CONTROLS) + 1


def test_measured_widths_keep_each_control_with_its_own_width() -> None:
    surface = FakeSurface(full_script(), paints=BRUSH_PAINTED_WIDTHS)
    result = calibrate(request(measure=True), surface, silent)
    positions = {(brush.x, brush.y): brush.width for brush in result.brushes}
    assert positions == {
        control: float(BRUSH_PAINTED_WIDTHS[control]) for control in BRUSH_CONTROLS
    }


def test_failed_measurement_falls_back_instead_of_inventing_a_width() -> None:
    # No control paints anything, which is what a mis-captured brush control
    # looks like from here.
    surface = FakeSurface(full_script(), paints={})
    result = calibrate(request(measure=True), surface, silent)
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


def profile_for_verification() -> Profile:
    """Return a small profile whose palette sits outside its canvas."""
    return Profile(
        name="fake",
        canvas=ScreenRect(left=400, top=200, width=800, height=600),
        screen=SCREEN,
        background=BACKGROUND,
        palette=(
            Swatch(x=20, y=100, color=(0, 0, 0)),
            Swatch(x=20, y=130, color=(255, 255, 255)),
            Swatch(x=20, y=160, color=(220, 40, 60)),
        ),
        brush_tool=Control(x=20, y=40),
        fill_tool=Control(x=20, y=70),
        brushes=(BrushControl(x=60, y=40, width=1.0, measured=True),),
        dpi_scale=1.0,
        cost=DEFAULT_COST_MODEL,
        pacing=DEFAULT_PACING,
        created="2026-08-12T00:00:00+00:00",
    )


def test_a_matching_screen_passes_verification() -> None:
    profile = profile_for_verification()
    colours = {(swatch.x, swatch.y): swatch.color for swatch in profile.palette}
    result = verify_against_screen(profile, lambda x, y: colours[(x, y)], SCREEN)
    assert result.ok
    assert result.complaints == ()


def test_a_moved_window_is_caught_before_anything_is_drawn() -> None:
    # What a browser tab strip looks like where a palette used to be.
    profile = profile_for_verification()
    result = verify_against_screen(profile, lambda _x, _y: (32, 34, 38), SCREEN)
    assert not result.ok
    assert "palette colours are not where the profile says" in result.complaints[0]


def test_a_single_shifted_swatch_is_enough_to_refuse() -> None:
    profile = profile_for_verification()
    colours = {(swatch.x, swatch.y): swatch.color for swatch in profile.palette}
    colours[(20, 160)] = (30, 200, 90)
    result = verify_against_screen(profile, lambda x, y: colours[(x, y)], SCREEN)
    assert not result.ok
    assert "swatch 3" in result.complaints[0]


def test_a_swatch_that_only_drifted_a_little_still_passes() -> None:
    # Scaling and subpixel rendering move a flat colour by a unit or two, and
    # that is not a moved window.
    profile = profile_for_verification()
    colours = {
        (swatch.x, swatch.y): tuple(min(255, channel + 2) for channel in swatch.color)
        for swatch in profile.palette
    }
    result = verify_against_screen(profile, lambda x, y: colours[(x, y)], SCREEN)
    assert result.ok


def test_a_canvas_off_this_screen_is_refused() -> None:
    profile = replace(
        profile_for_verification(), canvas=ScreenRect(left=3000, top=200, width=800, height=600)
    )
    colours = {(swatch.x, swatch.y): swatch.color for swatch in profile.palette}
    result = verify_against_screen(profile, lambda x, y: colours[(x, y)], SCREEN)
    assert not result.ok
    assert "not on this screen" in result.complaints[0]


def test_a_control_off_this_screen_is_refused() -> None:
    profile = replace(profile_for_verification(), brush_tool=Control(x=4000, y=10))
    colours = {(swatch.x, swatch.y): swatch.color for swatch in profile.palette}
    result = verify_against_screen(profile, lambda x, y: colours[(x, y)], SCREEN)
    assert not result.ok
    assert "brush tool at 4000,10 is not on this screen" in result.complaints[0]


def test_applying_a_colour_opens_the_picker_it_was_given_closed() -> None:
    # Calibration binds a picker that is already open; execution starts from a
    # closed one, so the same routine has to open it first.
    surface = FakeSurface([])
    picker = ColorPicker(
        open=Control(x=10, y=900),
        red=Control(x=10, y=940),
        green=Control(x=40, y=940),
        blue=Control(x=70, y=940),
        preview=Control(x=10, y=900),
    )
    apply_color(surface, picker, (1, 2, 3))
    assert surface.clicks[0] == (picker.open.x, picker.open.y)
    assert surface.clicks[-1] == (picker.open.x, picker.open.y)


def picker_script() -> list[tuple[int, tuple[int, int]]]:
    return [
        *full_script(),
        (CAPTURE_KEY, PICKER_OPEN),
        (CAPTURE_KEY, PICKER_RED),
        (CAPTURE_KEY, PICKER_GREEN),
        (CAPTURE_KEY, PICKER_BLUE),
        (CAPTURE_KEY, PICKER_PREVIEW),
    ]


def test_a_picker_is_captured_and_proved_to_work() -> None:
    surface = FakeSurface(picker_script())
    request = CalibrationRequest(
        name="fake", screen=SCREEN, dpi_scale=1.0, measure_by_drawing=False, bind_picker=True
    )
    result = calibrate(request, surface, silent)
    assert result.picker is not None
    assert (result.picker.red.x, result.picker.red.y) == PICKER_RED
    # The wizard typed the test colour in and read it back off the screen.
    assert surface.typed[-3:] == [str(value) for value in PICKER_TEST_COLOR]


def test_a_picker_that_does_not_take_is_refused() -> None:
    class Deaf(FakeSurface):
        def type_text(self, text: str) -> None:
            self.typed.append(text)

    surface = Deaf(picker_script())
    request = CalibrationRequest(
        name="fake", screen=SCREEN, dpi_scale=1.0, measure_by_drawing=False, bind_picker=True
    )
    with pytest.raises(ProfileError, match="did not take"):
        calibrate(request, surface, silent)


def test_a_profile_without_a_picker_still_calibrates() -> None:
    assert calibrate(request(), FakeSurface(full_script()), silent).picker is None


def test_the_typed_sequence_matches_what_execution_will_send() -> None:
    # Calibration proves the picker works by driving it; execution drives it
    # from a schedule. If the two ever disagree, the proof proves nothing.
    surface = FakeSurface(picker_script())
    request = CalibrationRequest(
        name="fake", screen=SCREEN, dpi_scale=1.0, measure_by_drawing=False, bind_picker=True
    )
    profile = calibrate(request, surface, silent)
    assert profile.picker is not None
    actions = picker_actions(profile.picker, DEFAULT_PACING, PICKER_TEST_COLOR)
    clicked = [(action.x, action.y) for action in actions if action.kind is ActionKind.MOVE]
    typed = [action.text for action in actions if action.kind is ActionKind.TYPE]
    assert typed == [str(value) for value in PICKER_TEST_COLOR]
    assert clicked[0] == PICKER_OPEN
    assert clicked[-1] == PICKER_OPEN
    for field in (PICKER_RED, PICKER_GREEN, PICKER_BLUE):
        assert field in clicked
