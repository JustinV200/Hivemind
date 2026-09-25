"""Tests for a Warden's half of its Cell's isolation: a revoked grant, and a pause it ships at once.

Roadmap step 10.6a (ADR-0043). When the Queen isolates this Warden's Cell she revokes its grants
and says so with `GrantRevoked`: the Warden's autopilot records it (bookkeeping, never a model's
call), forgets the grant, drops the assignment parked for it and starts nothing new under it; a
stale replay of an older revision changes nothing. She also waits a bounded time for each bee's
`worker.paused`, which reaches her trail only when this Cell's segment ships, so a bee reporting
itself PAUSED ships the segment at once.

Fits into the Hive:
    Mirrors handle_revoke in src/hivemind/wardens/ticks/assign.py, the GrantRevoked row of
    src/hivemind/wardens/autopilot/table.py and record_progress in src/hivemind/wardens/ticks/
    heartbeat.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.isolation.access for the Queen's side of the revocation.
"""

from __future__ import annotations

import asyncio
from typing import cast

from builders.supervision import make_inbox_item
from builders.wardens import make_warden_deps
from builders.workers import make_assignment

from hivemind.supervision import load_policy
from hivemind.supervision.attendant import InboxKind
from hivemind.wardens.autopilot import WardenAction, decide
from hivemind.wardens.spawn.sub_bee import SubBee
from hivemind.wardens.ticks.assign import handle_grant, handle_revoke
from hivemind.wardens.ticks.heartbeat import record_progress
from hivemind.wardens.warden import Warden
from hivemind.workers.runtime import WorkerRuntime
from hivemind.workers.state import WorkerState
from waggle.clock import Clock
from waggle.codec import Codec
from waggle.ids import GrantId, TaskId, WardenId, new_cell_id, new_grant_id, new_worker_id
from waggle.messages.forage import GrantIssued, GrantRevoked
from waggle.messages.forage.values import RevocationCause
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.task import TaskProgress, TaskStage
from waggle.transport.memory import MemoryTransport


def _grant(clock: Clock, holder: WardenId, grant_id: GrantId, revision: int = 0) -> GrantIssued:
    """A grant of two sub-bees to `holder`, at `revision`."""
    return GrantIssued(
        grant_id=grant_id,
        holder=holder,
        cell_id=new_cell_id(clock),
        task_id=None,
        revision=revision,
        allowed=(),
        seats=(),
        token_budget=500_000,
        spend_budget=5.0,
        tokens_spent=0,
        spent=0.0,
        max_sub_bees=2,
        expires_at=clock.now(),
        reason="test grant",
    )


def _revoked(grant: GrantIssued, revision: int | None = None) -> GrantRevoked:
    """The Queen's revocation of `grant` (at its own revision unless told otherwise)."""
    return GrantRevoked(
        grant_id=grant.grant_id,
        holder=grant.holder,
        revision=grant.revision if revision is None else revision,
        cause=RevocationCause.RECLAIMED,
        reason="Cell isolated.",
    )


class _CountingSync:
    """A `TrailSync` that counts its syncs."""

    def __init__(self) -> None:
        self.syncs = 0

    async def sync(self) -> None:
        self.syncs += 1


def test_a_revocation_is_bookkeeping_never_a_models_call() -> None:
    deps, _queen_end, warden_id = make_warden_deps()
    grant = _grant(deps.clock, warden_id, new_grant_id(deps.clock))
    item = make_inbox_item(InboxKind.WAGGLE_MESSAGE, payload=_revoked(grant))

    assert decide(item, None, load_policy()) is WardenAction.RECORD


async def test_a_revoked_grant_is_forgotten_with_what_was_parked_for_it() -> None:
    deps, _queen_end, warden_id = make_warden_deps()
    warden = Warden(warden_id, deps)
    grant = _grant(deps.clock, warden_id, new_grant_id(deps.clock))
    await handle_grant(warden, grant)
    parked = make_assignment(clock=deps.clock, grant_id=grant.grant_id)
    warden._pending[TaskId(parked.task_id)] = parked

    await handle_revoke(warden, _revoked(grant))

    assert grant.grant_id not in warden._grants
    assert warden._pending == {}
    assert warden._sub_bee_slots.capacity == 0  # Nothing new starts under what is left.


async def test_a_stale_revocation_of_an_older_revision_changes_nothing() -> None:
    deps, _queen_end, warden_id = make_warden_deps()
    warden = Warden(warden_id, deps)
    grant = _grant(deps.clock, warden_id, new_grant_id(deps.clock), revision=2)
    await handle_grant(warden, grant)

    await handle_revoke(warden, _revoked(grant, revision=1))

    assert warden._grants[grant.grant_id] == grant
    assert warden._sub_bee_slots.capacity == 2


async def test_a_bee_reporting_itself_paused_ships_the_trail_at_once() -> None:
    sync = _CountingSync()
    deps, _queen_end, warden_id = make_warden_deps(trail_sync=sync)
    warden = Warden(warden_id, deps)
    sub_bee = _track_a_sub_bee(warden)
    progress = TaskProgress(
        task_id=sub_bee.task_id,
        attempt=1,
        stage=TaskStage.PAUSED,
        summary="Paused: Cell isolated.",
        clearance=WireHoneyClearance.C1,
        fraction_done=None,
        handoff=None,
    )

    await record_progress(warden, sub_bee.worker_id, progress)
    await record_progress(
        warden, sub_bee.worker_id, progress.model_copy(update={"stage": TaskStage.WORKING})
    )

    assert sync.syncs == 1  # The pause shipped; routine progress waits for the heartbeat's sync.
    sub_bee.runtime_task.cancel()


def _track_a_sub_bee(warden: Warden) -> SubBee:
    """Track a minimal SubBee (never a running WorkerRuntime), as the heartbeat tests do."""
    link, _peer = MemoryTransport.pair(Codec(), Codec())
    assignment = make_assignment(clock=warden._deps.clock)
    sub_bee = SubBee(
        worker_id=new_worker_id(warden._deps.clock),
        task_id=assignment.task_id,
        assignment=assignment,
        attempt=1,
        state=WorkerState.RUNNING,
        binding="worker",
        last_handoff=None,
        link=link,
        runtime=cast(WorkerRuntime, None),
        runtime_task=asyncio.ensure_future(asyncio.sleep(0)),
        last_telemetry=None,
    )
    warden._sub_bees[sub_bee.worker_id] = sub_bee
    return sub_bee
