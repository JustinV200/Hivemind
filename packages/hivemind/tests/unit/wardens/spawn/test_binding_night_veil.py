"""Tests for hivemind.wardens.spawn.binding under Night Veil: every binding stays local (10.3a/b).

A task bound to NIGHT_VEIL binds only models served from its own machine (ADR-0030), and a
fallback is where a binding spills, so the whole chain must be local. The slot_binding point states
whether it is (`binding_is_local`) and the Guard's Night Veil floor refuses anything else, a
Queen-ordered rebind included.

Fits into the Hive:
    Mirrors src/hivemind/wardens/spawn/binding.py (codingrules section 5.1: one module's tests
    split by feature, here the Night Veil locality rule).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.spawn.binding for the module under test.
    - hivemind.guard.policy.floors.night_veil for the floor that refuses.
"""

from __future__ import annotations

import dataclasses

from builders.wardens import make_warden_deps

from hivemind.cell import CombShieldLevel
from hivemind.forage import Effort, ModelSlot, SlotBinding
from hivemind.guard import CapabilitySet, PolicyContext, queen_principal, warden_principal
from hivemind.pheromone import TrailQuery
from hivemind.wardens.deps import WardenDeps
from hivemind.wardens.spawn.binding import BindingCheck, authorize_binding, binding_is_local
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_grant_id, new_hive_id, new_warden_id
from waggle.messages.forage import AllowedBinding, GrantIssued, SourceRef
from waggle.messages.forage.values import Effort as WireEffort

_CLOCK = FakeClock()
_DRONE = CapabilitySet.parse("llm:worker", "tool:*")
_NIGHT_VEIL = PolicyContext(
    comb_shield=CombShieldLevel.NIGHT_VEIL, bound_tier=CombShieldLevel.NIGHT_VEIL
)
_LOCAL_RULE = "guard.tier_floor.night_veil_local_slots"


def _row(key: str, provider: str, fallback: str | None = None) -> SlotBinding:
    """One `[llm.slots]` row."""
    return SlotBinding(
        key=key, provider=provider, model="m", fallback=fallback, effort=Effort.MEDIUM
    )


# `worker` is served in process, and falls back to a hosted provider.
_SPILLING = (_row("worker", "fake", "hosted_worker"), _row("hosted_worker", "hosted"))


def _deps(*bindings: SlotBinding) -> WardenDeps:
    """A Warden whose only local provider is the in-process `fake`, over `bindings` if given."""
    deps, _queen_end, _warden_id = make_warden_deps()
    rows = bindings or deps.bindings
    return dataclasses.replace(deps, bindings=rows, local_providers=frozenset({"fake"}))


def _grant() -> GrantIssued:
    """A grant naming the WORKER slot."""
    source = SourceRef(source_id="local", provider="fake", model="m", host_cell_id=None)
    return GrantIssued(
        grant_id=new_grant_id(_CLOCK),
        holder=new_warden_id(_CLOCK),
        cell_id=new_cell_id(_CLOCK),
        task_id=None,
        revision=0,
        allowed=(AllowedBinding(slot="WORKER", source=source, max_effort=WireEffort.MEDIUM),),
        seats=(),
        token_budget=500_000,
        spend_budget=5.0,
        tokens_spent=0,
        spent=0.0,
        max_sub_bees=1,
        expires_at=_CLOCK.now(),
        reason="test grant",
    )


def _check(
    key: str, context: PolicyContext = _NIGHT_VEIL, *, by_queen: bool = False
) -> BindingCheck:
    """A binding of a Drone to `key` under `context`, ordered by the Warden or the Queen."""
    queen = queen_principal(new_hive_id(_CLOCK))
    orderer = queen if by_queen else warden_principal(new_warden_id(_CLOCK))
    return BindingCheck(
        orderer=orderer,
        capabilities=_DRONE,
        grant=_grant(),
        binding_key=key,
        slot=ModelSlot.WORKER,
        context=context,
    )


async def _rules(deps: WardenDeps) -> list[object]:
    """The rule of every `guard.denied` row on the Warden's trail."""
    return [e.payload["rule"] for e in await deps.trail.query(TrailQuery(kind="guard.denied"))]


def test_a_chain_served_wholly_in_process_is_local() -> None:
    assert binding_is_local("worker", _deps())  # worker -> worker_fallback, both `fake`.


def test_a_chain_with_a_hosted_fallback_is_not_local() -> None:
    deps = _deps(*_SPILLING)

    assert not binding_is_local("worker", deps)
    assert not binding_is_local("hosted_worker", deps)


def test_a_key_no_row_names_is_never_called_local() -> None:
    assert not binding_is_local("nowhere", _deps())


def test_a_cyclic_chain_stops_at_the_first_key_seen_again() -> None:
    deps = _deps(_row("worker", "fake", "again"), _row("again", "fake", "worker"))

    assert binding_is_local("worker", deps)


async def test_a_night_veil_task_binds_a_local_chain() -> None:
    deps = _deps()

    decision = await authorize_binding(deps, _check("worker"))

    assert decision.allowed


async def test_a_night_veil_task_cannot_be_rebound_to_a_hosted_slot() -> None:
    # The Queen's REBIND names the hosted fallback key; the task is bound to NIGHT_VEIL.
    deps = _deps(*_SPILLING)

    decision = await authorize_binding(deps, _check("hosted_worker", by_queen=True))

    assert not decision.allowed
    assert decision.rule == _LOCAL_RULE
    assert await _rules(deps) == [_LOCAL_RULE]


async def test_a_night_veil_binding_whose_chain_spills_to_hosted_is_refused() -> None:
    deps = _deps(*_SPILLING)

    decision = await authorize_binding(deps, _check("worker"))

    assert decision.rule == _LOCAL_RULE


async def test_a_meadow_task_may_bind_a_hosted_chain() -> None:
    deps = _deps(*_SPILLING)
    meadow = PolicyContext(comb_shield=CombShieldLevel.MEADOW, bound_tier=CombShieldLevel.MEADOW)

    decision = await authorize_binding(deps, _check("hosted_worker", meadow))

    assert decision.allowed
    assert await _rules(deps) == []
