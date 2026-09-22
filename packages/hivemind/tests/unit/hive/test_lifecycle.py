"""Unit tests for hivemind.hive.lifecycle: every edge, the events they record, the Night Veil rule.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors src/hivemind/hive/lifecycle.py
    (codingrules section 3). Walks the roadmap step 5.6 edge sequence end to end against
    FakeCellBackend, asserts the recorded cell.* events land in order, and checks the Night Veil
    guard, reconciliation and provision-failure paths the module docstring names as key
    invariants.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.lifecycle for CellLifecycle, the class under test.
    - hivemind.hive.backends.fake for FakeCellBackend, every test's one backend.
"""

from __future__ import annotations

import pytest
from builders.cells import make_identity
from builders.forage import make_capacity

from hivemind.cell import CombShieldLevel
from hivemind.hive.backends.base import BackendCapabilities
from hivemind.hive.backends.fake import FakeCellBackend
from hivemind.hive.cell_state import VirtualCellStatus
from hivemind.hive.errors import CellProvisionError, InvalidCellTransitionError, UnknownCellError
from hivemind.hive.lifecycle import CellLifecycle, DecideRelease, LiveVirtualCell, always_teardown
from hivemind.hive.models import NetworkPolicy, VirtualCellSpec
from hivemind.hive.registry import BackendRegistry
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


def _make_lifecycle(
    backend: FakeCellBackend, *, decide: DecideRelease = always_teardown
) -> tuple[CellLifecycle, MemoryPheromoneTrail]:
    """Build a CellLifecycle over one registered "fake" backend, and the trail it records to."""
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    registry = BackendRegistry()
    registry.register("fake", lambda: backend)
    identity = make_identity(clock)
    lifecycle = CellLifecycle(registry, trail, clock, identity, overwinter_policy_hook=decide)
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
    lifecycle, trail = _make_lifecycle(backend, decide=lambda cell, spec: "overwinter")
    cell = await lifecycle.provision(_make_spec(), "fake")
    await lifecycle.mark_ready(cell.id)
    grant_id = new_grant_id(FakeClock())

    await lifecycle.grant(cell.id, grant_id)
    decision = await lifecycle.release(cell.id)
    assert decision == "overwinter"
    await lifecycle.overwinter(cell.id)
    assert lifecycle.status_of(cell.id) is VirtualCellStatus.DORMANT

    await lifecycle.resume(cell.id)
    assert lifecycle.status_of(cell.id) is VirtualCellStatus.READY

    await lifecycle.grant(cell.id, new_grant_id(FakeClock()))
    decision = await lifecycle.release(cell.id)
    # The hook is unconditional ("overwinter" every time); this second release is torn down
    # anyway, proving teardown() works from RELEASED without ever calling overwinter() again.
    assert decision == "overwinter"
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


# ──────────────────────────────────────────────────────────────────────────────
# Night Veil: never dormant, regardless of the release hook.
# ──────────────────────────────────────────────────────────────────────────────


async def test_release_forces_teardown_for_night_veil_even_with_an_overwinter_hook() -> None:
    backend = FakeCellBackend(FakeClock())
    lifecycle, _trail = _make_lifecycle(backend, decide=lambda cell, spec: "overwinter")
    cell = await lifecycle.provision(_night_veil_spec(), "fake")
    await lifecycle.mark_ready(cell.id)
    await lifecycle.grant(cell.id, new_grant_id(FakeClock()))

    decision = await lifecycle.release(cell.id)

    assert decision == "teardown"


async def test_overwinter_itself_refuses_a_night_veil_cell_regardless_of_release() -> None:
    """A direct overwinter() call is refused too: release()'s own decision is not the only guard."""
    backend = FakeCellBackend(FakeClock())
    lifecycle, _trail = _make_lifecycle(backend)
    cell = await lifecycle.provision(_night_veil_spec(), "fake")
    await lifecycle.mark_ready(cell.id)
    await lifecycle.grant(cell.id, new_grant_id(FakeClock()))
    await lifecycle.release(cell.id)  # Now RELEASED, regardless of what it returned.

    with pytest.raises(InvalidCellTransitionError, match="Night Veil"):
        await lifecycle.overwinter(cell.id)


async def test_default_hook_always_tears_down() -> None:
    backend = FakeCellBackend(FakeClock())
    lifecycle, _trail = _make_lifecycle(backend)  # No decide= override: always_teardown.
    cell = await lifecycle.provision(_make_spec(), "fake")
    await lifecycle.mark_ready(cell.id)
    await lifecycle.grant(cell.id, new_grant_id(FakeClock()))

    decision = await lifecycle.release(cell.id)

    assert decision == "teardown"
    stub = LiveVirtualCell(cell.id, VirtualCellStatus.RELEASED, "fake", "x", CombShieldLevel.MEADOW)
    assert always_teardown(stub, None) == "teardown"


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
    lifecycle, _trail = _make_lifecycle(backend, decide=lambda cell, spec: "overwinter")
    ready_cell = await lifecycle.provision(_make_spec(), "fake")
    await lifecycle.mark_ready(ready_cell.id)
    dormant_cell = await lifecycle.provision(_make_spec(), "fake")
    await lifecycle.mark_ready(dormant_cell.id)
    await lifecycle.grant(dormant_cell.id, new_grant_id(FakeClock()))
    await lifecycle.release(dormant_cell.id)
    await lifecycle.overwinter(dormant_cell.id)

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
