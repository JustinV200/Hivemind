"""Tests for hivemind.queen.forage.night_veil: night_veil_local_only and restrict_to_local.

Fits into the Hive:
    Mirrors src/hivemind/queen/forage/night_veil.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.forage.night_veil for the module under test.
    - docs/adr/0030-night-veil-retention-and-clearance-boundary.md for the local-only rule.
"""

from __future__ import annotations

import pytest
from builders.forage import make_source

from hivemind.forage import ForageMap, HostingPlan, ModelSlot, SlotPlan, SourceChain
from hivemind.queen.forage.night_veil import (
    NightVeilPlanError,
    night_veil_local_only,
    restrict_to_local,
)
from waggle.clock import FakeClock
from waggle.ids import new_cell_id

_CLOCK = FakeClock()
_CELL_ID = new_cell_id(_CLOCK)
_HIVE_STAND_ID = new_cell_id(_CLOCK)  # Just another Cell id here: no special-casing in the module.


def _map() -> ForageMap:
    """A ForageMap with one local, one Hive-Stand-hosted and one fully-hosted source."""
    sources = (
        make_source(source_id="local", host_cell_id=_CELL_ID),
        make_source(source_id="hive_stand", host_cell_id=_HIVE_STAND_ID),
        make_source(source_id="hosted", host_cell_id=None),
    )
    return ForageMap(sources, clock=_CLOCK)


def _plan(*, worker_chain: SourceChain, default: SourceChain) -> HostingPlan:
    return HostingPlan(
        cell_id=_CELL_ID,
        slots=(SlotPlan(slot=ModelSlot.WORKER, chain=worker_chain),),
        default=default,
        reason="test plan",
    )


# ──────────────────────────────────────────────────────────────────────────────
# night_veil_local_only
# ──────────────────────────────────────────────────────────────────────────────


def test_an_all_local_plan_has_no_violations() -> None:
    plan = _plan(worker_chain=SourceChain(primary="local"), default=SourceChain(primary="local"))

    assert night_veil_local_only(plan, _map()) == ()


def test_a_hosted_primary_is_a_violation_naming_the_slot() -> None:
    plan = _plan(worker_chain=SourceChain(primary="hosted"), default=SourceChain(primary="local"))

    violations = night_veil_local_only(plan, _map())

    assert len(violations) == 1
    assert "slot WORKER" in violations[0]
    assert "hosted" in violations[0]


def test_a_hive_stand_fallback_is_a_violation() -> None:
    plan = _plan(
        worker_chain=SourceChain(primary="local", fallbacks=("hive_stand",)),
        default=SourceChain(primary="local"),
    )

    violations = night_veil_local_only(plan, _map())

    assert len(violations) == 1
    assert "hive_stand" in violations[0]


def test_a_non_local_default_chain_is_a_violation_naming_default() -> None:
    plan = _plan(worker_chain=SourceChain(primary="local"), default=SourceChain(primary="hosted"))

    violations = night_veil_local_only(plan, _map())

    assert len(violations) == 1
    assert "slot default" in violations[0]


def test_an_unknown_source_id_is_treated_as_non_local() -> None:
    plan = _plan(
        worker_chain=SourceChain(primary="not-on-the-map"), default=SourceChain(primary="local")
    )

    violations = night_veil_local_only(plan, _map())

    assert len(violations) == 1


# ──────────────────────────────────────────────────────────────────────────────
# restrict_to_local
# ──────────────────────────────────────────────────────────────────────────────


def test_restrict_to_local_drops_non_local_fallbacks_but_keeps_the_slot() -> None:
    plan = _plan(
        worker_chain=SourceChain(primary="local", fallbacks=("hosted", "hive_stand")),
        default=SourceChain(primary="local"),
    )

    restricted = restrict_to_local(plan, _map(), _CELL_ID)

    (worker,) = restricted.slots
    assert worker.chain == SourceChain(primary="local")
    assert night_veil_local_only(restricted, _map()) == ()


def test_restrict_to_local_promotes_a_local_fallback_to_primary() -> None:
    plan = _plan(
        worker_chain=SourceChain(primary="hosted", fallbacks=("local",)),
        default=SourceChain(primary="local"),
    )

    restricted = restrict_to_local(plan, _map(), _CELL_ID)

    (worker,) = restricted.slots
    assert worker.chain.primary == "local"


def test_restrict_to_local_drops_a_slot_left_with_no_local_source() -> None:
    plan = _plan(
        worker_chain=SourceChain(primary="hosted", fallbacks=("hive_stand",)),
        default=SourceChain(primary="local"),
    )

    restricted = restrict_to_local(plan, _map(), _CELL_ID)

    assert restricted.slots == ()  # WORKER had nothing local left, so it is simply not planned.


def test_restrict_to_local_raises_when_the_default_chain_has_no_local_source() -> None:
    plan = _plan(worker_chain=SourceChain(primary="local"), default=SourceChain(primary="hosted"))

    with pytest.raises(NightVeilPlanError, match="default"):
        restrict_to_local(plan, _map(), _CELL_ID)


def test_restrict_to_local_raises_on_a_cell_id_mismatch() -> None:
    plan = _plan(worker_chain=SourceChain(primary="local"), default=SourceChain(primary="local"))
    other_cell = new_cell_id(_CLOCK)

    with pytest.raises(NightVeilPlanError, match="must match"):
        restrict_to_local(plan, _map(), other_cell)


def test_restrict_to_local_preserves_revision_and_notes_the_restriction_in_the_reason() -> None:
    plan = _plan(
        worker_chain=SourceChain(primary="local"), default=SourceChain(primary="local")
    ).model_copy(update={"revision": 3})

    restricted = restrict_to_local(plan, _map(), _CELL_ID)

    assert restricted.revision == 3
    assert "Restricted to local-only" in restricted.reason
