"""Provide `hive wardens list`: reconstruct every Warden's own state from the Pheromone Trail.

There is no live link into a running `hive run`'s Queen process this phase (v0; the Landing Board,
roadmap phase 10, is where one arrives), so `hive wardens list --manifest hive.toml [--json]`
reconstructs what it can from the trail instead of asking a live `hivemind.queen.queen.Queen`:
every Warden id ever seen in a `warden.*` event, with that event's own latest kind as its state;
the grants issued to it, from `forage.granted`'s own `warden_id` payload field (added in this same
dispatch, `hivemind.queen.dispatcher`'s own module docstring, since nothing previously recorded
that kind); and every sub-bee id seen in a `worker.*` event, with its own latest kind as its state.
No live telemetry (tokens used, current goal, spend) is shown: `ContextTelemetry` travels only on
a live Waggle `Heartbeat`, which this v0 CLI never receives (see this package's own README).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Called by an operator's shell through the `hive`
    console script (`hivemind.cli.app`). Calls into `hivemind.cli.stores` and `hivemind.pheromone`
    only.

Key invariants:
    - v0 has exactly one Warden (the Hive Stand's own) and a `worker.*` event's own payload
      carries no `warden_id` (nothing needed one before this command), so every sub-bee id this
      command has ever seen is listed under every Warden id it has ever seen; exact for the
      one-Warden Hive this phase ships, and flagged here as a limitation a multi-Warden phase
      must close by adding that field.
    - `open_trail` (which runs its own `asyncio.run` internally, `hivemind.cli.stores`'s own
      docstring) is always called from this module's synchronous command body, never from inside
      this module's own `asyncio.run(_read_wardens(...))` call.

See Also:
    - .claude/roadmap.md step 3.21 for this command's own roadmap bullet.
    - hivemind.queen.dispatcher for the `forage.granted` event this command reads `warden_id` from.
    - hivemind.wardens.warden for the `warden.*` kinds this command's own state column mirrors.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import Sequence

import typer
from pydantic import BaseModel, ConfigDict, Field

from hivemind.cli.stores import (
    DEFAULT_MANIFEST,
    JsonOption,
    ManifestOption,
    load_manifest_or_exit,
    open_trail,
)
from hivemind.pheromone import PheromoneEvent, PheromoneTrail, TrailQuery

app = typer.Typer(name="wardens", help="List Wardens, reconstructed from the Pheromone Trail.")

__all__ = ["app"]


class _WardenRow(BaseModel):
    """One reconstructed Warden: its last-seen state, its grants, and its sub-bees."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    warden_id: str = Field(description="The Warden's own id.")
    state: str = Field(description="Its latest `warden.*` event kind.")
    grant_ids: tuple[str, ...] = Field(description="Every `forage.granted` grant id issued to it.")
    sub_bees: tuple[str, ...] = Field(
        description="Every sub-bee id seen so far, as `<worker_id>:<latest worker.* kind>`."
    )


@app.command("list")
def list_command(manifest: ManifestOption = DEFAULT_MANIFEST, as_json: JsonOption = False) -> None:
    """List every Warden this Hive's trail has ever seen, with its grants and sub-bees."""
    loaded = load_manifest_or_exit(manifest)
    # Runs its own asyncio.run internally (module docstring): called here, synchronously, before
    # this command's own asyncio.run(_read_wardens(...)) below ever starts.
    trail = open_trail(loaded.resolve_path(loaded.hive.db))
    rows = asyncio.run(_read_wardens(trail))
    if as_json:
        typer.echo(json.dumps([row.model_dump(mode="json") for row in rows], indent=2))
        return
    _print_table(rows)


async def _read_wardens(trail: PheromoneTrail) -> tuple[_WardenRow, ...]:
    """Reconstruct every Warden row from the trail's own `warden.*`/`forage.granted`/`worker.*`."""
    warden_states = _last_kind_by_subject(await trail.query(TrailQuery(family="warden")))
    grants = _grant_ids_by_warden(
        await trail.query(TrailQuery(family="forage", kind="forage.granted"))
    )
    worker_states = _last_kind_by_subject(await trail.query(TrailQuery(family="worker")))
    sub_bees = tuple(f"{worker_id}:{state}" for worker_id, state in worker_states.items())
    return tuple(
        _WardenRow(
            warden_id=warden_id, state=state, grant_ids=grants.get(warden_id, ()), sub_bees=sub_bees
        )
        for warden_id, state in warden_states.items()
    )


def _last_kind_by_subject(events: Sequence[PheromoneEvent]) -> dict[str, str]:
    """Return each event's own `subject_id` mapped to its latest `kind`.

    `events` is in the trail's own oldest-first order, so a later event's kind always overwrites
    an earlier one recorded for the same subject.
    """
    latest: dict[str, str] = {}
    for event in events:
        latest[event.subject_id] = event.kind
    return latest


def _grant_ids_by_warden(events: Sequence[PheromoneEvent]) -> dict[str, tuple[str, ...]]:
    """Group every `forage.granted` event's own `subject_id` (a grant id) by its `warden_id`."""
    grouped: dict[str, list[str]] = defaultdict(list)
    for event in events:
        warden_id = event.payload.get("warden_id")
        if isinstance(warden_id, str):
            grouped[warden_id].append(event.subject_id)
    return {warden_id: tuple(ids) for warden_id, ids in grouped.items()}


def _print_table(rows: tuple[_WardenRow, ...]) -> None:
    """Print one fixed-width table row per Warden."""
    typer.echo(f"{'WARDEN':<30}  {'STATE':<10}  {'GRANTS':>6}  SUB-BEES")
    for row in rows:
        sub_bees = ", ".join(row.sub_bees) if row.sub_bees else "-"
        typer.echo(f"{row.warden_id:<30}  {row.state:<10}  {len(row.grant_ids):>6}  {sub_bees}")
