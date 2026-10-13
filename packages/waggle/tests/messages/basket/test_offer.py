"""Tests for the basket family's negotiation half (basket.offer): BasketOffer, BasketRefuse.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Holds EXAMPLES, one valid instance of every
    basket class (the transfer half included, so the registry step can load the whole family by
    this one file path); and for BasketOffer and BasketRefuse pins construction, the JSON round
    trip, the rejection of an extra field, every bound, and every validator spec section 8.12
    names: unique platform tags, the JSON-object check on manifest_json, and the stable refusal
    codes.

Key invariants:
    - EXAMPLES holds exactly one instance of each of the four basket classes.

See Also:
    - waggle.messages.basket.offer for the module under test.
    - test_chunk.py for BasketChunk and BasketAck, the family's transfer half.
    - docs/waggle/spec.md section 8.12 for the fields, bounds and validators pinned here.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id
from waggle.messages.base import MAX_REASON_CHARS, WaggleMessage
from waggle.messages.basket import BasketAck, BasketChunk, BasketOffer, BasketRefusal, BasketRefuse
from waggle.messages.basket.offer import (
    MAX_BLOB_BYTES,
    MAX_MANIFEST_CHARS,
    MAX_MEDIA_TYPE_CHARS,
)

CLOCK = FakeClock()
SHA256 = "ef" * 32  # 32 digest bytes as 64 lowercase hex characters.
DEEP_NESTING = "[" * 5_000  # Deeper than the JSON decoder's recursion limit, well under every cap.
BASKET_CLASSES: tuple[type[WaggleMessage], ...] = (
    BasketOffer,
    BasketChunk,
    BasketAck,
    BasketRefuse,
)

OFFER = BasketOffer(
    sha256=SHA256,
    size_bytes=1_048_576,
    media_kind="tool_package",
    media_type="application/x-tar",
    platform_tags=("LINUX", "MACOS"),
    clearance="C1",
    origin_cell_id=new_id(IdKind.CELL, CLOCK),
    origin_task_id=new_id(IdKind.TASK, CLOCK),
    origin_bee=new_id(IdKind.WARDEN, CLOCK),
    tainted=False,
    manifest_json='{"sha256": "' + SHA256 + '", "signer": "node", "signature": "c2ln"}',
    destination_cell_id=new_id(IdKind.CELL, CLOCK),
    reason="Ship the promoted tool to a second matching Cell.",
)
REFUSE = BasketRefuse(
    sha256=SHA256,
    code=BasketRefusal.CLEARANCE,
    reason="The blob is labelled C2 and this Night Veil Cell holds nothing above C1.",
)

# One valid instance of every basket class, in catalogue order; the registry step loads this
# tuple by file path, so its name and shape are part of the contract.
EXAMPLES: tuple[WaggleMessage, ...] = (
    OFFER,
    BasketChunk(sha256=SHA256, offset=0, data="aGVsbG8=", is_last=True),
    BasketAck(sha256=SHA256, received_bytes=5, complete=True, reason=""),
    REFUSE,
)


def _rebuild(example: WaggleMessage, **changes: object) -> WaggleMessage:
    """Re-validate ``example`` with some fields replaced."""
    return type(example).model_validate({**example.model_dump(), **changes})


# ──────────────────────────────────────────────────────────────────────────────
# Every class
# ──────────────────────────────────────────────────────────────────────────────


def test_examples_hold_exactly_one_instance_of_every_basket_class() -> None:
    assert tuple(type(example) for example in EXAMPLES) == BASKET_CLASSES


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: type(example).__name__)
def test_basket_message_round_trips_and_is_frozen(example: WaggleMessage) -> None:
    assert type(example).model_validate(example.model_dump(mode="json")) == example
    with pytest.raises(ValidationError, match="frozen"):
        example.sha256 = "0" * 64  # type: ignore[attr-defined]  # The assignment is the test.


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: type(example).__name__)
def test_basket_message_rejects_an_extra_field(example: WaggleMessage) -> None:
    with pytest.raises(ValidationError, match="extra"):
        _rebuild(example, hop_count=1)


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: type(example).__name__)
@pytest.mark.parametrize("digest", [SHA256.upper(), "ab", SHA256 + "0", "z" * 64])
def test_basket_message_digest_must_be_lowercase_hex_sha256(
    example: WaggleMessage, digest: str
) -> None:
    with pytest.raises(ValidationError, match="pattern"):
        _rebuild(example, sha256=digest)


@pytest.mark.parametrize("message_type", [BasketOffer, BasketAck, BasketRefuse])
def test_reason_is_bounded_by_the_shared_limit(message_type: type[WaggleMessage]) -> None:
    example = next(item for item in EXAMPLES if type(item) is message_type)

    assert _rebuild(example, reason="x" * MAX_REASON_CHARS)
    with pytest.raises(ValidationError, match=f"at most {MAX_REASON_CHARS}"):
        _rebuild(example, reason="x" * (MAX_REASON_CHARS + 1))


def test_basket_refusal_members_and_values_match_the_spec() -> None:
    assert [member.name for member in BasketRefusal] == [
        "CLEARANCE",
        "TAINTED",
        "NO_ROOM",
        "UNKNOWN_SIGNER",
        "INTEGRITY",
        "PLATFORM",
        "UNKNOWN_BLOB",
    ]
    assert all(member.value == member.name for member in BasketRefusal)


# ──────────────────────────────────────────────────────────────────────────────
# BasketOffer
# ──────────────────────────────────────────────────────────────────────────────


def test_basket_offer_accepts_a_platform_independent_deposit_from_no_task() -> None:
    offer = _rebuild(OFFER, platform_tags=(), origin_task_id=None, origin_bee=None, tainted=True)

    assert isinstance(offer, BasketOffer)
    assert offer.platform_tags == ()
    assert offer.origin_bee is None
    assert offer.tainted is True


def test_basket_offer_accepts_a_worker_as_origin_bee() -> None:
    offer = _rebuild(OFFER, origin_bee=new_id(IdKind.WORKER, CLOCK))

    assert isinstance(offer, BasketOffer)


@pytest.mark.parametrize(
    "tags", [("LINUX", "LINUX"), ("WINDOWS", "MACOS", "WINDOWS")], ids=["twice", "among three"]
)
def test_basket_offer_platform_tags_must_be_unique(tags: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError, match="unique"):
        _rebuild(OFFER, platform_tags=tags)


def test_basket_offer_platform_tags_must_be_known_platforms() -> None:
    with pytest.raises(ValidationError, match="platform_tags"):
        _rebuild(OFFER, platform_tags=("BEOS",))


@pytest.mark.parametrize("clearance", ["C0", "C1", "C2"])
def test_basket_offer_carries_every_clearance_label(clearance: str) -> None:
    assert _rebuild(OFFER, clearance=clearance)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"size_bytes": 0}, "greater than or equal to 1"),
        ({"size_bytes": MAX_BLOB_BYTES + 1}, "less than or equal to"),
        ({"media_kind": ""}, "pattern"),
        ({"media_kind": "Tool Package"}, "pattern"),
        ({"media_kind": "x" * 65}, "pattern"),
        ({"media_type": ""}, "at least 1"),
        ({"media_type": "x" * (MAX_MEDIA_TYPE_CHARS + 1)}, f"at most {MAX_MEDIA_TYPE_CHARS}"),
        ({"clearance": "public"}, "clearance"),
        ({"origin_cell_id": new_id(IdKind.TASK, CLOCK)}, "cell_"),
        ({"origin_task_id": new_id(IdKind.CELL, CLOCK)}, "task_"),
        ({"origin_bee": new_id(IdKind.CELL, CLOCK)}, "accepted prefixes"),
        ({"destination_cell_id": new_id(IdKind.WARDEN, CLOCK)}, "cell_"),
    ],
)
def test_basket_offer_bounds_and_ids(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(OFFER, **changes)


def test_basket_offer_accepts_the_largest_blob_it_can_claim() -> None:
    assert _rebuild(OFFER, size_bytes=MAX_BLOB_BYTES)


@pytest.mark.parametrize(
    ("manifest", "reason"),
    [
        ("[]", "JSON object"),
        ('"manifest"', "JSON object"),
        ("null", "JSON object"),
        ("not json", "not valid JSON"),
        ("", "not valid JSON"),
        (DEEP_NESTING, "not valid JSON"),
        ('{"a": "' + "x" * MAX_MANIFEST_CHARS + '"}', f"at most {MAX_MANIFEST_CHARS}"),
    ],
)
def test_basket_offer_manifest_must_be_a_bounded_json_object(manifest: str, reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(OFFER, manifest_json=manifest)


def test_basket_offer_keeps_its_manifest_as_text() -> None:
    assert isinstance(OFFER.manifest_json, str)
    assert OFFER.model_dump(mode="json")["manifest_json"] == OFFER.manifest_json


# ──────────────────────────────────────────────────────────────────────────────
# BasketRefuse
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("code", list(BasketRefusal))
def test_basket_refuse_carries_every_refusal_code(code: BasketRefusal) -> None:
    refused = _rebuild(REFUSE, code=code.value)

    assert isinstance(refused, BasketRefuse)
    assert refused.code is code


def test_basket_refuse_rejects_an_unknown_code() -> None:
    with pytest.raises(ValidationError, match="code"):
        _rebuild(REFUSE, code="TOO_BIG")
