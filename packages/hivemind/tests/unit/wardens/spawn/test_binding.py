"""Tests for hivemind.wardens.spawn.binding: authorize_binding, the slot_binding point.

Fits into the Hive:
    Mirrors src/hivemind/wardens/spawn/binding.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.spawn.binding for the module under test.
    - docs/adr/0039-capability-model-attenuation-and-enforcement-points.md for slot binding.
"""

from __future__ import annotations

from builders.wardens import make_warden_deps

from hivemind.forage import ModelSlot
from hivemind.guard import CapabilitySet, PolicyContext, queen_principal, warden_principal
from hivemind.pheromone import TrailQuery
from hivemind.wardens.deps import WardenDeps
from hivemind.wardens.spawn.binding import BindingCheck, authorize_binding
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_grant_id, new_hive_id, new_warden_id
from waggle.messages.forage import AllowedBinding, GrantIssued, SourceRef
from waggle.messages.forage.values import Effort as WireEffort

_CLOCK = FakeClock()
_DRONE = CapabilitySet.parse("llm:worker", "tool:*")  # What a Drone's slice binds: WORKER only.


def _grant(*slots: str) -> GrantIssued:
    """A grant naming one binding per slot in `slots`, all on one local source."""
    source = SourceRef(source_id="local", provider="fake", model="test-model", host_cell_id=None)
    return GrantIssued(
        grant_id=new_grant_id(_CLOCK),
        holder=new_warden_id(_CLOCK),
        cell_id=new_cell_id(_CLOCK),
        task_id=None,
        revision=0,
        allowed=tuple(
            AllowedBinding(slot=slot, source=source, max_effort=WireEffort.MEDIUM) for slot in slots
        ),
        seats=(),
        token_budget=500_000,
        spend_budget=5.0,
        tokens_spent=0,
        spent=0.0,
        max_sub_bees=1,
        expires_at=_CLOCK.now(),
        reason="test grant",
    )


def _check(key: str, grant: GrantIssued, held: CapabilitySet = _DRONE) -> BindingCheck:
    return BindingCheck(
        orderer=warden_principal(new_warden_id(_CLOCK)),
        capabilities=held,
        grant=grant,
        binding_key=key,
        slot=ModelSlot.WORKER,
        context=PolicyContext(),
    )


async def _denials(deps: WardenDeps) -> list[dict[str, object]]:
    return [
        dict(event.payload) for event in await deps.trail.query(TrailQuery(kind="guard.denied"))
    ]


async def test_a_slot_the_grant_names_and_the_set_allows_is_bound() -> None:
    deps, _queen_end, _warden_id = make_warden_deps()

    decision = await authorize_binding(deps, _check("worker", _grant("WORKER")))

    assert decision.allowed
    assert await _denials(deps) == []


async def test_a_named_binding_on_the_slots_own_chain_is_bound_as_that_slot() -> None:
    deps, _queen_end, _warden_id = make_warden_deps()  # `worker` falls back to `worker_fallback`.

    decision = await authorize_binding(deps, _check("worker_fallback", _grant("WORKER")))

    assert decision.allowed


async def test_a_slot_the_grant_does_not_name_is_refused() -> None:
    deps, _queen_end, _warden_id = make_warden_deps()
    check = _check("judge", _grant("WORKER"), CapabilitySet.parse("llm:*"))

    decision = await authorize_binding(deps, check)

    assert not decision.allowed
    [denial] = await _denials(deps)
    assert denial["point"] == "slot_binding"
    assert denial["capability"] == "llm:judge"


async def test_a_slot_the_sub_bees_set_does_not_allow_is_refused_even_if_granted() -> None:
    deps, _queen_end, _warden_id = make_warden_deps()

    decision = await authorize_binding(deps, _check("judge", _grant("WORKER", "JUDGE")))

    assert not decision.allowed


async def test_a_key_no_slot_row_serves_is_refused_under_its_own_scope_rule() -> None:
    deps, _queen_end, _warden_id = make_warden_deps()
    check = BindingCheck(
        orderer=queen_principal(new_hive_id(_CLOCK)),
        capabilities=_DRONE,
        grant=_grant("WORKER"),
        binding_key="never_declared",
        slot=ModelSlot.WORKER,
        context=PolicyContext(),
    )

    decision = await authorize_binding(deps, check)

    assert decision.rule == "guard.scope.binding_key"
    [denial] = await _denials(deps)
    assert denial["principal_kind"] == "queen"
    assert "never_declared" in str(denial["reason"])
