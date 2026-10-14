"""Provide `hive swarm invite`: mint a single-use invite for a device and print it once.

`hive swarm --manifest hive.toml invite [--access LEVEL] [--shield TIER] [--label NAME]` is the
operator's first act on a new device (docs/waggle/enrolment.md section 3). It mints one invite
through the enrolment door: a token that admits one device within `[swarm] invite_ttl_minutes`, up
to the access level given (`--access`, else `[swarm] default_access`) and at the Comb Shield tier
the operator vouches for (`--shield`: `meadow`, or `propolis` for a device that already runs a VPN
the Hive will verify and never touches). The `pollen1.` string it prints, on standard output and
alone, so `hive swarm invite | ssh pi 'pollen enrol -'` works, is the only copy there will ever
be: the registry keeps only the token's SHA-256, the trail records the label, the cap, the tier and
the expiry and nothing else, and the sentence around it goes to standard error. The invite names
the address the device will dial and pins the Hive's key and the listener's certificate, so it is
a secret until it is used or expires. Night Veil cannot be asked for: that tier exists only for
Virtual Cells.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the CLI's `swarm` command group. Mounted by
    `hivemind.cli.swarm` as `invite`. Calls into this group's `context`,
    `hivemind.cli.compose.swarm` (the door), `hivemind.cli.tools.context` and `hivemind.swarm`
    only.

Key invariants:
    - The invite string is printed exactly once, on standard output, and appears in no log line,
      error message or trail event.
    - Nothing about the invite but the string is secret, and the string is the only thing on
      standard output.
    - A refusal (Night Veil, a Swarm that is off, a label too long) mints nothing.

See Also:
    - docs/waggle/enrolment.md sections 3.1 and 3.3 for the command and the string it prints.
    - hivemind.swarm.enrolment.door for `mint_invite`.
"""

from __future__ import annotations

from typing import Annotated

import typer

from hivemind.cell import AccessLevel
from hivemind.cli.compose.swarm import build_invite_door
from hivemind.cli.swarm.context import (
    DEFAULT_SHIELD,
    SwarmCliContext,
    cli_context,
    open_registry,
    operator_recorder,
    parse_access,
    parse_shield,
    require_swarm,
)
from hivemind.cli.tools.context import run_or_exit
from hivemind.swarm import MintedInvite

AccessOption = Annotated[
    str | None,
    typer.Option(
        "--access",
        help="The most the device may be granted: read_only, scratch or full "
        "(default: [swarm] default_access). The device gets the lower of this and what it asks.",
    ),
]
ShieldOption = Annotated[
    str,
    typer.Option(
        "--shield",
        help="The Comb Shield tier you vouch for: meadow, or propolis for a device that already "
        "runs a VPN (the Hive verifies it before each lease and never installs or starts one).",
    ),
]
LabelOption = Annotated[
    str | None,
    typer.Option("--label", help="A name for the device, shown by `hive swarm list` (64 chars)."),
]

__all__ = ["invite_command"]


def invite_command(
    ctx: typer.Context,
    access: AccessOption = None,
    shield: ShieldOption = DEFAULT_SHIELD,
    label: LabelOption = None,
) -> None:
    """Mint a single-use invite for a device; the string printed is its only copy."""
    cli_ctx = cli_context(ctx)
    require_swarm(cli_ctx)
    cap = parse_access(access) if access is not None else _default_access(cli_ctx)
    tier = parse_shield(shield)
    door = build_invite_door(cli_ctx.manifest, open_registry(cli_ctx), operator_recorder(cli_ctx))
    # A blank label is no label: `list` would otherwise show an empty name no one can type back.
    minted = run_or_exit(door.mint_invite(cap, tier, (label or "").strip() or None))
    _print_invite(minted)


def _default_access(cli_ctx: SwarmCliContext) -> AccessLevel:
    """The access level an invite carries when `--access` is not given: `[swarm] default_access`."""
    return AccessLevel.from_wire(cli_ctx.manifest.swarm.default_access)


def _print_invite(minted: MintedInvite) -> None:
    """Say what was minted on standard error, then print the string alone on standard output."""
    name = f"{minted.label!r}" if minted.label else "a new device"
    # Standard error carries no secret: who it is for, what it allows and when it lapses.
    typer.echo(
        f"Invite for {name}: access up to {minted.access_level.name}, tier "
        f"{minted.comb_shield.name}, valid once until {minted.expires_at.isoformat()}.\n"
        "On the device run `pollen enrol -` and paste the string below (so it never appears in a "
        "process list).",
        err=True,
    )
    # The one place the string leaves this process; standard output carries it and nothing else.
    typer.echo(minted.invite_string)
