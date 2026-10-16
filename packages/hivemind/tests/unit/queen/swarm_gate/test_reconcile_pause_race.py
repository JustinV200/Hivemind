"""Tests for the swarm gate surviving a Brood Chamber row the Queen's tick moved under a pass.

The gate's passes and flows run beside the Queen's tick and move the same chamber rows. A bee that
finishes while a rebuild's pause waits for its Handoff is the ordinary case; the pass must leave
the task where the tick put it and go on. Before the fix the chamber's `InvalidTransitionError`
escaped the pause, the sweep and the gate loop's recoverable set, and ended the whole Hive.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/reconcile.py and src/hivemind/queen/swarm_gate/errors.py
    (codingrules section 3, split by feature per 14.2): a Level 0 device with a RUNNING task whose
    terms change, so the pass rebuilds its Warden and pauses its bees first.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.cluster.protocol for the pause that reads the task again after its wait.
    - hivemind.queen.swarm_gate.errors for the one recoverable set every catch in the gate uses.
"""

from __future__ import annotations

import pytest
from builders.swarm import GateRig, place_on_device

from hivemind.brood_chamber import InvalidTransitionError, TaskOutcome, TaskStatus
from hivemind.cell import AccessLevel
from hivemind.queen.cluster import protocol
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.swarm_gate.errors import CHAMBER_CONFLICTS
from hivemind.queen.swarm_gate.gate import _GateLoop
from hivemind.swarm import SwarmNode
from hivemind.swarm.enrolment.capabilities import granted_capabilities
from waggle.ids import TaskId


async def _change_terms(rig: GateRig, node: SwarmNode) -> SwarmNode:
    """Narrow the device's access, so the next pass rebuilds its Level 0 Warden."""
    await rig.hive.registry.set_access(
        node.device_id,
        AccessLevel.READ_ONLY,
        granted_capabilities(AccessLevel.READ_ONLY, node.cell_id),
    )
    changed = await rig.hive.registry.node(node.device_id)
    assert changed is not None
    return changed


async def test_a_task_that_finishes_while_the_rebuilds_pause_waits_is_left_finished(
    rig: GateRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = protocol._send_handoff_intervene

    async def complete_then_send(
        deps: QueenDeps, link: WardenLink, task_id: TaskId, reason: str
    ) -> None:
        # The Queen's tick lands the bee's result while the pause is waiting on the Handoff.
        await deps.chamber.complete(
            task_id,
            TaskOutcome(status=TaskStatus.SUCCEEDED, summary="done", verified_by=link.warden_id),
        )
        await real(deps, link, task_id, reason)

    monkeypatch.setattr(protocol, "_send_handoff_intervene", complete_then_send)
    async with rig.serving():
        placed = await place_on_device(rig)
        await _change_terms(rig, placed.node)

        await rig.reconciler.sweep()  # The gate loop's own pass: it must not raise.

        task = await rig.queen_deps.chamber.get(placed.task.id)
        assert task.status is TaskStatus.SUCCEEDED  # Left where the tick put it, never paused.
        rebuilt = rig.wardens.get(placed.node.device_id)
        assert rebuilt is not None and rebuilt.node.access_level is AccessLevel.READ_ONLY


async def test_a_chamber_conflict_in_one_nodes_sync_does_not_end_the_pass(
    rig: GateRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def conflict(*_args: object, **_kwargs: object) -> tuple[TaskId, ...]:
        raise InvalidTransitionError(TaskStatus.SUCCEEDED, TaskStatus.PAUSED)

    monkeypatch.setattr("hivemind.queen.swarm_gate.reconcile.pause_on_warden", conflict)
    async with rig.serving():
        placed = await place_on_device(rig)
        await _change_terms(rig, placed.node)
        other = await rig.enrol()
        await rig.connect(other)

        await rig.reconciler.sweep()  # Logged for the one node; the rest of the pass goes on.

        assert rig.wardens.get(other.device_id) is not None  # The other device was still served.


def test_the_gate_loop_survives_every_chamber_conflict() -> None:
    # The loop's set is the one every catch in the gate uses: a chamber row the tick moved is as
    # ordinary as a device that went away, and must never end the loop (and with it the Hive).
    assert set(CHAMBER_CONFLICTS) <= set(_GateLoop._recoverable_errors)
