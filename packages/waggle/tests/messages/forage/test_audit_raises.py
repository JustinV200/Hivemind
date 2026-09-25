"""Tests for Waggle 1.10's GrantIssued.audit_raises and its RaisedAuditRate value model.

A grant carries every Capping audit-rate raise in force when it is issued: a tier by name, the
raised rate and when it lapses, at most one per tier. It round-trips and crosses the codec
unchanged; a grant from before minor 10, with no such field, still validates with none; and every
bound the spec names holds.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Split by feature from test_grants.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/waggle/spec.md sections 4 (the 1.8 entry) and 8.4 (GrantIssued, RaisedAuditRate).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import PROTOCOL_MINOR, PROTOCOL_VERSION, Envelope
from waggle.ids import IdKind, new_id
from waggle.messages.base import WaggleMessage
from waggle.messages.forage import GrantIssued, RaisedAuditRate
from waggle.messages.forage.grants import MAX_AUDIT_RAISES

MakeEnvelope = Callable[..., Envelope]
CLOCK = FakeClock()
LATER = CLOCK.now() + timedelta(hours=1)


def _raise(tier: str = "SCRATCH_WRITE", rate: float = 0.27) -> RaisedAuditRate:
    return RaisedAuditRate(tier=tier, rate=rate, until=LATER)


def _grant(**changes: object) -> GrantIssued:
    fields: dict[str, object] = {
        "grant_id": new_id(IdKind.GRANT, CLOCK),
        "holder": new_id(IdKind.WARDEN, CLOCK),
        "cell_id": new_id(IdKind.CELL, CLOCK),
        "task_id": new_id(IdKind.TASK, CLOCK),
        "revision": 0,
        "allowed": (),
        "seats": (),
        "token_budget": 1_000,
        "spend_budget": 1.0,
        "tokens_spent": 0,
        "spent": 0.0,
        "max_sub_bees": 1,
        "expires_at": LATER,
        "reason": "the allocator's decision",
    }
    return GrantIssued.model_validate({**fields, **changes})


def test_the_protocol_is_at_minor_eight() -> None:
    assert (PROTOCOL_VERSION, PROTOCOL_MINOR) == ("1.10", 10)


def test_a_grant_carrying_raises_round_trips(
    plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    grant = _grant(audit_raises=(_raise(), _raise("NETWORK_EGRESS", 1.0)))
    envelope = make_envelope(grant)

    decoded = plain_codec.decode(plain_codec.encode(envelope))

    assert GrantIssued.model_validate(grant.model_dump(mode="json")) == grant
    assert isinstance(decoded.payload, WaggleMessage) and decoded.payload == grant


def test_a_grant_from_before_minor_eight_validates_with_no_raises() -> None:
    older = _grant().model_dump(mode="json")
    del older["audit_raises"]

    assert GrantIssued.model_validate(older).audit_raises == ()


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"audit_raises": (_raise(), _raise(rate=0.5))}, "more than once"),
        ({"audit_raises": (_raise(),) * (MAX_AUDIT_RAISES + 1)}, f"at most {MAX_AUDIT_RAISES}"),
    ],
)
def test_a_grant_carries_at_most_one_raise_per_tier(
    changes: dict[str, object], reason: str
) -> None:
    with pytest.raises(ValidationError, match=reason):
        _grant(**changes)


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"rate": 0.0}, "greater than 0"),
        ({"rate": 1.5}, "less than or equal to 1"),
        ({"tier": "scratch_write"}, "pattern"),
        ({"tier": "A" * 65}, "at most 64"),
        ({"until": LATER.replace(tzinfo=None)}, "timezone-aware"),
    ],
)
def test_a_raise_holds_every_bound_the_spec_names(fields: dict[str, object], reason: str) -> None:
    base = {"tier": "SCRATCH_WRITE", "rate": 0.27, "until": LATER}
    with pytest.raises(ValidationError, match=reason):
        RaisedAuditRate.model_validate({**base, **fields})


def test_a_tier_travels_by_name_so_an_unknown_one_still_validates() -> None:
    # A receiver ignores a tier it does not know; the frame itself must never be refused for it.
    assert _grant(audit_raises=(_raise("A_TIER_FROM_A_LATER_MINOR"),)).audit_raises
