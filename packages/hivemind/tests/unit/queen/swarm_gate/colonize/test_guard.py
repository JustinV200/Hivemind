"""Tests for hivemind.queen.swarm_gate.colonize.guard: the Guard check every rung change passes.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/colonize/guard.py (codingrules section 3). The Enforcer
    is the real one over the shipped Guard policy, or over a policy whose `queen` role lacks the
    `device` capability.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.colonize.guard for the module under test.
    - hivemind.guard.policy.points for the `nuc_promotion` enforcement point.
"""

from __future__ import annotations

from builders.queen import with_guard_policy
from builders.swarm import GateRig

from hivemind.guard import load_guard_policy
from hivemind.manifest import GuardRoleSection, GuardSection
from hivemind.pheromone import TrailQuery
from hivemind.queen.swarm_gate.colonize import authorise_rung


async def test_the_queens_default_set_may_change_a_devices_rung(rig: GateRig) -> None:
    node = await rig.enrol()

    allowed = await authorise_rung(rig.queen_deps, node)

    assert allowed is True
    assert await rig.queen_deps.trail.query(TrailQuery(kind="guard.denied")) == ()


async def test_a_set_that_lacks_the_device_capability_is_refused_and_the_refusal_recorded(
    rig: GateRig,
) -> None:
    section = GuardSection(roles={"queen": GuardRoleSection(allow=("warden:spawn",))})
    deps = with_guard_policy(rig.queen_deps, load_guard_policy(None, section))
    node = await rig.enrol()

    allowed = await authorise_rung(deps, node)

    assert allowed is False
    [denial] = await deps.trail.query(TrailQuery(kind="guard.denied"))
    assert denial.payload["capability"] == f"device:{node.device_id}"
    assert denial.payload["point"] == "nuc_promotion"


async def test_the_check_is_for_the_one_device_not_all_of_them(rig: GateRig) -> None:
    section = GuardSection(roles={"queen": GuardRoleSection(allow=("warden:spawn",))})
    deps = with_guard_policy(rig.queen_deps, load_guard_policy(None, section))
    first, second = await rig.enrol(), await rig.enrol()

    await authorise_rung(deps, first)
    await authorise_rung(deps, second)

    denials = await deps.trail.query(TrailQuery(kind="guard.denied"))
    assert [d.payload["capability"] for d in denials] == [
        f"device:{first.device_id}",
        f"device:{second.device_id}",
    ]
