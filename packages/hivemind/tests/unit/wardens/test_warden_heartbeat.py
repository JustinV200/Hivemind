"""Tests for Warden: heartbeat aggregation with ChildTelemetry rows, and the WORKER_STALLED path.

Fits into the Hive:
    Mirrors src/hivemind/wardens/warden.py and ticks/heartbeat.py (codingrules section 3); split
    by feature (14.2) from test_warden_lifecycle.py, test_warden_spawn_and_accept.py,
    test_warden_alarms.py and test_warden_forwarding.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.heartbeat for the module under test.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import cast

from builders.wardens import make_warden_deps
from builders.workers import ScriptedWorker, make_assignment

from hivemind.wardens.warden import Warden
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from waggle.clock import Clock, FakeClock
from waggle.ids import GrantId, new_cell_id, new_warden_id
from waggle.messages.forage import AllowedBinding, GrantIssued, SourceRef
from waggle.messages.forage.values import Effort as WireEffort
from waggle.messages.task import TaskAssign, WorkerRole


def _grant(active_clock: Clock, grant_id: GrantId) -> GrantIssued:
    return GrantIssued(
        grant_id=grant_id,
        holder=new_warden_id(active_clock),
        cell_id=new_cell_id(active_clock),
        task_id=None,
        revision=0,
        allowed=(
            AllowedBinding(
                slot="WORKER",
                source=SourceRef(
                    source_id="local", provider="fake", model="test-model", host_cell_id=None
                ),
                max_effort=WireEffort.MEDIUM,
            ),
        ),
        seats=(),
        token_budget=500_000,
        spend_budget=5.0,
        tokens_spent=0,
        spent=0.0,
        max_sub_bees=1,
        expires_at=active_clock.now(),
        reason="test grant",
    )


async def _settle(cycles: int = 20) -> None:
    """Give the background run() task several event-loop turns to react to a clock advance."""
    for _ in range(cycles):
        await asyncio.sleep(0)


def _hang_forever_worker_factory() -> Callable[[WorkerRole], ScriptedWorker]:
    """Build a worker_factory whose sub-bee never claims completion or sends its own heartbeat.

    Both heartbeat tests below need a sub-bee that stays registered through several clock
    advances: `make_assignment`'s own default acceptance criterion (a FILE_EXISTS this script
    never satisfies) would otherwise let the default worker_factory's near-instant CLAIMED
    result reach `hivemind.wardens.ticks.results.handle_accept` and retire the sub-bee before
    either test's own assertions run.
    """

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: object
    ) -> WorkerOutcome:
        # Far beyond every clock.advance() in these tests, and distinct from the Warden's own
        # heartbeat_interval_s, so this sleep's deadline never ties with the Warden's own
        # heartbeat-deadline sleep in waggle.clock.FakeClock's own pending-sleeper queue.
        await ctx.clock.sleep(1_000_000.0)
        raise AssertionError("a hung sub-bee's own role should never wake during this test")

    def factory(role: WorkerRole) -> ScriptedWorker:
        return ScriptedWorker(script, role=role)

    return factory


async def test_heartbeat_carries_a_child_telemetry_row_per_sub_bee() -> None:
    deps, queen_end, warden_id = make_warden_deps(
        worker_factory=_hang_forever_worker_factory(), worker_heartbeat_interval_s=1_000.0
    )
    assignment = make_assignment(clock=deps.clock)
    grant = _grant(deps.clock, assignment.grant_id)
    warden = Warden(warden_id, deps)
    await warden.start()
    run_task = asyncio.ensure_future(warden.run())

    await queen_end.send(assignment)
    await queen_end.send(grant)
    await _settle()

    cast(FakeClock, deps.clock).advance(deps.heartbeat_interval_s + 0.1)
    heartbeat = await queen_end.wait_for_heartbeat()

    assert len(heartbeat.children) == 1
    assert heartbeat.children[0].worker_id == warden.sub_bees[0].worker_id
    assert heartbeat.warden_state is not None

    await warden.stop()
    await asyncio.wait_for(run_task, timeout=5.0)


async def test_a_sub_bee_missing_its_heartbeats_raises_worker_stalled() -> None:
    # The sub-bee's own heartbeat interval is set far beyond every advance below, so only the
    # Warden's own (much shorter) cadence ever fires, and missed_heartbeats accumulates instead
    # of being reset every round by a fresh Heartbeat from the sub-bee.
    #
    # supervision/defaults/default-policy.toml's own WORKER_STALLED rows: min_attempts=1 -> RESPAWN,
    # min_attempts=3 -> ESCALATE -- the same two-respawns-before-escalate shape
    # test_warden_alarms.py's own module docstring documents for WORKER_CRASHED, and for the same
    # reason: `hivemind.wardens.ticks.heartbeat.raise_stalled_alarms` keys its policy lookup on
    # `sub_bee.attempt` (1, 2, 3, ... across every respawn), not on `missed_heartbeats` (which
    # restarts at 0 on every fresh SubBee). A RESPAWN is autopilot-only and never reaches the
    # Queen (same as a first or second WORKER_CRASHED); only the third stall in a row -- one
    # `missed_heartbeats_before_stalled`-worth of missed heartbeats per attempt, three attempts
    # over -- reaches WORKER_STALLED's own ESCALATE row and an AlarmRaised the Queen ever sees.
    deps, queen_end, warden_id = make_warden_deps(
        worker_factory=_hang_forever_worker_factory(),
        heartbeat_interval_s=1.0,
        worker_heartbeat_interval_s=1_000.0,
        missed_heartbeats_before_stalled=3,
    )
    assignment = make_assignment(clock=deps.clock)
    grant = _grant(deps.clock, assignment.grant_id)
    warden = Warden(warden_id, deps)
    await warden.start()
    run_task = asyncio.ensure_future(warden.run())

    await queen_end.send(assignment)
    await queen_end.send(grant)
    await _settle()

    fake_clock = cast(FakeClock, deps.clock)
    for _ in range(9):  # 3 respawned attempts x missed_heartbeats_before_stalled == 3 each.
        fake_clock.advance(1.1)
        await _settle()

    alarm = await queen_end.wait_for_alarm()
    assert alarm.kind.value == "WORKER_STALLED"

    await warden.stop()
    await asyncio.wait_for(run_task, timeout=5.0)
