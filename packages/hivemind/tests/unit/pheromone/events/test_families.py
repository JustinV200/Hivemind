"""Tests for hivemind.pheromone.events.families: the eleven event families and the JSON codec.

Fits into the Hive:
    Mirrors src/hivemind/pheromone/events/families.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.pheromone.events.families for the module under test.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from hivemind.pheromone.errors import UnknownEventFamilyError
from hivemind.pheromone.events.base import LlmUsage, PheromoneEvent
from hivemind.pheromone.events.families import (
    EVENT_FAMILIES,
    AlarmEvent,
    CappingEvent,
    CellEvent,
    ForageEvent,
    LlmEvent,
    MemoryEvent,
    QueenEvent,
    SwarmEvent,
    TaskEvent,
    ToolEvent,
    WardenEvent,
    _build_event_families,  # White-box test of the collision assertion only; not public API.
    event_class_for,
    parse_event,
    parse_event_json,
)
from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id

# Every family but LlmEvent: LlmEvent's extra required-together fields need their own kwargs
# builder, so it is tested separately below rather than parametrised alongside these ten.
_NON_LLM_FAMILIES: tuple[type[PheromoneEvent], ...] = (
    CellEvent,
    TaskEvent,
    AlarmEvent,
    ForageEvent,
    MemoryEvent,
    QueenEvent,
    WardenEvent,
    ToolEvent,
    SwarmEvent,
    CappingEvent,
)
_ALL_FAMILIES: tuple[type[PheromoneEvent], ...] = (*_NON_LLM_FAMILIES, LlmEvent)


def _base_kwargs(clock: FakeClock, **overrides: object) -> dict[str, object]:
    """Build a valid PheromoneEvent kwargs dict (minus `kind`), minting fresh ids from `clock`."""
    kwargs: dict[str, object] = {
        "id": new_id(IdKind.EVENT, clock),
        "hive_id": new_id(IdKind.HIVE, clock),
        "node_id": new_id(IdKind.NODE, clock),
        "at": clock.now(),
        "actor": "system",
        "subject_id": new_id(IdKind.TASK, clock),
        "payload": {},
    }
    kwargs.update(overrides)
    return kwargs


# ──────────────────────────────────────────────────────────────────────────────
# Each family accepts its own vocabulary and rejects everything else
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("event_cls", _NON_LLM_FAMILIES)
def test_family_event_accepts_every_kind_in_its_own_vocabulary(
    event_cls: type[PheromoneEvent],
) -> None:
    clock = FakeClock()

    for kind in sorted(event_cls.KINDS):
        event = event_cls(kind=kind, **_base_kwargs(clock))
        assert event.kind == kind
        assert event.family == event_cls.FAMILY


@pytest.mark.parametrize("event_cls", _NON_LLM_FAMILIES)
def test_family_event_rejects_a_kind_from_another_family(event_cls: type[PheromoneEvent]) -> None:
    clock = FakeClock()
    # Any kind from a different family's vocabulary is a shape-valid, still-rejected value.
    foreign_kind = next(
        kind for other in _NON_LLM_FAMILIES if other is not event_cls for kind in other.KINDS
    )

    with pytest.raises(ValidationError):
        event_cls(kind=foreign_kind, **_base_kwargs(clock))


@pytest.mark.parametrize("event_cls", _ALL_FAMILIES)
def test_family_event_rejects_an_unknown_kind(event_cls: type[PheromoneEvent]) -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError):
        event_cls(kind=f"{event_cls.FAMILY}.nonexistent_kind_xyz", **_base_kwargs(clock))


@pytest.mark.parametrize("event_cls", _ALL_FAMILIES)
def test_family_event_rejects_a_malformed_kind(event_cls: type[PheromoneEvent]) -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError):
        event_cls(kind="NotAValidKindAtAll", **_base_kwargs(clock))


# ──────────────────────────────────────────────────────────────────────────────
# LlmEvent's extra fields
# ──────────────────────────────────────────────────────────────────────────────


def test_llm_event_call_requires_slot_provider_and_usage() -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError):
        LlmEvent(kind="llm.call", **_base_kwargs(clock))


def test_llm_event_call_accepts_slot_provider_and_usage() -> None:
    clock = FakeClock()
    usage = LlmUsage(input_tokens=10, output_tokens=5, cached_tokens=0, cost_usd=0.01)

    event = LlmEvent(
        kind="llm.call", slot="WORKER", provider="anthropic", usage=usage, **_base_kwargs(clock)
    )

    assert event.slot == "WORKER"
    assert event.provider == "anthropic"
    assert event.usage == usage


@pytest.mark.parametrize("kind", ["llm.rebound", "llm.fallback", "llm.spill"])
def test_llm_event_non_call_kinds_do_not_require_slot_provider_or_usage(kind: str) -> None:
    clock = FakeClock()

    event = LlmEvent(kind=kind, **_base_kwargs(clock))

    assert event.slot is None
    assert event.provider is None
    assert event.usage is None


# ──────────────────────────────────────────────────────────────────────────────
# EVENT_FAMILIES and event_class_for
# ──────────────────────────────────────────────────────────────────────────────


def test_memory_event_kinds_include_the_phase_3_14_additions() -> None:
    # roadmap step 3.14 (memory v0): added alongside the store that first needs them.
    assert {"memory.episode", "memory.note", "memory.pinned"} <= MemoryEvent.KINDS


def test_event_families_covers_exactly_the_eleven_families() -> None:
    assert set(EVENT_FAMILIES) == {
        "cell",
        "task",
        "alarm",
        "forage",
        "memory",
        "queen",
        "warden",
        "tool",
        "swarm",
        "capping",
        "llm",
    }
    assert len(EVENT_FAMILIES) == 11


@pytest.mark.parametrize("event_cls", _ALL_FAMILIES)
def test_event_class_for_returns_the_right_class(event_cls: type[PheromoneEvent]) -> None:
    kind = next(iter(sorted(event_cls.KINDS)))

    assert event_class_for(kind) is event_cls


def test_event_class_for_raises_for_an_unknown_family() -> None:
    with pytest.raises(UnknownEventFamilyError):
        event_class_for("nope.something")


def test_event_class_for_raises_for_a_malformed_kind() -> None:
    with pytest.raises(UnknownEventFamilyError):
        event_class_for("no-dot-at-all")


def test_build_event_families_rejects_two_classes_sharing_a_family() -> None:
    # White-box check of the module-import-time assertion EVENT_FAMILIES is built with: two
    # classes (here, the same class twice) sharing a FAMILY must never pass silently.
    with pytest.raises(AssertionError):
        _build_event_families((CellEvent, CellEvent))


# ──────────────────────────────────────────────────────────────────────────────
# parse_event / parse_event_json round trips and error cases
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("event_cls", _NON_LLM_FAMILIES)
def test_parse_event_round_trips_every_non_llm_family(event_cls: type[PheromoneEvent]) -> None:
    clock = FakeClock()
    kind = next(iter(sorted(event_cls.KINDS)))
    original = event_cls(kind=kind, **_base_kwargs(clock))

    parsed = parse_event(original.model_dump(mode="json"))

    assert parsed == original
    assert type(parsed) is event_cls


def test_parse_event_round_trips_llm_call() -> None:
    clock = FakeClock()
    usage = LlmUsage(input_tokens=1, output_tokens=2, cached_tokens=0, cost_usd=0.0)
    original = LlmEvent(
        kind="llm.call", slot="QUEEN", provider="anthropic", usage=usage, **_base_kwargs(clock)
    )

    parsed = parse_event(original.model_dump(mode="json"))

    assert parsed == original
    assert type(parsed) is LlmEvent


def test_parse_event_json_round_trips() -> None:
    clock = FakeClock()
    original = CellEvent(kind="cell.provisioned", **_base_kwargs(clock))
    raw = json.dumps(original.model_dump(mode="json"))

    parsed = parse_event_json(raw)

    assert parsed == original


def test_parse_event_raises_for_a_missing_kind() -> None:
    with pytest.raises(UnknownEventFamilyError):
        parse_event({})


def test_parse_event_raises_for_a_non_string_kind() -> None:
    with pytest.raises(UnknownEventFamilyError):
        parse_event({"kind": 123})


def test_parse_event_raises_for_an_unknown_family() -> None:
    with pytest.raises(UnknownEventFamilyError):
        parse_event({"kind": "nope.something"})


def test_parse_event_raises_for_a_malformed_kind() -> None:
    with pytest.raises(UnknownEventFamilyError):
        parse_event({"kind": "no-dot-at-all"})


@pytest.mark.parametrize("raw", ["[]", "42", '"just a string"', "null"])
def test_parse_event_json_raises_for_a_non_object_document(raw: str) -> None:
    with pytest.raises(UnknownEventFamilyError):
        parse_event_json(raw)
