"""Tests for hivemind.queen.cluster.protocol.resume: the Handoff a clustered task resumes from.

Clustering pauses every bee bound to a provider and resumes each from its Handoff when the provider
returns (ADR-0024). A Warden that is not the Queen's own (a colonized device's, a Virtual Cell's)
keeps its bees' Handoffs in a store of its own, so what the Queen's memory holds is at most an older
checkpoint, and the newest she can hold is the HANDOFF Nectar the bee deposited. Resume used to read
only her memory, and so resumed a task from nothing, or from an older Handoff, though the bee had
gone further. Every test here starts with her memory empty (or holding only an older checkpoint) and
the Honey Store holding the deposit, which is how that defect hid from tests that seeded her store.

Fits into the Hive:
    Mirrors src/hivemind/queen/cluster/protocol.py (codingrules section 3), split by feature (14.2)
    from test_protocol.py. The recall's own rules are held in
    tests/unit/queen/swarm_gate/test_recall.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.cluster.protocol for the module under test.
    - hivemind.queen.swarm_gate.recall for the Handoff it asks for.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path

import pytest
from builders.memory import make_handoff
from builders.queen import WardenEnd, make_queen_deps, plan_responder
from builders.recall import Deposits, Refusal, Site, make_deposits

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.llm import FakeLLMProvider
from hivemind.queen.cluster.protocol import cluster, resume
from hivemind.queen.queen import Queen
from hivemind.queen.state import ClusterState
from waggle.clock import FakeClock
from waggle.ids import TaskId
from waggle.messages.task import TaskAssign


def _plan(goal: str) -> dict[str, object]:
    """A one-task plan, as test_protocol.py scripts it."""
    return {
        "tasks": [
            {
                "key": "root",
                "title": "Root task",
                "objective": f"Do the work for: {goal}",
                "acceptance": [
                    {
                        "kind": "FILE_EXISTS",
                        "subject": "scratch/done.txt",
                        "argv": [],
                        "expected": None,
                    }
                ],
                "needs": {},
                "clearance": "C1",
                "depends_on": [],
            }
        ]
    }


@dataclass
class _Scene:
    """A task Clustering paused, on a Warden whose bee deposited a Handoff she holds none of."""

    clock: FakeClock
    deposits: Deposits
    queen: Queen
    end: WardenEnd
    state: ClusterState
    task_id: TaskId
    site: Site

    async def resumed(self) -> TaskAssign:
        """Resume the provider; the assignment the Warden is then sent for the task."""
        before = len(self.end.assignments)

        outcome = await resume("fake", self.deposits.deps, self.state, self.queen.wardens)

        assert outcome.resumed_task_ids == (self.task_id,)
        # The first dispatch is already there; the resume's own is the next to arrive.
        await self.end.pump_until(lambda: len(self.end.assignments) > before)
        return self.end.assignments[-1]


async def _clustered(tmp_path: Path, *, hold: str | None = None) -> _Scene:
    """Run a task, pause it by Clustering, and give the Queen a Honey Store for the resume.

    Args:
        tmp_path: A writable directory for the Honey Store.
        hold: A progress line for a checkpoint the Queen already holds in her own memory (the one
            a move carried), or None for her memory to be empty.
    """
    clock = FakeClock()
    provider = FakeLLMProvider(responder=plan_responder(_plan))
    deps, link, end = make_queen_deps(clock, fake_provider=provider)
    queen = Queen(deps)
    await queen.attach_warden(link)
    task_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await end.wait_for_assignment()
    deposits = await make_deposits(deps, clock, tmp_path)
    if hold is not None:
        await deposits.hold(task_id, make_handoff(clock, progress=hold))
    state = ClusterState()
    await cluster("fake", "provider_down", deps, state, queen.wardens)
    assert (await deps.chamber.get(task_id)).status is TaskStatus.PAUSED
    site = Site(link.warden_id, link.cell.id, task_id)
    return _Scene(clock, deposits, queen, end, state, task_id, site)


async def test_resume_goes_back_to_the_handoff_the_bee_deposited_with_her_own_store_empty(
    tmp_path: Path,
) -> None:
    scene = await _clustered(tmp_path)
    deposited = await scene.deposits.deposit(
        scene.site, make_handoff(scene.clock, progress="Nearly done.")
    )

    assignment = await scene.resumed()

    # She filed the recalled Handoff as a checkpoint of her own, and the task resumes from that.
    assert assignment.resume_from is not None
    assert assignment.resume_from.event_id != deposited.event_id
    assert (await scene.deposits.held(assignment.resume_from)).progress == "Nearly done."


async def test_a_deposit_newer_than_the_checkpoint_she_holds_is_the_one_resumed_from(
    tmp_path: Path,
) -> None:
    scene = await _clustered(tmp_path, hold="Before the outage.")
    scene.clock.advance(60)  # The bee went on after the checkpoint she holds.
    await scene.deposits.deposit(
        scene.site, make_handoff(scene.clock, progress="During the outage.")
    )

    assignment = await scene.resumed()

    assert assignment.resume_from is not None
    assert (await scene.deposits.held(assignment.resume_from)).progress == "During the outage."


async def test_the_checkpoint_she_holds_stands_when_no_deposit_is_newer(tmp_path: Path) -> None:
    scene = await _clustered(tmp_path, hold="Held.")

    assignment = await scene.resumed()

    assert assignment.resume_from is not None
    assert (await scene.deposits.held(assignment.resume_from)).progress == "Held."
    assert await scene.deposits.decided("handoff_recalled") == []


@pytest.mark.parametrize("case", list(Refusal), ids=lambda case: case.value)
async def test_resume_refuses_what_every_recall_refuses(tmp_path: Path, case: Refusal) -> None:
    scene = await _clustered(tmp_path)
    await scene.deposits.refused(scene.site, case)

    assignment = await scene.resumed()

    # The task resumes all the same, from nothing: a refusal is never an error.
    assert assignment.resume_from is None
    refusals = [row["detail"] for row in await scene.deposits.decided("handoff_not_recalled")]
    assert refusals == ([case.recorded] if case.recorded is not None else [])


async def test_a_queen_with_no_honey_store_resumes_from_what_she_holds(tmp_path: Path) -> None:
    scene = await _clustered(tmp_path, hold="Held.")
    bare = dataclasses.replace(scene.deposits.deps, honey=None)
    before = len(scene.end.assignments)

    outcome = await resume("fake", bare, scene.state, scene.queen.wardens)

    assert outcome.resumed_task_ids == (scene.task_id,)
    await scene.end.pump_until(lambda: len(scene.end.assignments) > before)
    resumed_from = scene.end.assignments[-1].resume_from
    assert resumed_from is not None
    assert (await scene.deposits.held(resumed_from)).progress == "Held."
