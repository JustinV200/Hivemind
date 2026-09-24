"""Tests for hivemind.wardens.warden with Exoskeleton work: a Cell that cannot equip a task says so.

Fits into the Hive:
    Exercises hivemind.wardens.ticks.assign's AttachError path through a whole Warden
    (codingrules section 3: a behaviour across several modules gets its own module here).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.spawn.equip for where attach is called.
"""

from __future__ import annotations

import asyncio

from builders.wardens import make_warden_deps
from builders.workers import make_assignment

from hivemind.wardens import Warden
from waggle.clock import Clock
from waggle.ids import GrantId, new_cell_id, new_warden_id
from waggle.messages.forage import AllowedBinding, GrantIssued, SourceRef
from waggle.messages.forage.values import Effort as WireEffort
from waggle.messages.supervision import AlarmKind
from waggle.messages.task import ExoskeletonNeed


def _grant(clock: Clock, grant_id: GrantId) -> GrantIssued:
    source = SourceRef(source_id="local", provider="fake", model="test-model", host_cell_id=None)
    return GrantIssued(
        grant_id=grant_id,
        holder=new_warden_id(clock),
        cell_id=new_cell_id(clock),
        task_id=None,
        revision=0,
        allowed=(AllowedBinding(slot="WORKER", source=source, max_effort=WireEffort.MEDIUM),),
        seats=(),
        token_budget=500_000,
        spend_budget=5.0,
        tokens_spent=0,
        spent=0.0,
        max_sub_bees=1,
        expires_at=clock.now(),
        reason="test grant",
    )


async def test_a_task_its_cell_cannot_equip_raises_exoskeleton_failed_to_the_queen() -> None:
    deps, queen_end, warden_id = make_warden_deps()  # A terminal-only Cell: no display at all.
    assignment = make_assignment(clock=deps.clock, exoskeleton=ExoskeletonNeed())
    warden = Warden(warden_id, deps)
    await warden.start()
    run_task = asyncio.ensure_future(warden.run())

    await queen_end.send(assignment)
    await queen_end.send(_grant(deps.clock, assignment.grant_id))
    alarm = await queen_end.wait_for_alarm()

    await warden.stop()
    await asyncio.wait_for(run_task, timeout=5.0)
    assert alarm.kind is AlarmKind.EXOSKELETON_FAILED
    assert alarm.context.task_id == assignment.task_id
    assert "neither start a display" in alarm.detail
