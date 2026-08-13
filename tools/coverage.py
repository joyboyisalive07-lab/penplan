"""Measure statement coverage of the planning modules, with the standard library.

Usage: ``python tools/coverage.py`` from a checkout, optionally with
``--min 85`` to fail when the bar is not met.

The dev dependency list is three names on purpose, so this uses
``sys.monitoring``, which has reported line events since Python 3.12, rather
than adding a fourth package. It counts the lines that ran against the lines
that could have run, which is statement coverage and nothing more: it does not
measure branches, and it does not pretend to.

``input_win.py`` is excluded. Its job is to talk to Windows, and covering it
would mean either mocking the whole API surface, which tests the mock, or
moving the real mouse in a test run, which is worse.
"""

import argparse
import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "penplan"
sys.path.insert(0, str(ROOT / "src"))

# Everything the planner does is here. The Windows layer and the window are
# judged by running them, not by counting their lines.
EXCLUDED = frozenset({"input_win.py", "ui.py", "__main__.py"})
TOOL_ID = sys.monitoring.PROFILER_ID
DEFAULT_MINIMUM = 85.0


def executable_lines(path: Path) -> set[int]:
    """Return the line numbers that could run in a module.

    Taken from the syntax tree rather than from the source text, so that
    docstrings, comments, blank lines and the continuation lines of a wrapped
    expression are not counted as statements nobody executed.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt) and not _is_docstring(node):
            lines.add(node.lineno)
    return lines


def _is_docstring(node: ast.stmt) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)


def measure(paths: list[Path]) -> dict[Path, set[int]]:
    """Run the test suite and return the lines each file executed."""
    wanted = {str(path): path for path in paths}
    seen: dict[Path, set[int]] = {path: set() for path in paths}

    def record(code: object, line: int) -> object:
        path = wanted.get(getattr(code, "co_filename", ""))
        if path is not None:
            seen[path].add(line)
        return sys.monitoring.DISABLE if path is None else None

    sys.monitoring.use_tool_id(TOOL_ID, "penplan-coverage")
    sys.monitoring.register_callback(TOOL_ID, sys.monitoring.events.LINE, record)
    sys.monitoring.set_events(TOOL_ID, sys.monitoring.events.LINE)
    try:
        pytest.main([str(ROOT / "tests"), "-q", "-p", "no:cacheprovider"])
    finally:
        sys.monitoring.set_events(TOOL_ID, 0)
        sys.monitoring.free_tool_id(TOOL_ID)
    return seen


def main() -> int:
    """Report per-file coverage and return a process exit code."""
    parser = argparse.ArgumentParser(description="Measure statement coverage")
    parser.add_argument("--min", type=float, default=DEFAULT_MINIMUM)
    arguments = parser.parse_args()

    paths = sorted(p for p in PACKAGE.glob("*.py") if p.name not in EXCLUDED)
    covered = measure(paths)
    total_lines = 0
    total_hit = 0
    print(f"\n{'module':16s} {'lines':>7s} {'covered':>8s} {'percent':>8s}")
    for path in paths:
        lines = executable_lines(path)
        hit = lines & covered[path]
        total_lines += len(lines)
        total_hit += len(hit)
        share = 100.0 * len(hit) / len(lines) if lines else 100.0
        print(f"{path.name:16s} {len(lines):7d} {len(hit):8d} {share:7.1f}%")
    overall = 100.0 * total_hit / total_lines if total_lines else 100.0
    print(f"{'total':16s} {total_lines:7d} {total_hit:8d} {overall:7.1f}%")
    if overall < arguments.min:
        print(f"below the {arguments.min:.0f} per cent bar")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
