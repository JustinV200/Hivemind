"""Tests for hivemind.forage.models.pools: LocalPool, Ceilings, SourceChain, SlotPlan, HostingPlan.

Fits into the Hive:
    Mirrors src/hivemind/forage/models/pools.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.forage.models.pools for the module under test.
"""

from __future__ import annotations

import pytest
from builders.forage import make_host_capacity, make_reserve, make_source
from pydantic import ValidationError

from hivemind.forage.models.pools import Ceilings, HostingPlan, LocalPool, SlotPlan, SourceChain
from hivemind.forage.slots import ModelSlot
from waggle.clock import FakeClock
from waggle.ids import new_cell_id
from waggle.messages.forage import CeilingsReport as WireCeilingsReport
from waggle.messages.forage import SlotPlan as WireSlotPlan
from waggle.messages.forage import SourceChain as WireSourceChain


def test_local_pool_round_trips_json() -> None:
    original = LocalPool(host_capacity=make_host_capacity(), local_seats=(), reserve=make_reserve())

    restored = LocalPool.model_validate_json(original.model_dump_json())

    assert restored == original


def test_local_pool_is_frozen_and_forbids_extras() -> None:
    pool = LocalPool(host_capacity=make_host_capacity(), local_seats=(), reserve=make_reserve())

    with pytest.raises(ValidationError, match="frozen"):
        pool.reserve = make_reserve(seats=2)  # The assignment is the test.
    with pytest.raises(ValidationError, match="extra"):
        LocalPool.model_validate({**pool.model_dump(), "nope": 1})


def _ceilings(**overrides: object) -> Ceilings:
    fields: dict[str, object] = {
        "max_sub_bees": 4,
        "model_vram_bytes": 1_000,
        "model_disk_bytes": 2_000,
        "loadable_sources": ("src_1",),
        "exportable_seats": 1,
    }
    fields.update(overrides)
    return Ceilings(**fields)


def test_ceilings_round_trips_through_the_wire_form() -> None:
    original = _ceilings()

    wire = original.to_wire()

    assert isinstance(wire, WireCeilingsReport)
    assert Ceilings.from_wire(wire) == original


def test_ceilings_rejects_max_sub_bees_above_the_wire_bound() -> None:
    with pytest.raises(ValidationError):
        _ceilings(max_sub_bees=1_000)


def test_ceilings_is_frozen_and_forbids_extras() -> None:
    ceilings = _ceilings()

    with pytest.raises(ValidationError, match="frozen"):
        ceilings.exportable_seats = 2  # The assignment is the test.
    with pytest.raises(ValidationError, match="extra"):
        Ceilings.model_validate({**ceilings.model_dump(), "nope": 1})


def test_source_chain_round_trips_through_the_wire_form() -> None:
    primary = make_source(source_id="src_primary")
    fallback = make_source(source_id="src_fallback")
    original = SourceChain(primary="src_primary", fallbacks=("src_fallback",))
    sources = {"src_primary": primary, "src_fallback": fallback}

    wire = original.to_wire(sources)

    assert isinstance(wire, WireSourceChain)
    assert SourceChain.from_wire(wire) == original


def test_source_chain_defaults_fallbacks_to_empty() -> None:
    chain = SourceChain(primary="src_primary")

    assert chain.fallbacks == ()


def test_slot_plan_round_trips_through_the_wire_form() -> None:
    primary = make_source(source_id="src_primary")
    original = SlotPlan(slot=ModelSlot.WORKER, chain=SourceChain(primary="src_primary"))

    wire = original.to_wire({"src_primary": primary})

    assert isinstance(wire, WireSlotPlan)
    assert SlotPlan.from_wire(wire) == original


def _hosting_plan(**overrides: object) -> HostingPlan:
    fields: dict[str, object] = {
        "cell_id": new_cell_id(FakeClock()),
        "slots": (SlotPlan(slot=ModelSlot.WORKER, chain=SourceChain(primary="src_primary")),),
        "default": SourceChain(primary="src_primary"),
        "reason": "local model covers it",
    }
    fields.update(overrides)
    return HostingPlan(**fields)


def test_hosting_plan_defaults_revision_to_zero() -> None:
    plan = _hosting_plan()

    assert plan.revision == 0


def test_hosting_plan_rejects_a_slot_named_twice() -> None:
    duplicate = SlotPlan(slot=ModelSlot.WORKER, chain=SourceChain(primary="src_other"))

    with pytest.raises(ValidationError, match="unique"):
        _hosting_plan(
            slots=(
                SlotPlan(slot=ModelSlot.WORKER, chain=SourceChain(primary="src_primary")),
                duplicate,
            )
        )


def test_hosting_plan_json_round_trips() -> None:
    original = _hosting_plan()

    restored = HostingPlan.model_validate_json(original.model_dump_json())

    assert restored == original


def test_hosting_plan_is_frozen_and_forbids_extras() -> None:
    plan = _hosting_plan()

    with pytest.raises(ValidationError, match="frozen"):
        plan.reason = "changed"  # The assignment is the test.
    with pytest.raises(ValidationError, match="extra"):
        HostingPlan.model_validate({**plan.model_dump(), "nope": 1})
