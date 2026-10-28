"""Provide `hive supersede`: move the Hive Stand, or settle a Supersedure a crash left open.

Roadmap 13.2a, ADR-0065. `hive supersede NODE` acts as the Hive Stand's console over the running
`hive serve`'s loopback listener, as `hive cells sting-cut` does: the operator password logs the
console in, the typed phrase `supersede my hive stand` is asked for, and the route steps up and
starts the move through the Queen's door (the human-only point, the preflight, the freeze). The
command prints what started; the copy and the handover go on on the Queen's loop, and the trail
says how it ended.

`hive supersede --settle superseded|aborted` is the operator's word on a Supersedure this node's
trail started and never finished (the Queen crashed or was stopped mid-move), which keeps that
Queen from starting. It runs with no Queen: the operator password must open the console key, the
Queen lock is held, and the outcome is recorded on this node's own trail with the human as actor.
Check the candidate first (docs/runbooks/supersedure.md): `superseded` means it has the Hive.

Fits into the Hive:
    Layer 7 (the terminal), inside `hivemind.cli.supersede`. Registered as `hive supersede` by
    `hivemind.cli.app`. Calls into `hivemind.cli.entrance` (the console, the Queen lock),
    `hivemind.cli.abscond.consent` (re-auth), `hivemind.cli.stores`, `hivemind.entrance.routes.
    isolation` (the route's body and view) and `hivemind.queen.supersedure` only.

Key invariants:
    - Nothing starts without the console's step-up and the typed phrase; nothing is settled
      without the password and the Queen lock.

See Also:
    - docs/runbooks/supersedure.md for the operator's walk-through.
"""

from __future__ import annotations

import asyncio
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer

from hivemind.cli.abscond.consent import reauthenticate
from hivemind.cli.entrance import hold_queen_lock
from hivemind.cli.entrance.console import MANIFEST, Stand, refusing, run_console
from hivemind.cli.landing import SignedIn, read_password
from hivemind.cli.landing.options import PASSWORD_STDIN, carried_flag, carried_path
from hivemind.cli.stores import DEFAULT_MANIFEST, load_manifest_or_exit, open_trail
from hivemind.common.errors import ConflictError
from hivemind.entrance.auth.step_up import BREAK_GLASS_PHRASES, ActionKind
from hivemind.entrance.routes.isolation import SupersedeBody, SupersedeView
from hivemind.guard.policy import OPERATOR_ID
from hivemind.manifest import HiveManifest
from hivemind.queen.supersedure import Recorder, latest, settle
from waggle.clock import SystemClock

GROUP = "supersede"  # The `hive` command these answer under, for their refusal line.
_SETTLE_HOLDER = "hive supersede --settle"


class Verdict(Enum):
    """The operator's word on an unfinished Supersedure."""

    SUPERSEDED = "superseded"
    ABORTED = "aborted"


NodeArgument = Annotated[
    str | None,
    typer.Argument(help="The colonized node to move onto (node_...).", show_default=False),
]
SettleOption = Annotated[
    Verdict | None,
    typer.Option("--settle", help="Settle this node's unfinished Supersedure, with no Queen."),
]

__all__ = ["Verdict", "supersede_command"]


def supersede_command(
    ctx: typer.Context, node: NodeArgument = None, settle_as: SettleOption = None
) -> None:
    """Move the Hive Stand onto NODE (after a step-up, with the typed phrase), or --settle one."""
    if node is not None and settle_as is None:
        _move(ctx, node)
    elif node is None and settle_as is not None:
        _settle(ctx, settle_as)
    else:
        typer.echo("hive supersede: give a NODE to move onto, or --settle, not both.", err=True)
        raise typer.Exit(code=2)


def _move(ctx: typer.Context, node: str) -> None:
    """Order the move as the console, after the phrase (module docstring)."""
    expected = BREAK_GLASS_PHRASES[ActionKind.SUPERSEDURE]

    async def move(board: SignedIn, _stand: Stand) -> SupersedeView:
        """Ask for the phrase once logged in, then order the move on loopback."""
        # Asked after the password, so `--password-stdin` takes the first line and this the next.
        typed = await asyncio.to_thread(typer.prompt, f"Type '{expected}' to confirm")
        body = SupersedeBody(node_id=node, phrase=typed)
        return await board.call("POST", "/v1/hive/supersede", body, SupersedeView)

    view = run_console(ctx, GROUP, move, group=GROUP)
    typer.echo(
        f"Supersedure onto {view.node_id} started (queen.supersedure_started {view.started_id})."
    )
    typer.echo(f"  The Queen is frozen; the candidate must be ready by {view.grace_until}.")
    typer.echo(
        "  `hive trail --kind queen.superseded` (or queen.supersedure_aborted) says how it ended."
    )


def _settle(ctx: typer.Context, verdict: Verdict) -> None:
    """Record the operator's word on this node's unfinished Supersedure (module docstring)."""
    manifest = load_manifest_or_exit(carried_path(ctx, MANIFEST) or DEFAULT_MANIFEST)
    password = refusing(
        GROUP, lambda: read_password(carried_flag(ctx, PASSWORD_STDIN)), group=GROUP
    )
    refusing(GROUP, lambda: asyncio.run(reauthenticate(manifest, password)), group=GROUP)
    db = manifest.resolve_path(manifest.hive.db)
    event_id = refusing(GROUP, lambda: _record(manifest, db, verdict), group=GROUP)
    typer.echo(f"Settled as {verdict.value} ({event_id}).")


def _record(manifest: HiveManifest, db: Path, verdict: Verdict) -> str:
    """Under the Queen lock: find the unfinished Supersedure and record the verdict."""
    with hold_queen_lock(db, _SETTLE_HOLDER):
        trail = open_trail(db)
        record = asyncio.run(latest(trail))
        if record is None or record.outcome is not None:
            raise ConflictError("This node's trail holds no unfinished Supersedure to settle.")
        ids = manifest.hive
        recorder = Recorder(trail, ids.id, ids.node_id, SystemClock())
        superseded = verdict is Verdict.SUPERSEDED
        return asyncio.run(settle(recorder, record, superseded, OPERATOR_ID))
