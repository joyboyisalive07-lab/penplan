"""Render the source and the dry run side by side, for the documentation.

Usage: ``python tools/make_comparison.py IMAGE docs/img/dry-run.png``.

Both panels come out of the planner itself: the left is what was fed in, the
right is what the executor would draw, rendered by the same code that verifies
the fills. Nothing is touched up.
"""

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from penplan.budget import PlanRequest, plan_within_budget
from penplan.palette import Palette
from penplan.profile import load
from penplan.render import render_plan

BACKGROUND = (18, 20, 26)
LABEL = (134, 143, 162)
GAP = 24
LABEL_HEIGHT = 30
PANEL = 460


def main() -> int:
    """Write the comparison image and return a process exit code."""
    parser = argparse.ArgumentParser(description="Render source beside dry run")
    parser.add_argument("image", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--detail", type=float, default=0.6)
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
        )
    )
    background = Palette(profile.colors).nearest(profile.background)
    preview = render_plan(plan, background)

    panels = [source, preview]
    scaled = [
        panel.resize(
            (PANEL, max(1, round(PANEL * panel.height / panel.width))), Image.Resampling.LANCZOS
        )
        for panel in panels
    ]
    height = max(panel.height for panel in scaled)
    sheet = Image.new(
        "RGB", (PANEL * 2 + GAP * 3, height + LABEL_HEIGHT + GAP * 2), BACKGROUND
    )
    draw = ImageDraw.Draw(sheet)
    for index, (panel, caption) in enumerate(
        zip(scaled, ("SOURCE", "DRY RUN"), strict=True)
    ):
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
