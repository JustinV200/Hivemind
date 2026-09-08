"""Unit tests for scripts/check_fanout.py.

Fits into the Hive:
    Layer: none (tests for a dev-time gate). Exercises check_fanout.main against sample trees
    written to tmp_path, mirroring codingrules 14's "fakes over mocks" preference: real
    directories on disk, not a mocked filesystem.

Key invariants:
    - None: this module holds tests, not behaviour.

See Also:
    - scripts/check_fanout.py, the module under test.
"""

# codingrules 6.2: a sentence-shaped test name is its own docstring, and `assert` is how pytest
# reports failure; scripts/tests/ gets the same S101/D103 exemption packages/*/tests/** has.

from pathlib import Path

import check_fanout
import pytest


def _fill(directory: Path, count: int, suffix: str = ".py") -> Path:
    """Create `count` empty modules named m0..m<count-1> under `directory`."""
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        (directory / f"m{index}{suffix}").write_text("", encoding="utf-8")
    return directory


def test_check_fanout_passes_at_the_limit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    package = _fill(tmp_path / "packages/x/src/x", check_fanout.MODULE_LIMIT)
    (package / "__init__.py").write_text("", encoding="utf-8")

    exit_code = check_fanout.main([str(tmp_path / "packages")])

    assert exit_code == 0
    assert capsys.readouterr().out == ""


def test_check_fanout_flags_a_directory_over_the_limit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    package = _fill(tmp_path / "packages/x/src/x", check_fanout.MODULE_LIMIT + 1)

    exit_code = check_fanout.main([str(tmp_path / "packages")])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert f"{package}:0: directory holds 11 modules (limit 10)" in out


def test_check_fanout_does_not_count_a_sub_package_against_its_parent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A parent at the limit plus a child at the limit is two clean directories, not one over.
    parent = _fill(tmp_path / "packages/x/src/x", check_fanout.MODULE_LIMIT)
    _fill(parent / "child", check_fanout.MODULE_LIMIT)

    exit_code = check_fanout.main([str(tmp_path / "packages")])

    assert exit_code == 0
    assert capsys.readouterr().out == ""


def test_check_fanout_counts_typescript_modules_too(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _fill(tmp_path / "packages/web/src/views", check_fanout.MODULE_LIMIT + 1, suffix=".tsx")

    exit_code = check_fanout.main([str(tmp_path / "packages")])

    assert exit_code == 1
    assert "directory holds 11 modules" in capsys.readouterr().out


def test_check_fanout_ignores_test_trees_and_directories_outside_src(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # codingrules 5.6: tests mirror src and may split by feature; scripts/ is flat by design.
    _fill(tmp_path / "packages/x/tests", check_fanout.MODULE_LIMIT + 5)
    _fill(tmp_path / "packages/x/src/x/tests", check_fanout.MODULE_LIMIT + 5)
    _fill(tmp_path / "scripts", check_fanout.MODULE_LIMIT + 5)

    exit_code = check_fanout.main([str(tmp_path / "packages"), str(tmp_path / "scripts")])

    assert exit_code == 0
    assert capsys.readouterr().out == ""


def test_check_fanout_reports_a_missing_path_instead_of_passing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nowhere"

    exit_code = check_fanout.main([str(missing)])

    assert exit_code == 1
    assert f"{missing}:0: not a directory" in capsys.readouterr().out


def test_main_prints_help_and_exits_cleanly_via_argparse() -> None:
    with pytest.raises(SystemExit) as info:
        check_fanout.main(["--help"])

    assert info.value.code == 0
