"""Unit tests for hivemind.hive.lifecycle: every edge, the events they record, the Night Veil rule.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors src/hivemind/hive/lifecycle.py
    (codingrules section 3). Walks the roadmap step 5.6 edge sequence end to end against
    FakeCellBackend, asserts the recorded cell.* events land in order, and checks the Night Veil
    guard, reconciliation and provision-failure paths the module docstring names as key
    invariants. Since this dispatch's own reconciliation of CellLifecycle and OverwinterPool, also
    exercises `overwinter()`/`resume()`/`claim_for_image()`/`evict_expired()` calling into both a
    real (in-memory) OverwinterPool and FakeCellBackend's own pause/resume/destroy.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.lifecycle for CellLifecycle, the class under test.
    - hivemind.hive.overwinter.pool for OverwinterPool, its bookkeeping collaborator.
    - hivemind.hive.backends.fake for FakeCellBackend, every test's one backend.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from builders.cells import make_identity
from builders.forage import make_capacity

from hivemind.cell import CombShieldLevel, SnapshotId
from hivemind.hive.backends.base import BackendCapabilities
from hivemind.hive.backends.fake import FakeCellBackend
from hivemind.hive.cell_state import VirtualCellStatus
from hivemind.hive.errors import CellProvisionError, InvalidCellTransitionError, UnknownCellError
from hivemind.hive.lifecycle import CellLifecycle, OverwinterSettings
from hivemind.hive.models import NetworkPolicy, VirtualCellSpec
from hivemind.hive.overwinter.policy import OverwinterConfig, OverwinterDecision, ReleaseOutcome
from hivemind.hive.overwinter.pool import OverwinterPool
from hivemind.hive.registry import BackendRegistry
from hivemind.hive.snapshot.ledger import SnapshotLedger, SnapshotNotFoundError, SnapshotRecord
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.pheromone.trail.protocol import TrailQuery
from waggle.clock import FakeClock
from waggle.ids import CellId, new_grant_id, new_hive_id, new_warden_id


def _make_spec(**overrides: object) -> VirtualCellSpec:
    """Build a valid VirtualCellSpec, with sensible defaults for every field a test ignores."""
    fields: dict[str, object] = {
        "image": "base-ubuntu",
        "cpu_cores": 2.0,
        "memory_bytes": 2 * 1024**3,
        "disk_bytes": 10 * 1024**3,
        "capacity": make_capacity(),
        "hive_id": new_hive_id(FakeClock()),
    }
    fields.update(overrides)
    return VirtualCellSpec(**fields)


def _night_veil_spec(**overrides: object) -> VirtualCellSpec:
    """Build a valid NIGHT_VEIL VirtualCellSpec: comb_shield and network_policy must agree."""
    return _make_spec(
        comb_shield=CombShieldLevel.NIGHT_VEIL, network_policy=NetworkPolicy.VPN_TOR, **overrides
    )


def _outcome(**overrides: object) -> ReleaseOutcome:
    """Build a ReleaseOutcome that clears every ADR-0029 rule, overridable per test."""
    fields: dict[str, object] = {
        "rolled_back_whole_cell": False,
        "has_block_wax": False,
        "single_use": False,
        "backend_can_pause": True,
    }
    fields.update(overrides)
    return ReleaseOutcome(**fields)  # type: ignore[arg-type]


def _overwinter_config(**overrides: object) -> OverwinterConfig:
    """Build a generous OverwinterConfig, overridable per test."""
    fields: dict[str, object] = {
        "enabled": True,
        "max_cells": 10,
        "max_per_image": 10,
        "max_dormant_s": 3600.0,
        "disk_budget_mb": 1024 * 1024,  # Generous: _make_spec's own default disk is 10 GB.
    }
    fields.update(overrides)
    return OverwinterConfig(**fields)  # type: ignore[arg-type]


async def _no_op_scrub(cell: object) -> None:
    """A Scrubber that does nothing; most tests don't care what scrubbing does."""


def _make_lifecycle(
    backend: FakeCellBackend,
    *,
    with_pool: bool = False,
    snapshot_ledger: SnapshotLedger | None = None,
) -> tuple[CellLifecycle, MemoryPheromoneTrail]:
    """Build a CellLifecycle over one registered "fake" backend, and the trail it records to.

    Args:
        backend: The FakeCellBackend to register under "fake".
        with_pool: True builds a real OverwinterPool and a generous OverwinterConfig too, so
            release() can actually decide OVERWINTER; False (the default) leaves both unset, so
            release() always decides TEARDOWN (the old always_teardown default's behaviour).
        snapshot_ledger: Roadmap step 5.10: the SnapshotLedger `teardown()` deletes a Cell's own
            snapshots from, when one is given; None (the default) matches every pre-5.10 test.
    """
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    registry = BackendRegistry()
    registry.register("fake", lambda: backend)
    identity = make_identity(clock)
    overwinter = (
        OverwinterSettings(
            pool=OverwinterPool(clock, _overwinter_config()), config=_overwinter_config()
        )
        if with_pool
        else None
    )
    lifecycle = CellLifecycle(registry, trail, clock, identity, overwinter=overwinter)
    if snapshot_ledger is not None:
        lifecycle.attach_snapshot_ledger(snapshot_ledger)
    return lifecycle, trail


async def _kinds_for(trail: MemoryPheromoneTrail, subject_id: str) -> list[str]:
    """Return every recorded kind for `subject_id`, in trail order."""
    events = await trail.query(TrailQuery(subject_id=subject_id, limit=100))
    return [event.kind for event in events]


# ──────────────────────────────────────────────────────────────────────────────
# The happy path: provision -> mark_ready -> grant -> release -> overwinter -> resume -> teardown.
# ──────────────────────────────────────────────────────────────────────────────


async def test_provision_tracks_the_cell_as_provisioning() -> None:
    backend = FakeCellBackend(FakeClock())
    lifecycle, _trail = _make_lifecycle(backend)

    cell = await lifecycle.provision(_make_spec(), "fake")

    assert lifecycle.status_of(cell.id) is VirtualCellStatus.PROVISIONING


async def test_provision_records_provisioning_then_provisioned_in_order() -> None:
    backend = FakeCellBackend(FakeClock())
    lifecycle, trail = _make_lifecycle(backend)

    cell = await lifecycle.provision(_make_spec(), "fake")

    assert await _kinds_for(trail, cell.id) == ["cell.provisioning", "cell.provisioned"]


async def test_mark_ready_moves_provisioning_to_ready_and_records_it() -> None:
    backend = FakeCellBackend(FakeClock())
    lifecycle, trail = _make_lifecycle(backend)
    cell = await lifecycle.provision(_make_spec(), "fake")
    warden_id = new_warden_id(FakeClock())

    await lifecycle.mark_ready(cell.id, warden_id)

    assert lifecycle.status_of(cell.id) is VirtualCellStatus.READY
    assert (await _kinds_for(trail, cell.id))[-1] == "cell.ready"
    tracked = next(r for r in lifecycle.live_cells() if r.cell_id == cell.id)
    assert tracked.warden_id == warden_id


async def test_full_happy_path_records_every_edge_in_order() -> None:
    backend = FakeCellBackend(FakeClock())
    lifecycle, trail = _make_lifecycle(backend, with_pool=True)
    cell = await lifecycle.provision(_make_spec(), "fake")
    await lifecycle.mark_ready(cell.id)
    grant_id = new_grant_id(FakeClock())

    await lifecycle.grant(cell.id, grant_id)
    decision = await lifecycle.release(cell.id, _outcome())
    assert decision is OverwinterDecision.OVERWINTER
    await lifecycle.overwinter(cell.id, scrub=_no_op_scrub)
    assert lifecycle.status_of(cell.id) is VirtualCellStatus.DORMANT
    assert backend.pause_calls == [cell.id]

    await lifecycle.resume(cell.id)
    assert lifecycle.status_of(cell.id) is VirtualCellStatus.READY
    assert backend.resume_calls == [cell.id]

    await lifecycle.grant(cell.id, new_grant_id(FakeClock()))
    decision = await lifecycle.release(cell.id, _outcome(single_use=True))
    # single_use vetoes OVERWINTER this time, proving teardown() works from RELEASED without a
    # second overwinter() call.
    assert decision is OverwinterDecision.TEARDOWN
    await lifecycle.teardown(cell.id)

    assert lifecycle.status_of(cell.id) is None  # Removed once DESTROYED.
    assert await _kinds_for(trail, cell.id) == [
        "cell.provisioning",
        "cell.provisioned",
        "cell.ready",
        "cell.granted",
        "cell.virtual_released",
        "cell.overwintered",
        "cell.resumed",
        "cell.granted",
        "cell.virtual_released",
        "cell.destroying",
        "cell.destroyed",
    ]


async def test_release_without_a_pool_always_tears_down() -> None:
    backend = FakeCellBackend(FakeClock())
    lifecycle, _trail = _make_lifecycle(backend)  # No pool: the old always_teardown default.
    cell = await lifecycle.provision(_make_spec(), "fake")
    await lifecycle.mark_ready(cell.id)
    await lifecycle.grant(cell.id, new_grant_id(FakeClock()))

    decision = await lifecycle.release(cell.id, _outcome())

    assert decision is OverwinterDecision.TEARDOWN


async def test_teardown_calls_backend_destroy_and_removes_the_record() -> None:
    backend = FakeCellBackend(FakeClock())
    lifecycle, _trail = _make_lifecycle(backend)
    cell = await lifecycle.provision(_make_spec(), "fake")
    await lifecycle.mark_ready(cell.id)

    await lifecycle.teardown(cell.id)

    assert backend.destroy_calls == [cell.id]
    assert lifecycle.status_of(cell.id) is None


async def test_teardown_is_reachable_directly_from_ready() -> None:
    """READY -> DESTROYING is a legal edge (e.g. abscond, a Cell torn down before ever granted)."""
    backend = FakeCellBackend(FakeClock())
    lifecycle, _trail = _make_lifecycle(backend)
    cell = await lifecycle.provision(_make_spec(), "fake")
    await lifecycle.mark_ready(cell.id)

    await lifecycle.teardown(cell.id)  # Must not raise.

    assert lifecycle.status_of(cell.id) is None


async def test_teardown_deletes_the_cells_own_snapshots_from_the_ledger() -> None:
    """Roadmap step 5.10: a destroyed Cell's own snapshots die with it."""
    clock = FakeClock()
    backend = FakeCellBackend(clock)
    ledger = SnapshotLedger()
    lifecycle, _trail = _make_lifecycle(backend, snapshot_ledger=ledger)
    cell = await lifecycle.provision(_make_spec(), "fake")
    await lifecycle.mark_ready(cell.id)
    taken_at = clock.now()
    snapshot_id = SnapshotId("snap_test_1")
    ledger.record(
        SnapshotRecord(
            id=snapshot_id,
            cell_id=cell.id,
            taken_at=taken_at,
            bytes_estimate=100,
            expires_at=taken_at,
        )
    )

    await lifecycle.teardown(cell.id)

    with pytest.raises(SnapshotNotFoundError):
        ledger.get(snapshot_id)


async def test_teardown_with_no_snapshot_ledger_configured_still_tears_down() -> None:
    """None (the default) skips the ledger cleanup step entirely; must not raise."""
    backend = FakeCellBackend(FakeClock())
    lifecycle, _trail = _make_lifecycle(backend)  # snapshot_ledger=None, the default.
    cell = await lifecycle.provision(_make_spec(), "fake")
    await lifecycle.mark_ready(cell.id)

    await lifecycle.teardown(cell.id)  # Must not raise.

    assert lifecycle.status_of(cell.id) is None


async def test_teardown_of_a_cell_with_no_snapshots_is_a_no_op_on_the_ledger() -> None:
    """delete_for_cell is idempotent; a never-snapshotted Cell's teardown does not raise."""
    backend = FakeCellBackend(FakeClock())
    ledger = SnapshotLedger()
    lifecycle, _trail = _make_lifecycle(backend, snapshot_ledger=ledger)
    cell = await lifecycle.provision(_make_spec(), "fake")
    await lifecycle.mark_ready(cell.id)

    await lifecycle.teardown(cell.id)  # Must not raise.

    assert lifecycle.status_of(cell.id) is None


# ──────────────────────────────────────────────────────────────────────────────
# claim_for_image: the convenience path for a caller with only an image, not a cell_id.
# ──────────────────────────────────────────────────────────────────────────────


async def test_claim_for_image_resumes_the_oldest_dormant_cell() -> None:
    backend = FakeCellBackend(FakeClock())
    lifecycle, _trail = _make_lifecycle(backend, with_pool=True)
    cell = await lifecycle.provision(_make_spec(image="base-ubuntu"), "fake")
    await lifecycle.mark_ready(cell.id)
    await lifecycle.grant(cell.id, new_grant_id(FakeClock()))
    await lifecycle.release(cell.id, _outcome())
    await lifecycle.overwinter(cell.id, scrub=_no_op_scrub)

    resumed_id = await lifecycle.claim_for_image("base-ubuntu")

    assert resumed_id == cell.id
    assert lifecycle.status_of(cell.id) is VirtualCellStatus.READY
    assert backend.resume_calls == [cell.id]


async def test_claim_for_image_returns_none_when_nothing_matches() -> None:
    backend = FakeCellBackend(FakeClock())
    lifecycle, _trail = _make_lifecycle(backend, with_pool=True)

    assert await lifecycle.claim_for_image("no-such-image") is None


async def test_claim_for_image_returns_none_without_a_pool() -> None:
    backend = FakeCellBackend(FakeClock())
    lifecycle, _trail = _make_lifecycle(backend)  # No pool.

    assert await lifecycle.claim_for_image("base-ubuntu") is None


# ──────────────────────────────────────────────────────────────────────────────
# evict_expired: pool bookkeeping selects the ids, the lifecycle tears each one down.
# ──────────────────────────────────────────────────────────────────────────────


async def test_evict_expired_tears_down_every_cell_past_its_own_deadline() -> None:
    backend = FakeCellBackend(FakeClock())
    lifecycle, trail = _make_lifecycle(backend, with_pool=True)
    cell = await lifecycle.provision(_make_spec(), "fake")
    await lifecycle.mark_ready(cell.id)
    await lifecycle.grant(cell.id, new_grant_id(FakeClock()))
    await lifecycle.release(cell.id, _outcome())
    await lifecycle.overwinter(cell.id, scrub=_no_op_scrub)

    # FakeClock() with no `start` always begins at the same fixed instant (waggle.clock's own
    # docstring), which is also what `lifecycle`'s own internal clock reads here: nothing in this
    # test ever calls `.advance()`, so a fresh FakeClock().now() and the lifecycle's own `now()`
    # still agree.
    far_future = FakeClock().now() + timedelta(hours=2)  # Past the default 3600s max_dormant_s.
    evicted = await lifecycle.evict_expired(far_future)

    assert evicted == (cell.id,)
    assert lifecycle.status_of(cell.id) is None
    assert backend.destroy_calls == [cell.id]
    assert (await _kinds_for(trail, cell.id))[-2:] == ["cell.destroying", "cell.destroyed"]


async def test_evict_expired_returns_empty_without_a_pool() -> None:
    backend = FakeCellBackend(FakeClock())
    lifecycle, _trail = _make_lifecycle(backend)  # No pool.

    assert await lifecycle.evict_expired(FakeClock().now()) == ()


# ──────────────────────────────────────────────────────────────────────────────
# Night Veil: never dormant, regardless of the pool or the policy.
# ──────────────────────────────────────────────────────────────────────────────


async def test_release_forces_teardown_for_night_veil_even_with_a_pool() -> None:
    backend = FakeCellBackend(FakeClock())
    lifecycle, _trail = _make_lifecycle(backend, with_pool=True)
    cell = await lifecycle.provision(_night_veil_spec(), "fake")
    await lifecycle.mark_ready(cell.id)
    await lifecycle.grant(cell.id, new_grant_id(FakeClock()))

    decision = await lifecycle.release(cell.id, _outcome())

    assert decision is OverwinterDecision.TEARDOWN


async def test_overwinter_itself_refuses_a_night_veil_cell_regardless_of_release() -> None:
    """A direct overwinter() call is refused too: release()'s own decision is not the only guard."""
    backend = FakeCellBackend(FakeClock())
    lifecycle, _trail = _make_lifecycle(backend, with_pool=True)
    cell = await lifecycle.provision(_night_veil_spec(), "fake")
    await lifecycle.mark_ready(cell.id)
    await lifecycle.grant(cell.id, new_grant_id(FakeClock()))
    await lifecycle.release(cell.id, _outcome())  # Now RELEASED, regardless of what it returned.

    with pytest.raises(InvalidCellTransitionError, match="Night Veil"):
        await lifecycle.overwinter(cell.id, scrub=_no_op_scrub)


# ──────────────────────────────────────────────────────────────────────────────
# Provision failure: FAILED is validated but never left tracked; nothing to clean up.
# ──────────────────────────────────────────────────────────────────────────────


async def test_provision_failure_records_provision_failed_and_leaves_no_record() -> None:
    backend = FakeCellBackend(FakeClock())
    backend.set_provision_failure("no capacity")
    lifecycle, trail = _make_lifecycle(backend)

    with pytest.raises(CellProvisionError):
        await lifecycle.provision(_make_spec(), "fake")

    assert lifecycle.live_cells() == ()
    events = await trail.query(TrailQuery(kind="cell.provision_failed", limit=10))
    assert len(events) == 1
    assert events[0].payload["backend"] == "fake"


async def test_provision_failure_never_records_provisioned_or_ready() -> None:
    backend = FakeCellBackend(FakeClock())
    backend.set_provision_failure("boom")
    lifecycle, trail = _make_lifecycle(backend)

    with pytest.raises(CellProvisionError):
        await lifecycle.provision(_make_spec(), "fake")

    all_events = await trail.query(TrailQuery(limit=100))
    assert all(event.kind != "cell.provisioned" for event in all_events)
    assert all(event.kind != "cell.ready" for event in all_events)


# ──────────────────────────────────────────────────────────────────────────────
# Reconciliation: rebuild from backend.list_cells, never overwriting an already-known record.
# ──────────────────────────────────────────────────────────────────────────────


async def test_reconcile_picks_up_a_cell_this_process_never_provisioned() -> None:
    clock = FakeClock()
    backend = FakeCellBackend(clock)
    spec = _make_spec()
    cell = await backend.provision(
        spec
    )  # Provisioned directly on the backend, bypassing lifecycle.
    lifecycle, _trail = _make_lifecycle(backend)

    await lifecycle.reconcile(spec.hive_id)

    assert lifecycle.status_of(cell.id) is VirtualCellStatus.READY  # FakeCellBackend's own default.


async def test_reconcile_never_overwrites_an_already_tracked_record() -> None:
    backend = FakeCellBackend(FakeClock())
    lifecycle, _trail = _make_lifecycle(backend)
    hive_id = new_hive_id(FakeClock())
    cell = await lifecycle.provision(_make_spec(hive_id=hive_id), "fake")
    await lifecycle.mark_ready(cell.id)  # Now READY, unlike backend.list_cells's own default.

    # backend.list_cells would report this same cell as READY too (FakeCellBackend's own default
    # status on provision), so this only proves the already-known branch is a genuine no-op: a
    # bug that instead re-inserted a fresh record here would silently lose the warden_id below.
    await lifecycle.reconcile(hive_id)

    assert lifecycle.status_of(cell.id) is VirtualCellStatus.READY


# ──────────────────────────────────────────────────────────────────────────────
# Unknown-cell handling and forbidden edges.
# ──────────────────────────────────────────────────────────────────────────────


async def test_grant_on_an_unknown_cell_raises_unknown_cell_error() -> None:
    backend = FakeCellBackend(FakeClock())
    lifecycle, _trail = _make_lifecycle(backend)

    with pytest.raises(UnknownCellError):
        await lifecycle.grant(CellId("cell_does_not_exist"), new_grant_id(FakeClock()))


async def test_mark_ready_twice_raises_invalid_transition() -> None:
    backend = FakeCellBackend(FakeClock())
    lifecycle, _trail = _make_lifecycle(backend)
    cell = await lifecycle.provision(_make_spec(), "fake")
    await lifecycle.mark_ready(cell.id)

    with pytest.raises(InvalidCellTransitionError):
        await lifecycle.mark_ready(cell.id)


# ──────────────────────────────────────────────────────────────────────────────
# Candidate views for queen.placement's own inventory (fed live, not as static tuples).
# ──────────────────────────────────────────────────────────────────────────────


async def test_dormant_candidates_lists_only_dormant_cells() -> None:
    backend = FakeCellBackend(FakeClock())
    lifecycle, _trail = _make_lifecycle(backend, with_pool=True)
    ready_cell = await lifecycle.provision(_make_spec(), "fake")
    await lifecycle.mark_ready(ready_cell.id)
    dormant_cell = await lifecycle.provision(_make_spec(), "fake")
    await lifecycle.mark_ready(dormant_cell.id)
    await lifecycle.grant(dormant_cell.id, new_grant_id(FakeClock()))
    await lifecycle.release(dormant_cell.id, _outcome())
    await lifecycle.overwinter(dormant_cell.id, scrub=_no_op_scrub)

    candidates = lifecycle.dormant_candidates()

    assert [c.cell_id for c in candidates] == [dormant_cell.id]


async def test_virtual_backend_candidates_narrows_headroom_by_live_count() -> None:
    backend = FakeCellBackend(
        FakeClock(),
        capabilities=BackendCapabilities(can_snapshot=False, can_pause=True, headroom=2),
    )
    lifecycle, _trail = _make_lifecycle(backend)
    await lifecycle.provision(_make_spec(), "fake")

    candidates = lifecycle.virtual_backend_candidates()

    assert len(candidates) == 1
    assert candidates[0].name == "fake"
    assert candidates[0].capabilities.headroom == 1  # 2 declared, 1 already provisioned.


async def test_virtual_backend_candidates_unbounded_headroom_stays_none() -> None:
    backend = FakeCellBackend(FakeClock())  # Default: headroom=None (unbounded).
    lifecycle, _trail = _make_lifecycle(backend)
    await lifecycle.provision(_make_spec(), "fake")

    candidates = lifecycle.virtual_backend_candidates()

    assert candidates[0].capabilities.headroom is None
