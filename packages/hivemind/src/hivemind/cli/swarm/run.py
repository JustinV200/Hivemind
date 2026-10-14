"""Provide `hive swarm run`: run a goal that can be placed on one enrolled device and nowhere else.

`hive swarm --manifest hive.toml run NODE "GOAL" [--clearance C1] [--timeout 120]` is `hive run`
with the goal pinned to a device's Cell (NODE is a device id, a Cell id or a unique label). It
builds a Hive from the manifest, exactly as `hive run` does, and that Hive starts the swarm
listener, so the device's Pollen Packet (the thin gateway on it) redials it and the Hive's swarm
gate gives the device a Warden. Once the Warden is attached the goal is submitted with a capability
ceiling that names `cell:real:<the device's Cell>` and not `cell:hive_stand`, which is what makes
the Queen's placement choose that device or nothing (`hivemind.cli.swarm.pinned`), and the
run is followed and reported like `hive run`'s: one line per event of interest, then the verdict,
and the same exit codes (0 succeeded, 1 failed, 2 timed out).

It is the in-process path only. `hive run --remote` cannot pin a goal to a Cell: the Landing Board
takes a goal's text, budget, tier and clearance, and derives the goal's ceiling from the
submitting device's approved capabilities, so there is nowhere to name a device's Cell. Because the
Hive it builds binds the swarm listener's port, stop any other `hive run` or `hive serve` on the
same manifest first.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the CLI's `swarm` command group. Mounted by
    `hivemind.cli.swarm` as `run`. Calls into this group's `context`, `hivemind.cell`,
    `hivemind.cli.compose` (build_hive, run_hive, GoalRun), `.pinned` (the pinned runner),
    `hivemind.cli.run` (the printers and options it shares with `hive run`),
    `hivemind.cli.tools.context`, `hivemind.swarm` and waggle only.

Key invariants:
    - The goal's ceiling holds exactly one Real Cell, the named device's, so it can be placed
      nowhere else; a revoked device, an unknown name or a Swarm that is off is refused before any
      Hive is built.
    - Like `hive run`, no failure leaves this command as a traceback: the body's one broad catch
      (codingrules section 10's third allowed site) turns it into a line on standard error.
    - The Hive is run inside `run_hive`, which stops everything it started and releases every
      lease, the device's included, so the device is left as it was found.

See Also:
    - tests/e2e/test_swarm_level0.py for the same pin on a scripted goal, and
      tests/e2e/test_swarm_cli.py for this command against a real packet.
    - hivemind.cli.run for `hive run`, whose output and exit codes this follows.
"""

from __future__ import annotations

import asyncio
import os
from typing import Annotated

import typer

from hivemind.cell import HoneyClearance
from hivemind.cli.compose import GoalReport, Hive, build_hive, run_hive
from hivemind.cli.compose.hive import GoalRun
from hivemind.cli.run import (
    DEFAULT_CLEARANCE,
    DEFAULT_TIMEOUT_S,
    ClearanceOption,
    TimeoutOption,
    describe_failure,
    print_event,
    print_summary,
)
from hivemind.cli.swarm.context import NodeArgument, cli_context, open_desk, require_swarm
from hivemind.cli.swarm.pinned import run_goal_on_device
from hivemind.cli.tools.context import EXIT_REFUSED, run_or_exit
from hivemind.swarm import SwarmNode
from waggle.clock import SystemClock

GoalArgument = Annotated[str, typer.Argument(help="The goal text, exactly as the human stated it.")]
EXIT_TIMED_OUT = 2  # The same code `hive run` exits with when the goal ran out of time.

__all__ = ["run_command"]


def run_command(
    ctx: typer.Context,
    node: NodeArgument,
    goal: GoalArgument,
    clearance: ClearanceOption = DEFAULT_CLEARANCE,
    timeout: TimeoutOption = DEFAULT_TIMEOUT_S,
) -> None:
    """Run GOAL on NODE: a goal that can be placed only on that device's Cell.

    Builds a Hive from the manifest, as `hive run` does; the Hive starts the swarm listener, so
    the device's packet reconnects to it. Stop any other `hive run` or `hive serve` on this
    manifest first, since they hold the listener's port. There is no --remote: a remote goal
    cannot name a Cell, because the Landing Board derives a goal's ceiling from the submitting
    device's approved capabilities.
    """
    cli_ctx = cli_context(ctx)
    require_swarm(cli_ctx)
    device = run_or_exit(open_desk(cli_ctx).find_live(node))
    # build_hive must run outside any event loop: it opens its stores, each of which runs its own
    # asyncio.run, exactly as `hive run` has to (hivemind.cli.run's own note).
    hive = build_hive(cli_ctx.manifest, environ=os.environ, clock=SystemClock())
    run = GoalRun(HoneyClearance[clearance], timeout, print_event)
    try:
        report = asyncio.run(_run(hive, device, goal, run))
    except Exception as exc:
        # SAFETY: the command body's own broad catch (codingrules section 10's third allowed
        # site): a run that fails for any reason, a device that never reconnects included, ends
        # in one clean stderr line and exit 1, never a traceback on an operator's terminal.
        typer.echo(f"hive swarm run failed: {describe_failure(exc)}", err=True)
        raise typer.Exit(code=EXIT_REFUSED) from exc
    print_summary(report, as_json=False)
    if report.timed_out:
        raise typer.Exit(code=EXIT_TIMED_OUT)
    raise typer.Exit(code=0 if report.succeeded else EXIT_REFUSED)


async def _run(hive: Hive, device: SwarmNode, goal: str, run: GoalRun) -> GoalReport:
    """Start the Hive, wait for the device to reconnect, and run the pinned goal to its end."""
    async with run_hive(hive):
        return await run_goal_on_device(hive, device, goal, run)
