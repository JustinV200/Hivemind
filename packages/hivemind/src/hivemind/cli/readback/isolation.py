"""Provide `hive cells isolate` and `hive cells lift`: the human's two levers on a Cell's isolation.

Roadmap step 10.6a gives the human two levers the Queen's own decision never pulls: isolate a
Cell on the human's order (the only way the Hive Stand's own lease is ever isolated), and lift an
isolation or the Queen's placement holds on a Cell (ADR-0043). Both are Landing Board routes
(`POST /v1/cells/{cell_id}/isolate` and `/lift`) that need `entrance:steward` and an interactive
device inside its step-up window, so these commands act as the Hive Stand's console over the
running `hive serve`'s loopback listener, exactly as `hive entrance open` does: the console logs
in with the operator password and steps up with it when the route asks. Nothing here touches a
table: the route is the Queen's one isolation path, which records, pauses, cuts egress and taints.

Fits into the Hive:
    Layer 7 (the terminal), inside `hivemind.cli.readback`. Its commands are merged into
    `hive cells` by `hivemind.cli.readback.cells`. Calls into `hivemind.cli.entrance.console`
    (the console session), `hivemind.cli.landing` (SignedIn), `hivemind.cli.stores` (the JSON
    flag) and `hivemind.entrance.routes.isolation` (the route's body and views) only.

Key invariants:
    - Every lever goes through the Landing Board as the console device; a lift with nothing to
      lift, or a Cell the Queen does not know, is the route's refusal, printed, exit 1.
    - A reason travels as the trail's short phrase; the report id, when given, is checked
      against the report id pattern before anything is sent.

See Also:
    - docs/guard/isolation.md for the isolation path and the human's levers.
    - hivemind.entrance.routes.isolation.cells for the two routes.
"""

from __future__ import annotations

from typing import Annotated

import typer

from hivemind.cli.entrance.console import ConsoleCommand, Stand, refusing, run_console
from hivemind.cli.landing import SignedIn
from hivemind.cli.stores import JsonOption
from hivemind.entrance.routes.isolation import CellIsolationView, CellLiftView, IsolateBody

GROUP = "cells"  # The `hive` group these commands answer under, for their refusal line.

app = typer.Typer()

__all__ = ["app", "isolate_command", "lift_command"]

CellArgument = Annotated[str, typer.Argument(help="The Cell's id (cell_...).", show_default=False)]
ReasonOption = Annotated[
    str, typer.Option("--reason", help="Why, as a short phrase for the trail.", show_default=False)
]
ReportOption = Annotated[
    str | None,
    typer.Option(
        "--report", help="The Guard report this answers (guardrep_...); it dates the taint."
    ),
]


def isolate_command(
    ctx: typer.Context,
    cell_id: CellArgument,
    reason: ReasonOption,
    report: ReportOption = None,
    as_json: JsonOption = False,
) -> None:
    """Isolate a Cell on the human's order, the Hive Stand's own included (after a step-up)."""
    # Checked before the password is asked for: a malformed report id is refused, never sent.
    body = refusing("isolate", lambda: IsolateBody(reason=reason, report_id=report), group=GROUP)

    async def isolate(board: SignedIn, _stand: Stand) -> CellIsolationView:
        """Order the isolation on loopback; the console steps up when the route asks."""
        return await board.call("POST", f"/v1/cells/{cell_id}/isolate", body, CellIsolationView)

    view = run_console(ctx, "isolate", isolate, group=GROUP)
    if as_json:
        typer.echo(view.model_dump_json(indent=2))
        return
    for line in _isolation_lines(view):
        typer.echo(line)


def lift_command(ctx: typer.Context, cell_id: CellArgument, as_json: JsonOption = False) -> None:
    """Lift a Cell's isolation and the Queen's holds on it (after a step-up)."""

    async def lift(board: SignedIn, _stand: Stand) -> CellLiftView:
        """Lift on loopback; the console steps up when the route asks."""
        return await board.call("POST", f"/v1/cells/{cell_id}/lift", None, CellLiftView)

    view = run_console(ctx, "lift", lift, group=GROUP)
    if as_json:
        typer.echo(view.model_dump_json(indent=2))
        return
    for line in _lift_lines(view):
        typer.echo(line)


def _isolation_lines(view: CellIsolationView) -> list[str]:
    """What an isolation did, as the operator reads it."""
    if view.already_isolated:
        return [f"Nothing changed: Cell {view.cell_id} was isolated already."]
    lines = [
        f"Isolated Cell {view.cell_id} (cell.isolated {view.event_id}).",
        f"  placement blocked by Cell Wax {view.wax_id}",
        f"  grants revoked: {_listed(view.revoked_grant_ids)}",
        f"  tasks paused: {_listed(view.paused_task_ids)}",
        f"  egress: {view.egress}; memory items tainted: {view.tainted_count}",
    ]
    if view.unacknowledged_task_ids:
        # A bee that did not answer in time is named, never hidden: its pause was not confirmed.
        lines.append(f"  no answer in time from: {_listed(view.unacknowledged_task_ids)}")
    return lines


def _lift_lines(view: CellLiftView) -> list[str]:
    """What a lift did, as the operator reads it; trust is not restored with placement."""
    ended = view.isolated_event_id or "none (only placement holds stood)"
    return [
        f"Lifted Cell {view.cell_id} (cell.isolation_lifted {view.event_id}).",
        f"  isolation ended: {ended}; placement holds released: {view.released_holds}",
        f"  egress: {view.egress}",
        "  Tainted memory stays tainted until a judge clears it; paused tasks stay paused.",
    ]


def _listed(ids: list[str]) -> str:
    """Ids as one comma-separated field, or `none`."""
    return ", ".join(ids) if ids else "none"


# Both act as the console, so both carry --manifest and --password-stdin (ConsoleCommand).
app.command("isolate", cls=ConsoleCommand)(isolate_command)
app.command("lift", cls=ConsoleCommand)(lift_command)
