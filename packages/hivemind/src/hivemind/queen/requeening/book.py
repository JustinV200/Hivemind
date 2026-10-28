"""Define RequeeningBook: the Queen's in-memory side of Requeening, and its counts.

Requeening (ADR-0062) runs in two halves: `begin_requeening` before the Queen's loop starts, and
`settle_requeening` on her tick until every Warden she expects back has reattached or the grace
has run out. `RequeeningBook` is what joins them, held on `QueenDeps.requeening`: the store her
durable records live in, the two cadences from `[queen]`, the pairs of envelopes she handled
before her restart (so a resend is dropped), when she last wrote a snapshot, and, while recovery
is still settling, a `PendingRecovery` saying whom she waits for and until when.
`RequeenCounts` is what `queen.requeened` reports: counts only, never an id (the Night Veil
boundary, ADR-0062).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    requeening sub-package. Held by `hivemind.queen.deps.QueenDeps.requeening`; written by
    `.recover`, `.handled` and `.snapshot`. Calls into `hivemind.queen.requeening.store` and
    waggle only.

Key invariants:
    - `pending` is set by `begin_requeening` and cleared by the settle that records
      `queen.requeened`; a Queen whose book has none is not recovering.
    - `replayed` only ever holds pairs handled before this process started: a pair she handles in
      this life is the transport's to drop, never this book's.
    - `RequeenCounts` holds counts alone.

See Also:
    - docs/adr/0062-requeening-recovery-sources-of-truth.md for the two halves.
    - hivemind.queen.requeening.recover for the code that fills and empties `pending`.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from hivemind.queen.requeening.store import MemoryRequeeningStore, RequeeningStore
from waggle.ids import WardenId

DEFAULT_REQUEENING_GRACE_S = 90.0  # Three of the redial's 30 s backoff caps (ADR-0062).
DEFAULT_SNAPSHOT_INTERVAL_S = 60.0  # The Queen's own Handoff cadence (roadmap 13.2).

__all__ = [
    "DEFAULT_REQUEENING_GRACE_S",
    "DEFAULT_SNAPSHOT_INTERVAL_S",
    "PendingRecovery",
    "RequeenCounts",
    "RequeeningBook",
    "RequeeningHold",
]


@dataclass(frozen=True, slots=True)
class RequeenCounts:
    """What one Requeening did, as counts: the payload of `queen.requeened`."""

    given_back: int = 0  # Orphaned tasks placed afresh (PENDING), a Handoff noted where one was.
    night_veil_cancelled: int = 0  # Orphans on a Night Veil Cell: teardown-only, so cancelled.
    paused_resumed: int = 0  # Clustering pauses resumed through a Warden that came back.
    paused_replaced: int = 0  # Clustering pauses whose Warden is gone, placed afresh instead.
    leases_released: int = 0  # Hive Stand leases a dead process left open, given back.
    grants_released: int = 0  # Grants of tasks that had ended, returned to the pool.
    grants_revoked: int = 0  # Grants whose holder is gone, revoked HOLDER_OFFLINE.
    wardens_back: int = 0  # Wardens she waited for that reattached.
    wardens_missing: int = 0  # Wardens she waited for that did not.

    def plus(self, other: RequeenCounts) -> RequeenCounts:
        """Return the field-by-field sum of these counts and `other`'s.

        Args:
            other: The counts of a later step.

        Returns:
            New counts; neither operand changes.
        """
        names = (each.name for each in dataclasses.fields(self))
        return RequeenCounts(**{name: getattr(self, name) + getattr(other, name) for name in names})


@dataclass(frozen=True, slots=True)
class PendingRecovery:
    """What the second half of a Requeening still waits for, and what the first half did."""

    deadline: datetime  # When the grace runs out and every missing Warden counts as gone.
    expected: frozenset[WardenId]  # The Wardens of live tasks on Cells her backends still list.
    counts: RequeenCounts  # What the first half already did.
    snapshot_at: datetime | None  # The snapshot recovery started from; None when there was none.


class RequeeningHold(Protocol):
    """Something that keeps a Requeening from settling: a Supersedure's candidate (ADR-0065)."""

    async def holds(self) -> bool:
        """Return True while the Queen may not leave REQUEENING, nor place anything."""
        ...


@dataclass(slots=True)
class RequeeningBook:
    """The Queen's Requeening state in this process (codingrules 8.5: owned and mutated here)."""

    store: RequeeningStore = field(default_factory=MemoryRequeeningStore)
    grace_s: float = DEFAULT_REQUEENING_GRACE_S  # `[queen] requeening_grace_s`.
    snapshot_interval_s: float = DEFAULT_SNAPSHOT_INTERVAL_S  # `[queen] snapshot_interval_s`.
    replayed: frozenset[tuple[str, str]] = frozenset()  # (Warden, envelope) handled before.
    last_snapshot_at: datetime | None = None  # None until her first snapshot in this life.
    pending: PendingRecovery | None = None  # Set while recovery is still settling.
    hold: RequeeningHold | None = None  # Armed by the composition root; cleared once it lifts.
