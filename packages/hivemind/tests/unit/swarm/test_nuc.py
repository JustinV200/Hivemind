"""Tests for hivemind.swarm.nuc.decide_nuc: should a colonized device start a model server?

The decision is pure (same facts, same answer), so it is tested as a table. There must be a reason
(the operator asked, or the Hive Stand's seats are past the threshold, or a task must survive a
lost link), and the device must be colonized, have declared a server, have models the Hive allows
that server, and have VRAM free for at least one of them. A device nobody wants promoted is refused
as `not_asked`, whatever else it lacks; one somebody wants is refused for the first thing it lacks,
in a fixed order, as one stable word.

Fits into the Hive:
    Mirrors src/hivemind/swarm/nuc.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.nuc for the module under test.
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for the decision's three reasons.
"""

from __future__ import annotations

import dataclasses

import pytest
from builders.forage import make_capacity, make_host_capacity, make_source

from hivemind.forage import ForageCapacity, GpuInfo, ModelSource
from hivemind.swarm.nuc import (
    NucDecision,
    NucFacts,
    NucReason,
    OfferedServer,
    decide_nuc,
    free_vram_bytes,
    vram_of,
)
from waggle.messages.swarm import RuntimeLevel

_GIB = 1024**3
_SEAT_PRESSURE = 0.75


def _gpu(free_gib: float, total_gib: float = 24.0) -> GpuInfo:
    """A GPU with `free_gib` of its `total_gib` free."""
    return GpuInfo(
        name="scripted gpu", vram_bytes=int(total_gib * _GIB), vram_free_bytes=int(free_gib * _GIB)
    )


def _capacity(*free_gib: float) -> ForageCapacity:
    """A device capacity with one GPU per figure in `free_gib`."""
    return make_capacity(host=make_host_capacity(gpus=tuple(_gpu(free) for free in free_gib)))


def _model(source_id: str, vram_gib: float | None) -> ModelSource:
    """A Forage map entry needing `vram_gib` of VRAM (None: it runs on the CPU)."""
    need = None if vram_gib is None else int(vram_gib * _GIB)
    return make_source(source_id=source_id, model=f"{source_id}-model", vram_bytes_required=need)


def _facts(**overrides: object) -> NucFacts:
    """A COLONIZED device the operator asked to promote, with one server and 16 GiB free."""
    fields: dict[str, object] = {
        "level": RuntimeLevel.COLONIZED,
        "capacity": _capacity(16.0),
        "declared": ("box",),
        "offered": (OfferedServer("box", (_model("small", 4.0), _model("large", 12.0))),),
        "desired_level": RuntimeLevel.NUC,
        "hive_pressure": 0.0,
        "seat_pressure": _SEAT_PRESSURE,
    }
    fields.update(overrides)
    return NucFacts(**fields)  # type: ignore[arg-type]


def test_a_colonized_device_the_operator_asked_for_is_promoted_with_its_models() -> None:
    decision = decide_nuc(_facts())

    assert decision == NucDecision(
        NucReason.OPERATOR, None, "box", ("small", "large"), ("small", "large")
    )
    assert decision.should_promote and decision.refusal is None


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"desired_level": None, "hive_pressure": 0.75}, NucReason.PRESSURE),
        ({"desired_level": None, "hive_pressure": 0.99}, NucReason.PRESSURE),
        ({"desired_level": None, "needs_outage_survival": True}, NucReason.AUTONOMY),
        (
            {"desired_level": None, "hive_pressure": 0.9, "needs_outage_survival": True},
            NucReason.PRESSURE,  # The first reason that holds is the one given.
        ),
        (
            {"hive_pressure": 0.9, "needs_outage_survival": True},
            NucReason.OPERATOR,  # The operator's own word outranks both.
        ),
        (
            {"desired_level": RuntimeLevel.COLONIZED, "needs_outage_survival": True},
            NucReason.AUTONOMY,  # Asking for the rung the device is on asks for nothing.
        ),
    ],
)
def test_the_reason_is_the_first_one_that_holds(
    overrides: dict[str, object], reason: NucReason
) -> None:
    assert decide_nuc(_facts(**overrides)).reason is reason


def test_nothing_asking_for_it_is_refused_whatever_the_device_lacks() -> None:
    # No server, no room, not colonized, but nobody wants a Nuc: "not_asked", not a list of faults.
    facts = _facts(
        desired_level=None, level=RuntimeLevel.GATEWAY, declared=(), capacity=_capacity()
    )

    assert decide_nuc(facts) == NucDecision(None, "not_asked")


def test_pressure_just_under_the_threshold_is_not_a_reason() -> None:
    assert decide_nuc(_facts(desired_level=None, hive_pressure=0.74)).refusal == "not_asked"


@pytest.mark.parametrize("level", [RuntimeLevel.GATEWAY, RuntimeLevel.NUC])
def test_only_a_colonized_device_is_promoted(level: RuntimeLevel) -> None:
    # A device with no Warden on it has nobody to start a server; a Nuc already has one.
    assert decide_nuc(_facts(level=level)).refusal == "not_colonized"


def test_a_device_whose_warden_declared_no_server_is_refused() -> None:
    assert decide_nuc(_facts(declared=())).refusal == "no_server"


def test_a_server_the_hive_lists_no_models_for_is_refused_as_having_no_allowlist() -> None:
    facts = _facts(declared=("stranger",))  # The Hive's manifest knows "box", not this one.

    assert decide_nuc(facts) == NucDecision(None, "no_allowlist")


def test_a_hive_that_offers_nothing_at_all_allows_no_models() -> None:
    assert decide_nuc(_facts(offered=())).refusal == "no_allowlist"


def test_a_server_whose_every_model_is_too_large_for_the_free_vram_is_refused() -> None:
    facts = _facts(capacity=_capacity(3.0))  # The smallest model needs 4 GiB.

    assert decide_nuc(facts) == NucDecision(None, "vram_too_small")


def test_a_model_that_fits_exactly_is_enough() -> None:
    decision = decide_nuc(_facts(capacity=_capacity(4.0)))

    assert decision.loadable == ("small",) and decision.initial == ("small",)


def test_only_the_models_that_fit_the_free_vram_are_allowed() -> None:
    decision = decide_nuc(_facts(capacity=_capacity(8.0)))

    assert decision.loadable == ("small",)  # The 12 GiB one never fits this device.


def test_models_that_fit_alone_but_not_together_are_all_allowed_but_not_all_loaded_first() -> None:
    offered = (OfferedServer("box", (_model("a", 10.0), _model("b", 10.0))),)

    decision = decide_nuc(_facts(offered=offered, capacity=_capacity(16.0)))

    assert decision.loadable == ("a", "b")  # Either may be loaded later, one at a time.
    assert decision.initial == ("a",)  # Together they would not fit.


def test_an_earlier_model_may_crowd_out_a_later_one_but_never_the_reverse() -> None:
    offered = (
        OfferedServer("box", (_model("big", 12.0), _model("mid", 8.0), _model("tiny", 2.0))),
    )

    decision = decide_nuc(_facts(offered=offered, capacity=_capacity(16.0)))

    # 12 loads, 8 no longer fits in the 4 left, 2 does: the Hive's order decides.
    assert decision.initial == ("big", "tiny") and decision.loadable == ("big", "mid", "tiny")


def test_a_model_that_declares_no_vram_runs_on_the_cpu_and_needs_none() -> None:
    offered = (OfferedServer("box", (_model("cpu-model", None),)),)

    decision = decide_nuc(_facts(offered=offered, capacity=_capacity()))  # A device with no GPU.

    assert decision.should_promote and decision.initial == ("cpu-model",)


def test_the_free_vram_of_every_gpu_counts() -> None:
    offered = (OfferedServer("box", (_model("split", 20.0),)),)

    assert decide_nuc(_facts(offered=offered, capacity=_capacity(10.0, 10.0))).should_promote
    assert decide_nuc(_facts(offered=offered, capacity=_capacity(10.0, 9.0))).refusal == (
        "vram_too_small"
    )


def test_the_first_declared_server_that_fits_is_chosen() -> None:
    offered = (
        OfferedServer("hungry", (_model("huge", 40.0),)),
        OfferedServer("modest", (_model("small", 4.0),)),
        OfferedServer("other", (_model("also-small", 2.0),)),
    )

    decision = decide_nuc(_facts(declared=("hungry", "modest", "other"), offered=offered))

    assert decision.server_id == "modest"  # "hungry" fits nothing; "other" comes later.


def test_a_declared_server_the_hive_does_not_list_is_skipped_for_one_it_does() -> None:
    decision = decide_nuc(_facts(declared=("stranger", "box")))

    assert decision.server_id == "box"


def test_the_hive_offering_a_server_the_device_never_declared_starts_nothing() -> None:
    offered = (OfferedServer("elsewhere", (_model("small", 4.0),)),)

    assert decide_nuc(_facts(offered=offered)).refusal == "no_allowlist"


def test_the_first_thing_the_device_lacks_is_the_word_given() -> None:
    broken = _facts(
        level=RuntimeLevel.GATEWAY,
        declared=(),
        offered=(),
        capacity=_capacity(),
        desired_level=None,
    )
    broken = dataclasses.replace(broken, needs_outage_survival=True)  # Someone wants it.

    assert decide_nuc(broken).refusal == "not_colonized"
    colonized = dataclasses.replace(broken, level=RuntimeLevel.COLONIZED)
    assert decide_nuc(colonized).refusal == "no_server"
    declared = dataclasses.replace(colonized, declared=("box",))
    assert decide_nuc(declared).refusal == "no_allowlist"
    offered = dataclasses.replace(declared, offered=_facts().offered)
    assert decide_nuc(offered).refusal == "vram_too_small"


def test_a_decision_changes_nothing_it_was_given() -> None:
    facts = _facts()

    first, second = decide_nuc(facts), decide_nuc(facts)

    assert first == second  # Pure: the same facts, the same answer.


def test_free_vram_is_the_sum_over_gpus_and_zero_without_one() -> None:
    assert free_vram_bytes(_capacity(1.0, 2.0)) == 3 * _GIB
    assert free_vram_bytes(_capacity()) == 0


def test_a_source_that_declares_no_vram_needs_zero() -> None:
    assert vram_of(_model("cpu", None)) == 0
    assert vram_of(_model("gpu", 2.0)) == 2 * _GIB
