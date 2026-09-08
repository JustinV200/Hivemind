"""Unit tests for scripts/check_coverage_floors.py.

Fits into the Hive:
    Layer: none (tests for a dev-time gate). Builds a small fake pyproject.toml and coverage.json
    per test rather than depending on a real pytest-cov run, so these tests need no pytest to be
    running underneath them (matching the module's own "Key invariants").

Key invariants:
    - None: this module holds tests, not behaviour.

See Also:
    - scripts/check_coverage_floors.py, the module under test.
"""

# codingrules 6.2: a sentence-shaped test name is its own docstring, and `assert` is how pytest
# reports failure. pyproject.toml's [tool.ruff.lint.per-file-ignores] already grants S101/D103 to
# packages/*/tests/**; scripts/tests/ is not in that list (roadmap 0.3 was not authorised to add
# to it), so the same exemption is granted per-file here instead.

import json
from pathlib import Path

import check_coverage_floors
import pytest


def _write_pyproject(tmp_path: Path, floors_toml: str) -> Path:
    """Write a minimal pyproject.toml with just the [tool.hivemind.coverage_floors] table."""
    path = tmp_path / "pyproject.toml"
    path.write_text(f"[tool.hivemind.coverage_floors]\n{floors_toml}\n", encoding="utf-8")
    return path


def _write_coverage_json(
    tmp_path: Path, files: dict[str, dict[str, int]], totals: dict[str, int]
) -> Path:
    """Write a coverage.json shaped like pytest-cov's `--cov-report=json` output."""
    path = tmp_path / "coverage.json"
    data = {
        "files": {p: {"summary": summary} for p, summary in files.items()},
        "totals": totals,
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


_ZERO_BRANCHES = {"num_branches": 0, "covered_branches": 0}


def test_check_coverage_floors_passes_when_every_floor_is_met(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pyproject = _write_pyproject(tmp_path, "common = 95\ntotal = 85\n")
    coverage_json = _write_coverage_json(
        tmp_path,
        files={
            "packages/hivemind/src/hivemind/common/errors.py": {
                "num_statements": 100,
                "covered_lines": 100,
                **_ZERO_BRANCHES,
            }
        },
        totals={"num_statements": 100, "covered_lines": 90, **_ZERO_BRANCHES},
    )

    exit_code = check_coverage_floors.main(
        ["--coverage-json", str(coverage_json), "--pyproject", str(pyproject)]
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "coverage for 'common' is 100.0% (floor 95%) [ok]" in out
    assert "coverage for 'total' is 90.0% (floor 85%) [ok]" in out


def test_check_coverage_floors_fails_when_a_prefix_is_below_its_floor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pyproject = _write_pyproject(tmp_path, "common = 95\ntotal = 85\n")
    coverage_json = _write_coverage_json(
        tmp_path,
        files={
            "packages/hivemind/src/hivemind/common/errors.py": {
                "num_statements": 100,
                "covered_lines": 50,
                **_ZERO_BRANCHES,
            }
        },
        totals={"num_statements": 100, "covered_lines": 90, **_ZERO_BRANCHES},
    )

    exit_code = check_coverage_floors.main(
        ["--coverage-json", str(coverage_json), "--pyproject", str(pyproject)]
    )

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "coverage for 'common' is 50.0% (floor 95%) [BELOW FLOOR]" in out


def test_check_coverage_floors_fails_when_the_total_is_below_its_floor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pyproject = _write_pyproject(tmp_path, "total = 85\n")
    coverage_json = _write_coverage_json(
        tmp_path, files={}, totals={"num_statements": 100, "covered_lines": 10, **_ZERO_BRANCHES}
    )

    exit_code = check_coverage_floors.main(
        ["--coverage-json", str(coverage_json), "--pyproject", str(pyproject)]
    )

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "coverage for 'total' is 10.0% (floor 85%) [BELOW FLOOR]" in out


def test_check_coverage_floors_skips_a_prefix_with_zero_statements(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # No file anywhere has an "adapters" path segment, so this floor has nothing to aggregate and
    # must be silently skipped rather than reported as 0% (see the module's "Key invariants").
    pyproject = _write_pyproject(tmp_path, "adapters = 80\ntotal = 85\n")
    coverage_json = _write_coverage_json(
        tmp_path,
        files={
            "packages/hivemind/src/hivemind/common/errors.py": {
                "num_statements": 10,
                "covered_lines": 10,
                **_ZERO_BRANCHES,
            }
        },
        totals={"num_statements": 10, "covered_lines": 10, **_ZERO_BRANCHES},
    )

    exit_code = check_coverage_floors.main(
        ["--coverage-json", str(coverage_json), "--pyproject", str(pyproject)]
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "adapters" not in out


def test_check_coverage_floors_counts_branch_coverage_in_the_percentage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pyproject = _write_pyproject(tmp_path, "guard = 95\n")
    coverage_json = _write_coverage_json(
        tmp_path,
        files={
            "packages/hivemind/src/hivemind/guard/policy.py": {
                "num_statements": 10,
                "covered_lines": 10,
                "num_branches": 10,
                "covered_branches": 5,
            }
        },
        totals={"num_statements": 10, "covered_lines": 10, **_ZERO_BRANCHES},
    )

    exit_code = check_coverage_floors.main(
        ["--coverage-json", str(coverage_json), "--pyproject", str(pyproject)]
    )

    # (10 covered_lines + 5 covered_branches) / (10 statements + 10 branches) * 100 = 75%.
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "coverage for 'guard' is 75.0% (floor 95%) [BELOW FLOOR]" in out


def test_check_coverage_floors_stops_with_a_clear_error_when_the_floors_table_is_missing(
    tmp_path: Path,
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[project]\nname = 'x'\n", encoding="utf-8")
    coverage_json = _write_coverage_json(
        tmp_path, files={}, totals={"num_statements": 0, "covered_lines": 0, **_ZERO_BRANCHES}
    )

    with pytest.raises(SystemExit) as excinfo:
        check_coverage_floors.main(
            ["--coverage-json", str(coverage_json), "--pyproject", str(pyproject)]
        )

    assert "coverage_floors" in str(excinfo.value)


def test_main_prints_help_and_exits_cleanly_via_argparse() -> None:
    with pytest.raises(SystemExit) as excinfo:
        check_coverage_floors.main(["--help"])

    assert excinfo.value.code == 0
