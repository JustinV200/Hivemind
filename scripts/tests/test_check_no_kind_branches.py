"""Unit tests for scripts/check_no_kind_branches.py.

Fits into the Hive:
    Layer: none (tests for a dev-time gate). Exercises the AST rules and the allowlist directly.

Key invariants:
    - None: this module holds tests, not behaviour.

See Also:
    - scripts/check_no_kind_branches.py, the module under test.
"""

# codingrules 6.2: a sentence-shaped test name is its own docstring, and `assert` is how pytest
# reports failure. pyproject.toml's [tool.ruff.lint.per-file-ignores] already grants S101/D103 to
# packages/*/tests/**; scripts/tests/ is not in that list (roadmap 0.3 was not authorised to add
# to it), so the same exemption is granted per-file here instead.

from pathlib import Path

import check_no_kind_branches
import pytest


def _write(tmp_path: Path, name: str, content: str) -> Path:
    """Write `content` to `tmp_path/name`, creating parent directories as needed."""
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def test_check_no_kind_branches_passes_on_a_capability_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sample = _write(
        tmp_path,
        "packages/hivemind/src/hivemind/workers/roles/forager.py",
        "def run(cell):\n    if cell.capabilities.has_display:\n        pass\n",
    )

    exit_code = check_no_kind_branches.main([str(sample)])

    assert exit_code == 0
    assert capsys.readouterr().out == ""


def test_check_no_kind_branches_flags_an_equality_branch_on_cell_kind(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sample = _write(
        tmp_path,
        "packages/hivemind/src/hivemind/workers/roles/forager.py",
        "def run(cell):\n    if cell.kind == CellKind.REAL:\n        pass\n",
    )

    exit_code = check_no_kind_branches.main([str(sample)])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert f"{sample}:2:" in out
    assert "CellKind" in out


def test_check_no_kind_branches_flags_an_is_comparison(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sample = _write(
        tmp_path,
        "packages/hivemind/src/hivemind/workers/roles/forager.py",
        "def run(kind):\n    if kind is CellKind.VIRTUAL:\n        pass\n",
    )

    exit_code = check_no_kind_branches.main([str(sample)])

    assert exit_code == 1


def test_check_no_kind_branches_flags_a_match_on_dot_kind(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = "def run(cell):\n    match cell.kind:\n        case _:\n            pass\n"
    sample = _write(tmp_path, "packages/hivemind/src/hivemind/workers/roles/forager.py", source)

    exit_code = check_no_kind_branches.main([str(sample)])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "matches on .kind" in out


def test_check_no_kind_branches_allows_placement_to_branch_on_kind(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sample = _write(
        tmp_path,
        "packages/hivemind/src/hivemind/queen/placement/choose.py",
        "def choose(cell):\n    if cell.kind == CellKind.REAL:\n        pass\n",
    )

    exit_code = check_no_kind_branches.main([str(sample)])

    assert exit_code == 0
    assert capsys.readouterr().out == ""


def test_check_no_kind_branches_allows_the_undertaker_to_branch_on_kind(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sample = _write(
        tmp_path,
        "packages/hivemind/src/hivemind/workers/roles/undertaker.py",
        "def clean_up(cell):\n    if cell.kind == CellKind.REAL:\n        pass\n",
    )

    exit_code = check_no_kind_branches.main([str(sample)])

    assert exit_code == 0
    assert capsys.readouterr().out == ""


def test_check_no_kind_branches_allows_cell_package_to_branch_on_kind(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sample = _write(
        tmp_path,
        "packages/hivemind/src/hivemind/cell/models.py",
        "def describe(cell):\n    if cell.kind == CellKind.REAL:\n        pass\n",
    )

    exit_code = check_no_kind_branches.main([str(sample)])

    assert exit_code == 0
    assert capsys.readouterr().out == ""


def test_check_no_kind_branches_exempts_test_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sample = _write(
        tmp_path,
        "packages/hivemind/tests/unit/workers/roles/test_forager.py",
        "def test_x(cell):\n    assert cell.kind == CellKind.REAL\n",
    )

    exit_code = check_no_kind_branches.main([str(sample)])

    assert exit_code == 0
    assert capsys.readouterr().out == ""
