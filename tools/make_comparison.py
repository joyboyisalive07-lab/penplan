"""Render the source, the dry run and the real result side by side.

Usage::

    python tools/make_comparison.py IMAGE OUT --profile PROFILE [--actual SHOT]

The middle panel comes out of the planner itself, rendered by the same code
that verifies the fills. The third, when given, is a screenshot of a canvas the
tool actually drew on. Nothing is touched up.
"""

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from penplan.budget import PlanRequest, plan_within_budget
from penplan.profile import load
from penplan.render import render_plan

BACKGROUND = (18, 20, 26)
LABEL = (134, 143, 162)
GAP = 24
LABEL_HEIGHT = 30
PANEL = 420


def _framed(panel: Image.Image, aspect: float) -> Image.Image:
    """Crop the bands a canvas of a different shape puts around the picture.

    A portrait image on a landscape canvas is letterboxed, in the plan and on
    the screen alike. Those bands are background the tool never draws, and
    leaving them in makes the panel a third the size of the one beside it.
    """
    width = min(panel.width, round(panel.height * aspect))
    height = min(panel.height, round(panel.width / aspect))
    left = (panel.width - width) // 2
    top = (panel.height - height) // 2
    return panel.crop((left, top, left + width, top + height))


def main() -> int:
    """Write the comparison image and return a process exit code."""
    parser = argparse.ArgumentParser(description="Render source beside dry run")
    parser.add_argument("image", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--detail", type=float, default=0.6)
    parser.add_argument(
        "--actual", type=Path, default=None, help="a screenshot of the canvas after drawing"
    )
    parser.add_argument(
        "--picker",
        action="store_true",
        help="choose colours from the image and type them, for a profile with a bound picker",
    )
    parser.add_argument("--colors", type=int, default=12, help="how many colours to choose")
    arguments = parser.parse_args()

    profile = load(arguments.profile)
    with Image.open(arguments.image) as opened:
        source = opened.convert("RGB")
    plan = plan_within_budget(
        PlanRequest(
            image=source,
            profile=profile,
            budget_seconds=arguments.seconds,
            detail=arguments.detail,
            use_picker=arguments.picker,
            colors=arguments.colors,
        )
    )
    preview = render_plan(plan)

    aspect = source.width / source.height
    panels = [source, _framed(preview, aspect)]
    captions = ["SOURCE", "DRY RUN"]
    if arguments.actual is not None:
        with Image.open(arguments.actual) as shot:
            panels.append(_framed(shot.convert("RGB"), aspect))
        captions.append("DRAWN ON THE CANVAS")
    scaled = [
        panel.resize(
            (PANEL, max(1, round(PANEL * panel.height / panel.width))), Image.Resampling.LANCZOS
        )
        for panel in panels
    ]
    height = max(panel.height for panel in scaled)
    columns = len(scaled)
    sheet = Image.new(
        "RGB",
        (PANEL * columns + GAP * (columns + 1), height + LABEL_HEIGHT + GAP * 2),
        BACKGROUND,
    )
    draw = ImageDraw.Draw(sheet)
    for index, (panel, caption) in enumerate(zip(scaled, captions, strict=True)):
        x = GAP + index * (PANEL + GAP)
        draw.text((x, GAP - 6), caption, fill=LABEL)
        sheet.paste(panel, (x, GAP + LABEL_HEIGHT))
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(arguments.output)
    report = plan.report
    print(f"wrote {arguments.output}")
    print(
        f"{len(plan.strokes)} strokes, {len(plan.fills)} fills, {plan.color_count} colours, "
        f"{plan.point_count} points, estimated {report.estimated_seconds:.1f} s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
