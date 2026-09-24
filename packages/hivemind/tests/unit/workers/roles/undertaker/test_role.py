"""Tests for hivemind.workers.roles.undertaker.role: Undertaker, its two operations and Protocols.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/undertaker/role.py (codingrules section 3: tests/unit
    mirrors src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.undertaker.role for the module under test.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest
from builders.cells import make_cell, make_identity, make_real_cell_lease
from builders.workers import make_assignment, make_context
from unit.workers.roles.undertaker.fakes import (
    FlakyDestroyBackend,
    FlakyLeaseReleaser,
    RecordingGrantRevoker,
    RecordingLeavingsRemover,
    RecordingWaxRetirer,
)

from hivemind.cell import CellKind, LeaseState, SessionClosedError
from hivemind.cell.fake import FakeLeaseReleaser
from hivemind.hive import CellBackend, CellDestroyError
from hivemind.hive.backends.fake import FakeCellBackend
from hivemind.pheromone import TrailQuery
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.workers.roles.undertaker.role import (
    NullLeavingsRemover,
    NullWaxRetirer,
    RetryPolicy,
    Undertaker,
    UndertakerDeps,
)
from waggle.clock import FakeClock
from waggle.ids import new_cell_id
from waggle.messages.task import WorkerRole


@dataclass(frozen=True, slots=True)
class _Rig:
    """One test's own Undertaker plus its concrete (not Protocol-typed) recorder collaborators."""

    undertaker: Undertaker
    backend: CellBackend
    grant_revoker: RecordingGrantRevoker
    wax_retirer: RecordingWaxRetirer
    leavings_remover: RecordingLeavingsRemover
    trail: MemoryPheromoneTrail


def _make_rig(
    clock: FakeClock,
    *,
    backend: CellBackend | None = None,
    retry: RetryPolicy | None = None,
) -> _Rig:
    """Build an Undertaker over fresh, concretely-typed fakes for one test."""
    fake_backend = backend if backend is not None else FakeCellBackend(clock)
    grant_revoker = RecordingGrantRevoker()
    wax_retirer = RecordingWaxRetirer()
    leavings_remover = RecordingLeavingsRemover()
    trail = MemoryPheromoneTrail(clock)
    deps = UndertakerDeps(
        backend=fake_backend,
        grant_revoker=grant_revoker,
        wax_retirer=wax_retirer,
        leavings_remover=leavings_remover,
        trail=trail,
        clock=clock,
        identity=make_identity(clock=clock),
    )
    return _Rig(
        undertaker=Undertaker(deps, retry=retry),
        backend=fake_backend,
        grant_revoker=grant_revoker,
        wax_retirer=wax_retirer,
        leavings_remover=leavings_remover,
        trail=trail,
    )


async def _drive_retries(
    task: asyncio.Task[object], clock: FakeClock, delays: Sequence[float]
) -> None:
    """Let a retrying coroutine reach each backoff sleep in turn, and advance the clock past it."""
    for delay in delays:
        await asyncio.sleep(0)  # Let the retry loop reach its clock.sleep() await.
        clock.advance(delay)


def test_undertaker_role_is_always_undertaker() -> None:
    rig = _make_rig(FakeClock())

    assert rig.undertaker.role == WorkerRole.UNDERTAKER


# ──────────────────────────────────────────────────────────────────────────────
# destroy_virtual
# ──────────────────────────────────────────────────────────────────────────────


async def test_destroy_virtual_destroys_and_calls_every_collaborator() -> None:
    clock = FakeClock()
    rig = _make_rig(clock)
    cell_id = new_cell_id(clock)

    event_id = await rig.undertaker.destroy_virtual(cell_id)

    assert isinstance(rig.backend, FakeCellBackend)
    assert rig.backend.destroy_calls == [cell_id]
    assert rig.grant_revoker.calls == [(cell_id, "Virtual Cell destroyed.")]
    assert [call[0] for call in rig.wax_retirer.calls] == [cell_id]
    assert [call[0] for call in rig.leavings_remover.calls] == [cell_id]
    events = await rig.trail.query(TrailQuery(kind="cell.destroyed"))
    assert len(events) == 1
    assert events[0].subject_id == cell_id
    # Roadmap step 5.13: `hive cells destroy` prints this id as its own receipt.
    assert event_id == events[0].id


async def test_destroy_virtual_is_idempotent_for_an_unknown_cell() -> None:
    clock = FakeClock()
    rig = _make_rig(clock)
    cell_id = new_cell_id(clock)

    # FakeCellBackend.destroy() is itself idempotent for an id it never provisioned; calling it
    # twice through the Undertaker must not raise either time.
    await rig.undertaker.destroy_virtual(cell_id)
    await rig.undertaker.destroy_virtual(cell_id)


async def test_destroy_virtual_retries_on_backend_failure_then_succeeds() -> None:
    clock = FakeClock()
    flaky = FlakyDestroyBackend(FakeCellBackend(clock), fail_times=2)
    retry = RetryPolicy(
        max_attempts=5, initial_backoff_s=1.0, backoff_factor=2.0, max_backoff_s=30.0
    )
    rig = _make_rig(clock, backend=flaky, retry=retry)
    cell_id = new_cell_id(clock)

    task = asyncio.create_task(rig.undertaker.destroy_virtual(cell_id))
    await _drive_retries(task, clock, delays=[1.0, 2.0])  # Two failures, then the third succeeds.

    await task
    assert flaky.destroy_attempts == 3


async def test_destroy_virtual_gives_up_after_max_attempts() -> None:
    clock = FakeClock()
    flaky = FlakyDestroyBackend(FakeCellBackend(clock), fail_times=10)  # Always fails, in-bound.
    retry = RetryPolicy(
        max_attempts=3, initial_backoff_s=1.0, backoff_factor=2.0, max_backoff_s=30.0
    )
    rig = _make_rig(clock, backend=flaky, retry=retry)
    cell_id = new_cell_id(clock)

    task = asyncio.create_task(rig.undertaker.destroy_virtual(cell_id))
    await _drive_retries(task, clock, delays=[1.0, 2.0])  # Two retries; the third attempt gives up.

    with pytest.raises(CellDestroyError):
        await task
    assert flaky.destroy_attempts == 3


# ──────────────────────────────────────────────────────────────────────────────
# release_real
# ──────────────────────────────────────────────────────────────────────────────


async def test_release_real_releases_and_revokes_the_grant(tmp_path: Path) -> None:
    clock = FakeClock()
    rig = _make_rig(clock)
    lease = make_real_cell_lease(tmp_path, clock=clock, releaser=FakeLeaseReleaser())
    await lease.open()

    report = await rig.undertaker.release_real(lease)

    assert report.is_restored is True
    assert rig.grant_revoker.calls == [(lease.cell_id, "Real Cell lease released.")]


async def test_release_real_never_touches_wax_or_leavings(tmp_path: Path) -> None:
    clock = FakeClock()
    rig = _make_rig(clock)
    lease = make_real_cell_lease(tmp_path, clock=clock, releaser=FakeLeaseReleaser())
    await lease.open()

    await rig.undertaker.release_real(lease)

    # roadmap step 5.8: "releasing a Real Cell never touches a ledgered path"; wax outlives leases.
    assert rig.wax_retirer.calls == []
    assert rig.leavings_remover.calls == []


async def test_release_real_is_idempotent(tmp_path: Path) -> None:
    clock = FakeClock()
    rig = _make_rig(clock)
    lease = make_real_cell_lease(tmp_path, clock=clock, releaser=FakeLeaseReleaser())
    await lease.open()

    first = await rig.undertaker.release_real(lease)
    second = await rig.undertaker.release_real(lease)

    assert first == second
    # lease.release() itself is idempotent (RealCellLease's own contract): a second call does not
    # ask the releaser again, but the Undertaker still revokes the grant a second, harmless time.
    assert len(rig.grant_revoker.calls) == 2


async def test_release_real_retries_a_failed_releaser_then_succeeds(tmp_path: Path) -> None:
    """Roadmap step 5.0a's `RELEASING -> ORPHANED` edge is what makes this retry legal at all.

    Before it, `release()` moved the lease to RELEASING and left it there when the releaser raised,
    so a second call raised `InvalidLeaseTransitionError` instead of trying the releaser again and
    `release_real` deliberately called it once (the `TODO(merge)` this replaces). Now a failed
    release orphans the lease, `ORPHANED -> RELEASING` is a legal edge, and the Undertaker retries
    it with backoff exactly like every other step it owns.
    """
    clock = FakeClock()
    releaser = FlakyLeaseReleaser(fail_times=2)  # Two failures, then a clean release.
    retry = RetryPolicy(
        max_attempts=5, initial_backoff_s=1.0, backoff_factor=2.0, max_backoff_s=30.0
    )
    rig = _make_rig(clock, retry=retry)
    lease = make_real_cell_lease(tmp_path, clock=clock, releaser=releaser)
    await lease.open()

    task = asyncio.create_task(rig.undertaker.release_real(lease))
    await _drive_retries(task, clock, delays=[1.0, 2.0])

    report = await task
    assert report.is_restored
    assert releaser.release_attempts == 3
    assert lease.state is LeaseState.RELEASED


async def test_release_real_gives_up_on_a_releaser_that_never_succeeds(tmp_path: Path) -> None:
    """Out of retries, the releaser's own error propagates and the lease is left ORPHANED."""
    clock = FakeClock()
    releaser = FlakyLeaseReleaser(fail_times=10)  # Always fails within this test's own bound.
    retry = RetryPolicy(
        max_attempts=3, initial_backoff_s=1.0, backoff_factor=2.0, max_backoff_s=30.0
    )
    rig = _make_rig(clock, retry=retry)
    lease = make_real_cell_lease(tmp_path, clock=clock, releaser=releaser)
    await lease.open()

    task = asyncio.create_task(rig.undertaker.release_real(lease))
    await _drive_retries(task, clock, delays=[1.0, 2.0])

    with pytest.raises(SessionClosedError):
        await task
    assert releaser.release_attempts == 3
    # ORPHANED, not stuck at RELEASING: a later sweep (or a later Undertaker) may try again.
    assert lease.state is LeaseState.ORPHANED


# ──────────────────────────────────────────────────────────────────────────────
# run (the Worker-protocol adapter)
# ──────────────────────────────────────────────────────────────────────────────


async def test_run_destroys_a_virtual_cell() -> None:
    clock = FakeClock()
    rig = _make_rig(clock)
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock)
    ctx = make_context(clock=clock, cell=cell)
    assignment = make_assignment(clock=clock, role=WorkerRole.UNDERTAKER)

    outcome = await rig.undertaker.run(ctx, assignment, resume_from=None)

    assert outcome.claimed is True
    assert outcome.handoff is None
    assert isinstance(rig.backend, FakeCellBackend)
    assert rig.backend.destroy_calls == [cell.id]
    assert rig.grant_revoker.calls == [(cell.id, "Virtual Cell destroyed.")]


async def test_run_leaves_a_real_cells_lease_to_its_own_holder() -> None:
    clock = FakeClock()
    rig = _make_rig(clock)
    cell = make_cell(kind=CellKind.REAL, clock=clock)
    ctx = make_context(clock=clock, cell=cell)
    assignment = make_assignment(clock=clock, role=WorkerRole.UNDERTAKER)

    outcome = await rig.undertaker.run(ctx, assignment, resume_from=None)

    assert outcome.claimed is True
    assert "Real" in outcome.summary
    assert isinstance(rig.backend, FakeCellBackend)
    assert rig.backend.destroy_calls == []
    assert rig.grant_revoker.calls == []


async def test_run_ignores_resume_from() -> None:
    clock = FakeClock()
    rig = _make_rig(clock)
    cell = make_cell(kind=CellKind.REAL, clock=clock)
    ctx = make_context(clock=clock, cell=cell)
    assignment = make_assignment(clock=clock, role=WorkerRole.UNDERTAKER)

    # resume_from is accepted for protocol conformance and ignored (module docstring): a None here
    # (this role never resumes anything) must not raise or change the outcome shape.
    outcome = await rig.undertaker.run(ctx, assignment, resume_from=None)

    assert outcome.claimed is True


# ──────────────────────────────────────────────────────────────────────────────
# NullLeavingsRemover
# ──────────────────────────────────────────────────────────────────────────────


async def test_null_leavings_remover_marks_nothing() -> None:
    clock = FakeClock()

    removed = await NullLeavingsRemover().mark_cell_removed(new_cell_id(clock), clock.now())

    assert removed == 0


# ──────────────────────────────────────────────────────────────────────────────
# NullWaxRetirer
# ──────────────────────────────────────────────────────────────────────────────


async def test_null_wax_retirer_retires_nothing() -> None:
    clock = FakeClock()

    retired = await NullWaxRetirer().retire_wax(new_cell_id(clock), clock.now())

    assert retired == 0
