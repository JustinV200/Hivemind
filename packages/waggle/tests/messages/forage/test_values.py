"""Tests for the forage family's values (waggle.messages.forage.values and capacity).

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Pins every forage enum's members and wire values
    to spec section 8.4, and for each of the ten value models construction, the JSON round trip,
    the rejection of an extra field and at least one bound, plus the LocalSourceReport
    consistency rule and ForageDelta.is_empty in both directions. The family's messages, and
    EXAMPLES, live in test_grants.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.messages.forage.values and waggle.messages.forage.capacity for the modules under
      test.
    - docs/waggle/spec.md section 8.4 for the fields and bounds pinned here.
"""

from __future__ import annotations

from enum import Enum

import pytest
from pydantic import BaseModel, ValidationError

from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id
from waggle.messages.base import MAX_SLOT_CHARS, MAX_SUB_BEES_ON_WIRE
from waggle.messages.forage.capacity import (
    MAX_FALLBACKS,
    MAX_LOADABLE_SOURCES,
    MAX_SERVER_ID_CHARS,
    MAX_SOURCES_PER_SERVER,
    CapacityTrigger,
    CeilingsReport,
    HostingMode,
    LocalPoolUsage,
    LocalSourceReport,
    ModelServerReport,
    SlotPlan,
    SourceChain,
)
from waggle.messages.forage.values import (
    MAX_MODEL_CHARS,
    MAX_MODEL_GRADE,
    MAX_PROVIDER_CHARS,
    MAX_SOURCE_ID_CHARS,
    MIN_MODEL_GRADE,
    AllowedBinding,
    Effort,
    ForageDelta,
    ForageOutcome,
    ForageRequestKind,
    RevocationCause,
    SeatReservation,
    SourceRef,
)

CLOCK = FakeClock()
CELL_ID = new_id(IdKind.CELL, CLOCK)
GIB = 1_073_741_824  # One gibibyte, so the figures below read as a real host.

# Member names in declaration order, per spec section 8.4; every value equals its name.
_MEMBERS: list[tuple[type[Enum], list[str]]] = [
    (CapacityTrigger, ["PROVISIONED", "ENROLLED", "CHANGED", "RECONNECTED", "PERIODIC"]),
    (Effort, ["LOW", "MEDIUM", "HIGH"]),
    (RevocationCause, ["EXPIRED", "HOLDER_OFFLINE", "RECLAIMED", "RELEASED", "STING_CUT"]),
    (ForageRequestKind, ["SHARED_SEATS", "SPEND", "BINDING", "SUB_BEES"]),
    (ForageOutcome, ["GRANTED", "PARTIAL", "DENIED"]),
    (HostingMode, ["SHARED_ONLY", "LOCAL_FIRST", "LOCAL_ONLY"]),
]


def _round_trips(model: BaseModel) -> bool:
    """Whether ``model`` survives model_dump(mode="json") and model_validate unchanged."""
    return type(model).model_validate(model.model_dump(mode="json")) == model


def _source(**overrides: object) -> SourceRef:
    """A hosted source, then ``overrides``."""
    fields: dict[str, object] = {
        "source_id": "hosted/strong",
        "provider": "hosted",
        "model": "strong-v1",
        "host_cell_id": None,
    }
    return SourceRef.model_validate({**fields, **overrides})


def _binding(**overrides: object) -> AllowedBinding:
    """A PLANNER binding on the hosted source, then ``overrides``."""
    fields: dict[str, object] = {"slot": "PLANNER", "source": _source(), "max_effort": "HIGH"}
    return AllowedBinding.model_validate({**fields, **overrides})


def _reservation(**overrides: object) -> SeatReservation:
    """Two seats plus hosted rates on one source, then ``overrides``."""
    fields: dict[str, object] = {
        "source_id": "hosted/strong",
        "seats": 2,
        "requests_per_minute": 60,
        "tokens_per_minute": 100_000,
    }
    return SeatReservation.model_validate({**fields, **overrides})


def _delta(**overrides: object) -> ForageDelta:
    """An empty delta (every count zero, nothing named), then ``overrides``."""
    fields: dict[str, object] = {"source_id": None, "slot": None, "minimum_grade": None}
    return ForageDelta.model_validate({**fields, **overrides})


def _local_source(**overrides: object) -> LocalSourceReport:
    """A loaded model with one seat free of four, then ``overrides``."""
    fields: dict[str, object] = {
        "model": "small-8b",
        "seats_total": 4,
        "seats_free": 1,
        "context_window": 8_192,
        "vram_bytes": 5 * GIB,
        "tokens_per_s": 42.0,
    }
    return LocalSourceReport.model_validate({**fields, **overrides})


def _server(**overrides: object) -> ModelServerReport:
    """A server serving one model, then ``overrides``."""
    fields: dict[str, object] = {"server_id": "nuc_server", "sources": (_local_source(),)}
    return ModelServerReport.model_validate({**fields, **overrides})


def _usage(**overrides: object) -> LocalPoolUsage:
    """A Warden using a little of everything, then ``overrides``."""
    fields: dict[str, object] = {
        "sub_bees_active": 2,
        "model_vram_bytes": 4 * GIB,
        "model_disk_bytes": 8 * GIB,
        "seats_exported": 1,
    }
    return LocalPoolUsage.model_validate({**fields, **overrides})


def _ceilings(**overrides: object) -> CeilingsReport:
    """Ceilings for a small Nuc, then ``overrides``."""
    fields: dict[str, object] = {
        "max_sub_bees": 4,
        "model_vram_bytes": 8 * GIB,
        "model_disk_bytes": 32 * GIB,
        "loadable_sources": ("nuc/small",),
        "exportable_seats": 2,
    }
    return CeilingsReport.model_validate({**fields, **overrides})


def _chain(**overrides: object) -> SourceChain:
    """A local primary with the hosted source as fallback, then ``overrides``."""
    fields: dict[str, object] = {
        "primary": _source(source_id="nuc/small", provider="local", host_cell_id=CELL_ID),
        "fallbacks": (_source(),),
    }
    return SourceChain.model_validate({**fields, **overrides})


# ──────────────────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("enum_type", "names"), _MEMBERS)
def test_enum_has_exactly_the_spec_members_with_values_equal_to_names(
    enum_type: type[Enum], names: list[str]
) -> None:
    assert [member.name for member in enum_type] == names
    assert [member.value for member in enum_type] == names


# ──────────────────────────────────────────────────────────────────────────────
# SourceRef, AllowedBinding, SeatReservation
# ──────────────────────────────────────────────────────────────────────────────


def test_source_ref_constructs_round_trips_and_names_a_host_cell() -> None:
    local = _source(host_cell_id=CELL_ID)

    assert _round_trips(_source())
    assert _round_trips(local)
    assert local.model_dump(mode="json")["host_cell_id"] == CELL_ID


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"source_id": "x" * (MAX_SOURCE_ID_CHARS + 1)}, f"at most {MAX_SOURCE_ID_CHARS}"),
        ({"provider": "x" * (MAX_PROVIDER_CHARS + 1)}, f"at most {MAX_PROVIDER_CHARS}"),
        ({"model": "x" * (MAX_MODEL_CHARS + 1)}, f"at most {MAX_MODEL_CHARS}"),
        ({"host_cell_id": "task_01ARZ3NDEKTSV4RRFFQ69G5FAV"}, "cell_"),
        ({"url": "http://example"}, "extra"),
    ],
)
def test_source_ref_bounds(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _source(**changes)


def test_allowed_binding_constructs_and_round_trips() -> None:
    binding = _binding()

    assert binding.max_effort is Effort.HIGH
    assert _round_trips(binding)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"slot": "planner"}, "pattern"),
        ({"slot": "_PLANNER"}, "pattern"),
        ({"slot": "P" * (MAX_SLOT_CHARS + 1)}, f"at most {MAX_SLOT_CHARS}"),
        ({"max_effort": "MAX"}, "max_effort"),
        ({"weight": 1}, "extra"),
    ],
)
def test_allowed_binding_bounds(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _binding(**changes)


def test_seat_reservation_constructs_and_round_trips_with_or_without_rates() -> None:
    assert _round_trips(_reservation())
    assert _round_trips(_reservation(requests_per_minute=None, tokens_per_minute=None))


@pytest.mark.parametrize("field", ["seats", "requests_per_minute", "tokens_per_minute"])
def test_seat_reservation_rejects_a_negative_figure(field: str) -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        _reservation(**{field: -1})


def test_seat_reservation_rejects_an_extra_field() -> None:
    with pytest.raises(ValidationError, match="extra"):
        _reservation(priority=1)


# ──────────────────────────────────────────────────────────────────────────────
# ForageDelta
# ──────────────────────────────────────────────────────────────────────────────


def test_forage_delta_defaults_every_count_to_zero_and_round_trips() -> None:
    empty = _delta()

    assert (empty.seats, empty.spend, empty.tokens, empty.sub_bees) == (0, 0.0, 0, 0)
    assert empty.is_empty
    assert _round_trips(empty)
    assert _round_trips(_delta(seats=1, source_id="hosted/strong", spend=0.5, tokens=10))


@pytest.mark.parametrize(
    "changes",
    [
        {"seats": 1},
        {"spend": 0.01},
        {"tokens": 1},
        {"sub_bees": 1},
        {"slot": "JUDGE"},
        {"minimum_grade": 3},
    ],
    ids=lambda changes: next(iter(changes)),
)
def test_forage_delta_is_not_empty_when_any_ask_is_set(changes: dict[str, object]) -> None:
    assert not _delta(**changes).is_empty


def test_forage_delta_source_id_alone_asks_nothing() -> None:
    assert _delta(source_id="hosted/strong").is_empty


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"seats": -1}, "greater than or equal to 0"),
        ({"spend": -0.01}, "greater than or equal to 0"),
        ({"tokens": -1}, "greater than or equal to 0"),
        ({"sub_bees": -1}, "greater than or equal to 0"),
        ({"sub_bees": MAX_SUB_BEES_ON_WIRE + 1}, f"less than or equal to {MAX_SUB_BEES_ON_WIRE}"),
        ({"slot": "judge"}, "pattern"),
        ({"minimum_grade": MIN_MODEL_GRADE - 1}, f"greater than or equal to {MIN_MODEL_GRADE}"),
        ({"minimum_grade": MAX_MODEL_GRADE + 1}, f"less than or equal to {MAX_MODEL_GRADE}"),
        ({"source_id": "x" * (MAX_SOURCE_ID_CHARS + 1)}, f"at most {MAX_SOURCE_ID_CHARS}"),
        ({"priority": 1}, "extra"),
    ],
)
def test_forage_delta_bounds(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _delta(**changes)


# ──────────────────────────────────────────────────────────────────────────────
# LocalSourceReport, ModelServerReport, LocalPoolUsage, CeilingsReport
# ──────────────────────────────────────────────────────────────────────────────


def test_local_source_report_constructs_and_round_trips() -> None:
    assert _round_trips(_local_source())
    assert _round_trips(_local_source(seats_free=4, tokens_per_s=None))


def test_local_source_report_rejects_more_free_seats_than_total_and_negatives() -> None:
    with pytest.raises(ValidationError, match="more than its total"):
        _local_source(seats_free=5)
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        _local_source(tokens_per_s=-1.0)
    with pytest.raises(ValidationError, match="extra"):
        _local_source(quantisation="q4")


def test_model_server_report_constructs_round_trips_and_bounds_its_sources() -> None:
    assert _round_trips(_server())
    assert _round_trips(_server(sources=()))
    with pytest.raises(ValidationError, match=f"at most {MAX_SOURCES_PER_SERVER}"):
        _server(sources=(_local_source(),) * (MAX_SOURCES_PER_SERVER + 1))
    with pytest.raises(ValidationError, match=f"at most {MAX_SERVER_ID_CHARS}"):
        _server(server_id="x" * (MAX_SERVER_ID_CHARS + 1))
    with pytest.raises(ValidationError, match="extra"):
        _server(url="http://example")


def test_local_pool_usage_constructs_round_trips_and_rejects_negatives() -> None:
    assert _round_trips(_usage())
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        _usage(seats_exported=-1)
    with pytest.raises(ValidationError, match="extra"):
        _usage(cpu_load=0.5)


def test_ceilings_report_constructs_round_trips_and_bounds() -> None:
    assert _round_trips(_ceilings())
    assert _round_trips(_ceilings(max_sub_bees=MAX_SUB_BEES_ON_WIRE, loadable_sources=()))
    with pytest.raises(ValidationError, match=f"less than or equal to {MAX_SUB_BEES_ON_WIRE}"):
        _ceilings(max_sub_bees=MAX_SUB_BEES_ON_WIRE + 1)
    with pytest.raises(ValidationError, match=f"at most {MAX_LOADABLE_SOURCES}"):
        _ceilings(loadable_sources=("s",) * (MAX_LOADABLE_SOURCES + 1))
    with pytest.raises(ValidationError, match=f"at most {MAX_SOURCE_ID_CHARS}"):
        _ceilings(loadable_sources=("x" * (MAX_SOURCE_ID_CHARS + 1),))
    with pytest.raises(ValidationError, match="extra"):
        _ceilings(max_spend=1.0)


# ──────────────────────────────────────────────────────────────────────────────
# SourceChain, SlotPlan
# ──────────────────────────────────────────────────────────────────────────────


def test_source_chain_constructs_round_trips_and_bounds_its_fallbacks() -> None:
    assert _round_trips(_chain())
    assert _round_trips(_chain(fallbacks=()))
    with pytest.raises(ValidationError, match=f"at most {MAX_FALLBACKS}"):
        _chain(fallbacks=(_source(),) * (MAX_FALLBACKS + 1))
    with pytest.raises(ValidationError, match="extra"):
        _chain(weight=1)


def test_slot_plan_constructs_round_trips_and_checks_its_slot() -> None:
    plan = SlotPlan(slot="JUDGE", chain=_chain())

    assert _round_trips(plan)
    with pytest.raises(ValidationError, match="pattern"):
        SlotPlan(slot="judge", chain=_chain())
    with pytest.raises(ValidationError, match="extra"):
        SlotPlan.model_validate({"slot": "JUDGE", "chain": _chain(), "weight": 1})
