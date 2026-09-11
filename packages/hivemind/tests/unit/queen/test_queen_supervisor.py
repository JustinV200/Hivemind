"""Tests for hivemind.queen.queen.Queen: the Supervisor protocol over attached Wardens.

Fits into the Hive:
    Mirrors src/hivemind/queen/queen.py (codingrules section 3); split by feature (14.2) from
    test_queen_dispatch.py, test_queen_results.py, test_queen_alarms.py,
    test_queen_questions.py, test_queen_liveness.py and test_queen_invariants.py. Exercises
    `children`/`telemetry`/`inspect`/`intervene`, the `hivemind.supervision.supervisor.Supervisor`
    protocol Queen implements over her attached Wardens (never over Workers directly).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.queen for Queen, the Supervisor implementation under test.
    - hivemind.supervision.supervisor for Supervisor, ChildKind and ChildRef.
"""

from __future__ import annotations

import asyncio

import pytest
from builders.queen import make_queen_deps
from builders.supervision import make_telemetry

from hivemind.llm import FakeLLMProvider
from hivemind.queen.errors import UnknownWardenError
from hivemind.queen.queen import Queen
from hivemind.supervision import ChildKind
from hivemind.supervision.intervention import Compact
from waggle.ids import WardenId
from waggle.messages.supervision import Heartbeat, InterventionAction, WardenState


def _heartbeat() -> Heartbeat:
    return Heartbeat(
        telemetry=make_telemetry(goal="Ship the haiku."),
        task_id=None,
        worker_state=None,
        warden_state=WardenState.ACTIVE,
        children=(),
        grant_id=None,
        grant_spend=None,
        interval_s=5.0,
    )


async def test_children_returns_one_child_ref_per_attached_warden() -> None:
    deps, link, warden_end = make_queen_deps(fake_provider=FakeLLMProvider())
    queen = Queen(deps)
    queen.attach_warden(link)

    children = await queen.children()

    assert len(children) == 1
    assert children[0].id == link.warden_id
    assert children[0].kind is ChildKind.WARDEN
    assert children[0].state == "STARTING"  # No Heartbeat reported yet.
    await warden_end.close()


async def test_telemetry_and_inspect_read_the_last_reported_heartbeat() -> None:
    deps, link, warden_end = make_queen_deps(fake_provider=FakeLLMProvider())
    queen = Queen(deps)
    queen.attach_warden(link)
    run_task = asyncio.ensure_future(queen.run())

    await warden_end.send(_heartbeat())
    await _wait_until_reported(queen, link.warden_id)

    telemetry = await queen.telemetry(link.warden_id)
    view = await queen.inspect(link.warden_id)

    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    assert telemetry.goal == "Ship the haiku."
    assert view.goal == "Ship the haiku."
    assert "tokens used" in view.progress
    await warden_end.close()


async def test_telemetry_raises_for_a_warden_the_queen_never_attached() -> None:
    deps, _link, warden_end = make_queen_deps(fake_provider=FakeLLMProvider())
    queen = Queen(deps)

    with pytest.raises(UnknownWardenError):
        await queen.telemetry("warden_does_not_exist")
    await warden_end.close()


async def test_inspect_raises_for_a_warden_the_queen_never_attached() -> None:
    deps, _link, warden_end = make_queen_deps(fake_provider=FakeLLMProvider())
    queen = Queen(deps)

    with pytest.raises(UnknownWardenError):
        await queen.inspect("warden_does_not_exist")
    await warden_end.close()


async def test_intervene_sends_the_intervention_to_the_named_wardens_own_link() -> None:
    deps, link, warden_end = make_queen_deps(fake_provider=FakeLLMProvider())
    queen = Queen(deps)
    queen.attach_warden(link)

    await queen.intervene(link.warden_id, Compact(reason="Its context is getting long."))
    intervene = await warden_end.wait_for_intervene()

    assert intervene.action is InterventionAction.COMPACT
    assert intervene.reason == "Its context is getting long."
    assert intervene.task_id is None
    await warden_end.close()


async def test_intervene_raises_for_a_warden_the_queen_never_attached() -> None:
    deps, _link, warden_end = make_queen_deps(fake_provider=FakeLLMProvider())
    queen = Queen(deps)

    with pytest.raises(UnknownWardenError):
        await queen.intervene("warden_does_not_exist", Compact(reason="Unreachable."))
    await warden_end.close()


async def _wait_until_reported(queen: Queen, warden_id: WardenId, limit: int = 200) -> None:
    for _ in range(limit):
        try:
            await queen.telemetry(warden_id)
            return
        except UnknownWardenError:
            await asyncio.sleep(0)
    raise AssertionError("The Heartbeat was never recorded.")
