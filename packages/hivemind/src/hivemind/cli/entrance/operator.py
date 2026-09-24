"""Provide ``hive entrance operator password|add``: the one operator's password, at the Hive Stand.

Brood 1.0 has exactly one operator, and every login at the Hive Entrance is a device key plus that
operator's password (ADR-0033). ``operator password`` sets it the first time (minting the Hive
Stand console's key, wrapped under it, and recording the console device), and afterwards changes
it only on presentation of the current one (re-wrapping the console key in the same step); both
work whether or not ``hive serve`` runs, because a running Entrance reads the operator row afresh
at every login. ``--reset`` is for a password that is lost: it runs only while ``hive serve`` is
stopped (it holds the serve lock), asks for a yes unless ``--yes``, and revokes every device and
session before minting a new console under the new password. Passwords come from a hidden prompt
(a new one typed twice) or, with ``--password-stdin``, one line each: a change reads the current
password, then the new one. ``operator add`` exists and refuses: ``[entrance] operators`` must be
raised above one first, and even then this Brood keeps a single operator credential.

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli.entrance``. Registered by
    ``hivemind.cli.entrance.group``. Calls into ``hivemind.entrance.enrol`` (bootstrap, change,
    reset), ``hivemind.cli.entrance.console`` (the tables, the serve lock) and
    ``hivemind.cli.landing`` (the password).

Key invariants:
    - A reset never runs beside ``hive serve``.
    - No password is taken from an argument or the environment, or printed.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, "One operator, one
      password".
    - hivemind.entrance.enrol.console for what each operation writes.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from hivemind.cli.entrance.console import (
    MANIFEST,
    YES,
    entrance_tables,
    offline_console,
    refusing,
)
from hivemind.cli.landing import (
    CURRENT_PROMPT,
    PASSWORD_STDIN,
    carried_command,
    carried_flag,
    carried_path,
    read_new_password,
    read_password,
)
from hivemind.cli.stores import DEFAULT_MANIFEST, ManifestOption, load_manifest_or_exit
from hivemind.entrance.enrol import (
    bootstrap_operator,
    change_operator_password,
    reset_operator,
)
from hivemind.manifest import HiveManifest
from waggle.clock import SystemClock

VERB = "operator password"  # The refusal line's command name.
RESET_WARNING = (
    "Resetting the operator password revokes every enrolled device and ends every session; "
    "each device enrols again afterwards."
)

app = typer.Typer(
    name="operator",
    help="The operator: the password every login needs (Brood 1.0 has one operator).",
)

__all__ = ["RESET_WARNING", "app", "operator_add_command", "password_command"]


@app.command("password", cls=carried_command(MANIFEST, PASSWORD_STDIN, YES))
def password_command(
    ctx: typer.Context,
    reset: Annotated[
        bool,
        typer.Option("--reset", help="The password is lost: revoke everything (serve stopped)."),
    ] = False,
) -> None:
    """Set the operator password, change it with the current one, or --reset a lost one."""
    manifest = load_manifest_or_exit(carried_path(ctx, MANIFEST) or DEFAULT_MANIFEST)
    from_stdin = carried_flag(ctx, PASSWORD_STDIN)
    if not reset:
        typer.echo(refusing(VERB, lambda: asyncio.run(_set_or_change(manifest, from_stdin))))
        return
    # A reset takes every device away: said plainly, and confirmed unless --yes.
    typer.echo(RESET_WARNING)
    if not carried_flag(ctx, YES) and not typer.confirm("Reset the operator password?"):
        typer.echo("Nothing was reset.", err=True)
        raise typer.Exit(code=1)
    typer.echo(refusing(VERB, lambda: asyncio.run(_reset(manifest, from_stdin))))


@app.command("add")
def operator_add_command(manifest: ManifestOption = DEFAULT_MANIFEST) -> None:
    """Add a second operator: refused unless [entrance] operators is raised above one."""
    loaded = load_manifest_or_exit(manifest)
    allowed = loaded.entrance.operators
    if allowed <= 1:
        reason = "the Hive is single-operator ([entrance] operators = 1)"
    else:
        # Raising the number is the manifest's half; the Entrance still keeps one credential.
        reason = (
            f"[entrance] operators = {allowed}, but this Brood keeps a single operator credential; "
            "a second operator is not supported yet"
        )
    typer.echo(f"hive entrance operator add refused: {reason}.", err=True)
    raise typer.Exit(code=1)


async def _set_or_change(manifest: HiveManifest, from_stdin: bool) -> str:
    """Set the password the first time, or change it with the current one."""
    async with entrance_tables(manifest, SystemClock()) as deps:
        # Latency: one local read; no row means no operator yet, so this is the first set.
        if await deps.store.get_operator() is None:
            new = read_new_password(from_stdin)
            console = await bootstrap_operator(deps, new.get_secret_value())
            return (
                f"Operator password set; the Hive Stand console is {console.id} "
                f"(key fingerprint {console.fingerprint})."
            )
        current = read_password(from_stdin, CURRENT_PROMPT)
        new = read_new_password(from_stdin)
        await change_operator_password(deps, current.get_secret_value(), new.get_secret_value())
        return "Operator password changed; the console key is sealed under the new one."


async def _reset(manifest: HiveManifest, from_stdin: bool) -> str:
    """Reset a lost password under the serve lock: every device out, a new console in."""
    async with offline_console(manifest, SystemClock()) as deps:
        # Asked for only once the lock is held: a running serve refuses before any typing.
        new = read_new_password(from_stdin)
        console = await reset_operator(deps, new.get_secret_value())
    return (
        f"Operator password reset. Every device was revoked; the new Hive Stand console is "
        f"{console.id} (key fingerprint {console.fingerprint}). Enrol each device again."
    )
