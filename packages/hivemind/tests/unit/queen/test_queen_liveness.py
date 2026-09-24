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
from typing import cast

from builders.forage import make_grant
from builders.queen import make_queen_deps, plan_responder
from builders.supervision import make_telemetry

from hivemind.forage.grant_state import GrantState
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


async def test_a_heartbeat_past_the_handoff_threshold_orders_an_intervene() -> None:
    # Roadmap step 4.6: "The Queen watches Warden telemetry and orders compact or handoff past
    # thresholds." deps.memory_budget's own defaults (compact_at=0.5, handoff_threshold=0.66,
    # hivemind.queen.deps._DEFAULT_COMPACT_AT/_DEFAULT_HANDOFF_THRESHOLD) are what this Heartbeat's
    # own telemetry is built to cross.
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    queen.attach_warden(link)
    run_task = asyncio.ensure_future(queen.run())
    full_telemetry = make_telemetry(tokens_used=7_500, context_window=8_192)  # ~92% full.
    heartbeat = Heartbeat(
        telemetry=full_telemetry,
        task_id=None,
        worker_state=None,
        warden_state=WardenState.ACTIVE,
        children=(),
        grant_id=None,
        grant_spend=None,
        interval_s=5.0,
    )

    await warden_end.send(heartbeat)
    intervene = await warden_end.wait_for_intervene()

    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    assert intervene.action.value == "HANDOFF"
    await warden_end.close()


async def test_a_closed_link_during_the_handoff_intervene_never_stops_liveness() -> None:
    """Phase-7 handoff open item 8: a closed link must not stop liveness from running after it.

    Mirrors test_a_heartbeat_past_the_handoff_threshold_orders_an_intervene's own setup, but the
    Warden's own end closes right after sending: `_watch_context`'s guarded send
    (hivemind.queen.ticks.liveness) must swallow the failure rather than crash the tick, so
    `check_liveness` -- sequenced right after item-handling in that very same `_run_tick` -- keeps
    running every tick afterwards and eventually marks this Warden offline.
    """
    deps, link, warden_end = make_queen_deps()
    queen = Queen(deps)
    queen.attach_warden(link)
    run_task = asyncio.ensure_future(queen.run())
    full_telemetry = make_telemetry(tokens_used=7_500, context_window=8_192)  # ~92% full.
    heartbeat = Heartbeat(
        telemetry=full_telemetry,
        task_id=None,
        worker_state=None,
        warden_state=WardenState.ACTIVE,
        children=(),
        grant_id=None,
        grant_spend=None,
        interval_s=5.0,
    )

    # Queued before the close, so the Queen still drains this heartbeat; its own reply-send
    # (the HANDOFF Intervene) then finds the link already gone.
    await warden_end.send(heartbeat)
    await warden_end.close()
    # heartbeat_miss_limit=3, heartbeat_interval_s=5.0 (builders.queen's own defaults): past 15s
    # with no further heartbeat (none can ever arrive now) crosses the offline limit.
    cast(FakeClock, deps.clock).advance(deps.heartbeat_interval_s * deps.heartbeat_miss_limit + 1.0)

    async def _offline() -> bool:
        current = queen.liveness.get(link.warden_id)
        return current is not None and current.is_offline

    await _wait_until(_offline)  # Only reachable if check_liveness kept running every tick.

    assert not run_task.done()  # The guarded send never ended the loop.
    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)


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


async def test_heartbeat_renews_live_grants_for_that_warden() -> None:
    # roadmap step 4.7: "renewed on the Warden's heartbeat."
    clock = FakeClock()
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(clock, fake_provider=provider)
    grant = make_grant(clock=clock, holder=link.warden_id, state=GrantState.ACTIVE)
    await deps.ledger.record_grant(grant)
    original_expiry = grant.expires_at
    clock.advance(120.0)  # Time passes before the heartbeat arrives.
    queen = Queen(deps)
    queen.attach_warden(link)
    run_task = asyncio.ensure_future(queen.run())

    await warden_end.send(_heartbeat())

    async def _renewed() -> bool:
        current = deps.ledger.grant(grant.id)
        return current is not None and current.expires_at > original_expiry

    await _wait_until(_renewed)

    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)
    await warden_end.close()


def test_check_liveness_returns_an_expired_grant_to_the_pool() -> None:
    # roadmap step 4.7's own exit criterion: "A Warden whose heartbeat stops has its grant back
    # in the pool after expiry."
    clock = FakeClock()
    deps, link, _warden_end = make_queen_deps(clock)
    grant = make_grant(
        clock=clock, holder=link.warden_id, state=GrantState.ACTIVE, expires_at=clock.now()
    )
    asyncio.run(deps.ledger.record_grant(grant))
    human_inbox = HumanInbox()
    liveness = {
        link.warden_id: WardenLiveness(
            last_heartbeat_at=clock.now(), missed_heartbeats=0, is_offline=False
        )
    }
    clock.advance(1.0)  # Past the grant's own expiry, with no renewal in between.

    asyncio.run(check_liveness(deps, (link,), liveness, human_inbox))

    assert deps.ledger.grant(grant.id) is None
    assert grant.id not in {g.id for g in deps.ledger.live_grants()}


async def _wait_until(condition: Callable[[], Awaitable[bool]], limit: int = 200) -> None:
    """Yield the event loop until `condition()` (an async callable) is True, or give up."""
    for _ in range(limit):
        if await condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("Condition never became true.")
