"""Define WardenLiveness, record_heartbeat and check_liveness: track each attached Warden's pulse.

Roadmap step 3.20's own dispatch map: "Heartbeat (Warden) -> RECORD liveness (last seen, misses
reset); a Warden missing `heartbeat_miss_limit` heartbeats -> MARK_WARDEN_OFFLINE (its tasks are
re-dispatched when it returns or failed after the alarm limit; v0: record and raise an Alarm at the
human)." `record_heartbeat` is the reset half, called from `hivemind.queen.queen.Queen`'s tick for
every received `Heartbeat`; `check_liveness` is the sweep: for every attached Warden with at least
one heartbeat on record, it recomputes how many whole heartbeat intervals have elapsed since the
last one (never an incremental per-tick counter, so calling it more often than once per interval
never over-counts), and raises one Alarm at the human the moment a Warden first crosses
`heartbeat_miss_limit`. Each Warden is judged against its own interval: the one its newest
Heartbeat declared (`Heartbeat.interval_s`), never less than the manifest's `heartbeat_interval_s`.
Wardens do not share one cadence -- a Virtual Cell's in-Cell Warden beats every 15 s while the Hive
Stand's keeps the manifest's, and a Swarm device, enrolled rather than provisioned, keeps whatever
its own configuration and battery allow -- and the Queen sets none of them, so judging all of them
by the manifest raised a false `CELL_UNREACHABLE` for every Warden slower than it; the manifest's
interval stays the floor, the least grace any Warden gets. A Heartbeat is proof of life only as of
when it was sent (its envelope's
own `sent_at`), and a real run (2026-09-24) showed why that matters: a tick stalled on a Virtual
Cell provision left a backlog of Heartbeats that the old reset, heard one per tick, turned into
one "back online" per stale Heartbeat and so one fresh Alarm each, about a Warden heartbeating the
whole time. So `record_heartbeat` now advances `last_heartbeat_at` (never backwards) but brings a
Warden back online only on a Heartbeat still inside the miss limit when it is recorded, and
`check_liveness` first folds in the newest Heartbeat each link has delivered but the tick has not
handled yet (`hivemind.queen.inbox.links.LinkReaders.heard`): liveness is the Warden's, never the
Queen's own attention. Roadmap step 4.7 adds two more moves that ride the exact same cadence:
`renew_grants_on_heartbeat` extends every live grant a Heartbeat's own Warden holds (a grant is a
lease, roadmap step 4.7: "renewed on the Warden's heartbeat"), and `check_liveness`'s own sweep now
also calls `hivemind.queen.forage.grants.sweep_expired`, so a grant whose lease lapses -- whether
its own Warden went offline or simply stopped renewing it -- returns to the pool the same tick.
`handle_infrastructure_item` (this module's own dispatch point) also reaches `hivemind.queen.
ticks.wax.handle_wax_item` for a `waggle.messages.cell.CellWaxProposed` (roadmap step 4.2a), the
same "ahead of `decide`, so a within-cap case needs no awake episode" shape as its ForageRequest
neighbour, and, just before it, `hivemind.queen.ticks.honey.handle_honey_item` for a
`NectarDeposit` or `HoneyQuery` (roadmap step 7.8: the Honey Store's routine traffic). Roadmap
step 4.6 adds one more move to the same Heartbeat handling: `handle_heartbeat_item` now also
checks the sending Warden's own reported `ContextTelemetry` against `hivemind.queen.ticks.context.
intervention_for`, and sends an `Intervene(COMPACT)`/`Intervene(HANDOFF)` straight
over that Warden's own link when its context has crossed the threshold -- mirroring `hivemind.
queen.queen.Queen._send_intervene`'s own wire-and-send shape rather than reaching back into
`queen.py` for it (that module is at its own size cap), so the Queen orders context interventions
without an awake episode, exactly like a routine Heartbeat itself. A Warden whose Cell the Hive
itself holds paused is never judged at all: an Overwintered Cell (ADR-0029) is `docker pause`d
or its VM stopped, so its Warden cannot beat, and a real Docker run (2026-09-25) showed a Cell
that heartbeated just before its pause drawing `CELL_UNREACHABLE` about 40 s into it, before the
link's own keepalive dropped the paused Cell at about 47 s. `check_liveness` reads the
held Cells from the lifecycle's own dormant list (`QueenDeps.dormant_cell_source`, or the static
`dormant_cells`), marks each such Warden `held` and skips it, and when the hold ends (the Cell
resumed) counts its misses from that moment, never from its last pre-pause beat. A snapshot's
freeze is announced by the Warden itself instead, as a longer declared interval
(`hivemind.wardens.ticks.heartbeat.announce_freeze`); a Cell that falls silent for any other
reason is judged exactly as before.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's ticks
    sub-package. Called by `hivemind.queen.queen.Queen`'s own tick, once per Heartbeat received
    (`record_heartbeat`, `renew_grants_on_heartbeat`) and unconditionally once per tick
    (`check_liveness`, with the newest Heartbeat each link has delivered). Calls into
    `hivemind.cell` (HoneyClearance), `hivemind.queen.chat` (post_alarm: an offline Warden's
    Alarm reaches the chat, roadmap step 10.5),
    `hivemind.queen.deps` (QueenDeps, WardenLink), `hivemind.queen.forage.grants`
    (renew_grants_for_warden, sweep_expired), `hivemind.queen.human_inbox` (HumanInbox),
    `hivemind.queen.ticks.honey` (handle_honey_item), `hivemind.queen.ticks.wax`
    (handle_wax_item), `hivemind.supervision` (Alarm, AlarmKind, AlarmSeverity, AlarmState) and
    waggle only.

Key invariants:
    - `check_liveness` re-derives `missed_heartbeats` from elapsed wall-clock time on every call,
      never by incrementing a counter per call: calling it more often than a Warden's interval
      never inflates the miss count.
    - A Warden's interval is the one its newest Heartbeat declared, floored at the manifest's
      `heartbeat_interval_s`: no Warden is ever judged more strictly than the manifest says.
    - A newly-offline transition raises exactly one Alarm: the `offline and not current.is_offline`
      guard fires only the tick a Warden first crosses the limit, never on every later check while
      it stays offline.
    - One Alarm per outage, in every ordering of records and checks: only `check_liveness` ever
      marks a Warden offline (raising that outage's one Alarm), and only a Heartbeat still inside
      the miss limit when `record_heartbeat` sees it brings one back online. A backlog of stale
      Heartbeats, however it is heard or interleaved, can therefore never end an outage and so
      never start another; it only moves `last_heartbeat_at` forward.
    - `last_heartbeat_at` never moves backwards: an older Heartbeat heard after a newer one adds
      nothing.
    - A held Warden (its Cell Overwintered) is never marked offline and raises no Alarm while the
      hold lasts; once it ends, its misses count from the end of the hold, not from before it.
    - `check_liveness`'s own expiry sweep runs every call, regardless of whether any Warden's own
      liveness changed this tick: a grant's `expires_at` is a wall-clock deadline independent of
      the Warden-by-Warden loop above it.

See Also:
    - .claude/roadmap.md step 3.20's own dispatch map for the Heartbeat/liveness rule this module
      implements; step 4.7 for the grant-lease renewal and expiry it now also drives.
    - hivemind.queen.human_inbox for HumanInbox, where an offline Warden's Alarm lands.
    - hivemind.queen.inbox.links for LinkReaders, whose `heard()` check_liveness folds in.
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
from hivemind.queen.chat import post_alarm
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.forage import grants as forage_grants
from hivemind.queen.human_inbox import HumanInbox
from hivemind.queen.inbox import Pulse
from hivemind.queen.ticks import context as context_tick
from hivemind.queen.ticks import forage as forage_tick
from hivemind.queen.ticks import honey as honey_tick
from hivemind.queen.ticks import wax as wax_tick
from hivemind.supervision import Alarm, AlarmKind, AlarmSeverity, AlarmState, to_wire
from hivemind.supervision.attendant import InboxItem
from waggle.envelope import wrap
from waggle.ids import CellId, WardenId, new_alarm_id
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
    interval_s: float | None = None  # What its newest Heartbeat declared; None before the first.
    held: bool = False  # Its Cell is paused by the Hive itself: its silence is never judged.
    hold_ended_at: datetime | None = None  # When its latest hold ended; misses count from here.


# What a Warden with no liveness row yet looks like to `record_heartbeat` (attach adds one first).
_NEVER_HEARD = WardenLiveness(last_heartbeat_at=None, missed_heartbeats=0, is_offline=False)


def record_heartbeat(
    deps: QueenDeps,
    liveness: MutableMapping[WardenId, WardenLiveness],
    warden_id: WardenId,
    pulse: Pulse,
) -> None:
    """Advance `warden_id`'s own liveness to a Heartbeat's `pulse`, never backwards.

    A Heartbeat still inside the miss limit now proves the Warden alive: it is back online. One
    already older than the miss limit (a backlog heard late) proves nothing about now: it may move
    `last_heartbeat_at` forward but leaves `is_offline` exactly as it was, so only
    `check_liveness` ever marks a Warden offline, once per outage (module docstring). The newest
    Heartbeat's declared interval is the one the Warden is judged by from then on.

    Args:
        deps: The Queen's collaborators; `clock`, `heartbeat_interval_s` (the floor) and
            `heartbeat_miss_limit` decide whether the pulse is still inside the miss limit.
        liveness: The Queen's own warden_id -> WardenLiveness table; mutated in place.
        warden_id: The Warden whose Heartbeat was heard.
        pulse: When it was sent (the envelope's own `sent_at`) and the interval it declared.
    """
    current = liveness.get(warden_id, _NEVER_HEARD)
    previous = current.last_heartbeat_at
    interval_s: float | None
    if previous is None or pulse.sent_at > previous:
        newest, interval_s = pulse.sent_at, pulse.interval_s  # Its own cadence now rules.
    else:
        newest, interval_s = previous, current.interval_s  # An older one changes neither.
    now = deps.clock.now()
    missed_by_pulse = _missed_since(_judged_interval_s(deps, pulse.interval_s), now, pulse.sent_at)
    if missed_by_pulse >= deps.heartbeat_miss_limit:
        # Stale on arrival, by its own cadence: never back online on it, never backwards either.
        liveness[warden_id] = dataclasses.replace(
            current, last_heartbeat_at=newest, interval_s=interval_s
        )
        return
    # A replace, not a fresh row: a hold on its Cell outlives any one Heartbeat.
    liveness[warden_id] = dataclasses.replace(
        current,
        last_heartbeat_at=newest,
        missed_heartbeats=_missed_since(_judged_interval_s(deps, interval_s), now, newest),
        is_offline=False,
        interval_s=interval_s,
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
    """Handle a Heartbeat, ForageRequest, Honey or CellWaxProposed item; report whether it did.

    The payload kinds `hivemind.queen.autopilot.table.decide` must never see fall to its own
    `NEEDS_JUDGEMENT` (an unrecognised payload) -- a routine Heartbeat is not a judgement call,
    roadmap step 4.7 requires a ForageRequest within headroom to be granted "with no awake
    episode", roadmap step 4.2a requires the same for a Warden's own NOTE/CAUTION within its
    per-Cell cap, and roadmap step 7.8's Nectar deposits and Honey queries are routine traffic
    (`hivemind.queen.ticks.honey`) -- so `hivemind.queen.queen.Queen`'s own tick reaches this
    ahead of `decide` for all of them, sharing the one dispatch rather than repeating the same
    `isinstance` checks there.

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden currently attached, keyed by id.
        item: The ordered InboxItem to check.
        last_heartbeat: The Queen's own `warden_id -> Heartbeat` mirror; mutated in place.
        liveness: The Queen's own `warden_id -> WardenLiveness` table; mutated in place.

    Returns:
        True if `item.payload` was a Heartbeat, a ForageRequest, a NectarDeposit, a HoneyQuery or
        a CellWaxProposed (either way, fully handled); False otherwise, so the caller falls
        through to its own ordinary dispatch.
    """
    if isinstance(item.payload, Heartbeat):
        await handle_heartbeat_item(deps, wardens, item, last_heartbeat, liveness)
        return True
    if isinstance(item.payload, WireForageRequest):
        await forage_tick.handle_forage_request_for_item(deps, wardens, item)
        return True
    if await honey_tick.handle_honey_item(deps, wardens, item):
        return True  # Roadmap step 7.8: a Nectar deposit chunk or a Honey query, answered here.
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
    five things a Heartbeat causes (mirror it, record liveness, hand it to `deps.on_heartbeat`,
    renew grants, watch context) live in one place instead of split across that call site and
    this module.

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
    # The item's received_at is the envelope's own sent_at (hivemind.queen.inbox.to_inbox_item).
    record_heartbeat(deps, liveness, warden_id, Pulse(item.received_at, heartbeat.interval_s))
    # A Heartbeat never reaches the trail, so the Hive Entrance's telemetry view hears of it
    # here, once she has recorded it; the hook only hands the sample on (QueenDeps.on_heartbeat).
    if deps.on_heartbeat is not None:
        deps.on_heartbeat(warden_id, heartbeat, item.received_at)
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
    # An order this Warden never receives is moot the same way a missing link is just above:
    # nothing here depends on delivery, and a Warden gone for good surfaces through the offline
    # check below regardless.
    await link.send(wrap(message, link.hop, clock=deps.clock))


async def check_liveness(
    deps: QueenDeps,
    wardens: Sequence[WardenLink],
    liveness: MutableMapping[WardenId, WardenLiveness],
    human_inbox: HumanInbox,
    heard: Mapping[WardenId, Pulse] | None = None,
) -> None:
    """Mark any attached Warden offline whose Heartbeat has not renewed within the miss limit.

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden currently attached.
        liveness: The Queen's own warden_id -> WardenLiveness table; mutated in place.
        human_inbox: Where a newly-offline Warden's Alarm is escalated (v0: record and raise).
        heard: The newest Heartbeat's Pulse each link has delivered, handled by the tick or not
            (`hivemind.queen.inbox.links.LinkReaders.heard`); None judges by `liveness` alone.
    """
    now = deps.clock.now()
    held = await _held_cells(deps)
    for link in wardens:
        # A Heartbeat heard while the tick was busy is still the Warden's own proof of life.
        pulse = heard.get(link.warden_id) if heard is not None else None
        if pulse is not None:
            record_heartbeat(deps, liveness, link.warden_id, pulse)
        if _hold(liveness, link.warden_id, link.cell.id in held, now):
            continue  # Paused by the Hive itself: its silence is her own doing, never an Alarm.
        if _crossed_miss_limit(deps, liveness, link.warden_id, now):
            # The transition only: one Alarm per Warden per outage, not one per later check;
            # it reaches the human in the chat too (roadmap step 10.5, ADR-0040).
            alarm = _offline_alarm(deps, link)
            human_inbox.add_alarm(alarm)
            await post_alarm(deps, alarm)
    # roadmap step 4.7's own exit criterion: "A Warden whose heartbeat stops has its grant back in
    # the pool after expiry." Unconditional, like the loop above: a grant's own expires_at is a
    # wall-clock deadline independent of any one Warden's liveness state, so this runs every tick
    # regardless of whether any Warden just went offline this time.
    await forage_grants.sweep_expired(deps.ledger, deps)


async def _held_cells(deps: QueenDeps) -> frozenset[CellId]:
    """Return every Cell the Hive itself holds paused: its Overwintered ones (ADR-0029)."""
    # The same live-feed-over-static rule the dispatcher's inventory follows for this list.
    source = deps.dormant_cell_source
    dormant = await source() if source is not None else deps.dormant_cells
    return frozenset(candidate.cell_id for candidate in dormant)


def _hold(
    liveness: MutableMapping[WardenId, WardenLiveness],
    warden_id: WardenId,
    is_held: bool,
    now: datetime,
) -> bool:
    """Record whether `warden_id`'s Cell is held now; True while it is (so it is not judged)."""
    current = liveness.get(warden_id)
    if current is None:
        return is_held  # No row yet: nothing to judge, and nothing to record either.
    if is_held:
        if not current.held or current.missed_heartbeats:
            # Nothing it misses while held counts: not now, and not once the hold ends either.
            liveness[warden_id] = dataclasses.replace(current, held=True, missed_heartbeats=0)
        return True
    if current.held:
        # Resumed on purpose just now: a full window from here, not from its last pre-pause beat.
        liveness[warden_id] = dataclasses.replace(
            current, held=False, hold_ended_at=now, missed_heartbeats=0
        )
    return False


def _crossed_miss_limit(
    deps: QueenDeps,
    liveness: MutableMapping[WardenId, WardenLiveness],
    warden_id: WardenId,
    now: datetime,
) -> bool:
    """Re-derive `warden_id`'s misses at `now`; True only the check it first crosses the limit."""
    current = liveness.get(warden_id)
    if current is None or current.last_heartbeat_at is None:
        return False  # No Heartbeat received yet; nothing to judge staleness against.
    interval_s = _judged_interval_s(deps, current.interval_s)
    # Misses count from its newest Heartbeat, or from the end of a hold if that came later.
    since = max(current.last_heartbeat_at, current.hold_ended_at or current.last_heartbeat_at)
    missed = _missed_since(interval_s, now, since)
    offline = missed >= deps.heartbeat_miss_limit
    if missed == current.missed_heartbeats and offline == current.is_offline:
        return False  # Nothing changed since the last check.
    liveness[warden_id] = dataclasses.replace(current, missed_heartbeats=missed, is_offline=offline)
    return offline and not current.is_offline


def _judged_interval_s(deps: QueenDeps, declared_s: float | None) -> float:
    """Return the interval a Warden is judged by: the one it declared, floored at the manifest's."""
    # The floor keeps the manifest's grace for every Warden: one declaring a faster cadence than
    # the Hive's gets no stricter judgement than the operator configured (module docstring).
    if declared_s is None:
        return deps.heartbeat_interval_s
    return max(declared_s, deps.heartbeat_interval_s)


def _missed_since(interval_s: float, now: datetime, at: datetime) -> int:
    """Count the whole intervals elapsed from `at` to `now` (never a per-tick counter)."""
    # Never below zero: a Heartbeat stamped ahead of the Queen's clock (a Cell's own clock running
    # a little fast) counts as just sent, never as negative misses.
    return max(0, int((now - at).total_seconds() // interval_s))


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
