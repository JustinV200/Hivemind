"""Test hivemind.cli.landing.options: options carried on the context reach their command.

Fits into the Hive:
    Mirrors src/hivemind/cli/landing/options.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import typer
from typer.testing import CliRunner

from hivemind.cli.landing import (
    PASSWORD_STDIN,
    CarriedOption,
    carried_command,
    carried_flag,
    carried_group,
    carried_path,
    carried_text,
)

runner = CliRunner()

_PROFILE = CarriedOption("hivemind.cli.test_profile", ("--profile",), "A profile.", default="main")
_CA_FILE = CarriedOption("hivemind.cli.test_ca_file", ("--ca-file",), "A CA file.")


def _command_app() -> typer.Typer:
    """One command with three carried options and one of its own."""
    # Plain help, as `hive`'s own app renders it (hivemind.cli.app): with Rich markup, a
    # forced terminal (GitHub Actions sets one) splits an option name with colour codes.
    app = typer.Typer(rich_markup_mode=None)

    @app.command(cls=carried_command(PASSWORD_STDIN, _PROFILE, _CA_FILE))
    def show(ctx: typer.Context, name: str) -> None:
        """Echo every carried value beside the command's own argument."""
        values = (
            name,
            carried_flag(ctx, PASSWORD_STDIN),
            carried_text(ctx, _PROFILE),
            carried_path(ctx, _CA_FILE),
        )
        typer.echo(" ".join(str(value) for value in values))

    return app


def _group_app() -> typer.Typer:
    """A group carrying a switch its subcommand reads."""
    app = typer.Typer(cls=carried_group(PASSWORD_STDIN), rich_markup_mode=None)

    @app.callback()
    def group() -> None:
        """The group itself does nothing."""

    @app.command()
    def inner(ctx: typer.Context) -> None:
        """Echo the group's carried switch."""
        typer.echo(str(carried_flag(ctx, PASSWORD_STDIN)))

    return app


def test_a_commands_carried_options_reach_it() -> None:
    result = runner.invoke(
        _command_app(), ["garden", "--password-stdin", "--profile", "laptop", "--ca-file", "ca.pem"]
    )

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "garden True laptop ca.pem"


def test_carried_options_not_given_read_as_their_defaults() -> None:
    result = runner.invoke(_command_app(), ["garden"])

    assert result.output.strip() == "garden False main None"


def test_a_groups_carried_option_reaches_its_subcommand() -> None:
    given = runner.invoke(_group_app(), ["--password-stdin", "inner"])
    absent = runner.invoke(_group_app(), ["inner"])

    assert given.output.strip() == "True"
    assert absent.output.strip() == "False"


def test_carried_options_appear_in_the_commands_help() -> None:
    result = runner.invoke(_command_app(), ["--help"])

    assert "--password-stdin" in result.output and "--profile" in result.output
