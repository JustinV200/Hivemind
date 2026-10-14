"""Fill a Queen's Honey Store the way a remote Warden's bee does, and read what she recalls from it.

A bee on a colonized device (or in a Virtual Cell) writes each Handoff in its Warden's own store
and deposits it as HANDOFF Nectar, which its Warden relays and the Queen's intake keeps (ADR-0035).
The Queen's own memory holds none of it, which is how the defect the recall closes hid: a test that
seeds her store cannot see a Handoff that lives somewhere else. `Deposits` is the one place the
deposit is made, cut and taken in by the real `split_deposit` and `NectarIntake`, so the Honey
Store holds exactly what it would. The three paths that resume a task from a Handoff (a device
taken back, a Warden renewed for new terms, Clustering's resume) each test the recall through it,
with nothing in her memory first. `Refusal` names the four things a recall refuses and plants each
one, so that all three paths are held to the same list. The rest are the reads and the setups those
tests share: the checkpoint she holds herself, a taint row on her trail, an isolation, and the
decisions she recorded.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the recall tests under
    tests/unit/queen. Builds on `builders.honey`, `builders.honey_wire` and `builders.memory`.

Key invariants:
    - A deposit is made through the real intake, labelled as a borrowed Cell's deposit is (C2 by
      the Real Cell floor), and nothing is written to the Queen's memory by it.
    - `Deposits.deps` is the Queen's collaborators with the Honey Store in them: a test that runs
      a Queen over other deps must hand her these.

See Also:
    - hivemind.queen.swarm_gate.recall for the module these tests drive.
    - hivemind.workers.nectar for `split_deposit`, the bee's side of a deposit.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from pathlib import Path

from builders.honey import open_test_honey_store
from builders.honey_wire import make_honey_access
from builders.memory import make_handoff

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.honey_store.access import HoneyAccess
from hivemind.honey_store.nectar.submission import DepositSource
from hivemind.memory import Handoff, MemoryContext, read_handoff, write_checkpoint
from hivemind.memory.taint import TAINTED_KIND
from hivemind.pheromone import CellEvent, MemoryEvent, TrailQuery
from hivemind.queen.deps import QueenDeps
from hivemind.workers.nectar import DepositMeta, split_deposit
from hivemind.workers.runtime.honey import HANDOFF_MEDIA_TYPE
from waggle.clock import Clock
from waggle.ids import CellId, TaskId, WardenId, new_event_id, new_worker_id
from waggle.messages import HandoffRef
from waggle.messages.honey import NectarKind

__all__ = ["Deposits", "Refusal", "Site", "make_deposits"]


class Refusal(Enum):
    """Something a recall refuses to resume a task from, with the word it records for it."""

    TAINTED = "tainted"  # Her trail says the Handoff is tainted, and no judge has cleared it.
    ISOLATED = "isolated"  # Written since the first suspect instant of an isolation that stands.
    ABOVE_CLEARANCE = "above_clearance"  # More sensitive than the task may read.
    NIGHT_VEIL = "night_veil"  # A Night Veil Cell's memory: the store never offers it, no record.

    @property
    def recorded(self) -> str | None:
        """The `detail` of the `queen.decided` the refusal leaves, or None when it leaves none."""
        return None if self is Refusal.NIGHT_VEIL else self.value


@dataclass(frozen=True)
class Site:
    """Where a bee worked: its Warden, its Cell and its task."""

    warden_id: WardenId
    cell_id: CellId
    task_id: TaskId


@dataclass(frozen=True)
class Deposits:
    """A Honey Store the Queen reads, and the ways a remote bee and a Guard fill the record."""

    deps: QueenDeps  # The Queen's collaborators, the Honey Store in them.
    clock: Clock
    honey: HoneyAccess

    def ref(self, handoff: Handoff, age_s: int = 0) -> HandoffRef:
        """The reference a bee's checkpoint of `handoff`, written `age_s` ago, would have."""
        return HandoffRef(
            event_id=new_event_id(self.clock),
            written_at=self.clock.now() - timedelta(seconds=age_s),
            clearance=handoff.clearance.to_wire(),
        )

    async def send(
        self,
        site: Site,
        ref: HandoffRef,
        content: bytes,
        tier: CombShieldLevel = CombShieldLevel.MEADOW,
    ) -> None:
        """Take in `content` as the HANDOFF deposit of the bee at `site`, for `ref`."""
        meta = DepositMeta(
            kind=NectarKind.HANDOFF,
            media_type=HANDOFF_MEDIA_TYPE,
            title="Handoff: a task",
            task_id=site.task_id,
            cell_id=site.cell_id,
            worker_id=new_worker_id(self.clock),
            observed_at=ref.written_at,
            clearance=ref.clearance,
            origin_tier=tier.to_wire(),
            event_id=ref.event_id,
        )
        # A device is a borrowed Cell, so everything it deposits is C2 in Honey, whatever it said.
        source = DepositSource(
            sender=site.warden_id, cell_id=site.cell_id, from_borrowed_cell=True, tier=tier
        )
        for chunk in split_deposit(content, meta):
            await self.honey.intake.receive_chunk(chunk, source)

    async def deposit(self, site: Site, handoff: Handoff, age_s: int = 0) -> HandoffRef:
        """Deposit `handoff` as the bee at `site` would, checkpointed `age_s` ago; its reference."""
        ref = self.ref(handoff, age_s)
        await self.send(site, ref, handoff.model_dump_json().encode("utf-8"))
        return ref

    async def refused(self, site: Site, case: Refusal) -> None:
        """Deposit a Handoff of the bee at `site` that a recall must refuse, for the reason `case`.

        The task is the default one, so it reads C1: a Handoff labelled C2 is above it.
        """
        if case is Refusal.ABOVE_CLEARANCE:
            await self.deposit(site, make_handoff(self.clock, clearance=HoneyClearance.C2))
        elif case is Refusal.NIGHT_VEIL:
            handoff = make_handoff(self.clock)
            await self.send(
                site,
                self.ref(handoff),
                handoff.model_dump_json().encode("utf-8"),
                CombShieldLevel.NIGHT_VEIL,
            )
        elif case is Refusal.ISOLATED:
            await self.deposit(site, make_handoff(self.clock), age_s=30)
            # Isolated now, suspecting the Cell's memory from a minute ago: its Warden was never
            # given the order to label what it wrote since, so no taint row says so.
            await self.isolate(site.cell_id, suspect_age_s=60)
        else:
            tainted = await self.deposit(site, make_handoff(self.clock))
            await self.taint(tainted.event_id)

    async def hold(self, task_id: TaskId, handoff: Handoff) -> HandoffRef:
        """File `handoff` in the Queen's own memory, as a checkpoint she or a Level 0 bee wrote."""
        deps = self.deps
        ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=self.clock)
        return await write_checkpoint(handoff, task_id, ctx)

    async def held(self, ref: HandoffRef) -> Handoff:
        """Read the Handoff the Queen holds under `ref`, as a placement's resume does."""
        return await read_handoff(self.deps.memory, ref, HoneyClearance.C2)

    async def taint(self, event_id: str, kind: str = TAINTED_KIND) -> None:
        """Put a taint row (or a clearing) about the checkpoint `event_id` on the Queen's trail."""
        deps = self.deps
        await deps.trail.record(
            MemoryEvent(
                id=new_event_id(self.clock),
                hive_id=deps.identity.hive_id,
                node_id=deps.identity.node_id,
                at=self.clock.now(),
                actor="system",
                kind=kind,
                subject_id=event_id,
                payload={"item_kind": "handoff"},
            )
        )

    async def isolate(self, cell_id: CellId, suspect_age_s: int) -> None:
        """Isolate `cell_id`, suspecting its memory from `suspect_age_s` ago."""
        deps = self.deps
        suspect_at = self.clock.now() - timedelta(seconds=suspect_age_s)
        await deps.trail.record(
            CellEvent(
                id=new_event_id(self.clock),
                hive_id=deps.identity.hive_id,
                node_id=deps.identity.node_id,
                at=self.clock.now(),
                actor="system",
                kind="cell.isolated",
                subject_id=cell_id,
                payload={"suspect_at": suspect_at.isoformat()},
            )
        )

    async def decided(self, reason: str) -> list[dict[str, object]]:
        """The payload of every `queen.decided` about `reason`, oldest first."""
        events = await self.deps.trail.query(TrailQuery(kind="queen.decided"))
        return [dict(event.payload) for event in events if event.payload.get("reason") == reason]


async def make_deposits(deps: QueenDeps, clock: Clock, tmp_path: Path) -> Deposits:
    """Open a real Honey Store and give the Queen's collaborators access to it.

    Args:
        deps: The Queen's collaborators, which hold no Honey Store.
        clock: The test's clock, which the store and every id it mints share.
        tmp_path: A writable directory for the store's database.

    Returns:
        The deposits handle; its `deps` are `deps` with the Honey Store in them.
    """
    honey = make_honey_access(await open_test_honey_store(tmp_path, clock), clock)
    return Deposits(dataclasses.replace(deps, honey=honey), clock, honey)
