"""Define OrphanedLease and OrphanReleaser: give back a Hive Stand lease a dead process left open.

The Hive Stand's lease lives in the Warden that holds it, and that Warden lives in the Queen's
process. A process that dies without its teardown leaves the lease unreleased: its scratch
directory on disk, and a `cell.leased` on the trail with no `cell.released` after it. Requeening
(ADR-0062) finds such a lease and hands it here. Its restore records and the ids of the processes
it started lived in the process that died, so its scratch is all that can still be undone: the
directory is removed (unless `[hive_stand] keep_scratch` keeps scratch on purpose), the paths it
touched outside scratch are reported as residual rather than guessed at, and the release is
recorded as the `cell.released` the dead process never wrote, marked `requeened`.

Fits into the Hive:
    Layer 2 (the Cell abstraction), inside `hivemind.cell.local` (the Hive Stand). Built by
    `hivemind.cell.local.source.HiveStandSource`, whose `release_orphan` delegates here. Calls
    into `hivemind.cell.local.releaser` (remove_scratch), `hivemind.cell.source`,
    `hivemind.cell.tiers`, `hivemind.pheromone` and waggle only.

Key invariants:
    - Nothing is removed unless the lease id parses as a lease id: an id read off the trail can
      never name a path outside the scratch root (`..`, a separator, an absolute path).
    - Every call records exactly one `cell.released`, whether or not the directory went.

See Also:
    - docs/adr/0062-requeening-recovery-sources-of-truth.md for when a lease is an orphan.
    - hivemind.cell.local.releaser for the release a living lease gets.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from hivemind.cell.local.releaser import remove_scratch
from hivemind.cell.source import CellIdentity
from hivemind.cell.tiers import CombShieldLevel
from hivemind.pheromone import CellEvent, PheromoneTrail
from waggle.clock import Clock
from waggle.errors import InvalidIdError
from waggle.ids import CellId, IdKind, new_event_id, parse_id

RELEASED_KIND = "cell.released"  # The event a living lease's release records too.

__all__ = ["OrphanReleaser", "OrphanedLease"]


@dataclass(frozen=True, slots=True)
class OrphanedLease:
    """A lease that a process which died left open on the Hive Stand, as its trail shows it."""

    lease_id: str  # From its `cell.leased`; must parse as a lease id before anything is removed.
    holder: str  # The Warden that held it, gone with its process.
    task_id: str | None  # The task it was taken for, when its `cell.leased` named one.
    touched_outside: int  # Paths it touched outside scratch, none of which can be restored now.


@dataclass(frozen=True, slots=True)
class OrphanReleaser:
    """Give back orphaned leases on one Hive Stand Cell (built by `HiveStandSource`)."""

    cell_id: CellId  # The Hive Stand's own Cell.
    scratch_root: Path  # `[hive_stand] scratch_root`: every lease's directory is one below it.
    keep_scratch: bool  # `[hive_stand] keep_scratch`: scratch is kept on purpose, never removed.
    comb_shield: CombShieldLevel  # The Cell's tier, as every `cell.released` names it.
    identity: CellIdentity  # Stamped on the `cell.released` this records.
    trail: PheromoneTrail  # Where it records it.
    clock: Clock  # Its event id and instant.

    async def release_orphan(self, orphan: OrphanedLease) -> bool:
        """Remove the lease's scratch and record its `cell.released`.

        Args:
            orphan: The lease, as Requeening read it from the trail.

        Returns:
            True when its scratch is gone (or kept on purpose); False when the lease id is
            malformed (nothing is touched) or the directory would not go.
        """
        scratch = self._scratch_of(orphan.lease_id)
        removed = False
        if scratch is not None:
            # Off the loop: a directory tree on this machine's disk, retried briefly past a lock.
            removed = self.keep_scratch or await asyncio.to_thread(remove_scratch, scratch)
        await self.trail.record(self._released(orphan, removed))
        return removed

    def _scratch_of(self, lease_id: str) -> Path | None:
        """Return lease `lease_id`'s scratch directory, or None when it names none of ours."""
        # SAFETY: the id comes off the trail; only a well-formed lease id (a prefix and a ULID,
        # nothing else) names a directory, so no `..` or separator can reach shutil.rmtree.
        try:
            parse_id(lease_id, IdKind.LEASE)
        except InvalidIdError:
            return None
        return self.scratch_root / lease_id

    def _released(self, orphan: OrphanedLease, removed: bool) -> CellEvent:
        """Build the `cell.released` an orphan's release records, marked as Requeening's."""
        return CellEvent(
            id=new_event_id(self.clock),
            hive_id=self.identity.hive_id,
            node_id=self.identity.node_id,
            at=self.clock.now(),
            actor=self.identity.actor,
            kind=RELEASED_KIND,
            subject_id=self.cell_id,
            payload={
                "lease_id": orphan.lease_id,
                "holder": orphan.holder,
                "task_id": orphan.task_id,
                "comb_shield": self.comb_shield.value,
                "killed_processes": 0,  # Its pids died with the process that recorded them.
                "residual_paths": orphan.touched_outside,
                "is_restored": removed and orphan.touched_outside == 0,
                "requeened": True,
            },
        )
