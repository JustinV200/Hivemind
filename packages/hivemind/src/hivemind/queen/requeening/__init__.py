"""Provide Requeening: restore a crashed Queen without silently orphaning any task.

Restoring her also restores her outstanding leases and Forage (the Hive's shared model-serving
capacity) grants. Every start of a Queen is a Requeening (ADR-0062): each fact comes back from
its own source of truth (tasks from the Brood Chamber, Virtual Cells and their keys from backend
labels, Hive Stand leases from the trail folded from her newest snapshot, grants from the ledger,
Wardens from whatever reattaches within a grace), orphaned tasks are placed afresh from their
newest Handoff, and `queen.requeened` records what was done, as counts. Her own periodic
snapshot (roadmap 13.2) bounds how much trail recovery reads, and a durable record of the Alarms
and questions she handled keeps a Warden's resend after her restart from being handled twice.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package.
    `begin_requeening` is called by the composition root before her loop starts;
    `settle_requeening` and `take_snapshot_when_due` by her tick (`hivemind.queen.ticks.
    housekeeping`); `is_replay`/`note_handled` by her tick's item handling and `note_question` by
    `hivemind.queen.questions`. Calls into sibling packages at Layer 6 or below.

Key invariants:
    - Nothing is taken back from a Warden she still waits for, nor from a Swarm device's Cell.
    - `queen.requeened` is recorded once per start and carries counts only (the Night Veil
      boundary, ADR-0062).
    - Nothing here imports `hivemind.queen.deps`, `.dispatcher` or `.swarm_gate` at module level:
      `QueenDeps` holds a `RequeeningBook`, so those are imported where used.

See Also:
    - docs/adr/0062-requeening-recovery-sources-of-truth.md for the decision.
    - docs/runbooks/requeening.md for the operator's side.
    - .claude/roadmap.md steps 13.1 and 13.2.

Public API:
    - begin_requeening, settle_requeening, RequeeningStart, REQUEENED_KIND: the two halves
      (recover).
    - RequeeningBook, PendingRecovery, RequeenCounts, RequeeningHold, DEFAULT_REQUEENING_GRACE_S,
      DEFAULT_SNAPSHOT_INTERVAL_S: the in-memory state on `QueenDeps.requeening`, and what may
      hold a Requeening from settling (a Supersedure's candidate, ADR-0065) (book).
    - is_replay, note_handled, note_question, restore_handled: handled Alarms and questions
      (handled).
    - take_snapshot_when_due, take_snapshot, current_leases: the Queen's own Handoff (snapshot).
    - HiveStandLeases, fold_leases, release_orphans: the Hive Stand's orphaned leases (leases).
    - take_back, clustering_paused, revoke_held_by, newest_handoff: orphaned tasks (orphans).
    - QueenSnapshot, OpenLease, HandledEnvelope, QuestionWiring: the durable rows (models).
    - RequeeningStore, MemoryRequeeningStore, SqliteRequeeningStore: where they live (store).
"""

from hivemind.queen.requeening.book import (
    DEFAULT_REQUEENING_GRACE_S,
    DEFAULT_SNAPSHOT_INTERVAL_S,
    PendingRecovery,
    RequeenCounts,
    RequeeningBook,
    RequeeningHold,
)
from hivemind.queen.requeening.handled import (
    is_replay,
    note_handled,
    note_question,
    restore_handled,
)
from hivemind.queen.requeening.leases import HiveStandLeases, fold_leases, release_orphans
from hivemind.queen.requeening.models import (
    HandledEnvelope,
    OpenLease,
    QueenSnapshot,
    QuestionWiring,
)
from hivemind.queen.requeening.orphans import (
    clustering_paused,
    newest_handoff,
    revoke_held_by,
    take_back,
)
from hivemind.queen.requeening.recover import (
    REQUEENED_KIND,
    RequeeningStart,
    begin_requeening,
    settle_requeening,
)
from hivemind.queen.requeening.snapshot import (
    current_leases,
    take_snapshot,
    take_snapshot_when_due,
)
from hivemind.queen.requeening.store import (
    MemoryRequeeningStore,
    RequeeningStore,
    SqliteRequeeningStore,
)

__all__ = [
    "DEFAULT_REQUEENING_GRACE_S",
    "DEFAULT_SNAPSHOT_INTERVAL_S",
    "REQUEENED_KIND",
    "HandledEnvelope",
    "HiveStandLeases",
    "MemoryRequeeningStore",
    "OpenLease",
    "PendingRecovery",
    "QueenSnapshot",
    "QuestionWiring",
    "RequeenCounts",
    "RequeeningBook",
    "RequeeningHold",
    "RequeeningStart",
    "RequeeningStore",
    "SqliteRequeeningStore",
    "begin_requeening",
    "clustering_paused",
    "current_leases",
    "fold_leases",
    "is_replay",
    "newest_handoff",
    "note_handled",
    "note_question",
    "release_orphans",
    "restore_handled",
    "revoke_held_by",
    "settle_requeening",
    "take_back",
    "take_snapshot",
    "take_snapshot_when_due",
]
