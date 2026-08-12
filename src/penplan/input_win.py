"""The only module that touches ctypes and the Windows API.

Three capabilities live here: synthetic mouse input through ``SendInput``,
screen pixel reads through GDI, and the global abort hotkey through
``RegisterHotKey``. Everything else in the package is pure Python and can be
tested with no screen attached.

The abort hotkey is built before the executor on purpose. No plan may be sent
to the mouse unless the user can stop it, so a hotkey that fails to register is
a hard error here, never a warning.
"""

from __future__ import annotations

import ctypes
import functools
import threading
import time
from ctypes import wintypes
from typing import TYPE_CHECKING, Final, Self

from penplan.model import Rgb, ScreenRect

if TYPE_CHECKING:
    from collections.abc import Sequence

_user32: Final = ctypes.WinDLL("user32", use_last_error=True)
_gdi32: Final = ctypes.WinDLL("gdi32", use_last_error=True)
_kernel32: Final = ctypes.WinDLL("kernel32", use_last_error=True)

INPUT_MOUSE: Final = 0
MOUSEEVENTF_MOVE: Final = 0x0001
MOUSEEVENTF_LEFTDOWN: Final = 0x0002
MOUSEEVENTF_LEFTUP: Final = 0x0004
MOUSEEVENTF_ABSOLUTE: Final = 0x8000
MOUSEEVENTF_VIRTUALDESK: Final = 0x4000
# Without this flag Windows merges consecutive moves into one, which is exactly
# how a canvas ends up receiving a straight line instead of the curve drawn.
MOUSEEVENTF_MOVE_NOCOALESCE: Final = 0x2000

SM_XVIRTUALSCREEN: Final = 76
SM_YVIRTUALSCREEN: Final = 77
SM_CXVIRTUALSCREEN: Final = 78
SM_CYVIRTUALSCREEN: Final = 79

WM_QUIT: Final = 0x0012
WM_HOTKEY: Final = 0x0312
MOD_NOREPEAT: Final = 0x4000
VK_ESCAPE: Final = 0x1B

CLR_INVALID: Final = 0xFFFFFFFF
LOGPIXELSX: Final = 88
MONITOR_DEFAULTTONEAREST: Final = 2
MDT_EFFECTIVE_DPI: Final = 0

# SendInput normalises absolute coordinates over this range; the driver maps
# them back with a truncating divide by the same number.
_ABSOLUTE_RANGE: Final = 65536
_ABSOLUTE_MAX: Final = _ABSOLUTE_RANGE - 1
# Aim at the middle of the target pixel's slot rather than its leading edge, so
# the driver's truncation cannot land the cursor one pixel short.
_PIXEL_CENTRE: Final = 0.5

# The single hotkey identifier this process ever registers on its own thread.
_ABORT_HOTKEY_ID: Final = 1
# Windows 10 1703 and later; the value is the documented sentinel handle.
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2: Final = -4
_PROCESS_PER_MONITOR_DPI_AWARE: Final = 2
_BASE_DPI: Final = 96

# A browser button that receives a press and a release in the same millisecond
# sometimes drops the click, and a click sent the instant the cursor arrives
# can be attributed to the previous position. Both waits are conservative and
# only apply to discrete clicks, not to stroke pacing.
_MOVE_SETTLE_SECONDS: Final = 0.012
_CLICK_HOLD_SECONDS: Final = 0.020

# A message loop that has been told to quit returns immediately; waiting longer
# than this means the thread is wedged and joining it further is pointless.
_HOTKEY_SHUTDOWN_SECONDS: Final = 2.0


class InputError(RuntimeError):
    """A Windows input or GDI call failed."""


class HotkeyUnavailableError(InputError):
    """The abort hotkey could not be registered, so nothing may be executed."""


class AbortedError(InputError):
    """The user pressed the abort hotkey."""


class _MouseInput(ctypes.Structure):
    _fields_: Final = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", wintypes.WPARAM),
    )


class _Input(ctypes.Structure):
    # Only the mouse arm of the INPUT union is declared. MOUSEINPUT is the
    # largest arm, so the structure size still matches what SendInput expects.
    _fields_: Final = (("type", wintypes.DWORD), ("mi", _MouseInput))


_user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int)
_user32.SendInput.restype = wintypes.UINT
_user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
_user32.GetSystemMetrics.restype = ctypes.c_int
_user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
_user32.GetCursorPos.restype = wintypes.BOOL
_user32.GetDC.argtypes = (wintypes.HWND,)
_user32.GetDC.restype = wintypes.HDC
_user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
_user32.ReleaseDC.restype = ctypes.c_int
_user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
_user32.RegisterHotKey.restype = wintypes.BOOL
_user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
_user32.UnregisterHotKey.restype = wintypes.BOOL
_user32.GetMessageW.argtypes = (
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
)
_user32.GetMessageW.restype = ctypes.c_int
_user32.PostThreadMessageW.argtypes = (
    wintypes.DWORD,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)
_user32.PostThreadMessageW.restype = wintypes.BOOL
_user32.MonitorFromPoint.argtypes = (wintypes.POINT, wintypes.DWORD)
_user32.MonitorFromPoint.restype = wintypes.HMONITOR
_gdi32.GetPixel.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int)
_gdi32.GetPixel.restype = wintypes.COLORREF
_gdi32.GetDeviceCaps.argtypes = (wintypes.HDC, ctypes.c_int)
_gdi32.GetDeviceCaps.restype = ctypes.c_int
_kernel32.GetCurrentThreadId.argtypes = ()
_kernel32.GetCurrentThreadId.restype = wintypes.DWORD


@functools.cache
def enable_dpi_awareness() -> bool:
    """Opt the process into physical pixels, and report whether that worked.

    Without this the system lies to a scaled display in both directions: the
    cursor positions read back are logical, and GDI hands out a stretched copy
    of the screen, so every calibrated coordinate is off by the scale factor.
    Newest API first, with the two older ones as fallbacks for older Windows.
    """
    try:
        _user32.SetProcessDpiAwarenessContext.argtypes = (ctypes.c_ssize_t,)
        _user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
        if _user32.SetProcessDpiAwarenessContext(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
            return True
    except (AttributeError, OSError):
        pass
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        shcore.SetProcessDpiAwareness.argtypes = (ctypes.c_int,)
        shcore.SetProcessDpiAwareness.restype = ctypes.c_long
        if shcore.SetProcessDpiAwareness(_PROCESS_PER_MONITOR_DPI_AWARE) == 0:
            return True
    except (AttributeError, OSError):
        pass
    try:
        return bool(_user32.SetProcessDPIAware())
    except (AttributeError, OSError):
        return False


def virtual_screen() -> ScreenRect:
    """Return the bounding rectangle of every monitor, in physical pixels."""
    return ScreenRect(
        left=_user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        top=_user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        width=_user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        height=_user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    )


def dpi_scale_at(x: int, y: int) -> float:
    """Return the display scale factor of the monitor containing a screen point.

    A profile records the scale it was calibrated at so it can be replayed on a
    display set to a different one; see :mod:`penplan.profile` for the
    conversion, which is pure arithmetic and lives outside this module.
    """
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        shcore.GetDpiForMonitor.argtypes = (
            wintypes.HMONITOR,
            ctypes.c_int,
            ctypes.POINTER(wintypes.UINT),
            ctypes.POINTER(wintypes.UINT),
        )
        shcore.GetDpiForMonitor.restype = ctypes.c_long
        monitor = _user32.MonitorFromPoint(wintypes.POINT(x, y), MONITOR_DEFAULTTONEAREST)
        dpi_x = wintypes.UINT()
        dpi_y = wintypes.UINT()
        if (
            shcore.GetDpiForMonitor(
                monitor, MDT_EFFECTIVE_DPI, ctypes.byref(dpi_x), ctypes.byref(dpi_y)
            )
            == 0
            and dpi_x.value > 0
        ):
            return dpi_x.value / _BASE_DPI
    except (AttributeError, OSError):
        pass
    with ScreenPixels() as pixels:
        return _gdi32.GetDeviceCaps(pixels.handle, LOGPIXELSX) / _BASE_DPI


def cursor_position() -> tuple[int, int]:
    """Return the cursor position in physical screen pixels."""
    point = wintypes.POINT()
    if not _user32.GetCursorPos(ctypes.byref(point)):
        raise InputError(_last_error("GetCursorPos"))
    return point.x, point.y


class ScreenPixels:
    """A held device context for the whole desktop, for reading pixel colours.

    Calibration reads a handful of pixels and execution verification reads a
    few hundred, and acquiring a device context per pixel costs more than the
    read itself, so the context is opened once and reused.
    """

    def __init__(self) -> None:
        self._handle: wintypes.HDC | None = None

    @property
    def handle(self) -> wintypes.HDC:
        """Return the open device context, or fail if it has been released."""
        if self._handle is None:
            msg = "screen device context is not open"
            raise InputError(msg)
        return self._handle

    def __enter__(self) -> Self:
        """Acquire the desktop device context."""
        handle = _user32.GetDC(None)
        if not handle:
            raise InputError(_last_error("GetDC"))
        self._handle = handle
        return self

    def __exit__(self, *_exc: object) -> None:
        """Release the desktop device context."""
        if self._handle is not None:
            _user32.ReleaseDC(None, self._handle)
            self._handle = None

    def at(self, x: int, y: int) -> Rgb:
        """Return the colour of one physical screen pixel.

        Reads fail on pixels covered by hardware-accelerated video or by a
        window with a protected surface; those come back as a hard error rather
        than a plausible black, because calibrating against a wrong colour
        silently ruins every drawing made with the profile.
        """
        value = _gdi32.GetPixel(self.handle, x, y)
        if value == CLR_INVALID:
            msg = f"screen pixel at ({x}, {y}) could not be read"
            raise InputError(msg)
        return (value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF)


def get_pixel(x: int, y: int) -> Rgb:
    """Return the colour of one physical screen pixel."""
    with ScreenPixels() as pixels:
        return pixels.at(x, y)


def normalize_absolute(x: int, y: int, screen: ScreenRect) -> tuple[int, int]:
    """Map a physical screen pixel onto SendInput's absolute coordinate space."""
    dx = round((x - screen.left + _PIXEL_CENTRE) * _ABSOLUTE_RANGE / screen.width)
    dy = round((y - screen.top + _PIXEL_CENTRE) * _ABSOLUTE_RANGE / screen.height)
    return _clamp_absolute(dx), _clamp_absolute(dy)


def _clamp_absolute(value: int) -> int:
    return max(0, min(_ABSOLUTE_MAX, value))


def _last_error(call: str) -> str:
    code = ctypes.get_last_error()
    return f"{call} failed with Windows error {code}"


def _mouse_event(flags: int, dx: int = 0, dy: int = 0) -> _Input:
    return _Input(
        type=INPUT_MOUSE,
        mi=_MouseInput(dx=dx, dy=dy, mouseData=0, dwFlags=flags, time=0, dwExtraInfo=0),
    )


def _send(events: Sequence[_Input]) -> None:
    batch = (_Input * len(events))(*events)
    sent = _user32.SendInput(len(events), batch, ctypes.sizeof(_Input))
    if sent != len(events):
        # The usual cause is a foreground window running elevated: user
        # interface privilege isolation drops input from a lower process, and
        # it does so silently apart from this count.
        msg = f"{_last_error('SendInput')}; sent {sent} of {len(events)} events"
        raise InputError(msg)


class Pointer:
    """The left mouse button, with its state tracked so it can always be released.

    Used as a context manager, the button is guaranteed to come back up when
    the block exits, however it exits. That is what keeps an abort from leaving
    the user holding a dragging mouse they did not press.
    """

    def __init__(self, screen: ScreenRect | None = None) -> None:
        self._screen = screen if screen is not None else virtual_screen()
        self._is_down = False

    @property
    def is_down(self) -> bool:
        """Return whether the left button is currently held down."""
        return self._is_down

    def __enter__(self) -> Self:
        """Start a pointer session."""
        return self

    def __exit__(self, *_exc: object) -> None:
        """Release the button if this session left it down."""
        self.release()

    def move_to(self, x: int, y: int) -> None:
        """Move the cursor to a physical screen pixel."""
        dx, dy = normalize_absolute(x, y, self._screen)
        _send(
            [
                _mouse_event(
                    MOUSEEVENTF_MOVE
                    | MOUSEEVENTF_ABSOLUTE
                    | MOUSEEVENTF_VIRTUALDESK
                    | MOUSEEVENTF_MOVE_NOCOALESCE,
                    dx,
                    dy,
                )
            ]
        )

    def press(self) -> None:
        """Press the left button, unless it is already down."""
        if self._is_down:
            return
        _send([_mouse_event(MOUSEEVENTF_LEFTDOWN)])
        self._is_down = True

    def release(self) -> None:
        """Release the left button, unless it is already up."""
        if not self._is_down:
            return
        _send([_mouse_event(MOUSEEVENTF_LEFTUP)])
        self._is_down = False

    def click(self, x: int, y: int) -> None:
        """Move to a screen pixel and click it once."""
        self.move_to(x, y)
        time.sleep(_MOVE_SETTLE_SECONDS)
        self.press()
        time.sleep(_CLICK_HOLD_SECONDS)
        self.release()


class AbortHotkey:
    """A process-wide hotkey that the user can press to stop everything.

    The hotkey is registered on a dedicated thread with its own message loop,
    because ``RegisterHotKey`` delivers ``WM_HOTKEY`` to the thread that
    registered it, and the executor's thread is busy sending input. The
    executor checks :meth:`raise_if_triggered` between events, so an abort
    takes effect within one input event.

    Registration failure is raised, not swallowed: another application already
    holding the key would otherwise leave the user with no way out.
    """

    def __init__(self, virtual_key: int = VK_ESCAPE) -> None:
        self._virtual_key = virtual_key
        self._triggered = threading.Event()
        self._registered = threading.Event()
        self._error: str | None = None
        self._thread_id: int | None = None
        self._thread: threading.Thread | None = None

    @property
    def triggered(self) -> bool:
        """Return whether the hotkey has been pressed since the last reset."""
        return self._triggered.is_set()

    def raise_if_triggered(self) -> None:
        """Raise :class:`AbortedError` if the user has asked to stop."""
        if self._triggered.is_set():
            msg = "aborted by the user"
            raise AbortedError(msg)

    def reset(self) -> None:
        """Forget a previous press, so the same registration can be reused."""
        self._triggered.clear()

    def __enter__(self) -> Self:
        """Register the hotkey and start its message loop."""
        self._thread = threading.Thread(target=self._run, name="penplan-abort", daemon=True)
        self._thread.start()
        self._registered.wait()
        if self._error is not None:
            raise HotkeyUnavailableError(self._error)
        return self

    def __exit__(self, *_exc: object) -> None:
        """Stop the message loop and unregister the hotkey."""
        if self._thread_id is not None:
            _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=_HOTKEY_SHUTDOWN_SECONDS)
        self._thread = None
        self._thread_id = None

    def _run(self) -> None:
        self._thread_id = _kernel32.GetCurrentThreadId()
        if not _user32.RegisterHotKey(None, _ABORT_HOTKEY_ID, MOD_NOREPEAT, self._virtual_key):
            self._error = (
                f"{_last_error('RegisterHotKey')}; another application is holding the abort key"
            )
            self._registered.set()
            return
        self._registered.set()
        try:
            self._pump()
        finally:
            _user32.UnregisterHotKey(None, _ABORT_HOTKEY_ID)

    def _pump(self) -> None:
        message = wintypes.MSG()
        while True:
            result = _user32.GetMessageW(ctypes.byref(message), None, 0, 0)
            if result <= 0:
                return
            if message.message == WM_HOTKEY:
                self._triggered.set()
