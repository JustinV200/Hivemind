"""Unit tests for scripts/check_sizes.py.

Fits into the Hive:
    Layer: none (tests for a dev-time gate). Exercises check_sizes.main against sample files
    written to tmp_path, mirroring codingrules 14's "fakes over mocks" preference: a real file on
    disk, not a mocked filesystem.

Key invariants:
    - None: this module holds tests, not behaviour.

See Also:
    - scripts/check_sizes.py, the module under test.
"""

# codingrules 6.2: a sentence-shaped test name is its own docstring, and `assert` is how pytest
# reports failure. pyproject.toml's [tool.ruff.lint.per-file-ignores] already grants S101/D103 to
# packages/*/tests/**; scripts/tests/ is not in that list (roadmap 0.3 was not authorised to add
# to it), so the same exemption is granted per-file here instead.

from pathlib import Path

import check_sizes
import pytest


def _write(tmp_path: Path, name: str, content: str) -> Path:
    """Write `content` to `tmp_path/name`, creating parent directories as needed."""
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def test_check_sizes_passes_on_a_clean_python_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sample = _write(tmp_path, "clean.py", "def f(a, b):\n    return a + b\n")

    exit_code = check_sizes.main([str(sample)])

    assert exit_code == 0
    assert capsys.readouterr().out == ""


def test_check_sizes_flags_a_python_file_over_the_line_limit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    long_source = "x = 1\n" * 301
    sample = _write(tmp_path, "long.py", long_source)

    exit_code = check_sizes.main([str(sample)])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert f"{sample}:1: file is 301 lines (limit 300)" in out


def test_check_sizes_allows_a_test_file_up_to_400_lines(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # codingrules 5.1: test files may reach 400 lines. A file under a "tests" directory gets the
    # wider limit, so 350 lines (over the normal 300 limit) must still pass here.
    sample = _write(tmp_path, "tests/test_something.py", "x = 1\n" * 350)

    exit_code = check_sizes.main([str(sample)])

    assert exit_code == 0
    assert capsys.readouterr().out == ""


def test_check_sizes_flags_a_function_over_the_line_limit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = "\n".join(f"    x{i} = {i}" for i in range(60))
    sample = _write(tmp_path, "long_function.py", f"def f():\n{body}\n    return x0\n")

    exit_code = check_sizes.main([str(sample)])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "function 'f' is" in out
    assert "(limit 50)" in out


def test_check_sizes_flags_a_class_over_the_line_limit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = "\n".join(f"    x{i} = {i}" for i in range(210))
    sample = _write(tmp_path, "long_class.py", f"class C:\n{body}\n")

    exit_code = check_sizes.main([str(sample)])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "class 'C' is" in out
    assert "(limit 200)" in out


def test_check_sizes_flags_a_function_with_too_many_parameters(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sample = _write(tmp_path, "many_params.py", "def f(a, b, c, d, e, f):\n    return a\n")

    exit_code = check_sizes.main([str(sample)])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "function 'f' has 6 parameters (limit 5)" in out


def test_check_sizes_excludes_self_and_cls_from_the_parameter_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Six named parameters plus self would fail if self were counted; it must not be.
    sample = _write(
        tmp_path,
        "method.py",
        "class C:\n    def m(self, a, b, c, d, e):\n        return a\n",
    )

    exit_code = check_sizes.main([str(sample)])

    assert exit_code == 0
    assert capsys.readouterr().out == ""


def test_check_sizes_reports_a_python_syntax_error_instead_of_crashing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sample = _write(tmp_path, "broken.py", "def f(:\n")

    exit_code = check_sizes.main([str(sample)])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "could not parse as Python" in out


def test_check_sizes_flags_a_typescript_file_over_the_line_limit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sample = _write(tmp_path, "long.ts", "const x = 1;\n" * 301)

    exit_code = check_sizes.main([str(sample)])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "file is 301 lines (limit 300)" in out


def test_check_sizes_flags_a_long_typescript_function_via_the_ts_heuristic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = "\n".join(f"  const x{i} = {i};" for i in range(60))
    sample = _write(tmp_path, "long.ts", f"function f() {{\n{body}\n}}\n")

    exit_code = check_sizes.main([str(sample)])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "function 'f' is" in out


def test_check_sizes_skips_the_generated_landing_board_types_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Regardless of content, the generated types.ts is exempt (codingrules section 3, rule 3).
    generated = _write(
        tmp_path,
        "packages/observation-web/src/landing_board/types.ts",
        "const x = 1;\n" * 400,
    )

    exit_code = check_sizes.main([str(tmp_path)])

    assert exit_code == 0
    assert capsys.readouterr().out == ""
    assert generated.exists()


def test_check_sizes_skips_a_vendored_node_modules_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, "node_modules/pkg/index.ts", "const x = 1;\n" * 400)

    exit_code = check_sizes.main([str(tmp_path)])

    assert exit_code == 0
    assert capsys.readouterr().out == ""


def test_main_prints_help_and_exits_cleanly_via_argparse() -> None:
    with pytest.raises(SystemExit) as excinfo:
        check_sizes.main(["--help"])

    assert excinfo.value.code == 0
