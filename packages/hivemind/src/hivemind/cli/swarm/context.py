"""Share what every `hive swarm` command needs: its `--manifest`, its desk and its arguments.

`hive swarm` is the operator's door into the Swarm (the mesh of existing machines the Hive borrows
as Real Cells, each carrying a Pollen Packet, the thin gateway installed on it). `--manifest`
belongs to the group, as it does for `hive tools` and `hive basket`: it comes right after `swarm`
and before the subcommand (`hive swarm --manifest hive.toml list`). The callback only keeps the
path; `cli_context` loads the manifest once a command body runs, so a subcommand's `--help` never
needs one. Every command that changes a device goes through `open_desk`, the operator's desk over
the Hive's own registry, recording its trail events as the actor `human`, exactly as the other
operator groups do. A device is named by its device id, a Cell id or its label (`NodeArgument`),
and the two enums an operator types are parsed here (`parse_access`, `parse_shield`), so the
commands themselves only call the desk and print.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the CLI's `swarm` command group. Imported
    by every module of the group. Calls into `hivemind.cell`, `hivemind.cli.stores`,
    `hivemind.cli.tools.context` (the exit codes the operator groups share), `hivemind.manifest`,
    `hivemind.swarm` and waggle only.

Key invariants:
    - The registry and the trail are opened on the Hive's one database file, each on a connection
      of its own, so a change made here is seen by a running Hive on its next beat.
    - Events are recorded as `human`, the trail's own literal for the person.
    - A name that is not an access level or a tier is a usage error (exit 2); a refusal by the
      Hive (a revoked device, Night Veil) is exit 1 with its stable code.

See Also:
    - hivemind.cli.tools.context and hivemind.cli.basket.context for the group-callback pattern.
    - hivemind.swarm.operator for the desk every change goes through.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer

from hivemind.cell import AccessLevel, CellIdentity, CombShieldLevel
from hivemind.cli.stores import (
    DEFAULT_MANIFEST,
    load_manifest_or_exit,
    open_swarm_registry,
    open_trail,
)
from hivemind.cli.tools.context import EXIT_REFUSED, HUMAN_ACTOR
from hivemind.manifest import HiveManifest
from hivemind.swarm import SwarmRecorder, SwarmRegistry
from hivemind.swarm.operator import OperatorDesk
from waggle.clock import SystemClock

NodeArgument = Annotated[
    str,
    typer.Argument(help="The device: its device id, its Cell id or its label (`hive swarm list`)."),
]

DEFAULT_SHIELD = "meadow"  # The baseline tier; PROPOLIS is for a device that already runs a VPN.

__all__ = [
    "DEFAULT_SHIELD",
    "NodeArgument",
    "SwarmCliContext",
    "cli_context",
    "open_desk",
    "open_registry",
    "operator_recorder",
    "parse_access",
    "parse_shield",
    "require_swarm",
    "swarm_callback",
]

_MANIFEST_HELP = f"The Hive Manifest TOML file (default: {DEFAULT_MANIFEST})."


@dataclass(frozen=True, slots=True)
class SwarmCliContext:
    """The manifest and database every `hive swarm` command shares."""

    manifest: HiveManifest  # The loaded Hive Manifest.
    db: Path  # The SQLite file holding this Hive's swarm registry and trail.


@dataclass(frozen=True, slots=True)
class _Options:
    """The group's own option, as typed, before any file is read."""

    manifest: Path  # `--manifest`.


def swarm_callback(
    ctx: typer.Context,
    manifest: Annotated[Path, typer.Option("--manifest", help=_MANIFEST_HELP)] = DEFAULT_MANIFEST,
) -> None:
    """Keep `--manifest` for whichever `hive swarm` subcommand runs."""
    # Only kept here, loaded by `cli_context` when a command body runs: `hive swarm list --help`
    # must answer on a machine with no manifest to hand, and a callback that loaded it could not.
    ctx.obj = _Options(manifest=manifest)


def cli_context(ctx: typer.Context) -> SwarmCliContext:
    """Load the manifest the group's option names, and resolve the database file.

    Args:
        ctx: The subcommand's own typer context; its parent ran `swarm_callback`.

    Returns:
        The shared manifest and database.

    Raises:
        typer.Exit: Code 2, through `load_manifest_or_exit`, when the manifest is missing or
            fails validation.
    """
    options: _Options = ctx.obj
    loaded = load_manifest_or_exit(options.manifest)
    return SwarmCliContext(manifest=loaded, db=loaded.resolve_path(loaded.hive.db))


def require_swarm(cli_ctx: SwarmCliContext) -> None:
    """Refuse a command that needs a listener when the manifest keeps the Swarm off.

    Args:
        cli_ctx: The shared context.

    Raises:
        typer.Exit: Code 1 when `[swarm] enabled` is false: an invite could not be redeemed and a
            goal could not reach a device, because no Hive built from this manifest listens.
    """
    if not cli_ctx.manifest.swarm.enabled:
        typer.echo(
            "The Swarm is off in this manifest ([swarm] enabled = false): no Hive built from it "
            "listens for devices. Turn it on and run the Hive first.",
            err=True,
        )
        raise typer.Exit(code=EXIT_REFUSED)


def open_registry(cli_ctx: SwarmCliContext) -> SwarmRegistry:
    """Open the Hive's swarm registry on its own connection.

    Args:
        cli_ctx: The shared context.

    Returns:
        A ready registry whose tables exist and are current.
    """
    return open_swarm_registry(cli_ctx.db)


def operator_recorder(cli_ctx: SwarmCliContext) -> SwarmRecorder:
    """Build the writer of the operator's trail events: the Hive's trail, as `human`.

    Args:
        cli_ctx: The shared context.

    Returns:
        A recorder over the Hive's trail, the system clock and the Hive's identity.
    """
    manifest = cli_ctx.manifest
    identity = CellIdentity(
        hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor=HUMAN_ACTOR
    )
    return SwarmRecorder(open_trail(cli_ctx.db), SystemClock(), identity)


def open_desk(cli_ctx: SwarmCliContext) -> OperatorDesk:
    """Open the operator's desk over the Hive's registry, recording as `human`.

    Args:
        cli_ctx: The shared context.

    Returns:
        The desk every `hive swarm` change goes through.
    """
    return OperatorDesk(open_registry(cli_ctx), operator_recorder(cli_ctx))


def parse_access(text: str) -> AccessLevel:
    """Read an access level the operator typed: `read_only`, `scratch` or `full`, any case.

    Args:
        text: The option or argument text.

    Returns:
        The level.

    Raises:
        typer.BadParameter: The text names no access level (exit 2).
    """
    return _by_name(AccessLevel, text, "an access level (read_only, scratch or full)")


def parse_shield(text: str) -> CombShieldLevel:
    """Read a Comb Shield tier the operator typed: `meadow` or `propolis`, any case.

    `night_veil` parses too, so the Hive can refuse it with its reason (a borrowed device is never
    torn down) instead of this layer answering "no such tier" for a tier that does exist.

    Args:
        text: The option or argument text.

    Returns:
        The tier.

    Raises:
        typer.BadParameter: The text names no tier (exit 2).
    """
    return _by_name(CombShieldLevel, text, "a Comb Shield tier (meadow or propolis)")


def _by_name[MemberT: Enum](members: type[MemberT], text: str, what: str) -> MemberT:
    """Find the enum member whose name is `text` in any case, or explain what was expected."""
    try:
        return members[text.strip().upper()]
    except KeyError:
        raise typer.BadParameter(f"{text!r} is not {what}.") from None
