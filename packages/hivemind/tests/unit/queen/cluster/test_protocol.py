"""Tests for hivemind.queen.cluster.protocol: cluster and resume.

Fits into the Hive:
    Mirrors src/hivemind/queen/cluster/protocol.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.cluster.protocol for the module under test.
    - docs/adr/0024-clustering-protocol.md for the decision these tests hold the code to.
"""

from __future__ import annotations

import asyncio

import pytest
from builders.forage import make_source
from builders.memory import make_handoff
from builders.queen import make_queen_deps, plan_responder

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.forage import AllowedBinding, GrantState, ModelSlot
from hivemind.forage.slots import Effort
from hivemind.llm import FakeLLMProvider
from hivemind.memory import write_checkpoint
from hivemind.memory.context import MemoryContext
from hivemind.pheromone.trail import TrailQuery
from hivemind.queen.cluster import protocol
from hivemind.queen.cluster.protocol import cluster, resume
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.queen import Queen
from hivemind.queen.state import ClusterState
from waggle.ids import TaskId, new_warden_id
from waggle.messages.supervision import InterventionAction

_OTHER_PROVIDER = "other"  # Bound to a second task's grant; proves an unrelated bee is untouched.


def _single_task_plan(goal: str) -> dict[str, object]:
    """The same one-task plan shape `test_queen_dispatch.py` already scripts, reused here."""
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


async def test_cluster_pauses_the_affected_task_and_records_queen_clustered() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    await queen.attach_warden(link)
    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    state = ClusterState()

    outcome = await cluster("fake", "provider_down", deps, state, queen.wardens)

    assert outcome.affected_task_ids == (goal_id,)
    assert outcome.already_clustered is False
    intervene = await warden_end.wait_for_intervene()
    assert intervene.task_id == goal_id
    assert intervene.action is InterventionAction.HANDOFF
    task_pause = await warden_end.wait_for_task_pause()
    assert task_pause.task_id == goal_id
    task = await deps.chamber.get(goal_id)
    assert task.status is TaskStatus.PAUSED
    events = await deps.trail.query(TrailQuery())
    clustered_events = [event for event in events if event.kind == "queen.clustered"]
    assert len(clustered_events) == 1
    assert clustered_events[0].payload["task_ids"] == [goal_id]
    assert state.clustered_providers == frozenset({"fake"})


async def test_cluster_sends_the_task_pause_only_once_the_checkpoint_has_landed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A paused Worker cannot hand off, so the pause must wait for the Handoff it asked for."""
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    await queen.attach_warden(link)
    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    # Records, at the moment each TaskPause is sent, whether the Handoff is already in Bee Bread.
    handoff_present_at_pause: list[bool] = []
    send_task_pause = protocol._send_task_pause

    async def spy(deps_: QueenDeps, link_: WardenLink, task_id: TaskId, reason: str) -> None:
        handoff_present_at_pause.append(await protocol.latest_handoff(deps_, task_id) is not None)
        await send_task_pause(deps_, link_, task_id, reason)

    monkeypatch.setattr(protocol, "_send_task_pause", spy)
    clustering = asyncio.ensure_future(
        cluster("fake", "provider_down", deps, ClusterState(), queen.wardens)
    )

    await warden_end.wait_for_intervene()  # The HANDOFF is out; the Queen waits on the checkpoint.
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    await write_checkpoint(make_handoff(), goal_id, ctx)
    outcome = await clustering

    assert outcome.affected_task_ids == (goal_id,)
    assert handoff_present_at_pause == [True]


async def test_cluster_leaves_leases_and_grants_untouched() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    await queen.attach_warden(link)
    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    grants_before = deps.ledger.live_grants()
    state = ClusterState()

    await cluster("fake", "provider_down", deps, state, queen.wardens)

    assert deps.ledger.live_grants() == grants_before
    assert goal_id  # The task's own placement is untouched: still bound to the same Warden.
    task = await deps.chamber.get(goal_id)
    assert task.warden_id == link.warden_id
    assert task.cell_id == link.cell.id


async def test_cluster_is_idempotent_for_an_already_clustered_provider() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    await queen.attach_warden(link)
    await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    state = ClusterState()
    first = await cluster("fake", "provider_down", deps, state, queen.wardens)
    assert first.already_clustered is False

    second = await cluster("fake", "provider_down", deps, state, queen.wardens)

    assert second.already_clustered is True
    assert second.affected_task_ids == ()


async def test_cluster_never_calls_complete_or_stream_on_the_provider() -> None:
    """Roadmap step 4.9: Clustering never awaits a model."""
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    await queen.attach_warden(link)
    await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    calls_before = len(provider.calls)
    state = ClusterState()

    await cluster("fake", "provider_down", deps, state, queen.wardens)

    assert len(provider.calls) == calls_before


async def test_resume_is_a_no_op_for_a_provider_that_is_not_clustered() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    await queen.attach_warden(link)
    await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    state = ClusterState()

    outcome = await resume("fake", deps, state, queen.wardens)

    assert outcome.already_running is True
    assert outcome.resumed_task_ids == ()


async def test_resume_moves_the_task_back_to_running_and_records_queen_resumed() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    await queen.attach_warden(link)
    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    state = ClusterState()
    await cluster("fake", "provider_down", deps, state, queen.wardens)

    outcome = await resume("fake", deps, state, queen.wardens)

    assert outcome.resumed_task_ids == (goal_id,)
    task = await deps.chamber.get(goal_id)
    assert task.status is TaskStatus.RUNNING
    events = await deps.trail.query(TrailQuery())
    resumed_events = [event for event in events if event.kind == "queen.resumed"]
    assert len(resumed_events) == 1
    assert resumed_events[0].payload["task_ids"] == [goal_id]
    assert state.clustered_providers == frozenset()


async def test_resume_carries_the_handoff_ref_and_never_re_plans() -> None:
    """No duplicated work: the resumed TaskAssign carries `resume_from`, and no fresh plan runs."""
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    await queen.attach_warden(link)
    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    plan_calls_before = len(provider.calls)
    state = ClusterState()
    await cluster("fake", "provider_down", deps, state, queen.wardens)
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    await write_checkpoint(make_handoff(), goal_id, ctx)

    await resume("fake", deps, state, queen.wardens)

    # wait_for_assignment's own `bool(self.assignments)` is already true from the first dispatch
    # (the goal's own fresh TaskAssign); wait for a *second* one to actually pump the resume's own
    # off the transport, rather than re-reading the stale first entry.
    await warden_end.pump_until(lambda: len(warden_end.assignments) >= 2)
    resumed_assign = warden_end.assignments[-1]
    assert resumed_assign.task_id == goal_id
    assert resumed_assign.resume_from is not None
    # No fresh planning call: the goal already has a task graph, and resume never re-plans.
    assert len(provider.calls) == plan_calls_before


async def test_resume_never_calls_complete_or_stream_on_the_provider() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    await queen.attach_warden(link)
    await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    state = ClusterState()
    await cluster("fake", "provider_down", deps, state, queen.wardens)
    calls_before = len(provider.calls)

    await resume("fake", deps, state, queen.wardens)

    assert len(provider.calls) == calls_before


async def test_cluster_never_touches_a_bee_bound_to_a_different_provider() -> None:
    """Roadmap step 4.9: "Bees on other providers continue."""
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    await queen.attach_warden(link)
    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    # A second, synthetic grant on a different provider's source, for a second task never really
    # dispatched: ForageMap exposes no post-construction "add a source" method, so this test pokes
    # its own private table directly (test-only; the module under test never does this).
    other_source = make_source(source_id="other-source", provider=_OTHER_PROVIDER)
    deps.map._sources[other_source.source_id] = other_source
    other_task_id = "other_task"
    other_grant = deps.ledger.live_grants()[0].model_copy(
        update={
            "id": "other_grant",
            "task_id": other_task_id,
            "allowed": (
                AllowedBinding(
                    slot=ModelSlot.WORKER,
                    source_id=other_source.source_id,
                    max_effort=Effort.MEDIUM,
                ),
            ),
            "state": GrantState.ACTIVE,
        }
    )
    await deps.ledger.record_grant(other_grant)
    state = ClusterState()

    outcome = await cluster("fake", "provider_down", deps, state, queen.wardens)

    assert outcome.affected_task_ids == (goal_id,)
    assert other_task_id not in outcome.affected_task_ids
    # The fake-bound task's own Intervene/TaskPause arrived; nothing else did (only one of each).
    await warden_end.wait_for_task_pause()
    assert len(warden_end.intervenes) == 1
    assert len(warden_end.task_pauses) == 1


async def test_pause_on_warden_stops_the_running_tasks_of_that_warden_without_clustering() -> None:
    """A Warden about to move onto its device has its bees checkpoint and stop (ADR-0052)."""
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    await queen.attach_warden(link)
    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    grants_before = deps.ledger.live_grants()

    paused = await protocol.pause_on_warden(
        deps, queen.wardens, link.warden_id, "The Warden moves."
    )

    assert paused == (goal_id,)
    assert (await warden_end.wait_for_intervene()).action is InterventionAction.HANDOFF
    assert (await warden_end.wait_for_task_pause()).task_id == goal_id
    assert (await deps.chamber.get(goal_id)).status is TaskStatus.PAUSED
    assert (
        deps.ledger.live_grants() == grants_before
    )  # Nothing about a provider or a grant changed.
    assert await deps.trail.query(TrailQuery(kind="queen.clustered")) == ()


async def test_pause_on_warden_leaves_the_tasks_of_another_warden_alone() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    await queen.attach_warden(link)
    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()

    paused = await protocol.pause_on_warden(deps, queen.wardens, new_warden_id(deps.clock), "x")

    assert paused == ()
    assert (await deps.chamber.get(goal_id)).status is TaskStatus.RUNNING


async def test_pause_on_warden_skips_a_task_whose_warden_is_no_longer_attached() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    await queen.attach_warden(link)
    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()

    paused = await protocol.pause_on_warden(deps, (), link.warden_id, "x")  # Nobody attached.

    assert paused == ()
    assert (await deps.chamber.get(goal_id)).status is TaskStatus.RUNNING
