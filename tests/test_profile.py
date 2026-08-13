"""Tests for the calibration profile format."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from penplan import profile as profile_module
from penplan.model import DEFAULT_COST_MODEL, DEFAULT_PACING, Point, ScreenRect
from penplan.profile import (
    PROFILE_FORMAT,
    BrushControl,
    ColorPicker,
    Control,
    Profile,
    ProfileError,
    Swatch,
    available_profiles,
    bundled_profiles,
    from_json_dict,
    load,
    logical_to_physical,
    physical_to_logical,
    scale_pixel,
    user_profiles_dir,
)

# The display scales Windows offers by default, as fractions.
SCALES = (1.0, 1.25, 1.5, 2.0)

PICKER = ColorPicker(
    open=Control(x=10, y=900),
    red=Control(x=10, y=940),
    green=Control(x=40, y=940),
    blue=Control(x=70, y=940),
    preview=Control(x=10, y=900),
)


def make_profile(**overrides: object) -> Profile:
    fields: dict[str, object] = {
        "name": "test",
        "canvas": ScreenRect(left=100, top=200, width=800, height=600),
        "screen": ScreenRect(left=0, top=0, width=1920, height=1080),
        "background": (255, 255, 255),
        "palette": (
            Swatch(x=10, y=20, color=(0, 0, 0)),
            Swatch(x=30, y=20, color=(255, 255, 255)),
            Swatch(x=50, y=20, color=(200, 30, 40)),
        ),
        "brush_tool": Control(x=5, y=5),
        "fill_tool": Control(x=5, y=25),
        "brushes": (
            BrushControl(x=70, y=20, width=2.0, measured=True),
            BrushControl(x=90, y=20, width=8.0, measured=True),
        ),
        "dpi_scale": 1.0,
        "cost": DEFAULT_COST_MODEL,
        "pacing": DEFAULT_PACING,
        "created": "2026-08-12T00:00:00+00:00",
    }
    fields.update(overrides)
    return Profile(**fields)  # type: ignore[arg-type]


def test_round_trip_through_disk_is_lossless(tmp_path: Path) -> None:
    original = make_profile()
    path = tmp_path / "nested" / "test.json"
    original.save(path)
    assert load(path) == original


def test_saved_profile_is_readable_json(tmp_path: Path) -> None:
    path = tmp_path / "test.json"
    make_profile().save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["format"] == PROFILE_FORMAT
    assert data["palette"][2]["color"] == [200, 30, 40]
    assert data["brushes"][0]["measured"] is True


def test_unknown_format_version_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "test.json"
    make_profile().save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["format"] = PROFILE_FORMAT + 1
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ProfileError, match="is not readable"):
        load(path)


def test_missing_field_is_refused() -> None:
    data = make_profile().to_json_dict()
    del data["canvas"]
    with pytest.raises(ProfileError, match="missing or has a malformed field"):
        from_json_dict(data)


def test_out_of_range_colour_is_refused() -> None:
    data = make_profile().to_json_dict()
    data["palette"][0]["color"] = [0, 300, 0]
    with pytest.raises(ProfileError, match="out of range"):
        from_json_dict(data)


def test_unreadable_file_names_itself(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ProfileError, match=r"broken\.json"):
        load(path)


def test_tiny_canvas_is_refused() -> None:
    with pytest.raises(ProfileError, match="smaller than the"):
        make_profile(canvas=ScreenRect(left=0, top=0, width=8, height=600))


def test_empty_palette_is_refused() -> None:
    with pytest.raises(ProfileError, match="at least one palette colour"):
        make_profile(palette=())


def test_unordered_brush_widths_are_refused() -> None:
    with pytest.raises(ProfileError, match="strictly increasing"):
        make_profile(
            brushes=(
                BrushControl(x=1, y=1, width=8.0, measured=True),
                BrushControl(x=2, y=1, width=2.0, measured=True),
            )
        )


def test_a_control_inside_the_canvas_is_refused() -> None:
    # What a calibration looks like when the canvas corners were captured
    # around the palette as well: every click on that swatch would draw.
    with pytest.raises(ProfileError, match="inside the canvas"):
        make_profile(
            palette=(Swatch(x=150, y=250, color=(0, 0, 0)),),
        )


def test_a_tool_inside_the_canvas_is_refused() -> None:
    with pytest.raises(ProfileError, match="brush tool at 150,250 is inside the canvas"):
        make_profile(brush_tool=Control(x=150, y=250))


def test_zero_brush_width_is_refused() -> None:
    with pytest.raises(ProfileError, match="width must be positive"):
        BrushControl(x=1, y=1, width=0.0, measured=False)


@pytest.mark.parametrize(
    ("value", "scale", "expected"),
    [
        (100, 1.0, 100),
        (0, 1.25, 0),
        (2, 1.25, 3),
        (4, 1.25, 5),
        (100, 1.25, 125),
        (1, 1.5, 2),
        (3, 1.5, 5),
        (100, 1.5, 150),
        (1, 2.0, 2),
        (100, 2.0, 200),
        (-1920, 1.25, -2400),
    ],
)
def test_logical_to_physical_rounds_the_way_windows_does(
    value: int, scale: float, expected: int
) -> None:
    assert logical_to_physical(value, scale) == expected


@pytest.mark.parametrize("scale", SCALES)
def test_scaling_round_trips_for_every_pixel(scale: float) -> None:
    for value in range(500):
        assert physical_to_logical(logical_to_physical(value, scale), scale) == value


def test_physical_to_logical_refuses_a_zero_scale() -> None:
    with pytest.raises(ProfileError, match="must be positive"):
        physical_to_logical(100, 0.0)


@pytest.mark.parametrize("scale", SCALES)
def test_rescaling_moves_every_recorded_position(scale: float) -> None:
    original = make_profile()
    rescaled = original.rescaled(scale)
    assert rescaled.dpi_scale == scale
    assert rescaled.canvas.left == scale_pixel(original.canvas.left, scale)
    assert rescaled.canvas.width == scale_pixel(original.canvas.width, scale)
    assert rescaled.palette[2].x == scale_pixel(original.palette[2].x, scale)
    assert rescaled.palette[2].color == original.palette[2].color
    assert rescaled.brush_tool.y == scale_pixel(original.brush_tool.y, scale)
    assert rescaled.brush_widths == tuple(width * scale for width in original.brush_widths)


def test_rescaling_back_returns_the_original_geometry() -> None:
    original = make_profile()
    assert original.rescaled(1.5).rescaled(1.0).canvas == original.canvas


def test_rescaling_refuses_a_zero_scale() -> None:
    with pytest.raises(ProfileError, match="must be positive"):
        make_profile().rescaled(0.0)


def test_canvas_mapping_covers_the_canvas_without_leaving_it() -> None:
    canvas = ScreenRect(left=100, top=200, width=800, height=600)
    subject = make_profile(canvas=canvas)
    assert subject.canvas_to_screen(Point(0, 0), 800, 600) == (100, 200)
    assert subject.canvas_to_screen(Point(799, 599), 800, 600) == (899, 799)
    # A coarse raster stretches over the same canvas.
    assert subject.canvas_to_screen(Point(0, 0), 100, 75) == (104, 204)
    assert subject.canvas_to_screen(Point(99, 74), 100, 75) == (896, 796)


def test_canvas_mapping_refuses_an_empty_raster() -> None:
    with pytest.raises(ProfileError, match="raster size must be positive"):
        make_profile().canvas_to_screen(Point(0, 0), 0, 10)


def test_user_profiles_dir_follows_appdata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", r"C:\Users\somebody\AppData\Roaming")
    assert user_profiles_dir() == Path(r"C:\Users\somebody\AppData\Roaming\penplan\profiles")


def test_user_profiles_dir_falls_back_without_appdata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APPDATA", raising=False)
    assert user_profiles_dir().parts[-3:] == (".config", "penplan", "profiles")


def test_available_profiles_prefer_the_user_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(profile_module, "user_profiles_dir", lambda: tmp_path)
    make_profile(name="mine").save(tmp_path / "mine.json")
    profiles = available_profiles()
    assert profiles["mine"].name == "mine"


def test_missing_profile_directories_are_empty_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(profile_module, "user_profiles_dir", lambda: tmp_path / "absent")
    assert available_profiles() == bundled_profiles()


def test_a_nameless_profile_is_refused() -> None:
    with pytest.raises(ProfileError, match="needs a name"):
        make_profile(name="")


def test_a_profile_without_brushes_is_refused() -> None:
    with pytest.raises(ProfileError, match="at least one brush size"):
        make_profile(brushes=())


def test_a_profile_with_no_scale_is_refused() -> None:
    with pytest.raises(ProfileError, match="dpi scale must be positive"):
        make_profile(dpi_scale=0.0)


def test_a_picker_survives_the_round_trip() -> None:
    original = make_profile(picker=PICKER)
    restored = from_json_dict(json.loads(json.dumps(original.to_json_dict())))
    assert restored.picker == PICKER


def test_a_malformed_picker_is_named_not_swallowed() -> None:
    broken = make_profile(picker=PICKER).to_json_dict()
    broken["picker"] = {"open": {"x": 1}}
    with pytest.raises(ProfileError, match="colour picker is malformed"):
        from_json_dict(broken)


def test_a_picker_control_inside_the_canvas_is_refused() -> None:
    # The same mis-calibration as a swatch inside the canvas: opening the
    # picker would put a mark on the drawing.
    inside = replace(PICKER, open=Control(x=200, y=300))
    with pytest.raises(ProfileError, match="inside the canvas"):
        make_profile(picker=inside)


def test_rescaling_carries_the_picker_with_it() -> None:
    rescaled = make_profile(picker=PICKER).rescaled(2.0)
    assert rescaled.picker is not None
    assert rescaled.picker.red.x == scale_pixel(PICKER.red.x, 2.0)


def test_a_profile_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "list.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ProfileError, match="does not contain a profile object"):
        load(path)
