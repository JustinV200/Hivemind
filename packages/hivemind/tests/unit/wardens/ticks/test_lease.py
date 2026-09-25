"""Tests for hivemind.wardens.ticks.lease: open_lease, behind the lease_creation point.

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/lease.py (codingrules section 3). `Warden.start` delegates
    here; the ordinary lease/WATCH paths are already exercised by test_warden_lifecycle.py, so this
    module covers roadmap step 10.3's own point: a Warden whose set does not hold its Cell's lease
    capability never leases and settles in WATCH, with the refusal on the trail.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.lease for the module under test.
"""

from __future__ import annotations

from builders.wardens import make_warden_deps

from hivemind.guard import Capability, load_guard_policy
from hivemind.manifest import GuardRoleSection, GuardSection
from hivemind.pheromone import TrailQuery
from hivemind.wardens.state import WardenState
from hivemind.wardens.warden import Warden


async def test_a_warden_holding_its_lease_capability_leases_and_goes_active() -> None:
    deps, _queen_end, warden_id = make_warden_deps()  # The Hive Stand's own `cell:hive_stand`.
    warden = Warden(warden_id, deps)

    await warden.start()

    assert warden.state is WardenState.ACTIVE
    assert warden.lease is not None
    assert await deps.trail.query(TrailQuery(kind="guard.denied")) == ()
    await warden.stop()


async def test_a_warden_whose_set_lacks_its_lease_capability_watches_without_leasing() -> None:
    # A policy whose `warden` role leases Virtual Cells only: the Hive Stand's lease is refused.
    section = GuardSection(roles={"warden": GuardRoleSection(allow=("cell:virtual", "llm:*"))})
    deps, _queen_end, warden_id = make_warden_deps(guard=load_guard_policy(None, section))
    warden = Warden(warden_id, deps)

    await warden.start()

    assert warden.state is WardenState.WATCH
    assert warden.lease is None
    [denial] = await deps.trail.query(TrailQuery(kind="guard.denied"))
    assert denial.subject_id == warden_id
    assert denial.payload["point"] == "lease_creation"
    assert denial.payload["capability"] == "cell:hive_stand"
    kinds = [event.kind for event in await deps.trail.query(TrailQuery())]
    assert "cell.leased" not in kinds
    assert "warden.watch" in kinds


async def test_the_lease_capability_is_whatever_the_composition_root_named() -> None:
    # The in-Cell Warden's own capability, under the shipped policy: allowed like the Hive Stand's.
    deps, _queen_end, warden_id = make_warden_deps(
        lease_capability=Capability.parse("cell:virtual")
    )
    warden = Warden(warden_id, deps)

    await warden.start()

    assert warden.state is WardenState.ACTIVE
    await warden.stop()
