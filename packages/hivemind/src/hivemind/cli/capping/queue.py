"""Provide `hive capping queue|show`: read the Capping proposal queue from the Pheromone Trail.

Two commands, each a thin typer layer over `hivemind.pheromone.PheromoneTrail`
(`hivemind.cli.stores.open_trail`): `queue` lists every proposal whose latest `capping.*` event is
not terminal, newest first; `show` prints one proposal's `capping.*` events in order, with their
payload fields. A `hivemind.supervision.capping.CappingGate` keeps its proposal table in the Queen
process's own memory (roadmap step 3.17's v0 shape), so the only place a CLI process -- which never
shares that memory -- can read the queue back is the trail every gate transition already writes
before it counts as complete (codingrules section 12). `track_proposals` rebuilds, per proposal id,
the fields `queue` needs (task id, cell id, tier, latest state) by walking every `capping.*` event
once, oldest first, because only `capping.proposed`'s own payload carries those a later event's
payload does not repeat; `hivemind.cli.capping.sample` (roadmap step 4.11's own audit addition)
reuses it for the same reason, rather than re-walking the trail a second way.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Mounted by `hivemind.cli.capping.app`. Calls into
    `hivemind.pheromone`, `hivemind.supervision.capping` (ProposalState, is_terminal) and
    `hivemind.cli.stores` only.

Key invariants:
    - `queue`'s ordering is always newest-latest-event first: the proposal whose most recent
      `capping.*` event happened most recently sorts first, matching `hive trail tail`'s own
      "recent activity first" instinct for an operator triaging what needs attention.
    - `track_proposals` skips `capping.summary` (a per-tier rollup, not one proposal's own
      transition, codingrules section 12) and any kind this module does not recognise, rather than
      raising: a future `capping.*` kind this CLI has not learned about yet degrades to "not shown
      in the queue", never a crashed command.
    - `show`'s events are printed in the trail's own order (oldest first), matching `hive trail
      tail`'s convention, so a proposal's story reads top-to-bottom the way it happened.

See Also:
    - .claude/codingrules.md section 8.12 for the Capping gate's propose-check-cap-apply-verify
      shape and why nothing lands uncapped.
    - .claude/codingrules.md section 12 for "every state-changing action... writes a
      PheromoneEvent before the action counts as complete", the rule that makes this command
      possible without talking to a live Queen process.
    - hivemind.supervision.capping.gate for CappingGate, the writer of every event this module
      reads.
    - hivemind.cli.capping.sample for the audit sub-command that reuses `track_proposals`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated

import typer
from pydantic import BaseModel, ConfigDict, Field

from hivemind.cli.stores import (
    DEFAULT_MANIFEST,
    DbOption,
    JsonOption,
    ManifestOption,
    open_trail,
    resolve_db,
)
from hivemind.pheromone import MAX_QUERY_LIMIT, PheromoneEvent, PheromoneTrail, TrailQuery
from hivemind.supervision.capping import ProposalState, is_terminal
from waggle.clock import SystemClock

__all__ = [
    "KIND_TO_STATE",
    "ProposalTrack",
    "capping_events",
    "queue_command",
    "show_command",
    "track_proposals",
]

# capping.* kinds this CLI tracks per proposal, mapped to the ProposalState they represent
# (hivemind.supervision.capping.gate records exactly these seven as capping.<name> on a proposal's
# subject_id; capping.summary is a per-tier rollup with no single proposal behind it, so it is
# deliberately absent here -- track_proposals skips whatever this table does not name).
KIND_TO_STATE: Mapping[str, ProposalState] = {
    "capping.proposed": ProposalState.PROPOSED,
    "capping.checked": ProposalState.CHECKING,
    "capping.capped": ProposalState.CAPPED,
    "capping.applied": ProposalState.APPLIED,
    "capping.verified": ProposalState.VERIFIED,
    "capping.rejected": ProposalState.REJECTED,
    "capping.rolled_back": ProposalState.ROLLED_BACK,
}


class _QueueRow(BaseModel):
    """One `hive capping queue` row: a pending proposal's id, task, tier, state and age."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal_id: str = Field(description="The proposal's own id.")
    task_id: str = Field(description="The task this proposal's action serves.")
    tier: str = Field(description="The proposal's declared RiskTier, as recorded.")
    state: str = Field(description="The ProposalState its latest capping.* event reached.")
    age_s: float = Field(description="Seconds since that latest event, from the system clock.")


@dataclass
class ProposalTrack:
    """One proposal's running state while `track_proposals` walks the trail in order."""

    task_id: str = ""
    cell_id: str = ""
    tier: str = ""
    last_kind: str = ""
    # Placeholder only: track_proposals always overwrites this on the very event that first
    # creates a track, before anything ever reads the default back.
    last_at: datetime = field(default_factory=lambda: datetime.min)


def queue_command(
    manifest: ManifestOption = DEFAULT_MANIFEST,
    db: DbOption = None,
    as_json: JsonOption = False,
) -> None:
    """List every proposal whose latest capping.* event is not terminal, newest first."""
    events = asyncio.run(capping_events(open_trail(resolve_db(manifest, db))))
    rows = _queue_rows(track_proposals(events), now=SystemClock().now())
    if as_json:
        typer.echo(json.dumps([row.model_dump(mode="json") for row in rows], indent=2))
        return
    _print_queue_table(rows)


def show_command(
    proposal_id: Annotated[str, typer.Argument(help="A Capping proposal id.")],
    manifest: ManifestOption = DEFAULT_MANIFEST,
    db: DbOption = None,
    as_json: JsonOption = False,
) -> None:
    """Print one proposal's capping.* events, in order, with their payload fields."""
    store = open_trail(resolve_db(manifest, db))
    events = asyncio.run(capping_events(store, subject_id=proposal_id))
    if as_json:
        typer.echo(json.dumps([_event_row(event) for event in events], indent=2))
        return
    for event in events:
        _print_event_line(event)


async def capping_events(
    store: PheromoneTrail, subject_id: str | None = None
) -> tuple[PheromoneEvent, ...]:
    """Query every capping.* event on `store`, oldest first, optionally for one proposal.

    Takes an opened trail rather than a path: `open_trail` runs its own `asyncio.run`, so
    calling it from inside this coroutine nested one `asyncio.run` in another and raised
    "cannot be called from a running event loop" for every real invocation. Opening in the
    sync command body is what every other store command already does.
    """
    # newest_first stays False (the default): oldest-first is what track_proposals needs to see
    # capping.proposed before any later transition, and what `show` prints as-is.
    query = TrailQuery(family="capping", subject_id=subject_id, limit=MAX_QUERY_LIMIT)
    return await store.query(query)


def track_proposals(events: Iterable[PheromoneEvent]) -> dict[str, ProposalTrack]:
    """Fold every capping.* event, oldest first, into one running track per proposal id."""
    tracks: dict[str, ProposalTrack] = {}
    for event in events:
        state = KIND_TO_STATE.get(event.kind)
        if state is None:
            continue  # capping.summary or an unrecognised kind: not one proposal's own state.
        track = tracks.setdefault(event.subject_id, ProposalTrack())
        if event.kind == "capping.proposed":
            # Only this kind's payload carries the task id, cell id and tier; every later
            # transition's payload is ids/counts about the check or outcome, not a repeat of these.
            track.task_id = str(event.payload.get("task_id", ""))
            track.cell_id = str(event.payload.get("cell_id", ""))
            track.tier = str(event.payload.get("tier", ""))
        track.last_kind = event.kind
        track.last_at = event.at
    return tracks


def _queue_rows(tracks: Mapping[str, ProposalTrack], now: datetime) -> tuple[_QueueRow, ...]:
    """Build the queue's rows from `tracks`: non-terminal only, newest latest-event first."""
    pending = [
        (proposal_id, track)
        for proposal_id, track in tracks.items()
        if not is_terminal(KIND_TO_STATE[track.last_kind])
    ]
    pending.sort(key=lambda item: item[1].last_at, reverse=True)
    return tuple(
        _QueueRow(
            proposal_id=proposal_id,
            task_id=track.task_id,
            tier=track.tier,
            state=KIND_TO_STATE[track.last_kind].value,
            age_s=(now - track.last_at).total_seconds(),
        )
        for proposal_id, track in pending
    )


def _print_queue_table(rows: tuple[_QueueRow, ...]) -> None:
    """Print one fixed-width table row per pending proposal."""
    typer.echo(f"{'PROPOSAL':<30}  {'TASK':<30}  {'TIER':<20}  {'STATE':<12}  AGE")
    for row in rows:
        typer.echo(
            f"{row.proposal_id:<30}  {row.task_id:<30}  {row.tier:<20}  {row.state:<12}  "
            f"{row.age_s:.0f}s"
        )


def _event_row(event: PheromoneEvent) -> dict[str, object]:
    """Build one `hive capping show --json` array element from a trail event."""
    return {"at": event.at.isoformat(), "kind": event.kind, "payload": event.payload}


def _print_event_line(event: PheromoneEvent) -> None:
    """Print one `hive capping show` table line: at, kind, then its payload as compact JSON."""
    payload = json.dumps(event.payload, separators=(",", ":"), sort_keys=True)
    typer.echo(f"{event.at.isoformat()}  {event.kind}  {payload}")
