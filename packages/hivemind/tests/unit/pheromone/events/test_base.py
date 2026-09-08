"""Tests for hivemind.pheromone.events.base: PheromoneEvent's shared validators, and LlmUsage.

Fits into the Hive:
    Mirrors src/hivemind/pheromone/events/base.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.pheromone.events.base for the module under test.
"""

from __future__ import annotations

from typing import ClassVar

import pytest
from pydantic import ValidationError

from hivemind.pheromone.events.base import MAX_PAYLOAD_STRING_CHARS, LlmUsage, PheromoneEvent
from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id


class _SampleEvent(PheromoneEvent):
    """A minimal, well-formed family subclass, used only to exercise the shared base validators."""

    FAMILY: ClassVar[str] = "sample"
    KINDS: ClassVar[frozenset[str]] = frozenset({"sample.happened"})


class _MismatchedFamilyEvent(PheromoneEvent):
    """Deliberately inconsistent subclass: KINDS names a kind outside FAMILY.

    Exists only so `_validate_kind`'s family-consistency check (belt-and-suspenders on top of
    "value in cls.KINDS") has a way to fail even though every real family in
    hivemind.pheromone.events.families keeps the two in sync by construction.
    """

    FAMILY: ClassVar[str] = "sample"
    KINDS: ClassVar[frozenset[str]] = frozenset({"other.mismatched"})


def _base_kwargs(clock: FakeClock, **overrides: object) -> dict[str, object]:
    """Build a valid _SampleEvent kwargs dict, minting fresh ids from `clock`.

    Args:
        clock: The FakeClock every id and the `at` field are minted/read from.
        overrides: Field values that replace the defaults below.
    """
    kwargs: dict[str, object] = {
        "id": new_id(IdKind.EVENT, clock),
        "hive_id": new_id(IdKind.HIVE, clock),
        "node_id": new_id(IdKind.NODE, clock),
        "at": clock.now(),
        "actor": "system",
        "kind": "sample.happened",
        "subject_id": new_id(IdKind.TASK, clock),
        "payload": {},
    }
    kwargs.update(overrides)
    return kwargs


# ──────────────────────────────────────────────────────────────────────────────
# kind: base class uninstantiable, family consistency, shape
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["sample.happened", "cell.provisioned", "task.submitted"])
def test_base_pheromone_event_rejects_every_kind(kind: str) -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError):
        PheromoneEvent(**_base_kwargs(clock, kind=kind))


def test_sample_event_accepts_its_own_kind() -> None:
    clock = FakeClock()

    event = _SampleEvent(**_base_kwargs(clock))

    assert event.kind == "sample.happened"


@pytest.mark.parametrize(
    "kind", ["SampleHappened", "sample.Happened", "sample", "sample.two.parts"]
)
def test_sample_event_rejects_a_malformed_kind(kind: str) -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError):
        _SampleEvent(**_base_kwargs(clock, kind=kind))


def test_sample_event_rejects_a_kind_outside_its_own_vocabulary() -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError):
        _SampleEvent(**_base_kwargs(clock, kind="sample.unknown_kind"))


def test_validate_kind_rejects_a_kind_whose_family_does_not_match_the_class() -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError):
        _MismatchedFamilyEvent(**_base_kwargs(clock, kind="other.mismatched"))


def test_family_property_returns_the_segment_before_the_dot() -> None:
    clock = FakeClock()

    event = _SampleEvent(**_base_kwargs(clock))

    assert event.family == "sample"


# ──────────────────────────────────────────────────────────────────────────────
# actor
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("actor", ["human", "system"])
def test_sample_event_accepts_literal_actors(actor: str) -> None:
    clock = FakeClock()

    event = _SampleEvent(**_base_kwargs(clock, actor=actor))

    assert event.actor == actor


@pytest.mark.parametrize("kind", [IdKind.HIVE, IdKind.WARDEN, IdKind.WORKER, IdKind.DEVICE])
def test_sample_event_accepts_actor_ids_of_the_four_principal_kinds(kind: IdKind) -> None:
    clock = FakeClock()
    actor_id = new_id(kind, clock)

    event = _SampleEvent(**_base_kwargs(clock, actor=actor_id))

    assert event.actor == actor_id


def test_sample_event_rejects_a_task_id_as_actor() -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError):
        _SampleEvent(**_base_kwargs(clock, actor=new_id(IdKind.TASK, clock)))


def test_sample_event_rejects_an_empty_actor() -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError):
        _SampleEvent(**_base_kwargs(clock, actor=""))


# ──────────────────────────────────────────────────────────────────────────────
# subject_id
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", list(IdKind))
def test_sample_event_accepts_subject_id_of_every_id_kind(kind: IdKind) -> None:
    clock = FakeClock()
    subject_id = new_id(kind, clock)

    event = _SampleEvent(**_base_kwargs(clock, subject_id=subject_id))

    assert event.subject_id == subject_id


def test_sample_event_rejects_a_garbage_subject_id() -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError):
        _SampleEvent(**_base_kwargs(clock, subject_id="not_an_id_at_all"))


# ──────────────────────────────────────────────────────────────────────────────
# payload
# ──────────────────────────────────────────────────────────────────────────────


def test_sample_event_rejects_a_top_level_forbidden_key() -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError):
        _SampleEvent(**_base_kwargs(clock, payload={"prompt": "hello"}))


def test_sample_event_rejects_a_nested_forbidden_key_regardless_of_case() -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError):
        _SampleEvent(**_base_kwargs(clock, payload={"a": {"MESSAGES": "hi"}}))


def test_sample_event_rejects_an_oversized_string_nested_in_a_list() -> None:
    clock = FakeClock()
    oversized = "x" * (MAX_PAYLOAD_STRING_CHARS + 1)

    with pytest.raises(ValidationError):
        _SampleEvent(**_base_kwargs(clock, payload={"a": [oversized]}))


def test_sample_event_rejects_an_oversized_total_payload() -> None:
    clock = FakeClock()
    # Each string is well under MAX_PAYLOAD_STRING_CHARS; only the combined serialised size (over
    # MAX_PAYLOAD_BYTES) should trip this one.
    big_payload = {f"k{i}": "x" * 200 for i in range(100)}

    with pytest.raises(ValidationError):
        _SampleEvent(**_base_kwargs(clock, payload=big_payload))


def test_sample_event_accepts_a_rich_nested_payload() -> None:
    clock = FakeClock()
    payload = {
        "count": 3,
        "ratio": 0.5,
        "ok": True,
        "missing": None,
        "items": [1, "two", 3.0, {"nested": "value"}],
        "detail": {"a": [True, False, None]},
    }

    event = _SampleEvent(**_base_kwargs(clock, payload=payload))

    assert event.payload == payload


def test_pheromone_event_is_not_hashable() -> None:
    clock = FakeClock()
    event = _SampleEvent(**_base_kwargs(clock))

    with pytest.raises(TypeError):
        hash(event)


# ──────────────────────────────────────────────────────────────────────────────
# LlmUsage
# ──────────────────────────────────────────────────────────────────────────────


def test_llm_usage_accepts_valid_fields() -> None:
    usage = LlmUsage(input_tokens=10, output_tokens=5, cached_tokens=2, cost_usd=0.03)

    assert usage.input_tokens == 10
    assert usage.output_tokens == 5
    assert usage.cached_tokens == 2
    assert usage.cost_usd == pytest.approx(0.03)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"input_tokens": -1, "output_tokens": 0, "cached_tokens": 0, "cost_usd": 0.0},
        {"input_tokens": 0, "output_tokens": -1, "cached_tokens": 0, "cost_usd": 0.0},
        {"input_tokens": 0, "output_tokens": 0, "cached_tokens": -1, "cost_usd": 0.0},
        {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "cost_usd": -0.01},
    ],
)
def test_llm_usage_rejects_a_negative_field(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        LlmUsage(**kwargs)
