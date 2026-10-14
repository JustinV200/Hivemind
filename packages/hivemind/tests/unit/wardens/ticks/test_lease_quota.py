"""Tests for hivemind.wardens.ticks.lease: the scratch quota a Warden's lease request names.

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/lease.py (codingrules section 3); split by feature (14.2)
    from test_lease.py and test_lease_device.py. The scratch one lease may hold is the Queen's to
    size (roadmap step 11.4): the Warden reads `Ceilings.scratch_disk_bytes_per_lease` when it
    makes each lease request, so a Cell that enforces a quota of its own (a device's packet) is
    asked to hold the lease to it. These tests read the request off a recording source, which is
    the only thing the Warden hands to the Cell.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.lease for the module under test.
    - hivemind.forage.Ceilings for the figure and its 0, which means "the Queen set none".
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable
from pathlib import Path
from typing import cast

from builders.quarantine import make_grant_issued, settle
from builders.wardens import make_warden_deps
from builders.workers import ScriptedWorker, make_assignment, make_outcome

from hivemind.cell import LeaseRequest, RealCellLease
from hivemind.cell.fake import FakeCellSource
from hivemind.cell.source import CellIdentity
from hivemind.forage import Ceilings
from hivemind.wardens import DeviceDeps, WardenDeps
from hivemind.wardens.ticks.control import handle_ceilings_set
from hivemind.wardens.warden import Warden
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from waggle.messages.forage import CeilingsSet
from waggle.messages.labels import Postcondition, PostconditionKind
from waggle.messages.task import TaskAssign, TaskOutcome, WorkerRole

_QUOTA_BYTES = 2_097_152  # The Queen's figure in these tests: 2 MiB, nothing like any default.


class _RecordingSource(FakeCellSource):
    """A fake source that remembers every request it was asked to lease for."""

    def __init__(self, base: FakeCellSource, deps: WardenDeps) -> None:
        identity = CellIdentity(
            hive_id=deps.identity.hive_id, node_id=deps.identity.node_id, actor="system"
        )
        # The same inventory the Warden's default source holds, so it leases the same Cell.
        super().__init__(base._cells.values(), identity, deps.trail, deps.clock)
        self.requests: list[LeaseRequest] = []

    async def lease(self, request: LeaseRequest) -> RealCellLease:
        self.requests.append(request)
        return await super().lease(request)


def _ceilings(scratch_disk_bytes_per_lease: int) -> Ceilings:
    """Ceilings that name `scratch_disk_bytes_per_lease` and nothing else of note."""
    return Ceilings(
        max_sub_bees=2,
        model_vram_bytes=0,
        model_disk_bytes=0,
        loadable_sources=(),
        exportable_seats=0,
        scratch_disk_bytes_per_lease=scratch_disk_bytes_per_lease,
    )


async def _ceilings_set(deps: WardenDeps, warden: Warden, scratch: int) -> CeilingsSet:
    """The Queen's `forage.ceilings_set` for `warden`'s Cell, naming `scratch` per lease."""
    (cell,) = await deps.source.cells()
    return CeilingsSet(
        cell_id=cell.id,
        holder=warden._warden_id,
        revision=0,
        ceilings=_ceilings(scratch).to_wire(),
        reason="The Queen sized the scratch a lease may hold.",
    )


def _recording() -> tuple[WardenDeps, _RecordingSource, Warden]:
    """A Warden over a recording source, not yet started."""
    deps, _queen_end, warden_id = make_warden_deps()
    source = _RecordingSource(cast(FakeCellSource, deps.source), deps)
    deps = dataclasses.replace(deps, source=source)
    return deps, source, Warden(warden_id, deps)


async def test_a_warden_the_queen_has_not_sized_asks_for_no_scratch_quota() -> None:
    _deps, source, warden = _recording()

    await warden.start()

    # Nothing was said about the scratch: the Cell's own default stands.
    (request,) = source.requests
    assert request.scratch_quota_bytes is None
    await warden.stop()


async def test_a_warden_asks_for_the_scratch_quota_its_ceilings_carry() -> None:
    deps, source, warden = _recording()
    handle_ceilings_set(warden, await _ceilings_set(deps, warden, _QUOTA_BYTES))

    await warden.start()

    (request,) = source.requests
    assert request.scratch_quota_bytes == _QUOTA_BYTES
    await warden.stop()


async def test_ceilings_that_name_no_scratch_figure_ask_for_no_quota_not_for_zero_bytes() -> None:
    deps, source, warden = _recording()
    # On the wire 0 is "no ceiling": a quota of 0 bytes would refuse every write the lease makes.
    handle_ceilings_set(warden, await _ceilings_set(deps, warden, 0))

    await warden.start()

    (request,) = source.requests
    assert request.scratch_quota_bytes is None
    await warden.stop()


async def test_ceilings_that_arrive_after_the_first_lease_size_the_next_one() -> None:
    # A device's Warden leases when it starts, long before the Queen's first dispatch sets its
    # ceilings, and leases again for the task that dispatch carries.
    deps, queen_end, warden_id = make_warden_deps(
        device=DeviceDeps(), worker_factory=_writes_output()
    )
    source = _RecordingSource(cast(FakeCellSource, deps.source), deps)
    deps = dataclasses.replace(deps, source=source)
    warden = Warden(warden_id, deps)
    await warden.start()
    run_task = asyncio.ensure_future(warden.run())
    assignment = _assignment(deps)
    await queen_end.send(make_grant_issued(deps.clock, assignment))
    await settle(lambda: warden.lease is None)  # Idle: the start-up lease is given back.

    await queen_end.send(await _ceilings_set(deps, warden, _QUOTA_BYTES))
    await queen_end.send(assignment)  # No lease held: the Warden leases again, then spawns.
    result = await queen_end.wait_for_result()

    assert result.outcome is TaskOutcome.SUCCEEDED
    assert [request.scratch_quota_bytes for request in source.requests] == [None, _QUOTA_BYTES]
    await warden.stop()
    await asyncio.wait_for(run_task, timeout=5.0)  # A hung stop fails here, not the suite.


def _writes_output() -> Callable[[WorkerRole], ScriptedWorker]:
    """A worker factory whose worker writes the file the assignment wants, then claims success."""

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: object
    ) -> WorkerOutcome:
        await asyncio.sleep(0)
        await ctx.session.put_file(Path("output.txt"), b"done")
        return make_outcome()

    def factory(role: WorkerRole) -> ScriptedWorker:
        return ScriptedWorker(script, role=role)

    return factory


def _assignment(deps: WardenDeps) -> TaskAssign:
    """Build a task whose acceptance is the file `_writes_output` writes."""
    return make_assignment(
        clock=deps.clock,
        acceptance=(
            Postcondition(
                kind=PostconditionKind.FILE_EXISTS, subject="output.txt", argv=(), expected=None
            ),
        ),
    )
