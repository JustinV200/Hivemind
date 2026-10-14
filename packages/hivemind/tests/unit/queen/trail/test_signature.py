"""Tests for hivemind.queen.trail.signature: a segment's signature against its node's key.

A Swarm node's segment must carry an Ed25519 signature by its registered key (roadmap 11.9,
ADR-0053); a Virtual Cell's is verified when it is present. The cases walk the rule's whole
table with real chunks cut by a real sender and signed over `segment_signing_bytes`: a required key
refuses an unsigned segment, a segment signed by another key, one tampered with after signing and
one the Queen holds no key for; an optional key lets an unsigned one through but still refuses a bad
signature; no opinion at all checks nothing; and a good signature passes either way.

Fits into the Hive:
    Mirrors src/hivemind/queen/trail/signature.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests.unit.queen.trail.test_sync for the receiver that applies this check.
"""

from __future__ import annotations

import pytest
from builders.segments import SegmentRig, make_segment_rig, segment_chunks, signed

from hivemind.queen.trail import (
    BadSegmentSignatureError,
    SegmentKey,
    UnsignedSegmentError,
    check_segment_signature,
)
from waggle.messages.swarm import TrailSegmentSync
from waggle.signing import Ed25519Signer


async def _final(rig: SegmentRig, signer: Ed25519Signer | None = None) -> TrailSegmentSync:
    """The final chunk of a one-event export, signed by `signer` when given."""
    chunks = await segment_chunks(rig)
    return (signed(chunks, signer) if signer is not None else chunks)[-1]


def _key(rig: SegmentRig, *, required: bool) -> SegmentKey:
    """The key the Queen holds for the rig's node: its real public key."""
    return SegmentKey(public_key=rig.signer.public_key_bytes, is_required=required)


@pytest.mark.parametrize("required", [True, False])
async def test_a_segment_signed_by_the_registered_key_passes(required: bool) -> None:
    rig = make_segment_rig()
    final = await _final(rig, rig.signer)

    check_segment_signature(final, _key(rig, required=required))


async def test_an_unsigned_segment_is_refused_when_its_nodes_key_is_required() -> None:
    rig = make_segment_rig()
    final = await _final(rig)

    with pytest.raises(UnsignedSegmentError) as error:
        check_segment_signature(final, _key(rig, required=True))

    assert error.value.node_id == rig.node_id


async def test_an_unsigned_segment_passes_when_its_nodes_key_is_optional() -> None:
    rig = make_segment_rig()
    final = await _final(rig)

    check_segment_signature(final, _key(rig, required=False))


@pytest.mark.parametrize("required", [True, False])
async def test_a_segment_signed_by_another_key_is_refused(required: bool) -> None:
    rig = make_segment_rig()
    final = await _final(rig, Ed25519Signer.generate())  # Signed with a key that is not the node's.

    with pytest.raises(BadSegmentSignatureError):
        check_segment_signature(final, _key(rig, required=required))


@pytest.mark.parametrize("field", ["event_count", "last_event_id", "first_event_id", "sha256"])
async def test_a_segment_tampered_with_after_it_was_signed_is_refused(field: str) -> None:
    rig = make_segment_rig()
    chunks = await segment_chunks(rig, events=2)
    other = {
        "event_count": 3,
        "last_event_id": chunks[-1].first_event_id,
        "first_event_id": chunks[-1].last_event_id,
        "sha256": "0" * 64,
    }[field]
    tampered = signed(chunks, rig.signer, **{field: other})[-1]

    with pytest.raises(BadSegmentSignatureError):
        check_segment_signature(tampered, _key(rig, required=True))


async def test_a_segment_signed_for_another_node_is_refused() -> None:
    rig = make_segment_rig()
    final = await _final(rig, rig.signer)
    claims_another_node = final.model_copy(update={"node_id": make_segment_rig().node_id})

    with pytest.raises(BadSegmentSignatureError):
        check_segment_signature(claims_another_node, _key(rig, required=True))


async def test_a_signed_segment_is_refused_when_the_queen_holds_no_key_to_check_it_with() -> None:
    rig = make_segment_rig()
    final = await _final(rig, rig.signer)

    with pytest.raises(BadSegmentSignatureError):
        check_segment_signature(final, SegmentKey(public_key=None, is_required=True))


async def test_an_unsigned_segment_of_a_node_with_no_key_is_refused_as_unsigned_when_required() -> (
    None
):
    rig = make_segment_rig()
    final = await _final(rig)

    with pytest.raises(UnsignedSegmentError):
        check_segment_signature(final, SegmentKey(public_key=None, is_required=True))


async def test_no_opinion_about_the_sender_checks_nothing() -> None:
    rig = make_segment_rig()

    check_segment_signature(await _final(rig), None)
    check_segment_signature(await _final(rig, Ed25519Signer.generate()), None)


async def test_the_refusal_names_the_node_and_never_the_signature_or_the_key() -> None:
    rig = make_segment_rig()
    final = await _final(rig, Ed25519Signer.generate())

    with pytest.raises(BadSegmentSignatureError) as error:
        check_segment_signature(final, _key(rig, required=True))

    text = str(error.value)
    assert rig.node_id in text
    assert final.segment_signature is not None and final.segment_signature not in text
    assert rig.signer.public_key_bytes.hex() not in text
