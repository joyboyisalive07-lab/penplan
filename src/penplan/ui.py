"""The window: three columns, one accent, and no surprises.

Left is the image you gave it, middle is exactly what the mouse will draw,
right is the handful of things worth deciding. The numbers under the controls
are always there, before anything is drawn: how long it will take, how much of
it there is, and what the time budget had to give up to fit.

Nothing here is a default tkinter widget that could be helped. The switches,
the slider and the button are drawn on canvases, because the stock ones bring a
grey nineties frame with them and this is meant to be looked at.
"""

from __future__ import annotations

import base64
import io
import sys
import threading
import tkinter as tk
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from tkinter import filedialog
from typing import TYPE_CHECKING, Final

from PIL import Image

from penplan.budget import PlanRequest, plan_within_budget, schedule
from penplan.input_win import (
    AbortHotkey,
    ExecutionResult,
    Executor,
    HotkeyUnavailableError,
    InputError,
    Pointer,
    accept_dropped_files,
    dpi_scale_at,
    enable_dpi_awareness,
    virtual_screen,
)
from penplan.palette import Palette
from penplan.profile import ProfileError, available_profiles
from penplan.render import render_plan

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from penplan.model import Action, DrawPlan
    from penplan.profile import Profile

BACKGROUND: Final = "#12141a"
PANEL: Final = "#1a1d25"
EDGE: Final = "#272c38"
TEXT: Final = "#e7eaf0"
MUTED: Final = "#868fa2"
ACCENT: Final = "#e3a04a"
WARNING: Final = "#e06c5a"

BODY: Final = ("Segoe UI", 10)
SMALL: Final = ("Segoe UI", 9)
TITLE: Final = ("Segoe UI Semibold", 13)
# Consolas is monospaced, so a column of numbers stays a column while it
# changes, which is the whole point of showing them live.
NUMBER: Final = ("Consolas", 12)

GAP: Final = 18
PAD: Final = 14
CONTROL_WIDTH: Final = 330
PREVIEW_SIDE: Final = 380
# Tall enough that every number and the button fit without scrolling, and
# small enough to sit beside a browser rather than on top of it.
WINDOW_WIDTH: Final = 1180
WINDOW_HEIGHT: Final = 780

COUNTDOWN_SECONDS: Final = 3
# Planning is not instant, so a keystroke in the budget box should not start a
# plan per character.
REPLAN_DELAY_MS: Final = 350

IMAGE_TYPES: Final = (
    ("Images", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"),
    ("All files", "*.*"),
)


def _rounded(
    canvas: tk.Canvas, box: tuple[int, int, int, int], radius: int, **options: object
) -> None:
    """Draw a rounded rectangle, which the canvas does not offer on its own."""
    left, top, right, bottom = box
    canvas.create_polygon(
        left + radius,
        top,
        right - radius,
        top,
        right,
        top,
        right,
        top + radius,
        right,
        bottom - radius,
        right,
        bottom,
        right - radius,
        bottom,
        left + radius,
        bottom,
        left,
        bottom,
        left,
        bottom - radius,
        left,
        top + radius,
        left,
        top,
        smooth=True,
        **options,
    )


class Toggle(tk.Canvas):
    """A switch, drawn rather than borrowed."""

    def __init__(
        self, parent: tk.Misc, *, on_change: Callable[[], None], value: bool = False
    ) -> None:
        super().__init__(parent, width=42, height=22, bg=PANEL, highlightthickness=0)
        self._value = value
        self._on_change = on_change
        self.bind("<Button-1>", self._clicked)
        self._draw()

    @property
    def value(self) -> bool:
        """Return whether the switch is on."""
        return self._value

    def _clicked(self, _event: tk.Event) -> None:
        self._value = not self._value
        self._draw()
        self._on_change()

    def _draw(self) -> None:
        self.delete("all")
        track = ACCENT if self._value else EDGE
        _rounded(self, (1, 3, 41, 19), 8, fill=track, outline=track)
        knob = 31 if self._value else 11
        self.create_oval(
            knob - 8, 3, knob + 8, 19, fill=BACKGROUND if self._value else MUTED, outline=""
        )


class Slider(tk.Canvas):
    """A horizontal slider, drawn rather than borrowed."""

    def __init__(self, parent: tk.Misc, *, value: float, on_change: Callable[[], None]) -> None:
        super().__init__(
            parent, width=CONTROL_WIDTH - 2 * PAD, height=26, bg=PANEL, highlightthickness=0
        )
        self._value = value
        self._on_change = on_change
        self.bind("<Button-1>", self._moved)
        self.bind("<B1-Motion>", self._moved)
        self.bind("<Configure>", lambda _event: self._draw())
        self._draw()

    @property
    def value(self) -> float:
        """Return the slider position, between 0 and 1."""
        return self._value

    def _moved(self, event: tk.Event) -> None:
        width = max(1, self.winfo_width() - 16)
        self._value = min(1.0, max(0.0, (event.x - 8) / width))
        self._draw()
        self._on_change()

    def _draw(self) -> None:
        self.delete("all")
        width = max(1, self.winfo_width() - 16)
        middle = 13
        self.create_line(8, middle, 8 + width, middle, fill=EDGE, width=3)
        knob = 8 + int(self._value * width)
        self.create_line(8, middle, knob, middle, fill=ACCENT, width=3)
        self.create_oval(knob - 7, middle - 7, knob + 7, middle + 7, fill=ACCENT, outline="")


class PrimaryButton(tk.Canvas):
    """The one button that starts things."""

    def __init__(self, parent: tk.Misc, *, text: str, command: Callable[[], None]) -> None:
        super().__init__(
            parent, width=CONTROL_WIDTH - 2 * PAD, height=44, bg=PANEL, highlightthickness=0
        )
        self._text = text
        self._command = command
        self._enabled = True
        self.bind("<Button-1>", lambda _event: self._enabled and self._command())
        self.bind("<Enter>", lambda _event: self._draw(hover=True))
        self.bind("<Leave>", lambda _event: self._draw(hover=False))
        self.bind("<Configure>", lambda _event: self._draw())
        self._draw()

    def configure_state(self, *, enabled: bool, text: str | None = None) -> None:
        """Enable or disable the button, and optionally relabel it."""
        self._enabled = enabled
        if text is not None:
            self._text = text
        self._draw()

    def _draw(self, *, hover: bool = False) -> None:
        self.delete("all")
        width = max(2, self.winfo_width())
        fill = ACCENT if self._enabled else EDGE
        if self._enabled and hover:
            fill = "#f0b061"
        _rounded(self, (0, 0, width - 1, 43), 10, fill=fill, outline=fill)
        self.create_text(
            width // 2,
            22,
            text=self._text,
            fill=BACKGROUND if self._enabled else MUTED,
            font=("Segoe UI Semibold", 11),
        )


class ImagePane(tk.Canvas):
    """A panel that shows one image, scaled to fit, with a caption."""

    def __init__(self, parent: tk.Misc, caption: str) -> None:
        super().__init__(parent, bg=PANEL, highlightthickness=1, highlightbackground=EDGE)
        self._caption = caption
        self._photo: tk.PhotoImage | None = None
        self._image: Image.Image | None = None
        self._message = ""
        self.bind("<Configure>", lambda _event: self._redraw())

    def show(self, image: Image.Image | None, message: str = "") -> None:
        """Display an image, or a message when there is nothing to show."""
        self._image = image
        self._message = message
        self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        width = self.winfo_width()
        height = self.winfo_height()
        self.create_text(PAD, PAD, text=self._caption, anchor="nw", fill=MUTED, font=SMALL)
        if self._image is None:
            self.create_text(
                width // 2, height // 2, text=self._message, fill=MUTED, font=BODY, width=width - 40
            )
            return
        box = (max(1, width - 2 * PAD), max(1, height - 3 * PAD))
        scale = min(box[0] / self._image.width, box[1] / self._image.height)
        size = (max(1, int(self._image.width * scale)), max(1, int(self._image.height * scale)))
        self._photo = _as_photo(self._image.resize(size, Image.Resampling.NEAREST))
        self.create_image(width // 2, PAD + (height - PAD) // 2, image=self._photo)


def _as_photo(image: Image.Image) -> tk.PhotoImage:
    """Convert a Pillow image into something Tk will display.

    Through PNG bytes rather than through Pillow's ImageTk, which needs Tk
    headers at build time and is missing from some wheels. Tk has read PNG
    since 8.6.
    """
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return tk.PhotoImage(data=base64.b64encode(buffer.getvalue()))


@dataclass(slots=True)
class _Fields:
    """The live numbers, so the layout code and the update code stay apart."""

    duration: tk.StringVar
    strokes: tk.StringVar
    fills: tk.StringVar
    colors: tk.StringVar
    points: tk.StringVar
    travel: tk.StringVar


class App:
    """The whole interface."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.profiles = _load_profiles()
        self.image: Image.Image | None = None
        self.image_name = ""
        self.plan: DrawPlan | None = None
        self.hotkey: AbortHotkey | None = None
        self.pending: str | None = None
        self.planning = False
        self.drawing = False

        root.title("penplan")
        root.configure(bg=BACKGROUND)
        root.minsize(1060, 700)
        self._apply_scaling()

        self.source_pane = ImagePane(root, "SOURCE")
        self.preview_pane = ImagePane(root, "DRY RUN")
        self.controls = tk.Frame(root, bg=PANEL, highlightthickness=1, highlightbackground=EDGE)
        self.source_pane.grid(row=0, column=0, sticky="nsew", padx=(GAP, 0), pady=GAP)
        self.preview_pane.grid(row=0, column=1, sticky="nsew", padx=(GAP, 0), pady=GAP)
        self.controls.grid(row=0, column=2, sticky="nsew", padx=GAP, pady=GAP)
        root.grid_rowconfigure(0, weight=1)
        root.grid_columnconfigure(0, weight=1, minsize=PREVIEW_SIDE)
        root.grid_columnconfigure(1, weight=1, minsize=PREVIEW_SIDE)
        root.grid_columnconfigure(2, weight=0, minsize=CONTROL_WIDTH)
        self.controls.grid_propagate(flag=False)
        self.controls.configure(width=CONTROL_WIDTH)

        self.fields = _Fields(*(tk.StringVar(value="-") for _ in range(6)))
        self.status = tk.StringVar(value="Choose a profile and an image")
        self.sacrifices = tk.StringVar(value="")
        self._set_icon()
        self._build_controls()
        self._show_source()
        self._install_drop()
        root.bind("<Escape>", lambda _event: self._abort())
        self._schedule_replan()

    def _set_icon(self) -> None:
        """Put the drawn icon on the window, if it can be found on disk."""
        try:
            with resources.as_file(resources.files("penplan") / "penplan.ico") as path:
                self.root.iconbitmap(default=str(path))
        except (OSError, ModuleNotFoundError, tk.TclError):
            # A missing icon is not worth refusing to start over.
            pass

    def _apply_scaling(self) -> None:
        """Undo what per-monitor awareness costs the interface.

        The process asks Windows for physical pixels so that calibration and
        the mouse agree, and the price is that Tk stops scaling itself. So the
        scale factor is applied here instead.
        """
        enable_dpi_awareness()
        screen = virtual_screen()
        scale = dpi_scale_at(screen.left + screen.width // 2, screen.top + screen.height // 2)
        self.root.tk.call("tk", "scaling", scale * 96 / 72)
        self.root.geometry(f"{int(WINDOW_WIDTH * scale)}x{int(WINDOW_HEIGHT * scale)}")

    def _build_controls(self) -> None:
        parent = self.controls
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", padx=PAD, pady=(PAD, 0))
        tk.Label(row, text="penplan", bg=PANEL, fg=TEXT, font=TITLE).pack(side="left")

        self._label("PROFILE")
        names = list(self.profiles) or ["no profiles found"]
        self.profile_name = tk.StringVar(value=names[0])
        menu = tk.OptionMenu(
            parent, self.profile_name, *names, command=lambda _value: self._schedule_replan()
        )
        menu.configure(
            bg=BACKGROUND,
            fg=TEXT,
            activebackground=EDGE,
            activeforeground=TEXT,
            font=BODY,
            highlightthickness=0,
            borderwidth=0,
            anchor="w",
            relief="flat",
            indicatoron=False,
        )
        menu["menu"].configure(
            bg=BACKGROUND,
            fg=TEXT,
            activebackground=ACCENT,
            activeforeground=BACKGROUND,
            font=BODY,
            borderwidth=0,
        )
        menu.pack(fill="x", padx=PAD, pady=(4, 0))

        self._label("IMAGE")
        self.image_label = tk.Label(
            parent, text="Drop an image here", bg=BACKGROUND, fg=MUTED, font=BODY, pady=10
        )
        self.image_label.pack(fill="x", padx=PAD, pady=(4, 0))
        self.image_label.bind("<Button-1>", lambda _event: self._choose_image())

        self._label("SECONDS AVAILABLE")
        self.budget = tk.Entry(
            parent,
            bg=BACKGROUND,
            fg=TEXT,
            insertbackground=ACCENT,
            font=NUMBER,
            relief="flat",
            justify="left",
        )
        self.budget.insert(0, "90")
        self.budget.bind("<KeyRelease>", lambda _event: self._schedule_replan())
        self.budget.pack(fill="x", padx=PAD, pady=(4, 0), ipady=6)

        self._label("DETAIL")
        self.detail = Slider(parent, value=0.6, on_change=self._schedule_replan)
        self.detail.pack(fill="x", padx=PAD, pady=(2, 0))

        self.dither = self._switch("Dither  (multiplies stroke count)", value=False)
        self.use_fills = self._switch("Use the fill tool", value=True)

        self.button = PrimaryButton(parent, text="Draw", command=self._start)
        tk.Label(
            parent,
            textvariable=self.status,
            bg=PANEL,
            fg=MUTED,
            font=SMALL,
            justify="left",
            anchor="w",
            wraplength=CONTROL_WIDTH - 2 * PAD,
        ).pack(side="bottom", fill="x", padx=PAD, pady=(0, PAD))
        self.button.pack(side="bottom", fill="x", padx=PAD, pady=(PAD, 6))
        tk.Label(
            parent,
            textvariable=self.sacrifices,
            bg=PANEL,
            fg=WARNING,
            font=SMALL,
            justify="left",
            anchor="w",
            wraplength=CONTROL_WIDTH - 2 * PAD,
        ).pack(side="bottom", fill="x", padx=PAD)

        tk.Frame(parent, bg=EDGE, height=1).pack(fill="x", padx=PAD, pady=(PAD, 0))
        self._readout("Estimated", self.fields.duration)
        self._readout("Strokes", self.fields.strokes)
        self._readout("Fills", self.fields.fills)
        self._readout("Colours", self.fields.colors)
        self._readout("Points", self.fields.points)
        self._readout("Travel saved", self.fields.travel)

    def _label(self, text: str) -> None:
        tk.Label(self.controls, text=text, bg=PANEL, fg=MUTED, font=SMALL, anchor="w").pack(
            fill="x", padx=PAD, pady=(PAD, 0)
        )

    def _switch(self, text: str, *, value: bool) -> Toggle:
        row = tk.Frame(self.controls, bg=PANEL)
        row.pack(fill="x", padx=PAD, pady=(PAD, 0))
        toggle = Toggle(row, on_change=self._schedule_replan, value=value)
        toggle.pack(side="left")
        tk.Label(row, text=text, bg=PANEL, fg=TEXT, font=BODY).pack(side="left", padx=(10, 0))
        return toggle

    def _readout(self, name: str, variable: tk.StringVar) -> None:
        row = tk.Frame(self.controls, bg=PANEL)
        row.pack(fill="x", padx=PAD, pady=(6, 0))
        tk.Label(row, text=name, bg=PANEL, fg=MUTED, font=SMALL, anchor="w").pack(side="left")
        tk.Label(row, textvariable=variable, bg=PANEL, fg=TEXT, font=NUMBER, anchor="e").pack(
            side="right"
        )

    def _install_drop(self) -> None:
        self.root.update_idletasks()
        try:
            accept_dropped_files(self.root.winfo_id(), self._dropped)
        except InputError:
            # Not fatal: the file dialog is still there, and saying so is
            # better than a window that silently ignores what is dropped on it.
            self.image_label.configure(text="Click to choose an image")

    def _dropped(self, paths: list[str]) -> None:
        if paths:
            self.load_image(Path(paths[0]))

    def _choose_image(self) -> None:
        chosen = filedialog.askopenfilename(title="Choose an image", filetypes=list(IMAGE_TYPES))
        if chosen:
            self.load_image(Path(chosen))

    def load_image(self, path: Path) -> None:
        """Open an image file and plan it."""
        try:
            with Image.open(path) as opened:
                self.image = opened.convert("RGB")
        except (OSError, ValueError) as error:
            self.status.set(f"Could not read that image: {error}")
            return
        self.image_name = path.name
        self.image_label.configure(text=path.name, fg=TEXT)
        self._show_source()
        self._schedule_replan()

    def _show_source(self) -> None:
        self.source_pane.show(self.image, "Drop an image here, or click the panel on the right")

    def _schedule_replan(self) -> None:
        if self.pending is not None:
            self.root.after_cancel(self.pending)
        self.pending = self.root.after(REPLAN_DELAY_MS, self._replan)

    def _replan(self) -> None:
        self.pending = None
        profile = self.profiles.get(self.profile_name.get())
        if profile is None:
            self.status.set("No profiles found. Calibrate one with tools/calibrate.py")
            return
        if self.image is None:
            self.status.set("Drop an image on the window, or click the image box")
            return
        if self.planning or self.drawing:
            self._schedule_replan()
            return
        self.planning = True
        self.status.set("Planning")
        request = PlanRequest(
            image=self.image,
            profile=profile,
            budget_seconds=_seconds(self.budget.get()),
            detail=self.detail.value,
            dither=self.dither.value,
            use_fills=self.use_fills.value,
        )
        threading.Thread(target=self._plan_worker, args=(request,), daemon=True).start()

    def _plan_worker(self, request: PlanRequest) -> None:
        try:
            plan = plan_within_budget(request)
        except (ProfileError, ValueError) as error:
            self.root.after(0, self._planning_failed, str(error))
            return
        background = Palette(request.profile.colors).nearest(request.profile.background)
        preview = render_plan(plan, background)
        self.root.after(0, self._planned, plan, preview)

    def _planning_failed(self, message: str) -> None:
        self.planning = False
        self.plan = None
        self.status.set(message)
        self.button.configure_state(enabled=False)

    def _planned(self, plan: DrawPlan, preview: Image.Image) -> None:
        self.planning = False
        self.plan = plan
        report = plan.report
        self.preview_pane.show(preview)
        self.fields.duration.set(f"{report.estimated_seconds:6.1f} s")
        self.fields.strokes.set(f"{len(plan.strokes):6d}")
        self.fields.fills.set(f"{len(plan.fills):6d}")
        self.fields.colors.set(f"{plan.color_count:6d}")
        self.fields.points.set(f"{plan.point_count:6d}")
        self.fields.travel.set(f"{report.ordering_improvement * 100:5.1f} %")
        self.sacrifices.set(
            "\n".join(f"- {item.detail}" for item in report.sacrifices) if report.sacrifices else ""
        )
        if report.fits_budget:
            self.status.set("Escape stops the drawing at any moment")
            self.button.configure_state(enabled=True, text="Draw")
        else:
            self.status.set(
                f"Does not fit: {report.estimated_seconds:.0f} s of work in "
                f"{report.budget_seconds:.0f} s. Draw anyway, or give it longer."
            )
            self.button.configure_state(enabled=True, text="Draw anyway")

    def _start(self) -> None:
        if self.plan is None or self.drawing:
            return
        self.drawing = True
        self.button.configure_state(enabled=False, text="Drawing")
        self._countdown(COUNTDOWN_SECONDS)

    def _countdown(self, left: int) -> None:
        if not self.drawing:
            return
        if left == 0:
            self._execute()
            return
        self.status.set(f"Focus the canvas. Starting in {left}. Escape stops it.")
        self.preview_pane.show(None, str(left))
        self.root.after(1000, self._countdown, left - 1)

    def _execute(self) -> None:
        profile = self.profiles[self.profile_name.get()]
        plan = self.plan
        if plan is None:
            return
        actions = schedule(plan.steps, (plan.width, plan.height), profile, profile.pacing)
        self.hotkey = AbortHotkey()
        try:
            self.hotkey.start()
        except HotkeyUnavailableError as error:
            self.drawing = False
            self.status.set(str(error))
            self.button.configure_state(enabled=True, text="Draw")
            return
        # Out of the way: a window sitting over the canvas would be drawn on.
        self.root.iconify()
        threading.Thread(target=self._draw_worker, args=(actions,), daemon=True).start()

    def _draw_worker(self, actions: Sequence[Action]) -> None:
        if self.hotkey is None:
            return
        try:
            result = Executor(Pointer(), self.hotkey).run(actions)
        except InputError as error:
            self.root.after(0, self._finished, None, str(error))
            return
        self.root.after(0, self._finished, result, "")

    def _finished(self, result: ExecutionResult | None, message: str) -> None:
        if self.hotkey is not None:
            self.hotkey.stop()
            self.hotkey = None
        self.drawing = False
        self.root.deiconify()
        self.button.configure_state(enabled=True, text="Draw")
        if message or result is None:
            self.status.set(message)
        elif result.aborted:
            self.status.set("Stopped. The mouse is yours again.")
        else:
            self.status.set(f"Done in {result.seconds:.1f} s")
        if self.plan is not None:
            profile = self.profiles[self.profile_name.get()]
            background = Palette(profile.colors).nearest(profile.background)
            self.preview_pane.show(render_plan(self.plan, background))

    def _abort(self) -> None:
        if self.drawing and self.hotkey is not None:
            self.hotkey.trigger()
            self.status.set("Stopping")


def _seconds(text: str) -> float:
    try:
        return max(1.0, float(text.strip()))
    except ValueError:
        return 60.0


def _load_profiles() -> dict[str, Profile]:
    try:
        return available_profiles()
    except ProfileError:
        return {}


def main(argv: Sequence[str] | None = None) -> int:
    """Open the window and run until it closes.

    An image path may be given on the command line, which is what makes the
    executable work as a target for the shell's "open with".
    """
    arguments = sys.argv[1:] if argv is None else list(argv)
    root = tk.Tk()
    app = App(root)
    if arguments:
        app.load_image(Path(arguments[0]))
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
