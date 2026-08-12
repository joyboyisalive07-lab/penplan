"""Tests for the Windows input layer.

Only the parts that need no screen are covered here: the absolute-coordinate
arithmetic, the structure layout SendInput depends on, and the abort hotkey
lifecycle. Anything that moves the real mouse belongs in
``tools/input_selftest.py``, which is run by hand.
"""

from __future__ import annotations

import ctypes

import pytest

from penplan.input_win import (
    _ABSOLUTE_MAX,
    _ABSOLUTE_RANGE,
    VK_ESCAPE,
    VK_F8,
    VK_F9,
    AbortedError,
    AbortHotkey,
    HotkeyListener,
    HotkeyUnavailableError,
    _Input,
    _MouseInput,
    normalize_absolute,
)
from penplan.model import ScreenRect

# The layouts SendInput expects, on the two pointer widths CPython builds for.
POINTER_SIZE = ctypes.sizeof(ctypes.c_void_p)
EXPECTED_MOUSEINPUT_SIZE = 32 if POINTER_SIZE == 8 else 24
EXPECTED_INPUT_SIZE = 40 if POINTER_SIZE == 8 else 28

# Common physical resolutions plus the odd one that is not a multiple of 16.
SCREEN_WIDTHS = (1366, 1920, 2560, 3840)


def driver_position(absolute: int, extent: int) -> int:
    """Model the mouse driver mapping an absolute coordinate back to a pixel.

    The driver truncates, which is why :func:`normalize_absolute` aims at the
    middle of a pixel's slot instead of its leading edge.
    """
    return absolute * extent // _ABSOLUTE_RANGE


def test_input_structures_match_the_windows_layout() -> None:
    assert ctypes.sizeof(_MouseInput) == EXPECTED_MOUSEINPUT_SIZE
    assert ctypes.sizeof(_Input) == EXPECTED_INPUT_SIZE


@pytest.mark.parametrize("width", SCREEN_WIDTHS)
def test_every_pixel_round_trips_through_the_driver(width: int) -> None:
    screen = ScreenRect(left=0, top=0, width=width, height=1080)
    for x in range(width):
        absolute_x, _ = normalize_absolute(x, 0, screen)
        assert driver_position(absolute_x, width) == x


def test_every_row_round_trips_through_the_driver() -> None:
    screen = ScreenRect(left=0, top=0, width=1920, height=1080)
    for y in range(screen.height):
        _, absolute_y = normalize_absolute(0, y, screen)
        assert driver_position(absolute_y, screen.height) == y


def test_negative_origin_is_relative_to_the_virtual_screen() -> None:
    # A second monitor placed left of and above the primary one.
    screen = ScreenRect(left=-1920, top=-200, width=3840, height=1280)
    for x, y in ((-1920, -200), (-960, 0), (0, 540), (1919, 1079)):
        absolute_x, absolute_y = normalize_absolute(x, y, screen)
        assert driver_position(absolute_x, screen.width) == x - screen.left
        assert driver_position(absolute_y, screen.height) == y - screen.top


def test_corners_stay_inside_the_absolute_range() -> None:
    screen = ScreenRect(left=0, top=0, width=1920, height=1080)
    assert normalize_absolute(0, 0, screen) == (17, 30)
    assert normalize_absolute(1919, 1079, screen) == (65519, 65506)


def test_out_of_range_points_are_clamped_not_wrapped() -> None:
    screen = ScreenRect(left=0, top=0, width=1920, height=1080)
    assert normalize_absolute(-5000, -5000, screen) == (0, 0)
    assert normalize_absolute(9000, 9000, screen) == (_ABSOLUTE_MAX, _ABSOLUTE_MAX)


def test_abort_hotkey_registers_and_releases() -> None:
    try:
        with AbortHotkey() as hotkey:
            assert not hotkey.triggered
            hotkey.raise_if_triggered()
    except HotkeyUnavailableError as error:
        pytest.skip(f"no interactive window station: {error}")


def test_triggered_hotkey_raises_and_resets() -> None:
    # The press is injected rather than typed: a test that needed a real key
    # press would need a focused window and a human.
    listener = HotkeyListener([VK_ESCAPE])
    listener.record(VK_ESCAPE)
    assert listener.is_pressed(VK_ESCAPE)
    assert listener.wait(timeout=0) == VK_ESCAPE
    listener.clear()
    assert not listener.is_pressed(VK_ESCAPE)
    assert listener.wait(timeout=0) is None


def test_abort_hotkey_reports_a_recorded_press() -> None:
    hotkey = AbortHotkey()
    hotkey.trigger()
    assert hotkey.triggered
    with pytest.raises(AbortedError, match="aborted by the user"):
        hotkey.raise_if_triggered()
    hotkey.reset()
    hotkey.raise_if_triggered()


def test_listener_needs_at_least_one_key() -> None:
    with pytest.raises(ValueError, match="at least one key"):
        HotkeyListener([])


def test_listener_delivers_each_key_once_in_order() -> None:
    listener = HotkeyListener([VK_ESCAPE, VK_F8, VK_F9])
    for key in (VK_F8, VK_F8, VK_F9):
        listener.record(key)
    assert [listener.wait(timeout=0) for _ in range(3)] == [VK_F8, VK_F8, VK_F9]
    assert listener.is_pressed(VK_F8)
    assert not listener.is_pressed(VK_ESCAPE)
