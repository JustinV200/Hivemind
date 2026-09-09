"""Tests for hivemind.forage.models.grants: grant models from AllowedBinding to RoyalReserve.

Fits into the Hive:
    Mirrors src/hivemind/forage/models/grants.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.forage.models.grants for the module under test.
"""

from __future__ import annotations

import pytest
from builders.forage import make_grant, make_reserve, make_source
from pydantic import ValidationError

from hivemind.forage.grant_state import GrantState
from hivemind.forage.models.grants import (
    AllowedBinding,
    ForageGrant,
    ForageRequest,
    ForageRequestKind,
    RoyalReserve,
    SeatReservation,
)
from hivemind.forage.slots import Effort, ModelSlot
from hivemind.forage.tempo import AccuracyBar, Tempo
from waggle.clock import FakeClock
from waggle.ids import new_grant_id
from waggle.messages.forage import AllowedBinding as WireAllowedBinding
from waggle.messages.forage import GrantIssued as WireGrantIssued
from waggle.messages.forage import SeatReservation as WireSeatReservation
from waggle.messages.forage.values import ForageRequestKind as WireForageRequestKind


def test_allowed_binding_round_trips_through_the_wire_form() -> None:
    source = make_source(source_id="src_1")
    original = AllowedBinding(slot=ModelSlot.WORKER, source_id="src_1", max_effort=Effort.HIGH)

    wire = original.to_wire(source)

    assert isinstance(wire, WireAllowedBinding)
    assert AllowedBinding.from_wire(wire) == original


def test_allowed_binding_to_wire_rejects_a_mismatched_source() -> None:
    other_source = make_source(source_id="src_other")
    binding = AllowedBinding(slot=ModelSlot.WORKER, source_id="src_1", max_effort=Effort.LOW)

    with pytest.raises(ValueError, match="src_1"):
        binding.to_wire(other_source)


def test_seat_reservation_round_trips_through_the_wire_form() -> None:
    original = SeatReservation(
        source_id="src_1", seats=2, requests_per_minute=None, tokens_per_minute=None
    )

    wire = original.to_wire()

    assert isinstance(wire, WireSeatReservation)
    assert SeatReservation.from_wire(wire) == original


def test_seat_reservation_rejects_a_negative_seat_count() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        SeatReservation(
            source_id="src_1", seats=-1, requests_per_minute=None, tokens_per_minute=None
        )


def test_royal_reserve_has_sensible_defaults() -> None:
    reserve = make_reserve()

    assert reserve.seats == 1
    assert reserve.memory_bytes == 512 * 1024 * 1024
    assert reserve.headroom_fraction == 0.1


@pytest.mark.parametrize("fraction", [-0.1, 1.0, 1.5])
def test_royal_reserve_rejects_a_headroom_fraction_outside_zero_to_one(fraction: float) -> None:
    with pytest.raises(ValidationError):
        make_reserve(headroom_fraction=fraction)


def test_royal_reserve_is_frozen_and_forbids_extras() -> None:
    reserve = make_reserve()

    with pytest.raises(ValidationError, match="frozen"):
        reserve.seats = 5  # The assignment is the test.
    with pytest.raises(ValidationError, match="extra"):
        RoyalReserve.model_validate({**reserve.model_dump(), "nope": 1})


def test_forage_grant_defaults_state_to_issued_and_spend_to_zero() -> None:
    grant = make_grant()

    assert grant.state is GrantState.ISSUED
    assert grant.revision == 0
    assert grant.tokens_spent == 0
    assert grant.spent == 0.0


def test_forage_grant_round_trips_through_the_wire_form() -> None:
    source = make_source(source_id="src_1")
    original = make_grant(
        allowed=(
            AllowedBinding(slot=ModelSlot.WORKER, source_id="src_1", max_effort=Effort.MEDIUM),
        ),
        seats=(
            SeatReservation(
                source_id="src_1", seats=1, requests_per_minute=None, tokens_per_minute=None
            ),
        ),
    )

    wire = original.to_wire({"src_1": source})

    assert isinstance(wire, WireGrantIssued)
    restored = ForageGrant.from_wire(wire)
    # from_wire always starts a fresh grant at ISSUED, matching the model's own default; every
    # other field must round-trip exactly.
    assert restored == original.model_copy(update={"state": GrantState.ISSUED})


def test_forage_grant_to_wire_raises_on_a_binding_whose_source_is_missing_from_the_mapping() -> (
    None
):
    grant = make_grant(
        allowed=(
            AllowedBinding(slot=ModelSlot.WORKER, source_id="src_missing", max_effort=Effort.LOW),
        )
    )

    with pytest.raises(KeyError):
        grant.to_wire({})


def test_forage_grant_is_frozen_and_forbids_extras() -> None:
    grant = make_grant()

    with pytest.raises(ValidationError, match="frozen"):
        grant.spent = 1.0  # The assignment is the test.
    with pytest.raises(ValidationError, match="extra"):
        ForageGrant.model_validate({**grant.model_dump(), "nope": 1})


def test_forage_request_kind_mirrors_the_wire_enum_member_for_member() -> None:
    hivemind_names = [member.name for member in ForageRequestKind]
    wire_names = [member.name for member in WireForageRequestKind]

    assert hivemind_names == wire_names
    assert [m.value for m in ForageRequestKind] == [m.value for m in WireForageRequestKind]


@pytest.mark.parametrize("kind", list(ForageRequestKind))
def test_forage_request_kind_round_trips_through_the_wire_form(kind: ForageRequestKind) -> None:
    assert ForageRequestKind.from_wire(kind.to_wire()) is kind


def _request(**overrides: object) -> ForageRequest:
    fields: dict[str, object] = {
        "grant_id": new_grant_id(FakeClock()),
        "kind": ForageRequestKind.SHARED_SEATS,
        "seats": 1,
        "tempo": Tempo(),
        "reason": "need more seats",
    }
    fields.update(overrides)
    return ForageRequest(**fields)


def test_forage_request_rejects_an_empty_ask() -> None:
    with pytest.raises(ValidationError, match="must want at least one non-zero field"):
        _request(seats=0)


def test_forage_request_is_empty_matches_the_all_zero_case() -> None:
    request = _request(seats=1)

    assert request.is_empty is False


def test_forage_request_accepts_a_binding_kind_with_a_slot_and_grade() -> None:
    request = _request(
        kind=ForageRequestKind.BINDING,
        seats=0,
        slot=ModelSlot.WORKER,
        minimum_grade=4,
        tempo=Tempo(accuracy=AccuracyBar.HIGH),
    )

    assert request.slot is ModelSlot.WORKER
    assert request.minimum_grade == 4
    assert request.is_empty is False


def test_forage_request_is_frozen_and_forbids_extras() -> None:
    request = _request()

    with pytest.raises(ValidationError, match="frozen"):
        request.seats = 2  # The assignment is the test.
    with pytest.raises(ValidationError, match="extra"):
        ForageRequest.model_validate({**request.model_dump(), "nope": 1})
