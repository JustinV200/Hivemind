"""Test the `hive` CLI's root command: `--version` and the no-argument help path.

Fits into the Hive:
    Mirrors src/hivemind/cli/app.py (codingrules section 3: tests/unit mirrors src/ one-to-one).
    Drives the typer application the same way an operator's shell would, through
    typer.testing.CliRunner, rather than calling internal functions directly.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.app for the module under test.
"""

from __future__ import annotations

import re
import sys

import pytest
from typer.testing import CliRunner

from hivemind.cli.app import app, main

# The full expected shape of a --version line, e.g. "hive 0.1.0.dev0 (Python 3.12.14 on
# Windows-11-10.0.26100-SP0)". Anchored so a stray extra line would fail the match.
VERSION_LINE_PATTERN = re.compile(r"^hive \S+ \(Python \d+\.\d+\.\d+ on .+\)$")

runner = CliRunner()


def test_hive_version_flag_prints_version_line_and_exits_zero() -> None:
    # Arrange: no state needed, the version comes from the installed package and interpreter.

    # Act
    result = runner.invoke(app, ["--version"])

    # Assert: exits clean and the sole output line matches the documented shape exactly.
    assert result.exit_code == 0
    assert VERSION_LINE_PATTERN.match(result.stdout.strip())


def test_hive_with_no_arguments_prints_help_and_exits_zero() -> None:
    # Arrange: no state needed.

    # Act
    result = runner.invoke(app, [])

    # Assert: a bare `hive` is documented to behave like --help, not like a missing-command error.
    assert result.exit_code == 0
    assert "Usage" in result.stdout


def test_main_runs_app_with_process_argv_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: typer's app() reads sys.argv itself, so main() is only exercisable by patching it,
    # not by passing arguments in directly.
    monkeypatch.setattr(sys, "argv", ["hive", "--version"])

    # Act & Assert: typer/click signal completion via SystemExit, even on the success path.
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
