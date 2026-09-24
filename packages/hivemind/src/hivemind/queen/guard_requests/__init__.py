"""Provide the Queen's side of a Guard request: her door, her durable table, and her inbox items.

ADR-0035: the Guard Bee (the Hive's security watcher) acts alone only to narrow the whole Hive;
anything aimed at one Cell or one bee it can only ask the Queen for, through
`hivemind.guard.GuardRequestDoor`. This sub-package is where that request lands. `door` is her
implementation of the door (`QueenGuardDoor`, the `GuardDoor` mixin that makes the running Queen a
door herself, and `guard_door` for a caller holding only `QueenDeps`): it files the request into
her own table, durably, and wakes her. `model`, `protocol`, `memory` and `sqlite` are that table
(`GuardRequest` rows, each with her `GuardDecision` once made and any `PlacementHold` it left).
`inbox` turns every undecided row into a `GUARD_REQUEST` inbox item her Attendant scores above
every Alarm and every human message. `deps` is the one `QueenDeps` field all of this rides on
(`GuardDeps`: the table, the dire patterns, the egress seam and the pause bound isolation uses).
Her decision on a request is the `decision` sub-package, imported by her tick directly: this face
never imports it, because `hivemind.queen.deps` imports this face and the decision needs `deps`.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package. The door
    is called by the Guard Bee (roadmap step 10.6); the table is read by her tick, her dispatcher's
    placement snapshot (holds) and the isolation lift. Calls into `hivemind.common`,
    `hivemind.guard`, `hivemind.hive` (CellEgress), `hivemind.manifest`, `hivemind.queen.autopilot`,
    `hivemind.supervision.attendant` and waggle only; `QueenDeps` only for its type.

Key invariants:
    - A request is durable before the door returns, and filed at most once per report id.
    - Nothing here decides: the Queen decides on her own tick (`decision`).

See Also:
    - docs/adr/0035-guard-bee-requests-queen-only-isolation-and-tainted-memory.md.
    - docs/guard/isolation.md for the request path end to end.

Public API (roadmap step 10.6a):
    - QueenGuardDoor, GuardDoor, guard_door: the Queen's GuardRequestDoor (door).
    - GuardRequest, GuardDecision, GuardBasis, PlacementHold, MAX_OUTCOME_CHARS, MAX_HELD_GOALS:
      one request and her decision on it (model).
    - GuardRequestStore, DEFAULT_PENDING_PAGE: the persistence seam (protocol).
    - InMemoryGuardRequestStore (memory), SqliteGuardRequestStore,
      apply_guard_request_migrations, SUBSYSTEM, MIGRATIONS_PACKAGE (sqlite): its two stores.
    - guard_items, guard_inbox_item, GUARD_REQUEST_PAYLOAD_KIND: the Queen's inbox items (inbox).
    - GuardDeps, DEFAULT_PAUSE_TIMEOUT_S: the one QueenDeps field (deps).
"""

from hivemind.queen.guard_requests.deps import DEFAULT_PAUSE_TIMEOUT_S, GuardDeps
from hivemind.queen.guard_requests.door import GuardDoor, QueenGuardDoor, guard_door
from hivemind.queen.guard_requests.inbox import (
    GUARD_REQUEST_PAYLOAD_KIND,
    guard_inbox_item,
    guard_items,
)
from hivemind.queen.guard_requests.memory import InMemoryGuardRequestStore
from hivemind.queen.guard_requests.model import (
    MAX_HELD_GOALS,
    MAX_OUTCOME_CHARS,
    GuardBasis,
    GuardDecision,
    GuardRequest,
    PlacementHold,
)
from hivemind.queen.guard_requests.protocol import DEFAULT_PENDING_PAGE, GuardRequestStore
from hivemind.queen.guard_requests.sqlite import (
    MIGRATIONS_PACKAGE,
    SUBSYSTEM,
    SqliteGuardRequestStore,
    apply_guard_request_migrations,
)

__all__ = [
    "DEFAULT_PAUSE_TIMEOUT_S",
    "DEFAULT_PENDING_PAGE",
    "GUARD_REQUEST_PAYLOAD_KIND",
    "MAX_HELD_GOALS",
    "MAX_OUTCOME_CHARS",
    "MIGRATIONS_PACKAGE",
    "SUBSYSTEM",
    "GuardBasis",
    "GuardDecision",
    "GuardDeps",
    "GuardDoor",
    "GuardRequest",
    "GuardRequestStore",
    "InMemoryGuardRequestStore",
    "PlacementHold",
    "QueenGuardDoor",
    "SqliteGuardRequestStore",
    "apply_guard_request_migrations",
    "guard_door",
    "guard_inbox_item",
    "guard_items",
]
