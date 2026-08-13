"""Calibration profiles: everything the tool knows about one canvas.

A profile is the only reason this tool works on a site it has never seen. It
holds where the canvas is, what the palette colours actually are, where the
tools live, and how fast the machine executes input. It is pure data with no
Windows API behind it, so the planner and the tests can work with a profile on
a machine with no screen at all.

Coordinates in a profile are physical screen pixels at the display scale
recorded in ``dpi_scale``; :meth:`Profile.rescaled` converts a profile to a
different scale.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any, Final

from penplan.model import CostModel, Pacing, Point, Rgb, ScreenRect

PROFILE_FORMAT: Final = 1
"""Bumped whenever a stored profile stops being readable by the old loader."""

PROFILE_SUFFIX: Final = ".json"
BUNDLED_PROFILES_PACKAGE: Final = "penplan.profiles"

# Windows lays out at scaled sizes with half-way values rounded up, while
# Python rounds half to even, so 2 logical pixels at 125 per cent are 3 physical
# pixels to Windows and 2 to round(). Matching Windows is what keeps a profile
# usable after the display scale changes.
_ROUND_HALF_UP_OFFSET: Final = 0.5

# A canvas smaller than this is a mis-click on one of the two corners rather
# than a canvas, and every downstream division by its size would be nonsense.
MIN_CANVAS_SIDE: Final = 16

_MAX_CHANNEL: Final = 0xFF


class ProfileError(ValueError):
    """A profile is malformed, or was written by a version that is not readable."""


@dataclass(frozen=True, slots=True)
class Swatch:
    """A palette entry: where to click, and the colour that click selects."""

    x: int
    y: int
    color: Rgb


@dataclass(frozen=True, slots=True)
class Control:
    """A clickable control whose only property is its position."""

    x: int
    y: int


@dataclass(frozen=True, slots=True)
class BrushControl:
    """A brush-size control, with the width the brush actually paints.

    ``measured`` records whether the width came from a test stroke on the real
    canvas or from the fallback progression, because the renderer's fidelity
    depends on it and the user deserves to know which one they are looking at.
    """

    x: int
    y: int
    width: float
    measured: bool

    def __post_init__(self) -> None:
        """Reject a non-positive brush width, which no drawing could use."""
        if self.width <= 0:
            msg = f"brush width must be positive, got {self.width}"
            raise ProfileError(msg)


@dataclass(frozen=True, slots=True)
class ColorPicker:
    """Where to type a colour that the palette does not contain.

    Some canvases let a colour be entered as three numbers. That is the one
    part of a colour picker worth automating: a gradient has to be aimed at and
    checked, three numbers are exact. With this the planner is no longer limited
    to the swatches on offer, which is the difference between a photograph
    rendered in eighteen crayons and one rendered in its own colours.

    ``preview`` is where the chosen colour shows on screen, and it is what
    calibration reads back to prove the binding works.
    """

    open: Control
    red: Control
    green: Control
    blue: Control
    preview: Control


@dataclass(frozen=True, slots=True)
class Profile:
    """One calibrated canvas.

    ``background`` is the colour of the blank canvas, sampled during
    calibration. The planner treats any region that already matches it as
    already drawn, which is usually the largest single saving in a plan.
    """

    name: str
    canvas: ScreenRect
    screen: ScreenRect
    background: Rgb
    palette: tuple[Swatch, ...]
    brush_tool: Control
    fill_tool: Control
    brushes: tuple[BrushControl, ...]
    dpi_scale: float
    cost: CostModel
    pacing: Pacing
    created: str
    picker: ColorPicker | None = None

    def __post_init__(self) -> None:
        """Reject a profile the planner could not use."""
        if not self.name:
            msg = "a profile needs a name"
            raise ProfileError(msg)
        if self.canvas.width < MIN_CANVAS_SIDE or self.canvas.height < MIN_CANVAS_SIDE:
            msg = (
                f"canvas is {self.canvas.width}x{self.canvas.height}, "
                f"smaller than the {MIN_CANVAS_SIDE} pixel minimum"
            )
            raise ProfileError(msg)
        if not self.palette:
            msg = "a profile needs at least one palette colour"
            raise ProfileError(msg)
        if not self.brushes:
            msg = "a profile needs at least one brush size"
            raise ProfileError(msg)
        widths = [brush.width for brush in self.brushes]
        if widths != sorted(widths) or len(set(widths)) != len(widths):
            msg = f"brush widths must be strictly increasing, got {widths}"
            raise ProfileError(msg)
        if self.dpi_scale <= 0:
            msg = f"dpi scale must be positive, got {self.dpi_scale}"
            raise ProfileError(msg)
        # A control inside the canvas is not a control, it is a mis-calibration:
        # clicking it would draw on the picture instead of selecting anything.
        picker_controls = (
            ()
            if self.picker is None
            else (
                (self.picker.open.x, self.picker.open.y, "colour picker"),
                (self.picker.red.x, self.picker.red.y, "red field"),
                (self.picker.green.x, self.picker.green.y, "green field"),
                (self.picker.blue.x, self.picker.blue.y, "blue field"),
            )
        )
        for x, y, name in (
            (self.brush_tool.x, self.brush_tool.y, "brush tool"),
            (self.fill_tool.x, self.fill_tool.y, "fill tool"),
            *((brush.x, brush.y, "brush size control") for brush in self.brushes),
            *((swatch.x, swatch.y, "palette swatch") for swatch in self.palette),
            *picker_controls,
        ):
            if self.canvas.contains(x, y):
                msg = f"the {name} at {x},{y} is inside the canvas, which cannot be right"
                raise ProfileError(msg)

    @property
    def colors(self) -> tuple[Rgb, ...]:
        """Return the palette colours in swatch order."""
        return tuple(swatch.color for swatch in self.palette)

    @property
    def brush_widths(self) -> tuple[float, ...]:
        """Return the brush widths in canvas pixels, thinnest first."""
        return tuple(brush.width for brush in self.brushes)

    def canvas_to_screen(
        self, point: Point, raster_width: int, raster_height: int
    ) -> tuple[int, int]:
        """Map a plan coordinate to the screen pixel the mouse should visit.

        A plan is drawn on a raster that may be coarser than the canvas, so the
        mapping stretches the raster over the canvas rectangle and lands on the
        centre of the raster cell rather than its corner.
        """
        if raster_width <= 0 or raster_height <= 0:
            msg = f"raster size must be positive, got {raster_width}x{raster_height}"
            raise ProfileError(msg)
        x = self.canvas.left + int(
            (point.x + _ROUND_HALF_UP_OFFSET) * self.canvas.width / raster_width
        )
        y = self.canvas.top + int(
            (point.y + _ROUND_HALF_UP_OFFSET) * self.canvas.height / raster_height
        )
        return (
            min(x, self.canvas.right - 1),
            min(y, self.canvas.bottom - 1),
        )

    def rescaled(self, dpi_scale: float) -> Profile:
        """Return the same profile as it would be at a different display scale.

        Windows lays a page out proportionally to the scale factor, so every
        recorded position moves with it. This is an approximation: a browser
        reflows text at the new size and a canvas whose size depends on the
        surrounding layout can end up a pixel or two off. It is close enough to
        aim the recalibration, not close enough to skip it, and the interface
        says so when it applies this.
        """
        if dpi_scale <= 0:
            msg = f"dpi scale must be positive, got {dpi_scale}"
            raise ProfileError(msg)
        factor = dpi_scale / self.dpi_scale
        return replace(
            self,
            canvas=_scale_rect(self.canvas, factor),
            screen=_scale_rect(self.screen, factor),
            palette=tuple(
                replace(swatch, x=scale_pixel(swatch.x, factor), y=scale_pixel(swatch.y, factor))
                for swatch in self.palette
            ),
            brush_tool=_scale_control(self.brush_tool, factor),
            fill_tool=_scale_control(self.fill_tool, factor),
            brushes=tuple(
                replace(
                    brush,
                    x=scale_pixel(brush.x, factor),
                    y=scale_pixel(brush.y, factor),
                    width=brush.width * factor,
                )
                for brush in self.brushes
            ),
            picker=None
            if self.picker is None
            else ColorPicker(
                open=_scale_control(self.picker.open, factor),
                red=_scale_control(self.picker.red, factor),
                green=_scale_control(self.picker.green, factor),
                blue=_scale_control(self.picker.blue, factor),
                preview=_scale_control(self.picker.preview, factor),
            ),
            dpi_scale=dpi_scale,
        )

    def to_json_dict(self) -> dict[str, Any]:
        """Return the profile as the dictionary that is written to disk."""
        return {
            "format": PROFILE_FORMAT,
            "name": self.name,
            "created": self.created,
            "dpi_scale": self.dpi_scale,
            "screen": _rect_to_json(self.screen),
            "canvas": _rect_to_json(self.canvas),
            "background": list(self.background),
            "palette": [
                {"x": swatch.x, "y": swatch.y, "color": list(swatch.color)}
                for swatch in self.palette
            ],
            "tools": {
                "brush": {"x": self.brush_tool.x, "y": self.brush_tool.y},
                "fill": {"x": self.fill_tool.x, "y": self.fill_tool.y},
            },
            "brushes": [
                {"x": brush.x, "y": brush.y, "width": brush.width, "measured": brush.measured}
                for brush in self.brushes
            ],
            "picker": None
            if self.picker is None
            else {
                name: {"x": control.x, "y": control.y}
                for name, control in (
                    ("open", self.picker.open),
                    ("red", self.picker.red),
                    ("green", self.picker.green),
                    ("blue", self.picker.blue),
                    ("preview", self.picker.preview),
                )
            },
            "cost": {
                "seconds_per_move": self.cost.seconds_per_move,
                "seconds_per_pixel": self.cost.seconds_per_pixel,
                "seconds_per_click": self.cost.seconds_per_click,
                "seconds_per_color_switch": self.cost.seconds_per_color_switch,
                "seconds_per_tool_switch": self.cost.seconds_per_tool_switch,
            },
            "pacing": {
                "point_seconds": self.pacing.point_seconds,
                "settle_seconds": self.pacing.settle_seconds,
                "hold_seconds": self.pacing.hold_seconds,
            },
        }

    def save(self, path: Path) -> None:
        """Write the profile as indented JSON, creating the directory if needed."""
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.to_json_dict(), indent=2, ensure_ascii=False)
        path.write_text(text + "\n", encoding="utf-8")


def scale_pixel(value: int, factor: float) -> int:
    """Convert a pixel measurement by a scale factor, the way Windows does.

    Windows rounds a half pixel up; Python's ``round`` rounds it to even. On a
    125 per cent display the two disagree on every other coordinate, which is
    enough to click the edge of a palette swatch instead of its middle.
    """
    return math.floor(value * factor + _ROUND_HALF_UP_OFFSET)


def logical_to_physical(value: int, dpi_scale: float) -> int:
    """Convert a logical pixel measurement to physical pixels."""
    return scale_pixel(value, dpi_scale)


def physical_to_logical(value: int, dpi_scale: float) -> int:
    """Convert a physical pixel measurement to logical pixels."""
    if dpi_scale <= 0:
        msg = f"dpi scale must be positive, got {dpi_scale}"
        raise ProfileError(msg)
    return scale_pixel(value, 1.0 / dpi_scale)


def _scale_rect(rect: ScreenRect, factor: float) -> ScreenRect:
    return ScreenRect(
        left=scale_pixel(rect.left, factor),
        top=scale_pixel(rect.top, factor),
        width=max(1, scale_pixel(rect.width, factor)),
        height=max(1, scale_pixel(rect.height, factor)),
    )


def _scale_control(control: Control, factor: float) -> Control:
    return Control(x=scale_pixel(control.x, factor), y=scale_pixel(control.y, factor))


def _rect_to_json(rect: ScreenRect) -> dict[str, int]:
    return {"left": rect.left, "top": rect.top, "width": rect.width, "height": rect.height}


def _rect_from_json(data: Any, field: str) -> ScreenRect:  # noqa: ANN401
    try:
        return ScreenRect(
            left=int(data["left"]),
            top=int(data["top"]),
            width=int(data["width"]),
            height=int(data["height"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        msg = f"{field} rectangle is malformed: {error}"
        raise ProfileError(msg) from error


def _color_from_json(data: Any) -> Rgb:  # noqa: ANN401
    try:
        red, green, blue = (int(channel) for channel in data)
    except (TypeError, ValueError) as error:
        msg = f"palette colour is malformed: {data!r}"
        raise ProfileError(msg) from error
    for channel in (red, green, blue):
        if not 0 <= channel <= _MAX_CHANNEL:
            msg = f"palette colour channel out of range: {data!r}"
            raise ProfileError(msg)
    return (red, green, blue)


def from_json_dict(data: dict[str, Any]) -> Profile:
    """Build a profile from a stored dictionary, rejecting anything unreadable."""
    stored_format = data.get("format")
    if stored_format != PROFILE_FORMAT:
        msg = f"profile format {stored_format!r} is not readable, expected {PROFILE_FORMAT}"
        raise ProfileError(msg)
    try:
        tools = data["tools"]
        cost = data["cost"]
        pacing = data["pacing"]
        return Profile(
            name=str(data["name"]),
            canvas=_rect_from_json(data["canvas"], "canvas"),
            screen=_rect_from_json(data["screen"], "screen"),
            background=_color_from_json(data["background"]),
            palette=tuple(
                Swatch(x=int(entry["x"]), y=int(entry["y"]), color=_color_from_json(entry["color"]))
                for entry in data["palette"]
            ),
            brush_tool=Control(x=int(tools["brush"]["x"]), y=int(tools["brush"]["y"])),
            fill_tool=Control(x=int(tools["fill"]["x"]), y=int(tools["fill"]["y"])),
            brushes=tuple(
                BrushControl(
                    x=int(entry["x"]),
                    y=int(entry["y"]),
                    width=float(entry["width"]),
                    measured=bool(entry["measured"]),
                )
                for entry in data["brushes"]
            ),
            dpi_scale=float(data["dpi_scale"]),
            cost=CostModel(
                seconds_per_move=float(cost["seconds_per_move"]),
                seconds_per_pixel=float(cost["seconds_per_pixel"]),
                seconds_per_click=float(cost["seconds_per_click"]),
                seconds_per_color_switch=float(cost["seconds_per_color_switch"]),
                seconds_per_tool_switch=float(cost["seconds_per_tool_switch"]),
            ),
            pacing=Pacing(
                point_seconds=float(pacing["point_seconds"]),
                settle_seconds=float(pacing["settle_seconds"]),
                hold_seconds=float(pacing["hold_seconds"]),
            ),
            created=str(data.get("created", "")),
            picker=_picker_from_json(data.get("picker")),
        )
    except (KeyError, TypeError) as error:
        msg = f"profile is missing or has a malformed field: {error}"
        raise ProfileError(msg) from error


def _picker_from_json(data: Any) -> ColorPicker | None:  # noqa: ANN401
    if not data:
        return None
    try:
        controls = {
            name: Control(x=int(data[name]["x"]), y=int(data[name]["y"]))
            for name in ("open", "red", "green", "blue", "preview")
        }
    except (KeyError, TypeError, ValueError) as error:
        msg = f"the colour picker is malformed: {error}"
        raise ProfileError(msg) from error
    return ColorPicker(**controls)


def load(path: Path) -> Profile:
    """Read a profile from disk."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        msg = f"{path} could not be read as a profile: {error}"
        raise ProfileError(msg) from error
    if not isinstance(data, dict):
        msg = f"{path} does not contain a profile object"
        raise ProfileError(msg)
    try:
        return from_json_dict(data)
    except ProfileError as error:
        msg = f"{path}: {error}"
        raise ProfileError(msg) from error


def timestamp() -> str:
    """Return the creation stamp written into a new profile."""
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


def user_profiles_dir() -> Path:
    """Return the directory holding profiles the user calibrated themselves.

    Kept beside the roaming application data rather than next to the
    executable, so the tool still works from a read-only flash drive.
    """
    roaming = os.environ.get("APPDATA")
    base = Path(roaming) if roaming else Path.home() / ".config"
    return base / "penplan" / "profiles"


def _load_directory(directory: Path) -> dict[str, Profile]:
    if not directory.is_dir():
        return {}
    return {path.stem: load(path) for path in sorted(directory.glob(f"*{PROFILE_SUFFIX}"))}


def bundled_profiles() -> dict[str, Profile]:
    """Return the profiles shipped with the tool, keyed by file name."""
    try:
        directory = resources.files(BUNDLED_PROFILES_PACKAGE)
    except ModuleNotFoundError:
        return {}
    return _load_directory(Path(str(directory)))


def available_profiles() -> dict[str, Profile]:
    """Return every profile, with the user's own overriding a bundled one.

    A malformed profile raises rather than being skipped: a file the user edited
    by hand and broke should say so, not disappear from the list.
    """
    profiles = bundled_profiles()
    profiles.update(_load_directory(user_profiles_dir()))
    return profiles
