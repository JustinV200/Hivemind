"""Tests for hivemind.queen.swarm_gate.recall: a lost device's newest Handoff, from the Honey Store.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/recall.py (codingrules section 3). A colonized device's
    bee deposits each Handoff as HANDOFF Nectar and its Warden relays it; the deposits here are cut
    and taken in by the real `split_deposit` and `NectarIntake`, so the Honey Store holds exactly
    what it would. Nothing is ever put in the Queen's own memory store first, which is how the
    defect these tests close (a task taken back from a colonized device resumed from nothing) hid:
    a test that seeds her store cannot see a Handoff that lives somewhere else.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.recall for the module under test.
    - tests.unit.queen.swarm_gate.test_takeback for how a task is taken back and its hint read.
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest
from builders.honey import open_test_honey_store
from builders.honey_wire import make_honey_access
from builders.memory import make_handoff
from builders.swarm import GRACE_S, GateRig, PlacedDevice, place_on_device

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.honey_store.access import HoneyAccess
from hivemind.honey_store.nectar.submission import DepositSource
from hivemind.memory import Handoff, MemoryContext, read_handoff, write_checkpoint
from hivemind.memory.taint import (
    TAINT_CLEARED_KIND,
    TAINTED_KIND,
    TaintMarker,
    TaintSource,
    TaintState,
)
from hivemind.pheromone import CellEvent, MemoryEvent, TrailQuery
from hivemind.queen import QueenDeps
from hivemind.queen.dispatcher import dispatch_ready
from hivemind.queen.dispatcher.ready.handoff import resume_plan
from hivemind.queen.swarm_gate import take_back
from hivemind.queen.swarm_gate.recall import MAX_CANDIDATES
from hivemind.swarm import RuntimeLevel, SwarmNode
from hivemind.workers.nectar import DepositMeta, split_deposit
from hivemind.workers.runtime.honey import HANDOFF_MEDIA_TYPE
from waggle.ids import TaskId, new_event_id, new_worker_id
from waggle.messages import HandoffRef
from waggle.messages.honey import NectarKind


@dataclass
class _Scene:
    """A colonized device with a task RUNNING on it, and a Queen with a Honey Store to ask."""

    rig: GateRig
    deps: QueenDeps
    honey: HoneyAccess
    placed: PlacedDevice

    @property
    def task_id(self) -> TaskId:
        """The task running on the device."""
        return self.placed.task.id


@asynccontextmanager
async def _scene(
    rig: GateRig, tmp_path: Path, level: RuntimeLevel = RuntimeLevel.COLONIZED
) -> AsyncIterator[_Scene]:
    """Serve a device on `level` with a task on it, over a Queen holding a real Honey Store."""
    honey = make_honey_access(await open_test_honey_store(tmp_path, rig.clock), rig.clock)
    rig.queen_deps = dataclasses.replace(rig.queen_deps, honey=honey)
    async with rig.serving():
        yield _Scene(rig, rig.queen_deps, honey, await place_on_device(rig, level))


def _ref(scene: _Scene, handoff: Handoff, age_s: int = 0) -> HandoffRef:
    """The reference a device's checkpoint of `handoff`, written `age_s` ago, would have."""
    clock = scene.rig.clock
    return HandoffRef(
        event_id=new_event_id(clock),
        written_at=clock.now() - timedelta(seconds=age_s),
        clearance=handoff.clearance.to_wire(),
    )


async def _send(
    scene: _Scene,
    ref: HandoffRef,
    content: bytes,
    node: SwarmNode | None = None,
    tier: CombShieldLevel = CombShieldLevel.MEADOW,
) -> None:
    """Take in `content` as a bee's HANDOFF deposit for `ref`, relayed by `node`'s Warden."""
    sender = node if node is not None else scene.placed.node
    meta = DepositMeta(
        kind=NectarKind.HANDOFF,
        media_type=HANDOFF_MEDIA_TYPE,
        title="Handoff: a task",
        task_id=scene.task_id,
        cell_id=sender.cell_id,
        worker_id=new_worker_id(scene.rig.clock),
        observed_at=ref.written_at,
        clearance=ref.clearance,
        origin_tier=tier.to_wire(),
        event_id=ref.event_id,
    )
    # A device is a borrowed Cell, so everything it deposits is C2 in Honey whatever it declared.
    source = DepositSource(
        sender=sender.warden_id, cell_id=sender.cell_id, from_borrowed_cell=True, tier=tier
    )
    for chunk in split_deposit(content, meta):
        await scene.honey.intake.receive_chunk(chunk, source)


async def _deposit(scene: _Scene, handoff: Handoff, age_s: int = 0) -> HandoffRef:
    """Deposit `handoff` as the device's bee would, checkpointed `age_s` ago; its reference."""
    ref = _ref(scene, handoff, age_s)
    await _send(scene, ref, handoff.model_dump_json().encode("utf-8"))
    return ref


async def _held(scene: _Scene, handoff: Handoff) -> HandoffRef:
    """File `handoff` in the Queen's own memory, as a checkpoint she (or a Level 0 bee) wrote."""
    ctx = MemoryContext(
        store=scene.deps.memory, identity=scene.deps.identity, clock=scene.rig.clock
    )
    return await write_checkpoint(handoff, scene.task_id, ctx)


async def _on_trail(scene: _Scene, event_id: str, kind: str) -> None:
    """Put a taint row about the checkpoint `event_id` on the Queen's trail, as a synced segment."""
    deps = scene.deps
    await deps.trail.record(
        MemoryEvent(
            id=new_event_id(scene.rig.clock),
            hive_id=deps.identity.hive_id,
            node_id=deps.identity.node_id,
            at=scene.rig.clock.now(),
            actor="system",
            kind=kind,
            subject_id=event_id,
            payload={"item_kind": "handoff"},
        )
    )


async def _isolate(scene: _Scene, suspect_age_s: int) -> None:
    """Isolate the device's Cell, suspecting its memory from `suspect_age_s` ago."""
    deps, clock = scene.deps, scene.rig.clock
    suspect_at = clock.now() - timedelta(seconds=suspect_age_s)
    await deps.trail.record(
        CellEvent(
            id=new_event_id(clock),
            hive_id=deps.identity.hive_id,
            node_id=deps.identity.node_id,
            at=clock.now(),
            actor="system",
            kind="cell.isolated",
            subject_id=scene.placed.node.cell_id,
            payload={"suspect_at": suspect_at.isoformat()},
        )
    )


async def _decided(scene: _Scene, reason: str) -> list[dict[str, object]]:
    """The payload of every `queen.decided` about `reason`, oldest first."""
    events = await scene.deps.trail.query(TrailQuery(kind="queen.decided"))
    return [dict(event.payload) for event in events if event.payload.get("reason") == reason]


async def _taken_back_from(scene: _Scene) -> HandoffRef | None:
    """Take the device's tasks back, and return the Handoff the next placement resumes from."""
    await take_back(scene.deps, scene.placed.node, "grace_over")
    return scene.deps.dispatch.resumes.take(scene.task_id)


async def _filed(scene: _Scene, ref: HandoffRef) -> Handoff:
    """Read the Handoff the Queen holds under `ref`, as the next placement's resume does."""
    return await read_handoff(scene.deps.memory, ref, HoneyClearance.C2)


async def test_a_task_taken_back_resumes_from_the_handoff_its_bee_deposited(
    rig: GateRig, tmp_path: Path
) -> None:
    async with _scene(rig, tmp_path) as scene:
        handoff = make_handoff(rig.clock, goal="Ship the endpoint.", clearance=HoneyClearance.C1)
        deposited = await _deposit(scene, handoff)

        ref = await _taken_back_from(scene)

        # The Queen's store held nothing; what she resumes from she filed, as a checkpoint of her
        # own (the device's event id stays the device's), with the label the bee gave it: the C2
        # the Honey Store gave the deposit is only the floor of a borrowed Cell.
        assert ref is not None and ref.event_id != deposited.event_id
        assert await _filed(scene, ref) == handoff
        [decision] = await _decided(scene, "handoff_recalled")
        assert decision["source_event_id"] == deposited.event_id
        assert (
            decision["event_id"] == ref.event_id
            and decision["cell_id"] == scene.placed.node.cell_id
        )


async def test_the_next_placement_on_another_node_carries_the_recalled_handoff_inline(
    rig: GateRig, tmp_path: Path
) -> None:
    async with _scene(rig, tmp_path) as scene:
        handoff = make_handoff(rig.clock, goal="Ship the endpoint.")
        await _deposit(scene, handoff)
        ref = await _taken_back_from(scene)
        task = await scene.deps.chamber.get(scene.task_id)

        # A device's Warden signs as a node of its own, so it has a store of its own to resume from.
        plan = await resume_plan(scene.deps, scene.placed.link, task, ref)

        assert plan.resume_from == ref
        assert plan.handoff is not None and plan.handoff.goal == "Ship the endpoint."


async def test_a_lost_devices_task_is_placed_on_another_device_with_the_handoff_it_deposited(
    rig: GateRig, tmp_path: Path
) -> None:
    async with _scene(rig, tmp_path) as scene:
        spare = await place_on_device(rig, RuntimeLevel.COLONIZED)
        assert spare.end is not None
        await _deposit(scene, make_handoff(rig.clock, goal="Ship the endpoint."))

        # The whole of it: the link is lost, the grace passes, the Queen decides, the dispatcher
        # places the task again. Her store held no Handoff of it at any point before this.
        await rig.unreachable.lost(scene.placed.node)
        rig.clock.advance(GRACE_S)
        await rig.unreachable.lost(scene.placed.node)
        await dispatch_ready(scene.deps, rig.queen.wardens)

        spare_end = spare.end
        await spare_end.pump_until(
            lambda: any(sent.task_id == scene.task_id for sent in spare_end.assignments)
        )
        [assignment] = [sent for sent in spare_end.assignments if sent.task_id == scene.task_id]
        assert assignment.resume_from is not None
        assert assignment.handoff is not None and assignment.handoff.goal == "Ship the endpoint."


async def test_the_newest_of_several_deposits_is_the_one_recalled(
    rig: GateRig, tmp_path: Path
) -> None:
    async with _scene(rig, tmp_path) as scene:
        older = make_handoff(rig.clock, progress="Halfway.")
        newer = make_handoff(rig.clock, progress="Nearly done.")
        await _deposit(scene, older, age_s=120)
        await _deposit(scene, newer, age_s=30)

        ref = await _taken_back_from(scene)

        assert ref is not None and (await _filed(scene, ref)).progress == "Nearly done."


async def test_a_deposit_newer_than_the_handoff_she_holds_replaces_it(
    rig: GateRig, tmp_path: Path
) -> None:
    async with _scene(rig, tmp_path) as scene:
        held = await _held(scene, make_handoff(rig.clock, progress="Before the device."))
        rig.clock.advance(60)  # The bee went on after the Handoff the colonization carried.
        await _deposit(scene, make_handoff(rig.clock, progress="On the device."))

        ref = await _taken_back_from(scene)

        assert ref is not None and ref != held
        assert (await _filed(scene, ref)).progress == "On the device."


async def test_the_handoff_she_holds_stands_when_no_deposit_is_newer(
    rig: GateRig, tmp_path: Path
) -> None:
    async with _scene(rig, tmp_path) as scene:
        await _deposit(scene, make_handoff(rig.clock, progress="Older."), age_s=60)
        held = await _held(scene, make_handoff(rig.clock, progress="Held."))

        ref = await _taken_back_from(scene)

        assert ref == held  # Nothing was recalled, so nothing was filed beside it.
        assert await _decided(scene, "handoff_recalled") == []


async def test_a_level0_devices_handoff_is_held_already_and_nothing_is_recalled(
    rig: GateRig, tmp_path: Path
) -> None:
    async with _scene(rig, tmp_path, RuntimeLevel.GATEWAY) as scene:
        handoff = make_handoff(rig.clock)
        held = await _held(scene, handoff)  # Its Warden is on the Hive Stand: her store.
        await _deposit(scene, handoff)  # The same checkpoint, sent up as well.

        ref = await _taken_back_from(scene)

        assert ref == held
        assert await _decided(scene, "handoff_recalled") == []


async def test_a_handoff_the_trail_says_is_tainted_is_not_recalled(
    rig: GateRig, tmp_path: Path
) -> None:
    async with _scene(rig, tmp_path) as scene:
        deposited = await _deposit(scene, make_handoff(rig.clock))
        await _on_trail(scene, deposited.event_id, TAINTED_KIND)

        ref = await _taken_back_from(scene)

        assert ref is None  # The task starts fresh rather than from what may carry an injection.
        [decision] = await _decided(scene, "handoff_not_recalled")
        assert decision["detail"] == "tainted"


async def test_a_tainted_newest_gives_way_to_the_newest_that_is_clean(
    rig: GateRig, tmp_path: Path
) -> None:
    async with _scene(rig, tmp_path) as scene:
        await _deposit(scene, make_handoff(rig.clock, progress="Clean."), age_s=120)
        suspect = await _deposit(scene, make_handoff(rig.clock, progress="Suspect."), age_s=10)
        await _on_trail(scene, suspect.event_id, TAINTED_KIND)

        ref = await _taken_back_from(scene)

        # What a quarantine does by hand: back to the last checkpoint before the suspect episode.
        assert ref is not None and (await _filed(scene, ref)).progress == "Clean."


async def test_a_handoff_a_judge_cleared_is_recalled(rig: GateRig, tmp_path: Path) -> None:
    async with _scene(rig, tmp_path) as scene:
        deposited = await _deposit(scene, make_handoff(rig.clock, progress="Cleared."))
        await _on_trail(scene, deposited.event_id, TAINTED_KIND)
        rig.clock.advance(1)
        await _on_trail(scene, deposited.event_id, TAINT_CLEARED_KIND)

        ref = await _taken_back_from(scene)

        assert ref is not None and (await _filed(scene, ref)).progress == "Cleared."


async def test_a_handoff_tainted_again_after_it_was_cleared_is_not_recalled(
    rig: GateRig, tmp_path: Path
) -> None:
    async with _scene(rig, tmp_path) as scene:
        deposited = await _deposit(scene, make_handoff(rig.clock))
        await _on_trail(scene, deposited.event_id, TAINTED_KIND)
        rig.clock.advance(1)
        await _on_trail(scene, deposited.event_id, TAINT_CLEARED_KIND)
        rig.clock.advance(1)
        await _on_trail(scene, deposited.event_id, TAINTED_KIND)  # A later incident.

        assert await _taken_back_from(scene) is None


async def test_a_handoff_written_since_a_standing_isolation_began_is_not_recalled(
    rig: GateRig, tmp_path: Path
) -> None:
    async with _scene(rig, tmp_path) as scene:
        before = await _deposit(scene, make_handoff(rig.clock, progress="Before."), age_s=300)
        await _deposit(scene, make_handoff(rig.clock, progress="During."), age_s=30)
        # Isolated now, suspecting the Cell's memory from a minute ago. Its Warden was never given
        # the order to label what it wrote since (the link is gone), so no taint row says so.
        await _isolate(scene, suspect_age_s=60)

        ref = await _taken_back_from(scene)

        # The newest is refused for the window it was written in; the one before it is not.
        assert ref is not None and (await _filed(scene, ref)).progress == "Before."
        assert before.event_id != ref.event_id
        [decision] = await _decided(scene, "handoff_recalled")
        assert decision["source_event_id"] == before.event_id


async def test_a_handoff_above_the_tasks_clearance_is_not_recalled(
    rig: GateRig, tmp_path: Path
) -> None:
    async with _scene(rig, tmp_path) as scene:
        await _deposit(scene, make_handoff(rig.clock, clearance=HoneyClearance.C2))

        ref = await _taken_back_from(scene)

        assert ref is None  # The task is C1, as a task is by default.
        [decision] = await _decided(scene, "handoff_not_recalled")
        assert decision["detail"] == "above_clearance"


async def test_a_label_raised_above_the_floor_is_the_label_the_handoff_is_judged_at(
    rig: GateRig, tmp_path: Path
) -> None:
    async with _scene(rig, tmp_path) as scene:
        handoff = make_handoff(rig.clock, clearance=HoneyClearance.C1)
        content = handoff.model_dump_json().encode("utf-8")
        ref = _ref(scene, handoff)
        await _send(scene, ref, content)
        # The same checkpoint again, declared C2: intake merges it and raises the label. A label
        # somebody declared is not the floor's doing, so the C1 the bee gave the document yields.
        raised = ref.model_copy(update={"clearance": HoneyClearance.C2.to_wire()})
        await _send(scene, raised, content)

        assert await _taken_back_from(scene) is None
        [decision] = await _decided(scene, "handoff_not_recalled")
        assert decision["detail"] == "above_clearance"


async def test_a_night_veil_cells_handoff_is_never_recalled(rig: GateRig, tmp_path: Path) -> None:
    async with _scene(rig, tmp_path) as scene:
        handoff = make_handoff(rig.clock)
        await _send(
            scene,
            _ref(scene, handoff),
            handoff.model_dump_json().encode("utf-8"),
            tier=CombShieldLevel.NIGHT_VEIL,
        )

        ref = await _taken_back_from(scene)

        # Kept by intake as the Cell's own side channel; the store never offers it, so there is not
        # even a refusal to record: the Cell's memory does not leave it (ADR-0030).
        assert ref is None
        assert await _decided(scene, "handoff_not_recalled") == []


async def test_another_devices_handoff_of_the_same_task_is_not_recalled(
    rig: GateRig, tmp_path: Path
) -> None:
    async with _scene(rig, tmp_path) as scene:
        other = await rig.enrol()
        handoff = make_handoff(rig.clock)
        await _send(scene, _ref(scene, handoff), handoff.model_dump_json().encode("utf-8"), other)

        assert await _taken_back_from(scene) is None


@pytest.mark.parametrize(
    "content",
    [b"not json at all", b'{"goal": "missing the rest"}'],
    ids=["not-json", "not-a-handoff"],
)
async def test_a_deposit_that_is_not_a_handoff_is_not_recalled(
    rig: GateRig, tmp_path: Path, content: bytes
) -> None:
    async with _scene(rig, tmp_path) as scene:
        await _send(scene, _ref(scene, make_handoff(rig.clock)), content)

        assert await _taken_back_from(scene) is None
        [decision] = await _decided(scene, "handoff_not_recalled")
        assert decision["detail"] == "unreadable"


async def test_a_handoff_that_arrives_already_labelled_is_not_recalled(
    rig: GateRig, tmp_path: Path
) -> None:
    async with _scene(rig, tmp_path) as scene:
        marker = TaintMarker(
            state=TaintState.TAINTED,
            source=TaintSource.QUARANTINE,
            reason="Quarantined.",
            event_id=new_event_id(rig.clock),
            at=rig.clock.now(),
        )
        # A bee writes every Handoff unlabelled and the stores refuse one that arrives labelled,
        # so a document that carries a label was not written by one: it is not filed.
        handoff = make_handoff(rig.clock, tainted=marker)
        await _send(scene, _ref(scene, handoff), handoff.model_dump_json().encode("utf-8"))

        assert await _taken_back_from(scene) is None
        [decision] = await _decided(scene, "handoff_not_recalled")
        assert decision["detail"] == "unreadable"


async def test_a_task_with_nothing_deposited_starts_fresh_as_before(
    rig: GateRig, tmp_path: Path
) -> None:
    async with _scene(rig, tmp_path) as scene:
        assert await _taken_back_from(scene) is None
        assert await _decided(scene, "handoff_not_recalled") == []


async def test_a_queen_with_no_honey_store_resumes_from_what_she_holds(
    rig: GateRig, tmp_path: Path
) -> None:
    async with _scene(rig, tmp_path) as scene:
        held = await _held(scene, make_handoff(rig.clock))
        bare = dataclasses.replace(scene.deps, honey=None)

        await take_back(bare, scene.placed.node, "grace_over")

        assert bare.dispatch.resumes.take(scene.task_id) == held


async def test_only_the_newest_few_deposits_are_looked_through(
    rig: GateRig, tmp_path: Path
) -> None:
    async with _scene(rig, tmp_path) as scene:
        await _deposit(scene, make_handoff(rig.clock, progress="Old but clean."), age_s=1_000)
        for index in range(MAX_CANDIDATES):  # Every one of the newest is refused.
            suspect = await _deposit(
                scene, make_handoff(rig.clock, progress=f"Suspect {index}."), age_s=index
            )
            await _on_trail(scene, suspect.event_id, TAINTED_KIND)

        # The clean one is past the window: an old Handoff is not trusted over starting afresh.
        assert await _taken_back_from(scene) is None
