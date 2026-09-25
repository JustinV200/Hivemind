"""Test hivemind.cli.landing.password: a hidden prompt, or one line of stdin per password.

Driven through a tiny typer command and ``CliRunner``, the way a real command reads it, so the
prompt, its confirmation and ``--password-stdin``'s line order are all exercised as an operator
meets them.

Fits into the Hive:
    Mirrors src/hivemind/cli/landing/password.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import typer
from typer.testing import CliRunner

from hivemind.cli.landing import LandingError, read_new_password, read_password

runner = CliRunner()


def _app(from_stdin: bool, change: bool = False) -> typer.Typer:
    """A command that reads a password (a change reads two) and echoes only their lengths."""
    app = typer.Typer()

    @app.command()
    def read() -> None:
        """Read, then echo lengths: a test must never need the value itself."""
        try:
            first = read_password(from_stdin)
            second = read_new_password(from_stdin) if change else None
        except LandingError as exc:
            typer.echo(f"refused: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        lengths = [len(first.get_secret_value())]
        if second is not None:
            lengths.append(len(second.get_secret_value()))
        typer.echo(" ".join(str(length) for length in lengths))
        typer.echo(repr(first))

    return app


def test_stdin_gives_one_password_per_line_in_order() -> None:
    result = runner.invoke(_app(True, change=True), [], input="current\r\nthe new one\n")

    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0] == "7 11"


def test_a_password_is_never_shown_by_its_repr() -> None:
    result = runner.invoke(_app(True), [], input="hunter2-hunter2\n")

    assert "hunter2-hunter2" not in result.output


def test_stdin_with_no_line_left_is_refused_in_a_sentence() -> None:
    result = runner.invoke(_app(True, change=True), [], input="only-one\n")

    assert result.exit_code == 1
    assert "--password-stdin found no line for the new operator password" in result.output


def test_an_empty_password_is_refused() -> None:
    result = runner.invoke(_app(True), [], input="\n")

    assert result.exit_code == 1
    assert "The operator password is empty" in result.output


def test_the_prompt_hides_what_is_typed() -> None:
    result = runner.invoke(_app(False), [], input="typed-at-the-terminal\n")

    assert result.exit_code == 0, result.output
    assert "typed-at-the-terminal" not in result.output
    assert "21" in result.output


def test_a_new_password_is_typed_twice_at_the_terminal() -> None:
    result = runner.invoke(_app(False, change=True), [], input="old\nfresh-one\nfresh-one\n")

    assert result.exit_code == 0, result.output
    assert "3 9" in result.output
