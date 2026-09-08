"""Fail if a package's test coverage is below the per-layer floor from codingrules 14.1.

Codingrules section 14.1 sets a different coverage floor for different kinds of code: 95% for
pure, cheap-to-test cores; 80% for adapters that touch the outside world; 60% for thin CLI and
read-only-view layers; 85% for the repository as a whole. `pytest-cov`'s own `fail_under` can only
enforce one number for the whole repository (see `[tool.coverage.report]` in pyproject.toml), so
roadmap step 0.2 recorded the per-layer numbers in `[tool.hivemind.coverage_floors]` and left
enforcing them to this script. It reads that table with `tomllib`, reads a coverage report already
written to a `coverage.json` file (produced separately by `pytest --cov-report=json`), aggregates
each floor's matching files, and fails on any floor -- or the total -- that is breached.

Fits into the Hive:
    Layer: none (a dev-time gate, not shipped code). Enforces codingrules section 14.1's coverage
    floors. Belongs to CI only (it needs a coverage.json a completed pytest run produced), not to
    pre-commit, which cannot afford a full test run on every commit.

Key invariants:
    - Never runs pytest itself: it only reads a coverage.json path given on the command line, so
      it is safe to run standalone against whatever the last full test run produced.
    - A floor whose matching files have zero executable statements is skipped, not failed --
      early in the roadmap, most packages are still docstring-only stubs (codingrules 5.4: an
      `__init__.py` with nothing to cover yet is normal, not a violation), and a 0-of-0 floor is
      not a meaningful pass or fail either way.

See Also:
    - .claude/codingrules.md section 14.1 for the floors this enforces.
    - pyproject.toml's [tool.hivemind.coverage_floors] for the table this reads.
"""

from __future__ import annotations

import argparse
import json
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import TypedDict

DEFAULT_COVERAGE_JSON = "coverage.json"  # pytest-cov's own default --cov-report=json filename.
DEFAULT_PYPROJECT = "pyproject.toml"

# The floors table's own key for the repository-wide number; every other key is a package-prefix
# floor aggregated from matching files (see _matches_prefix).
TOTAL_KEY = "total"

__all__ = ["main"]


class _FileSummary(TypedDict):
    """One coverage.json `files.<path>.summary` entry, narrowed to the fields this script sums."""

    num_statements: int
    covered_lines: int
    num_branches: int
    covered_branches: int


def main(argv: Sequence[str] | None = None) -> int:
    """Check every configured coverage floor against a coverage.json report.

    Args:
        argv: Command-line arguments, excluding the program name. ``None`` means use
            ``sys.argv[1:]``.

    Returns:
        0 if every floor (including the total) was met, 1 if any was breached.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Check pyproject.toml's [tool.hivemind.coverage_floors] against a coverage.json "
            "report (produced separately by `pytest --cov-report=json`)."
        )
    )
    parser.add_argument(
        "--coverage-json",
        default=DEFAULT_COVERAGE_JSON,
        help=f"Path to the coverage.json report (default: {DEFAULT_COVERAGE_JSON}).",
    )
    parser.add_argument(
        "--pyproject",
        default=DEFAULT_PYPROJECT,
        help=f"Path to pyproject.toml holding the floors table (default: {DEFAULT_PYPROJECT}).",
    )
    args = parser.parse_args(argv)

    floors = _load_floors(Path(args.pyproject))
    report = _load_coverage_report(Path(args.coverage_json))

    findings: list[str] = []
    for prefix, floor in floors.items():
        if prefix == TOTAL_KEY:
            continue  # Checked separately below, against the report's own repo-wide totals.
        findings.extend(_check_prefix(args.coverage_json, prefix, floor, report))
    findings.extend(_check_total(args.coverage_json, floors.get(TOTAL_KEY), report))

    for finding in findings:
        print(finding)  # scripts/ is one of the two places codingrules section 12 allows print().
    return 1 if any("BELOW FLOOR" in f for f in findings) else 0


def _load_floors(pyproject_path: Path) -> dict[str, int]:
    """Read `[tool.hivemind.coverage_floors]` from `pyproject_path`.

    Args:
        pyproject_path: Path to the workspace root's pyproject.toml.

    Returns:
        The floors table as a plain dict, e.g. `{"common": 95, ..., "total": 85}`.

    Raises:
        SystemExit: The file has no such table -- step 0.2 promised it exists, so a missing table
            is a configuration bug worth stopping for, not a silent 0-floor result.
    """
    with pyproject_path.open("rb") as handle:
        data = tomllib.load(handle)
    try:
        table = data["tool"]["hivemind"]["coverage_floors"]
    except KeyError:
        raise SystemExit(
            f"{pyproject_path}: missing [tool.hivemind.coverage_floors] (see codingrules 14.1)"
        ) from None
    return {str(key): int(value) for key, value in table.items()}


def _load_coverage_report(coverage_json_path: Path) -> dict[str, _FileSummary]:
    """Read a coverage.json report's per-file summaries, plus its overall total under TOTAL_KEY.

    Args:
        coverage_json_path: Path to a `pytest --cov-report=json` output file.

    Returns:
        A dict mapping each covered file's path (posix-style, as coverage.json stores it) to its
        summary, plus one extra entry under `TOTAL_KEY` for the report's own `"totals"` section.
    """
    with coverage_json_path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    files: dict[str, _FileSummary] = {
        path: _narrow_summary(entry["summary"]) for path, entry in data["files"].items()
    }
    files[TOTAL_KEY] = _narrow_summary(data["totals"])
    return files


def _narrow_summary(summary: dict[str, int]) -> _FileSummary:
    """Pull only the four fields this script sums out of a raw coverage.json summary dict."""
    return {
        "num_statements": summary["num_statements"],
        "covered_lines": summary["covered_lines"],
        "num_branches": summary.get("num_branches", 0),
        "covered_branches": summary.get("covered_branches", 0),
    }


def _check_prefix(
    coverage_json_path: str,
    prefix: str,
    floor: int,
    report: dict[str, _FileSummary],
) -> list[str]:
    """Aggregate every file matching `prefix` and report its coverage against `floor`.

    Args:
        coverage_json_path: The report's path, used only to label the finding line.
        prefix: A floor-table key, matched as a path segment (see `_matches_prefix`).
        floor: The minimum acceptable percentage.
        report: `_load_coverage_report`'s result.
    """
    matching = [
        summary
        for path, summary in report.items()
        if path != TOTAL_KEY and _matches_prefix(path, prefix)
    ]
    statements = sum(s["num_statements"] for s in matching)
    branches = sum(s["num_branches"] for s in matching)
    if statements + branches == 0:
        return []  # Nothing to cover yet (see the module docstring's "Key invariants").
    covered = sum(s["covered_lines"] for s in matching) + sum(
        s["covered_branches"] for s in matching
    )
    percent = covered / (statements + branches) * 100
    return [_format_finding(coverage_json_path, prefix, percent, floor)]


def _check_total(
    coverage_json_path: str, floor: int | None, report: dict[str, _FileSummary]
) -> list[str]:
    """Report the coverage.json report's own repository-wide total against the total floor."""
    if floor is None:
        return []  # No total floor configured; nothing to check.
    totals = report[TOTAL_KEY]
    denominator = totals["num_statements"] + totals["num_branches"]
    if denominator == 0:
        return []  # No statements measured at all: nothing to compare against a floor.
    covered = totals["covered_lines"] + totals["covered_branches"]
    percent = covered / denominator * 100
    return [_format_finding(coverage_json_path, TOTAL_KEY, percent, floor)]


def _matches_prefix(path: str, prefix: str) -> bool:
    """Return True if `prefix` appears as a whole path segment of `path`.

    Aggregating "by package prefix" (the roadmap step's wording) is implemented as: does this
    floor's name appear as one of the file's directory components? `common` matches
    `packages/hivemind/src/hivemind/common/...`; `waggle` matches `packages/waggle/src/waggle/...`.
    A floor name with no matching directory anywhere in the repo (for example `adapters`, which
    codingrules 14.1 describes as a category of directories -- backends, transports, stores --
    rather than one literally named `adapters`) simply never matches any file, so its statement
    count is 0 and `_check_prefix` skips it. That is a known limitation of this simple mapping,
    not a bug: revisit it if a floor name is ever added that needs matching more than one
    directory name.
    """
    return prefix in Path(path).parts


def _format_finding(coverage_json_path: str, label: str, percent: float, floor: int) -> str:
    """Format one report line, marked BELOW FLOOR only when the floor was actually breached."""
    status = "BELOW FLOOR" if percent < floor else "ok"
    return (
        f"{coverage_json_path}:0: coverage for '{label}' is {percent:.1f}% "
        f"(floor {floor}%) [{status}]"
    )


if __name__ == "__main__":
    raise SystemExit(main())
