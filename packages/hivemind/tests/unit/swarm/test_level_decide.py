"""Tests for hivemind.swarm.level.decide_colonize: should a device's Warden move onto it?

The decision is pure (same facts, same answer), so it is tested as a table. There must be a reason
(the operator asked, or the Hive Stand's pressure is past the threshold, or a task must survive
short outages), and the device must be able to carry a Warden: still on the first rung, running
Python 3.12 or newer, under an access level the Hive may work at, and with Forage that covers a
Warden plus one sub-bee. A device nobody wants colonized is refused as `not_asked`, whatever else
it lacks; one somebody wants is refused for the first thing it lacks, in a fixed order, as one
stable word.

Fits into the Hive:
    Mirrors src/hivemind/swarm/level.py (codingrules section 3); split by feature (14.2) from
    test_level.py, which covers the transition table.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.level for the module under test.
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for the decision's three reasons.
"""

from __future__ import annotations

import dataclasses

import pytest
from builders.forage import make_capacity, make_footprint, make_host_capacity

from hivemind.cell import AccessLevel
from hivemind.swarm.level import (
    ColonizeDecision,
    ColonizeFacts,
    ColonizeReason,
    colonize_reason,
    decide_colonize,
)
from waggle.messages.swarm import RuntimeLevel

_GIB = 1024**3
_RELIEF = 0.8


def _facts(**overrides: object) -> ColonizeFacts:
    """A GATEWAY device the operator asked to colonize and that can carry a Warden."""
    fields: dict[str, object] = {
        "level": RuntimeLevel.GATEWAY,
        "python_version": "3.12.4",
        "access_level": AccessLevel.SCRATCH,
        "capacity": make_capacity(host=make_host_capacity(cores=8, cpu_load=0.0)),
        "footprint": make_footprint(cpu_cores=1.0, memory_bytes=_GIB),
        "min_free_memory_bytes": 0,
        "desired_level": RuntimeLevel.COLONIZED,
        "hive_pressure": 0.0,
        "relief_pressure": _RELIEF,
    }
    fields.update(overrides)
    return ColonizeFacts(**fields)  # type: ignore[arg-type]


def _with_memory(free_gib: float, **overrides: object) -> ColonizeFacts:
    """The device with `free_gib` of free memory out of 64."""
    host = make_host_capacity(
        cores=8, cpu_load=0.0, memory_bytes=64 * _GIB, memory_free_bytes=int(free_gib * _GIB)
    )
    return _facts(capacity=make_capacity(host=host), **overrides)


def test_a_capable_device_the_operator_asked_for_is_colonized() -> None:
    decision = decide_colonize(_facts())

    assert decision == ColonizeDecision(ColonizeReason.OPERATOR)
    assert decision.should_colonize and decision.refusal is None


def test_asking_for_a_nuc_is_asking_for_the_first_rung_first() -> None:
    decision = decide_colonize(_facts(desired_level=RuntimeLevel.NUC))

    assert decision.reason is ColonizeReason.OPERATOR


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"desired_level": None, "hive_pressure": 0.8}, ColonizeReason.RELIEF),
        ({"desired_level": None, "hive_pressure": 0.95}, ColonizeReason.RELIEF),
        ({"desired_level": None, "needs_outage_survival": True}, ColonizeReason.OUTAGE),
        (
            {"desired_level": None, "hive_pressure": 0.9, "needs_outage_survival": True},
            ColonizeReason.RELIEF,  # The first reason that holds is the one given.
        ),
        (
            {"hive_pressure": 0.9, "needs_outage_survival": True},
            ColonizeReason.OPERATOR,  # The operator's own word outranks both.
        ),
    ],
)
def test_the_reason_is_the_first_one_that_holds(
    overrides: dict[str, object], reason: ColonizeReason
) -> None:
    assert decide_colonize(_facts(**overrides)).reason is reason


def test_nothing_asking_for_it_is_refused_whatever_the_device_lacks() -> None:
    # An old Python and no room, but nobody wants it colonized: "not_asked", not a list of faults.
    facts = _facts(desired_level=None, python_version="2.7.18", access_level=AccessLevel.READ_ONLY)

    assert decide_colonize(facts) == ColonizeDecision(None, "not_asked")


def test_pressure_just_under_the_threshold_is_not_a_reason() -> None:
    facts = _facts(desired_level=None, hive_pressure=0.79)

    assert decide_colonize(facts).refusal == "not_asked"


@pytest.mark.parametrize("level", [RuntimeLevel.COLONIZED, RuntimeLevel.NUC])
def test_only_the_first_rung_climbs_here(level: RuntimeLevel) -> None:
    assert decide_colonize(_facts(level=level)).refusal == "not_gateway"


@pytest.mark.parametrize(
    ("version", "word"),
    [
        (None, "python_unknown"),
        ("", "python_unknown"),
        ("not-a-version", "python_unknown"),
        ("2.7.18", "python_too_old"),
        ("3.11.9", "python_too_old"),
        ("3.9", "python_too_old"),
    ],
)
def test_a_device_without_python_3_12_is_refused(version: str | None, word: str) -> None:
    assert decide_colonize(_facts(python_version=version)).refusal == word


@pytest.mark.parametrize("version", ["3.12.0", "3.12.4", "3.13.0rc1", "3.14", "4.0.1"])
def test_python_3_12_or_newer_is_enough(version: str) -> None:
    assert decide_colonize(_facts(python_version=version)).should_colonize


@pytest.mark.parametrize(
    ("access", "colonized"),
    [
        (AccessLevel.READ_ONLY, False),
        (AccessLevel.SCRATCH, True),
        (AccessLevel.FULL, True),
    ],
)
def test_the_access_level_must_let_the_hive_work_on_the_device(
    access: AccessLevel, colonized: bool
) -> None:
    decision = decide_colonize(_facts(access_level=access))

    assert decision.should_colonize is colonized
    assert decision.refusal == (None if colonized else "access_too_low")


def test_forage_must_cover_a_warden_and_one_sub_bee() -> None:
    assert decide_colonize(_with_memory(2.0)).should_colonize  # Exactly two 1 GiB footprints.
    assert decide_colonize(_with_memory(1.9)).refusal == "forage_too_small"


def test_the_floor_of_free_memory_is_on_top_of_the_footprints() -> None:
    facts = _with_memory(2.5, min_free_memory_bytes=1 * _GIB)

    assert decide_colonize(facts).refusal == "forage_too_small"
    assert decide_colonize(
        dataclasses.replace(facts, min_free_memory_bytes=_GIB // 2)
    ).should_colonize


def test_cores_already_in_use_do_not_count() -> None:
    busy = make_host_capacity(cores=4, cpu_load=0.75, memory_bytes=64 * _GIB)  # One core is free.
    idle = make_host_capacity(cores=4, cpu_load=0.0, memory_bytes=64 * _GIB)

    assert decide_colonize(_facts(capacity=make_capacity(host=busy))).refusal == "forage_too_small"
    assert decide_colonize(_facts(capacity=make_capacity(host=idle))).should_colonize


def test_a_device_that_allows_no_sub_bee_cannot_carry_a_warden() -> None:
    capacity = make_capacity(host=make_host_capacity(cores=8, cpu_load=0.0), max_sub_bees=0)

    assert decide_colonize(_facts(capacity=capacity)).refusal == "forage_too_small"


def test_the_first_thing_the_device_lacks_is_the_word_given() -> None:
    # Everything fails: the order is rung, Python, access, Forage.
    broken = _facts(
        level=RuntimeLevel.NUC,
        python_version="2.7",
        access_level=AccessLevel.READ_ONLY,
        capacity=make_capacity(max_sub_bees=0),
    )

    assert decide_colonize(broken).refusal == "not_gateway"
    assert decide_colonize(dataclasses.replace(broken, level=RuntimeLevel.GATEWAY)).refusal == (
        "python_too_old"
    )
    fixed_python = dataclasses.replace(broken, level=RuntimeLevel.GATEWAY, python_version="3.12")
    assert decide_colonize(fixed_python).refusal == "access_too_low"


def test_the_same_facts_give_the_same_decision() -> None:
    facts = _facts()

    assert decide_colonize(facts) == decide_colonize(facts)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({}, ColonizeReason.OPERATOR),
        ({"desired_level": None, "hive_pressure": 0.8}, ColonizeReason.RELIEF),
        ({"desired_level": None, "needs_outage_survival": True}, ColonizeReason.OUTAGE),
        ({"desired_level": None, "hive_pressure": 0.79}, None),
        # The reason is read alone: a device the Hive may not work on still has one.
        (
            {
                "desired_level": None,
                "needs_outage_survival": True,
                "access_level": AccessLevel.READ_ONLY,
            },
            ColonizeReason.OUTAGE,
        ),
        # And of a device that already is colonized, which is how the Queen asks it before lowering.
        (
            {"desired_level": None, "level": RuntimeLevel.COLONIZED, "hive_pressure": 0.9},
            ColonizeReason.RELIEF,
        ),
    ],
)
def test_the_reason_alone_is_read_without_asking_whether_the_device_can_carry_a_warden(
    overrides: dict[str, object], reason: ColonizeReason | None
) -> None:
    assert colonize_reason(_facts(**overrides)) is reason
