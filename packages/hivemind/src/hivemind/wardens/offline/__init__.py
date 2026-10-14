"""Handle what a Warden does while it is cut off from the Queen, and how it catches her up.

A Warden (the always-on supervisor of one Cell, a unit of compute) that dials the Queen (the Hive's
central orchestrator) from another machine loses its link now and then, and codingrules 8.8 and 15
say it keeps working within what it owns, queues what must be reported, never grows, and is
clustered past a manifest limit (ADR-0053). This package is all of that, in four parts. The link:
`ReconnectingQueenLink` is a `Transport` that redials with capped backoff, re-announces the Cell,
replays a durable outbox, ships the trail and goes back online, and routes each send by one pure
table (`decide_offline`) while it is down. The state: an `Outage` remembers the tasks the Warden
paused and when the cut began. The freeze: entering OFFLINE pauses the sub-bees that have nothing
local to think with, and an outage past `max_offline_s` clusters fully. The way back: `reconcile`
compares the Warden with its link and its Cell on every tick, leaves OFFLINE, and starts the paused
tasks again from their Handoffs. A Level 0 device Warden, which never goes OFFLINE, uses the same
pause and resume when the swarm gate reports its Cell lost and found.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package. The
    composition root of a Warden that dials the Queen (`hivemind.cli.in_cell` in borrowed mode)
    builds the link and hands it to the Warden with an `Outage` and a `RemoteDeps` in
    `DeviceDeps`; `hivemind.wardens.warden.Warden` calls `reconcile` and `wake_future` from its
    tick. The tick handlers in `hivemind.wardens.ticks` never import this package: they reach an
    outage through `hivemind.wardens.deps.device_outage`. Calls into `hivemind.wardens.ticks`,
    `hivemind.wardens.state`, `hivemind.wardens.spawn`, `hivemind.pheromone`, `hivemind.memory`
    and waggle only.

Key invariants:
    - Only a Warden that dials the Queen goes OFFLINE; the Hive Stand's and a Level 0 device
      Warden keep their in-process link and never do.
    - What is queued, held, refused or dropped while the link is down is the one table in
      `policy`, and an unnamed message is refused, as on a dead link.
    - A reconnection catches the Queen up in a fixed order and goes online only after the queue
      and the held questions have been sent (`reconnect`).
    - `settled_state` never moves a Warden out of OFFLINE; only `leave_offline` does.
    - A paused task is held until its Warden can start it again, and the Queen's word on it (a
      cancel, a pause, a new attempt) always wins over the Warden's own hold.

See Also:
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for the decision.
    - .claude/codingrules.md sections 8.8 and 15, and Appendix C's Warden row, for the rules.
    - hivemind.wardens.trail_sync for the segment the reconnection ships.

Public API (roadmap step 11.9):
    - ReconnectingQueenLink, LinkPhase: the Transport that mends, and where it is in doing so.
    - Reconnection, QueenDialer, Announcer, AfterReconnect, TrailReconnect, WardenRecord: the
      injected dial, announcement and after-reconnect steps, and the usual last of them.
    - OfflineAction, OfflineRefusedError, decide_offline, owes_reply: the offline table.
    - OfflineOutbox, open_outbox: the durable queue and the questions held in memory.
    - Outage, HeldTask, RemoteDeps, QueenLinkView: what a device Warden remembers while cut off.
    - reconcile, enter_offline, cluster_fully, cut_cell, leave_offline: the tick's call, and the
      freeze and the way back it is made of; RESUME_SETTLE_S and RESUME_RETRY_S, the two pauses.
    - is_link_down, outage_of, remote_of, wake_future: what the Warden asks of its deps.
"""

from hivemind.wardens.offline.freeze import cluster_fully, cut_cell, enter_offline
from hivemind.wardens.offline.link import LinkPhase, ReconnectingQueenLink
from hivemind.wardens.offline.outage import (
    HeldTask,
    Outage,
    QueenLinkView,
    RemoteDeps,
    is_link_down,
    outage_of,
    remote_of,
    wake_future,
)
from hivemind.wardens.offline.outbox import OfflineOutbox, open_outbox
from hivemind.wardens.offline.policy import (
    OfflineAction,
    OfflineRefusedError,
    decide_offline,
    owes_reply,
)
from hivemind.wardens.offline.reconnect import (
    AfterReconnect,
    Announcer,
    QueenDialer,
    Reconnection,
    TrailReconnect,
    WardenRecord,
)
from hivemind.wardens.offline.resume import (
    RESUME_RETRY_S,
    RESUME_SETTLE_S,
    leave_offline,
    reconcile,
)

__all__ = [
    "RESUME_RETRY_S",
    "RESUME_SETTLE_S",
    "AfterReconnect",
    "Announcer",
    "HeldTask",
    "LinkPhase",
    "OfflineAction",
    "OfflineOutbox",
    "OfflineRefusedError",
    "Outage",
    "QueenDialer",
    "QueenLinkView",
    "ReconnectingQueenLink",
    "Reconnection",
    "RemoteDeps",
    "TrailReconnect",
    "WardenRecord",
    "cluster_fully",
    "cut_cell",
    "decide_offline",
    "enter_offline",
    "is_link_down",
    "leave_offline",
    "open_outbox",
    "outage_of",
    "owes_reply",
    "reconcile",
    "remote_of",
    "wake_future",
]
