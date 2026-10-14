"""Provide `hive swarm revoke`, `access`, `shield`, `promote` and `demote`: change a device.

The operator's five acts on a device that is already enrolled, each a call to the desk
(`hivemind.swarm.operator`) and one printed sentence. `revoke NODE` ends the device's enrolment for
good (the Hive's next beat closes its link, stops its Warden and refuses its key). `access NODE
LEVEL` sets how much of the device the Hive may touch (`read_only`, `scratch` or `full`), and
`shield NODE TIER` the Comb Shield tier the operator vouches for (`meadow` or `propolis`;
`night_veil` is refused, with the reason, because a borrowed device is never torn down).
`promote NODE` and `demote NODE` ask the Queen to move the device's Warden one rung up or down
the runtime ladder (GATEWAY, COLONIZED, NUC): they write the wish, and the running Hive acts on it.
The Queen also moves devices on her own, and the operator always wins against her: `demote` pins
the device at the rung it lands on (on the bottom rung it pins the device as a gateway and asks
for nothing), so she does not climb back by herself, and `promote` lets the pin go.
All five write the Hive's own database, so a Hive that is running applies them on its next beat and
one that is not applies them when it starts; each records its trail event (`swarm.revoked`,
`swarm.access_changed`, `swarm.shield_changed`) as the actor `human`, except a rung request, which
records nothing until the Queen acts. A command that finds the device already as asked says so and
writes nothing. NODE is a device id, a Cell id or a unique label.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the CLI's `swarm` command group. Mounted by
    `hivemind.cli.swarm` as `revoke`, `access`, `shield`, `promote` and `demote`. Calls into this
    group's `context` and `render`, `hivemind.cli.tools.context` (the error exits) and
    `hivemind.swarm.operator` only.

Key invariants:
    - A command contains no decision of its own: it names the device, asks the desk, prints.
    - A refusal (a revoked device, Night Veil, the end of the ladder, an ambiguous label) exits 1
      with its stable code and has written and recorded nothing.
    - A repeated command is harmless: it prints that nothing changed and records nothing.

See Also:
    - docs/waggle/enrolment.md sections 10 and 11 for what the Hive does after a change.
    - hivemind.swarm.operator.desk for the rules every act follows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import typer

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.cli.swarm.context import (
    NodeArgument,
    cli_context,
    open_desk,
    parse_access,
    parse_shield,
)
from hivemind.cli.swarm.render import describe
from hivemind.cli.tools.context import run_or_exit
from hivemind.swarm import RuntimeLevel, SwarmNode
from hivemind.swarm.operator import NodeChange, OperatorDesk

LevelArgument = Annotated[
    str, typer.Argument(help="The access level: read_only, scratch or full (any case).")
]
TierArgument = Annotated[
    str,
    typer.Argument(
        help="The Comb Shield tier: meadow, or propolis for a device that already runs a VPN. "
        "night_veil is refused: it exists only for Virtual Cells."
    ),
]

__all__ = [
    "access_command",
    "demote_command",
    "promote_command",
    "revoke_command",
    "shield_command",
]


@dataclass(frozen=True, slots=True)
class _Done:
    """An act that ran: the device as it was found, and what the desk did to it."""

    before: SwarmNode  # The device when the operator's words were resolved.
    change: NodeChange  # What the act wrote, and the device after it.


def revoke_command(ctx: typer.Context, node: NodeArgument) -> None:
    """Revoke a device for good: its link closes, its Warden stops and its key is refused."""
    desk = open_desk(cli_context(ctx))
    done = run_or_exit(_revoke(desk, node))
    name = describe(done.change.node)
    if not done.change.is_changed:
        typer.echo(f"{name} was already revoked; nothing changed.")
        return
    typer.echo(
        f"revoked {name}: the running Hive closes its link and stops its Warden on its next "
        "beat. Remove the packet on the device with `pollen uninstall`; enrolling it again takes "
        "a new invite."
    )


def access_command(ctx: typer.Context, node: NodeArgument, level: LevelArgument) -> None:
    """Set how much of a device the Hive may touch: read_only, scratch or full."""
    desk = open_desk(cli_context(ctx))
    wanted = parse_access(level)
    done = run_or_exit(_access(desk, node, wanted))
    name = describe(done.change.node)
    if not done.change.is_changed:
        typer.echo(f"{name} already has access level {wanted.name}; nothing changed.")
        return
    typer.echo(
        f"{name}: access level {done.before.access_level.name} -> {wanted.name}. The running "
        "Hive sends the device its new terms on its next beat; they apply from its next request."
    )


def shield_command(ctx: typer.Context, node: NodeArgument, tier: TierArgument) -> None:
    """Set a device's Comb Shield tier: meadow, or propolis for a device that runs a VPN."""
    desk = open_desk(cli_context(ctx))
    wanted = parse_shield(tier)
    done = run_or_exit(_shield(desk, node, wanted))
    name = describe(done.change.node)
    if not done.change.is_changed:
        typer.echo(f"{name} is already on the {wanted.name} tier; nothing changed.")
        return
    typer.echo(
        f"{name}: tier {done.before.comb_shield.name} -> {wanted.name}. The running Hive sends "
        "the device its new terms on its next beat."
    )


def promote_command(ctx: typer.Context, node: NodeArgument) -> None:
    """Ask for the rung above the device's, and let the Queen take it higher on her own again."""
    _move(ctx, node, promote=True)


def demote_command(ctx: typer.Context, node: NodeArgument) -> None:
    """Ask for the rung below the device's, and pin it there so the Queen does not climb back."""
    _move(ctx, node, promote=False)


def _move(ctx: typer.Context, reference: str, *, promote: bool) -> None:
    """Ask the desk for the adjacent rung and say what was asked of the running Hive."""
    desk = open_desk(cli_context(ctx))
    done = run_or_exit(_ask_rung(desk, reference, promote=promote))
    node = done.change.node
    name = describe(node)
    if not promote and node.level is RuntimeLevel.GATEWAY:
        # Nothing to carry out: a demotion of a gateway only pins it where it is.
        said = "pinned" if done.change.is_changed else "already pinned"
        typer.echo(
            f"{name} is {said} on {node.level.name}, the bottom rung: the Queen will not "
            "colonize it on her own. `hive swarm promote` lets her go."
        )
        return
    asked = node.desired_level.name if node.desired_level is not None else "nothing"
    if not done.change.is_changed:
        typer.echo(f"{name} already has {asked} asked for; nothing changed.")
        return
    held = done.before.ceiling is not None
    tail = (
        " It is pinned there: the Queen will not take it higher on her own."
        if not promote
        else (" Its pin is lifted: the Queen may take it higher on her own." if held else "")
    )
    typer.echo(
        f"{name} is on {node.level.name}; asked for {asked}. Nothing is recorded until the "
        f"running Hive acts: it moves the device's Warden on its next beat.{tail}"
    )


async def _revoke(desk: OperatorDesk, reference: str) -> _Done:
    """Find the device and revoke it."""
    found = await desk.find(reference)
    return _Done(found, await desk.revoke(found))


async def _access(desk: OperatorDesk, reference: str, level: AccessLevel) -> _Done:
    """Find the device and set its access level."""
    found = await desk.find(reference)
    return _Done(found, await desk.set_access(found, level))


async def _shield(desk: OperatorDesk, reference: str, tier: CombShieldLevel) -> _Done:
    """Find the device and set its Comb Shield tier."""
    found = await desk.find(reference)
    return _Done(found, await desk.set_shield(found, tier))


async def _ask_rung(desk: OperatorDesk, reference: str, *, promote: bool) -> _Done:
    """Find the device and ask for the rung above (or below) its own."""
    found = await desk.find(reference)
    return _Done(found, await desk.move(found, promote=promote))
