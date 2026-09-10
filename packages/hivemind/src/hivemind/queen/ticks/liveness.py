"""Define WardenLiveness, record_heartbeat and check_liveness: track each attached Warden's pulse.

Roadmap step 3.20's own dispatch map: "Heartbeat (Warden) -> RECORD liveness (last seen, misses
reset); a Warden missing `heartbeat_miss_limit` heartbeats -> MARK_WARDEN_OFFLINE (its tasks are
re-dispatched when it returns or failed after the alarm limit; v0: record and raise an Alarm at the
human)." `record_heartbeat` is the reset half, called from `hivemind.queen.queen.Queen`'s tick for
every received `Heartbeat`; `check_liveness` is the sweep: for every attached Warden with at least
one heartbeat on record, it recomputes how many whole `heartbeat_interval_s` intervals have elapsed
since the last one (never an incremental per-tick counter, so calling it more often than once per
interval never over-counts), and raises one Alarm at the human the moment a Warden first crosses
`heartbeat_miss_limit`.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's ticks
    sub-package. Called by `hivemind.queen.queen.Queen`'s own tick, once per Heartbeat received
    (`record_heartbeat`) and unconditionally once per tick (`check_liveness`). Calls into
    `hivemind.cell` (HoneyClearance), `hivemind.queen.deps` (QueenDeps, WardenLink),
    `hivemind.queen.human_inbox` (HumanInbox), `hivemind.supervision` (Alarm, AlarmKind,
    AlarmSeverity, AlarmState) and waggle only.

Key invariants:
    - `check_liveness` re-derives `missed_heartbeats` from elapsed wall-clock time on every call,
      never by incrementing a counter per call: calling it more often than
      `deps.heartbeat_interval_s` never inflates the miss count.
    - A newly-offline transition raises exactly one Alarm: the `offline and not current.is_offline`
      guard fires only the tick a Warden first crosses the limit, never on every later check while
      it stays offline.

See Also:
    - .claude/roadmap.md step 3.20's own dispatch map for the Heartbeat/liveness rule this module
      implements.
    - hivemind.queen.human_inbox for HumanInbox, where an offline Warden's Alarm lands.
"""

from __future__ import annotations

import dataclasses
from collections.abc import MutableMapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from hivemind.cell import HoneyClearance
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.human_inbox import HumanInbox
from hivemind.supervision import Alarm, AlarmKind, AlarmSeverity, AlarmState
from waggle.ids import WardenId, new_alarm_id
from waggle.messages.supervision import AlarmContext

__all__ = ["WardenLiveness", "check_liveness", "record_heartbeat"]


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
