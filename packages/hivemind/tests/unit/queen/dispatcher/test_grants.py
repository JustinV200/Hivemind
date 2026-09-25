"""Tests for hivemind.queen.dispatcher.grants: authorize_grant, the grant_issue point.

Fits into the Hive:
    Mirrors src/hivemind/queen/dispatcher/grants.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.dispatcher.grants for the module under test.
    - docs/adr/0039-capability-model-attenuation-and-enforcement-points.md for grant_issue.
"""

from __future__ import annotations

from builders.forage import make_grant
from builders.queen import make_queen_deps, with_guard_policy
from builders.tasks import make_task, make_task_spec

from hivemind.forage import ForageGrant, ModelSlot
from hivemind.forage.models import AllowedBinding, SeatReservation
from hivemind.forage.slots import Effort
from hivemind.guard import load_guard_policy
from hivemind.manifest import GuardRoleSection, GuardSection
from hivemind.pheromone import TrailQuery
from hivemind.queen.dispatcher.grants import authorize_grant


def _binding(slot: ModelSlot, source_id: str) -> AllowedBinding:
    return AllowedBinding(slot=slot, source_id=source_id, max_effort=Effort.MEDIUM)


def _two_binding_grant() -> ForageGrant:
    """A grant naming a WORKER and a JUDGE binding, each with its own seat reservation."""
    return make_grant(
        allowed=(_binding(ModelSlot.WORKER, "local"), _binding(ModelSlot.JUDGE, "hosted")),
        seats=(
            SeatReservation(source_id="local", seats=1),
            SeatReservation(source_id="hosted", seats=1),
        ),
    )


async def test_a_grant_every_binding_of_which_is_allowed_is_returned_unchanged() -> None:
    deps, link, _end = make_queen_deps()
    fresh = _two_binding_grant()

    kept = await authorize_grant(deps, link, make_task(), fresh)

    assert kept is fresh
    assert await deps.trail.query(TrailQuery(kind="guard.denied")) == ()


async def test_a_binding_the_goal_does_not_allow_is_removed_with_its_seats() -> None:
    deps, link, _end = make_queen_deps()
    task = make_task(spec=make_task_spec(capabilities=("llm:worker",)))

    kept = await authorize_grant(deps, link, task, _two_binding_grant())

    assert [binding.slot for binding in kept.allowed] == [ModelSlot.WORKER]
    assert [reservation.source_id for reservation in kept.seats] == ["local"]
    [denial] = await deps.trail.query(TrailQuery(kind="guard.denied"))
    assert denial.payload["point"] == "grant_issue"
    assert denial.payload["capability"] == "llm:judge"


async def test_a_binding_the_wardens_own_set_does_not_allow_is_removed() -> None:
    # A policy whose `warden` role binds only the WORKER slot: the JUDGE binding cannot be issued.
    section = GuardSection(roles={"warden": GuardRoleSection(allow=("llm:worker",))})
    base, link, _end = make_queen_deps()
    deps = with_guard_policy(base, load_guard_policy(None, section))

    kept = await authorize_grant(deps, link, make_task(), _two_binding_grant())

    assert [binding.slot for binding in kept.allowed] == [ModelSlot.WORKER]
    [denial] = await deps.trail.query(TrailQuery(kind="guard.denied"))
    assert denial.payload["principal_kind"] == "queen"
    assert denial.payload["capability"] == "llm:judge"


async def test_a_goal_allowing_no_binding_leaves_a_grant_with_none() -> None:
    deps, link, _end = make_queen_deps()
    task = make_task(spec=make_task_spec(capabilities=("tool:*",)))

    kept = await authorize_grant(deps, link, task, _two_binding_grant())

    assert kept.allowed == ()
    assert kept.seats == ()
    assert len(await deps.trail.query(TrailQuery(kind="guard.denied"))) == 2
