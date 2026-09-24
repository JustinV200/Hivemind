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
from collections.abc import Iterator

import pytest
import typer
from typer.testing import CliRunner

from hivemind.cli.app import app, main

# The full expected shape of a --version line, e.g. "hive 0.1.0.dev0 (Python 3.12.14 on
# Windows-11-10.0.26100-SP0)". Anchored so a stray extra line would fail the match.
VERSION_LINE_PATTERN = re.compile(r"^hive \S+ \(Python \d+\.\d+\.\d+ on .+\)$")
SECTION_PATTERN = re.compile(r"\[[a-z_][a-z_.]*\]")  # A manifest section, e.g. [hive].

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


def test_main_makes_stdout_tolerate_unencodable_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cp1252 console must not crash the CLI on an emoji in a task summary."""
    import io

    from hivemind.cli.app import _tolerate_console_encoding

    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", newline="\n")
    monkeypatch.setattr(sys, "stdout", stream)
    _tolerate_console_encoding()
    stream.write("done ✅\n")
    stream.flush()
    assert stream.buffer.getvalue() == b"done ?\n"


def _commands(command: object, path: tuple[str, ...]) -> Iterator[tuple[tuple[str, ...], object]]:
    """Every command under ``command``, with the words that reach it.

    Typer bundles its own click, so a group is known by its ``commands``, not by its class.
    """
    yield path, command
    for name, sub in sorted(getattr(command, "commands", {}).items()):
        yield from _commands(sub, (*path, name))


def _help_texts(command: object) -> Iterator[str]:
    """The command's own help, then each of its parameters' help."""
    yield getattr(command, "help", None) or ""
    for parameter in getattr(command, "params", ()):
        yield getattr(parameter, "help", None) or ""


def test_every_help_text_prints_the_manifest_sections_it_names() -> None:
    # Help names manifest sections as `[hive]`, `[entrance]`; Rich markup once read them as style
    # tags and printed nothing in their place ("refused unless  operators is raised above one").
    root = typer.main.get_command(app)
    named = [
        (path, section)
        for path, command in _commands(root, ())
        for text in _help_texts(command)
        for section in SECTION_PATTERN.findall(text)
    ]

    missing = [
        (path, section)
        for path, section in named
        if section not in runner.invoke(app, [*path, "--help"], terminal_width=400).stdout
    ]

    assert named, "no help text names a manifest section; the pattern has drifted"
    assert missing == []
