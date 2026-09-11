"""Tests for hivemind.queen.ticks.liveness: Heartbeat tracking and MARK_WARDEN_OFFLINE.

Fits into the Hive:
    Mirrors src/hivemind/queen/ticks/liveness.py (codingrules section 3); split by feature (14.2)
    from test_queen_dispatch.py, test_queen_results.py, test_queen_alarms.py,
    test_queen_questions.py, test_queen_supervisor.py and test_queen_invariants.py. The first test
    drives a real Heartbeat through the Queen's own tick loop; `check_liveness` itself is exercised
    directly (as `hivemind.queen.autopilot.table` is in test_table.py), since going offline is a
    pure function of elapsed wall-clock time that a live tick loop only ever wakes on a new
    envelope (module docstring of hivemind.queen.queen: "never gated behind an inbox item").

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.ticks.liveness for the module under test.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from builders.queen import make_queen_deps, plan_responder
from builders.supervision import make_telemetry

from hivemind.llm import FakeLLMProvider
from hivemind.queen.human_inbox import HumanInbox
from hivemind.queen.queen import Queen
from hivemind.queen.ticks.liveness import WardenLiveness, check_liveness
from waggle.clock import FakeClock
from waggle.messages.supervision import Heartbeat, WardenState


def _single_task_plan(goal: str) -> dict[str, object]:
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


def _heartbeat() -> Heartbeat:
    return Heartbeat(
        telemetry=make_telemetry(),
        task_id=None,
        worker_state=None,
        warden_state=WardenState.ACTIVE,
        children=(),
        grant_id=None,
        grant_spend=None,
        interval_s=5.0,
    )


async def test_heartbeat_updates_the_queens_own_liveness_view() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    queen.attach_warden(link)
    run_task = asyncio.ensure_future(queen.run())

    await warden_end.send(_heartbeat())

    async def _seen() -> bool:
        current = queen.liveness.get(link.warden_id)
        return current is not None and current.last_heartbeat_at is not None

    await _wait_until(_seen)

    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    current = queen.liveness[link.warden_id]
    assert current.missed_heartbeats == 0
    assert not current.is_offline
    await warden_end.close()


def test_check_liveness_never_flags_a_warden_before_its_first_heartbeat() -> None:
    clock = FakeClock()
    deps, link, _warden_end = make_queen_deps(clock)
    liveness = {
        link.warden_id: WardenLiveness(
            last_heartbeat_at=None, missed_heartbeats=0, is_offline=False
        )
    }
    human_inbox = HumanInbox()

    asyncio.run(check_liveness(deps, (link,), liveness, human_inbox))

    assert not liveness[link.warden_id].is_offline
    assert not human_inbox.alarms


def test_check_liveness_marks_a_warden_offline_exactly_once() -> None:
    clock = FakeClock()
    deps, link, _warden_end = make_queen_deps(clock)
    human_inbox = HumanInbox()
    liveness = {
        link.warden_id: WardenLiveness(
            last_heartbeat_at=clock.now(), missed_heartbeats=0, is_offline=False
        )
    }
    # heartbeat_miss_limit=3, heartbeat_interval_s=5.0 (builders.queen's own defaults): past 15s
    # with no fresh Heartbeat crosses the limit.
    clock.advance(deps.heartbeat_interval_s * deps.heartbeat_miss_limit + 1.0)

    asyncio.run(check_liveness(deps, (link,), liveness, human_inbox))
    first_pass_alarms = human_inbox.alarms
    assert liveness[link.warden_id].is_offline
    assert len(first_pass_alarms) == 1
    assert first_pass_alarms[0].context.cell_id == link.cell.id

    # A second sweep with nothing changed must never raise a second Alarm for the same outage.
    asyncio.run(check_liveness(deps, (link,), liveness, human_inbox))
    assert human_inbox.alarms == first_pass_alarms


async def _wait_until(condition: Callable[[], Awaitable[bool]], limit: int = 200) -> None:
    """Yield the event loop until `condition()` (an async callable) is True, or give up."""
    for _ in range(limit):
        if await condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("Condition never became true.")
