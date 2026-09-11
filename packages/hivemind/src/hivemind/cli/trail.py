"""Provide `hive trail`: tail, export and merge the Pheromone Trail.

Three commands, each a thin typer layer over `hivemind.pheromone.PheromoneTrail`
(`hivemind.cli.stores.open_trail`): `tail` prints recent events and, with `--follow`, keeps
printing new ones through `hivemind.pheromone.follow` until Ctrl-C; `export` writes one node's
`TrailSegment` to a file; `merge` reads a `TrailSegment` file back in. No rule about what a segment
is or how a merge behaves lives here; every one of those lives in `hivemind.pheromone` (codingrules
section 2's CLI row: "commands call into subsystem APIs, never contain logic").

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Called by an operator's shell through the `hive`
    console script (`hivemind.cli.app`). Calls into `hivemind.pheromone` and `hivemind.cli.stores`
    only.

Key invariants:
    - `tail`'s line format is fixed: `<at ISO>  <kind>  <subject_id>  <actor>  <payload compact
      JSON>`, one event per line, oldest first.
    - `--follow` exits 0 on Ctrl-C (a `KeyboardInterrupt` around its own `asyncio.run` call), never
      printing a traceback for the interrupt itself.
    - `merge` never raises on a segment that has already been merged; it just prints 0 inserted
      (`hivemind.pheromone.PheromoneTrail.merge_segment`'s own idempotence guarantee).

See Also:
    - hivemind.pheromone.trail.tail for follow, the generator `tail --follow` drives.
    - hivemind.pheromone.trail.protocol for TrailQuery and TrailSegment, the shapes this file
      reads and writes but never redefines.
    - hivemind.cli.stores for open_trail, resolve_db and the shared option annotations.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Annotated

import typer

from hivemind.cli.stores import (
    DEFAULT_MANIFEST,
    DbOption,
    ManifestOption,
    open_trail,
    resolve_db,
)
from hivemind.pheromone import (
    DEFAULT_POLL_INTERVAL_S,
    PheromoneEvent,
    PheromoneTrail,
    TrailQuery,
    TrailSegment,
    follow,
)
from waggle.clock import SystemClock
from waggle.ids import NodeId

app = typer.Typer(name="trail", help="Read and merge the Pheromone Trail.")

__all__ = ["app"]

DEFAULT_TAIL_COUNT = 50  # A screenful without --follow; matches the classic `tail -n 50`.


@app.command("tail")
def tail_command(
    manifest: ManifestOption = DEFAULT_MANIFEST,
    db: DbOption = None,
    n: Annotated[
        int, typer.Option("-n", help="How many recent events to print without --follow.")
    ] = DEFAULT_TAIL_COUNT,
    follow_flag: Annotated[
        bool, typer.Option("--follow", help="Keep printing new events until Ctrl-C.")
    ] = False,
    interval: Annotated[
        float, typer.Option("--interval", help="Seconds between polls with --follow.")
    ] = DEFAULT_POLL_INTERVAL_S,
) -> None:
    """Print recent trail events, oldest first; with --follow, keep printing new ones."""
    trail = open_trail(resolve_db(manifest, db))
    if follow_flag:
        # Ctrl-C is the documented way to stop --follow: exit 0 quietly, no traceback, the same
        # way a human expects `tail -f` to stop.
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(_follow_forever(trail, interval))
        return

    # Read newest_first so `limit` keeps the N most recent events, then print oldest first.
    events = asyncio.run(trail.query(TrailQuery(limit=n, newest_first=True)))
    for event in reversed(events):
        _print_event(event)


async def _follow_forever(trail: PheromoneTrail, interval: float) -> None:
    """Print every existing and future event until the surrounding asyncio.run is interrupted."""
    async for event in follow(trail, SystemClock(), poll_interval_s=interval):
        _print_event(event)


def _print_event(event: PheromoneEvent) -> None:
    """Print one trail line: `<at ISO>  <kind>  <subject_id>  <actor>  <payload compact JSON>`."""
    payload = json.dumps(event.payload, separators=(",", ":"), sort_keys=True)
    typer.echo(
        f"{event.at.isoformat()}  {event.kind}  {event.subject_id}  {event.actor}  {payload}"
    )


@app.command("export")
def export_command(
    node_id: Annotated[str, typer.Argument(help="The node id whose segment to export.")],
    out: Annotated[Path, typer.Argument(help="Where to write the segment JSON.")],
    manifest: ManifestOption = DEFAULT_MANIFEST,
    db: DbOption = None,
) -> None:
    """Export one node's trail segment to OUT and print how many events it holds."""
    trail = open_trail(resolve_db(manifest, db))
    segment = asyncio.run(trail.export_segment(NodeId(node_id)))
    out.write_text(segment.model_dump_json(indent=2), encoding="utf-8")
    typer.echo(f"exported {len(segment.events)} events to {out}")


@app.command("merge")
def merge_command(
    segment: Annotated[
        Path, typer.Argument(help="A TrailSegment JSON file, from `hive trail export`.")
    ],
    manifest: ManifestOption = DEFAULT_MANIFEST,
    db: DbOption = None,
) -> None:
    """Merge SEGMENT into this database's trail and print how many events were inserted."""
    try:
        # SAFETY: top of a CLI command (codingrules section 10): a bad file or a malformed
        # segment becomes one clean stderr line here instead of a traceback.
        parsed = TrailSegment.model_validate_json(segment.read_text(encoding="utf-8"))
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    trail = open_trail(resolve_db(manifest, db))
    inserted = asyncio.run(trail.merge_segment(parsed))
    typer.echo(f"inserted {inserted} events")
