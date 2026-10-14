"""Tests for hivemind.queen.swarm_gate.colonize.nuc.facts: the two pure halves of a promotion.

`nuc_decision` gives a colonized device that asks for the third rung to `decide_nuc` with what the
gate knows, and gives no decision to any other device; `nuc_ceilings` turns an accepted decision
into the bounds a model server needs. No Queen, registry or clock is built.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/colonize/nuc/facts.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.colonize.nuc.facts for the module under test.
    - tests/unit/swarm/test_nuc.py for the decision itself.
"""

from __future__ import annotations

import pytest
from builders.forage import make_capacity, make_host_capacity, make_source
from builders.swarm import gate_terms, make_swarm_node, nuc_terms
from builders.swarm.nuc import GIB

from hivemind.forage import Ceilings, GpuInfo
from hivemind.queen.swarm_gate.colonize.nuc.facts import (
    RestoreOrder,
    nuc_ceilings,
    nuc_decision,
    nuc_facts,
    restore_order,
)
from hivemind.swarm import NucDecision, NucReason, OfferedServer, RuntimeLevel, SwarmNode

C, N, G = RuntimeLevel.COLONIZED, RuntimeLevel.NUC, RuntimeLevel.GATEWAY


def _device(
    free_gib: float = 16.0, level: RuntimeLevel = C, desired_level: RuntimeLevel | None = N
) -> SwarmNode:
    """A device asking for `desired_level`, with one GPU that has `free_gib` free."""
    gpu = GpuInfo(name="gpu", vram_bytes=24 * GIB, vram_free_bytes=int(free_gib * GIB))
    capacity = make_capacity(host=make_host_capacity(gpus=(gpu,), disk_free_bytes=50 * GIB))
    return make_swarm_node(level=level, desired_level=desired_level, capacity=capacity)


def test_a_colonized_device_that_asks_for_the_nuc_rung_gets_the_decision_for_what_fits() -> None:
    decision = nuc_decision(_device(16.0), ("box",), nuc_terms())

    assert decision == NucDecision(
        NucReason.OPERATOR, None, "box", ("small", "large"), ("small", "large")
    )


def test_a_device_with_less_vram_is_offered_only_the_models_that_fit() -> None:
    decision = nuc_decision(_device(8.0), ("box",), nuc_terms())

    assert decision is not None and decision.loadable == ("small",)


@pytest.mark.parametrize(
    ("level", "desired"),
    [(G, N), (C, None), (C, G), (N, C)],
    ids=["gateway", "asks-nothing", "asks-below", "nuc-asks-down"],
)
def test_a_device_that_is_not_a_colonized_one_asking_for_the_nuc_rung_gets_no_decision(
    level: RuntimeLevel, desired: RuntimeLevel | None
) -> None:
    assert nuc_decision(_device(level=level, desired_level=desired), ("box",), nuc_terms()) is None


@pytest.mark.parametrize(
    ("declared", "word"),
    [((), "no_server"), (("elsewhere",), "no_allowlist")],
    ids=["declared-nothing", "declared-a-server-the-hive-does-not-allow"],
)
def test_a_warden_whose_server_the_hive_does_not_allow_is_refused_with_one_word(
    declared: tuple[str, ...], word: str
) -> None:
    decision = nuc_decision(_device(), declared, nuc_terms())

    assert decision is not None and decision.refusal == word


def test_a_device_with_too_little_vram_for_any_model_is_refused() -> None:
    decision = nuc_decision(_device(2.0), ("box",), nuc_terms())

    assert decision is not None and decision.refusal == "vram_too_small"


def test_the_ceilings_of_a_nuc_bound_models_by_what_the_device_has_free() -> None:
    node = _device(16.0)
    decision = nuc_decision(node, ("box",), nuc_terms())
    assert decision is not None
    current = Ceilings(
        max_sub_bees=3,
        model_vram_bytes=0,
        model_disk_bytes=0,
        loadable_sources=("old",),
        exportable_seats=1,
        scratch_disk_bytes_per_lease=7,
        resident_basket_disk_bytes=9,
    )

    ceilings = nuc_ceilings(current, node, decision)

    assert ceilings.model_vram_bytes == 16 * GIB and ceilings.model_disk_bytes == 50 * GIB
    assert ceilings.loadable_sources == ("small", "large")  # Every model that fits on its own.


def test_the_ceilings_of_a_nuc_change_nothing_a_nuc_does_not_need() -> None:
    node = _device(16.0)
    decision = nuc_decision(node, ("box",), nuc_terms())
    assert decision is not None
    current = Ceilings(
        max_sub_bees=3,
        model_vram_bytes=0,
        model_disk_bytes=0,
        loadable_sources=(),
        exportable_seats=1,
        scratch_disk_bytes_per_lease=7,
        resident_basket_disk_bytes=9,
    )

    ceilings = nuc_ceilings(current, node, decision)

    # The sub-bee cap (for access level and left-as-found reasons), the other disks and the seats
    # lent out stay what the Queen set; and a loopback server's seats are not exported by default.
    assert (ceilings.max_sub_bees, ceilings.exportable_seats) == (3, 1)
    assert (ceilings.scratch_disk_bytes_per_lease, ceilings.resident_basket_disk_bytes) == (7, 9)
    assert current.loadable_sources == ()  # The ceilings it was given are not edited.


def _held(loadable: tuple[str, ...], vram_gib: float) -> Ceilings:
    """What the Queen set for a Warden when she made its device a Nuc."""
    return Ceilings(
        max_sub_bees=3,
        model_vram_bytes=int(vram_gib * GIB),
        model_disk_bytes=0,
        loadable_sources=loadable,
        exportable_seats=0,
        scratch_disk_bytes_per_lease=0,
        resident_basket_disk_bytes=0,
    )


def test_the_facts_carry_the_pressure_and_the_need_the_queen_gives_them() -> None:
    node = _device(16.0, desired_level=None)

    facts = nuc_facts(node, ("box",), nuc_terms(), pressure=0.9, survives=True)

    assert (facts.hive_pressure, facts.needs_outage_survival) == (0.9, True)
    assert facts.seat_pressure == nuc_terms().nuc_seat_pressure
    assert (facts.declared, facts.level, facts.desired_level) == (("box",), C, None)


def test_the_facts_of_an_operators_request_carry_neither() -> None:
    facts = nuc_facts(_device(16.0), ("box",), nuc_terms())

    assert (facts.hive_pressure, facts.needs_outage_survival) == (0.0, False)


@pytest.mark.parametrize(
    ("loadable", "vram_gib", "declared", "expected"),
    [
        (("small", "large"), 16, ("box",), RestoreOrder("box", ("small", "large"))),
        (("small",), 8, ("box",), RestoreOrder("box", ("small",))),
        (("small", "large"), 12, ("box",), RestoreOrder("box", ("small",))),  # Not both together.
        (("small",), 16, (), RestoreOrder("box", ("small",))),  # Before its capacity report.
        (("large",), 16, ("box",), RestoreOrder("box", ("large",))),
        ((), 16, ("box",), None),  # She allowed it nothing.
        (("small",), 16, ("elsewhere",), None),  # It declares no server the Hive allows.
        (("gone",), 16, ("box",), None),  # What she allowed is not on offer any more.
        (("large",), 8, ("box",), None),  # Nothing she allowed fits the room she allowed.
    ],
)
def test_a_restarted_warden_is_told_to_start_what_the_queen_let_it_load(
    loadable: tuple[str, ...],
    vram_gib: float,
    declared: tuple[str, ...],
    expected: RestoreOrder | None,
) -> None:
    assert restore_order(_held(loadable, vram_gib), nuc_terms(), declared) == expected


def test_a_warden_the_queen_never_set_ceilings_for_has_nothing_to_restore() -> None:
    assert restore_order(None, nuc_terms(), ("box",)) is None


def test_the_first_declared_server_with_a_model_the_queen_allowed_is_chosen() -> None:
    other = OfferedServer("other", (make_source(source_id="o1", model="o1-model"),))
    box = nuc_terms().servers[0]
    terms = gate_terms(servers=(box, other))
    held = _held(("small", "o1"), 16)

    assert restore_order(held, terms, ("other", "box")) == RestoreOrder("other", ("o1",))
    assert restore_order(held, terms, ("box", "other")) == RestoreOrder("box", ("small",))
    assert restore_order(held, terms, ("unlisted", "other")) == RestoreOrder("other", ("o1",))
