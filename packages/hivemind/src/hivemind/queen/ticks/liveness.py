"""Define WardenLiveness, record_heartbeat and check_liveness: track each attached Warden's pulse.

Roadmap step 3.20's own dispatch map: "Heartbeat (Warden) -> RECORD liveness (last seen, misses
reset); a Warden missing `heartbeat_miss_limit` heartbeats -> MARK_WARDEN_OFFLINE (its tasks are
re-dispatched when it returns or failed after the alarm limit; v0: record and raise an Alarm at the
human)." `record_heartbeat` is the reset half, called from `hivemind.queen.queen.Queen`'s tick for
every received `Heartbeat`; `check_liveness` is the sweep: for every attached Warden with at least
one heartbeat on record, it recomputes how many whole `heartbeat_interval_s` intervals have elapsed
since the last one (never an incremental per-tick counter, so calling it more often than once per
interval never over-counts), and raises one Alarm at the human the moment a Warden first crosses
`heartbeat_miss_limit`. Roadmap step 4.7 adds two more moves that ride the exact same cadence:
`renew_grants_on_heartbeat` extends every live grant a Heartbeat's own Warden holds (a grant is a
lease, roadmap step 4.7: "renewed on the Warden's heartbeat"), and `check_liveness`'s own sweep now
also calls `hivemind.queen.forage.grants.sweep_expired`, so a grant whose lease lapses -- whether
its own Warden went offline or simply stopped renewing it -- returns to the pool the same tick.
`handle_infrastructure_item` (this module's own dispatch point) also reaches `hivemind.queen.
ticks.wax.handle_wax_item` for a `waggle.messages.cell.CellWaxProposed` (roadmap step 4.2a), the
same "ahead of `decide`, so a within-cap case needs no awake episode" shape as its ForageRequest
neighbour. Roadmap step 4.6 adds one more move to the same Heartbeat handling: `handle_heartbeat_
item` now also checks the sending Warden's own reported `ContextTelemetry` against `hivemind.queen.
ticks.context.intervention_for`, and sends an `Intervene(COMPACT)`/`Intervene(HANDOFF)` straight
over that Warden's own link when its context has crossed the threshold -- mirroring `hivemind.
queen.queen.Queen._send_intervene`'s own wire-and-send shape rather than reaching back into
`queen.py` for it (that module is at its own size cap), so the Queen orders context interventions
without an awake episode, exactly like a routine Heartbeat itself.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's ticks
    sub-package. Called by `hivemind.queen.queen.Queen`'s own tick, once per Heartbeat received
    (`record_heartbeat`, `renew_grants_on_heartbeat`) and unconditionally once per tick
    (`check_liveness`). Calls into `hivemind.cell` (HoneyClearance), `hivemind.queen.deps`
    (QueenDeps, WardenLink), `hivemind.queen.forage.grants` (renew_grants_for_warden,
    sweep_expired), `hivemind.queen.human_inbox` (HumanInbox), `hivemind.queen.ticks.wax`
    (handle_wax_item), `hivemind.supervision` (Alarm, AlarmKind, AlarmSeverity, AlarmState) and
    waggle only.

Key invariants:
    - `check_liveness` re-derives `missed_heartbeats` from elapsed wall-clock time on every call,
      never by incrementing a counter per call: calling it more often than
      `deps.heartbeat_interval_s` never inflates the miss count.
    - A newly-offline transition raises exactly one Alarm: the `offline and not current.is_offline`
      guard fires only the tick a Warden first crosses the limit, never on every later check while
      it stays offline.
    - `check_liveness`'s own expiry sweep runs every call, regardless of whether any Warden's own
      liveness changed this tick: a grant's `expires_at` is a wall-clock deadline independent of
      the Warden-by-Warden loop above it.

See Also:
    - .claude/roadmap.md step 3.20's own dispatch map for the Heartbeat/liveness rule this module
      implements; step 4.7 for the grant-lease renewal and expiry it now also drives.
    - hivemind.queen.human_inbox for HumanInbox, where an offline Warden's Alarm lands.
    - hivemind.queen.forage.grants for renew_grants_for_warden and sweep_expired themselves.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from hivemind.cell import HoneyClearance
from hivemind.memory.thresholds import Thresholds
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.forage import grants as forage_grants
from hivemind.queen.human_inbox import HumanInbox
from hivemind.queen.ticks import context as context_tick
from hivemind.queen.ticks import forage as forage_tick
from hivemind.queen.ticks import wax as wax_tick
from hivemind.supervision import Alarm, AlarmKind, AlarmSeverity, AlarmState, to_wire
from hivemind.supervision.attendant import InboxItem
from waggle.envelope import wrap
from waggle.ids import WardenId, new_alarm_id
from waggle.messages.forage import ForageRequest as WireForageRequest
from waggle.messages.supervision import AlarmContext, Heartbeat, Intervene

__all__ = [
    "WardenLiveness",
    "check_liveness",
    "handle_heartbeat_item",
    "handle_infrastructure_item",
    "record_heartbeat",
    "renew_grants_on_heartbeat",
]


@dataclass(frozen=True, slots=True)
class WardenLiveness:
    """One attached Warden's own pulse, as the Queen has observed it."""

    last_heartbeat_at: datetime | None  # None before the first Heartbeat ever arrives.
    missed_heartbeats: int
    is_offline: bool


def record_heartbeat(
    liveness: MutableMapping[WardenId, WardenLiveness], warden_id: WardenId, at: datetime
) -> None:
    """Reset `warden_id`'s own liveness to freshly seen, at `at`.

    Args:
        liveness: The Queen's own warden_id -> WardenLiveness table; mutated in place.
        warden_id: The Warden whose Heartbeat just arrived.
        at: When it was sent (the envelope's own `sent_at`).
    """
    liveness[warden_id] = WardenLiveness(
        last_heartbeat_at=at, missed_heartbeats=0, is_offline=False
    )


async def renew_grants_on_heartbeat(deps: QueenDeps, warden_id: WardenId) -> None:
    """Extend every live grant `warden_id` holds, on its own Heartbeat (roadmap step 4.7).

    A separate async call, not folded into `record_heartbeat`: that function's own callers treat
    it as synchronous liveness bookkeeping, and changing its signature to `async` would touch
    every one of them for a concern (grant leases) this module did not previously have.

    Args:
        deps: The Queen's collaborators; `ledger` and `grant_ttl_s` are what this renews with.
        warden_id: The Warden whose Heartbeat just arrived.
    """
    await forage_grants.renew_grants_for_warden(deps.ledger, deps, warden_id, deps.grant_ttl_s)


async def handle_infrastructure_item(
    deps: QueenDeps,
    wardens: Mapping[WardenId, WardenLink],
    item: InboxItem,
    last_heartbeat: MutableMapping[WardenId, Heartbeat],
    liveness: MutableMapping[WardenId, WardenLiveness],
) -> bool:
    """Handle a Heartbeat, ForageRequest or CellWaxProposed InboxItem; report whether it did any.

    The payload kinds `hivemind.queen.autopilot.table.decide` must never see fall to its own
    `NEEDS_JUDGEMENT` (an unrecognised payload) -- a routine Heartbeat is not a judgement call,
    roadmap step 4.7 requires a ForageRequest within headroom to be granted "with no awake
    episode", and roadmap step 4.2a requires the same for a Warden's own NOTE/CAUTION within its
    per-Cell cap -- so `hivemind.queen.queen.Queen`'s own tick reaches this ahead of `decide` for
    all three, sharing the one dispatch rather than repeating the same `isinstance` checks there.

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden currently attached, keyed by id.
        item: The ordered InboxItem to check.
        last_heartbeat: The Queen's own `warden_id -> Heartbeat` mirror; mutated in place.
        liveness: The Queen's own `warden_id -> WardenLiveness` table; mutated in place.

    Returns:
        True if `item.payload` was a Heartbeat, a ForageRequest or a CellWaxProposed (either way,
        fully handled); False otherwise, so the caller falls through to its own ordinary dispatch.
    """
    if isinstance(item.payload, Heartbeat):
        await handle_heartbeat_item(deps, wardens, item, last_heartbeat, liveness)
        return True
    if isinstance(item.payload, WireForageRequest):
        await forage_tick.handle_forage_request_for_item(deps, wardens, item)
        return True
    return await wax_tick.handle_wax_item(deps, wardens, item)


async def handle_heartbeat_item(
    deps: QueenDeps,
    wardens: Mapping[WardenId, WardenLink],
    item: InboxItem,
    last_heartbeat: MutableMapping[WardenId, Heartbeat],
    liveness: MutableMapping[WardenId, WardenLiveness],
) -> None:
    """Record one Heartbeat InboxItem, renew its own Warden's live grants, and watch its context.

    `hivemind.queen.queen.Queen`'s own tick calls this directly for a received Heartbeat, so the
    four things a Heartbeat causes (mirror it, reset liveness, renew grants, watch context) live
    in one place instead of split across that call site and this module.

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden currently attached, keyed by id; used to send a context Intervene
            straight back to the reporting Warden's own link (roadmap step 4.6).
        item: The ordered InboxItem; `item.payload` must be a `Heartbeat` (the one caller already
            checked this with `isinstance`; `cast` below trusts that rather than re-checking it).
        last_heartbeat: The Queen's own `warden_id -> Heartbeat` mirror; mutated in place.
        liveness: The Queen's own `warden_id -> WardenLiveness` table; mutated in place.
    """
    warden_id = WardenId(item.principal)
    heartbeat = cast(Heartbeat, item.payload)
    last_heartbeat[warden_id] = heartbeat
    record_heartbeat(liveness, warden_id, item.received_at)
    await renew_grants_on_heartbeat(deps, warden_id)
    await _watch_context(deps, wardens, warden_id, heartbeat)


async def _watch_context(
    deps: QueenDeps,
    wardens: Mapping[WardenId, WardenLink],
    warden_id: WardenId,
    heartbeat: Heartbeat,
) -> None:
    """Order compact or handoff on `warden_id`'s own link when its context crosses a threshold.

    Roadmap step 4.6: "The Queen watches Warden telemetry and orders compact or handoff past
    thresholds." No awake episode: the pure rule (`hivemind.queen.ticks.context.intervention_for`)
    already decided, so this only builds the wire message and sends it, the same shape
    `hivemind.queen.queen.Queen._send_intervene` uses for a human- or awake-ordered intervention.
    """
    thresholds = Thresholds(
        compact_at=deps.memory_budget.compact_at,
        handoff_threshold=deps.memory_budget.handoff_threshold,
    )
    intervention = context_tick.intervention_for(heartbeat.telemetry, thresholds)
    if intervention is None:
        return
    link = wardens.get(warden_id)
    if link is None:
        return  # Unreachable: no link to send an order over (mirrors ticks.forage's own rule).
    action, slot = to_wire(intervention)
    message = Intervene(
        action=action,
        subject=None,
        task_id=None,
        slot=slot,
        alarm_id=None,
        reason=intervention.reason,
    )
    await link.transport.send(wrap(message, link.hop, clock=deps.clock))


async def check_liveness(
    deps: QueenDeps,
    wardens: Sequence[WardenLink],
    liveness: MutableMapping[WardenId, WardenLiveness],
    human_inbox: HumanInbox,
) -> None:
    """Mark any attached Warden offline whose Heartbeat has not renewed within the miss limit.

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden currently attached.
        liveness: The Queen's own warden_id -> WardenLiveness table; mutated in place.
        human_inbox: Where a newly-offline Warden's Alarm is escalated (v0: record and raise).
    """
    now = deps.clock.now()
    for link in wardens:
        current = liveness.get(link.warden_id)
        if current is None or current.last_heartbeat_at is None:
            continue  # No Heartbeat received yet; nothing to judge staleness against.
        elapsed_s = (now - current.last_heartbeat_at).total_seconds()
        missed = int(elapsed_s // deps.heartbeat_interval_s)
        offline = missed >= deps.heartbeat_miss_limit
        if missed == current.missed_heartbeats and offline == current.is_offline:
            continue  # Nothing changed since the last check.
        liveness[link.warden_id] = dataclasses.replace(
            current, missed_heartbeats=missed, is_offline=offline
        )
        if offline and not current.is_offline:
            # The transition only: one Alarm per Warden per outage, not one per later check.
            human_inbox.add_alarm(_offline_alarm(deps, link))
    # roadmap step 4.7's own exit criterion: "A Warden whose heartbeat stops has its grant back in
    # the pool after expiry." Unconditional, like the loop above: a grant's own expires_at is a
    # wall-clock deadline independent of any one Warden's liveness state, so this runs every tick
    # regardless of whether any Warden just went offline this time.
    await forage_grants.sweep_expired(deps.ledger, deps)


def _offline_alarm(deps: QueenDeps, link: WardenLink) -> Alarm:
    """Build the Alarm a newly-offline Warden raises at the human (v0's own backstop)."""
    return Alarm(
        id=new_alarm_id(deps.clock),
        kind=AlarmKind.CELL_UNREACHABLE,
        severity=AlarmSeverity.WARNING,
        origin=link.warden_id,
        attempts=0,
        context=AlarmContext(
            task_id=None, cell_id=link.cell.id, worker_id=None, event_id=None, handoff=None
        ),
        detail=f"No heartbeat from Warden {link.warden_id} within the configured miss limit.",
        clearance=HoneyClearance.C1,
        raised_at=deps.clock.now(),
        state=AlarmState.HANDLING,
    )
