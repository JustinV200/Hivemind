"""Tests for hivemind.basket.transfer.wire: every pair round-trips and a chunk frame fits.

Fits into the Hive:
    Mirrors src/hivemind/basket/transfer/wire.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.basket.transfer.wire for the module under test.
    - docs/waggle/spec.md section 8.12 for the wire messages mapped here.
"""

from __future__ import annotations

import pytest
from builders.basket import make_blob, make_manifest
from hypothesis import given
from hypothesis import strategies as st

from hivemind.basket import BlobManifest, TransferError
from hivemind.basket.transfer import (
    CHUNK_BYTES,
    Ack,
    Chunk,
    Offer,
    RefusalCode,
    Refuse,
    ack_from_wire,
    ack_to_wire,
    chunk_from_wire,
    chunk_to_wire,
    offer_from_wire,
    offer_to_wire,
    refuse_from_wire,
    refuse_to_wire,
)
from hivemind.cell import HoneyClearance, OsFamily
from waggle.clock import FakeClock
from waggle.codec import MAX_FRAME_BYTES, Codec
from waggle.envelope import Hop, wrap
from waggle.ids import new_cell_id, new_hive_id, new_node_id, new_warden_id
from waggle.messages import labels as wire_labels
from waggle.messages.basket import BasketAck, BasketOffer, BasketRefusal

_SHA = "b" * 64


def _offer() -> Offer:
    blob = make_blob(
        b"wire me", platform_tags=(OsFamily.LINUX, OsFamily.MACOS), clearance=HoneyClearance.C2
    )
    return Offer(
        manifest=make_manifest(blob), destination_cell_id=new_cell_id(FakeClock()), reason="ship"
    )


def test_an_offer_round_trips_through_the_wire() -> None:
    offer = _offer()

    wire = offer_to_wire(offer)

    assert offer_from_wire(wire) == offer
    assert wire.sha256 == offer.sha256 and wire.media_kind == "archive"
    assert BlobManifest.from_json(wire.manifest_json) == offer.manifest


def test_an_offer_whose_claim_contradicts_its_manifest_is_refused() -> None:
    wire = offer_to_wire(_offer())
    lying = wire.model_copy(update={"clearance": wire_labels.HoneyClearance.C0})

    with pytest.raises(TransferError, match="clearance"):
        offer_from_wire(lying)


def test_an_offer_with_a_manifest_that_is_not_a_manifest_is_refused() -> None:
    wire = offer_to_wire(_offer())
    broken = BasketOffer.model_validate({**wire.model_dump(), "manifest_json": '{"blob": 1}'})

    with pytest.raises(TransferError, match="bad manifest"):
        offer_from_wire(broken)


@given(
    data=st.binary(min_size=1, max_size=2_048),
    offset=st.integers(min_value=0, max_value=1 << 40),
    is_last=st.booleans(),
)
def test_a_chunk_round_trips_for_any_small_byte_string(
    data: bytes, offset: int, is_last: bool
) -> None:
    chunk = Chunk(sha256=_SHA, offset=offset, data=data, is_last=is_last)

    wire = chunk_to_wire(chunk)

    assert chunk_from_wire(wire) == chunk
    assert wire.data.isascii()


def test_a_full_size_chunk_wrapped_in_an_envelope_fits_one_frame() -> None:
    clock = FakeClock()
    chunk = Chunk(sha256=_SHA, offset=0, data=b"\xff" * CHUNK_BYTES, is_last=False)
    hop = Hop(sender=new_warden_id(clock), recipient=new_hive_id(clock), node_id=new_node_id(clock))

    frame = Codec().encode(wrap(chunk_to_wire(chunk), hop, clock=clock))

    # Base64 inflates by a third; the envelope adds a few hundred bytes. Both fit the 1 MiB cap.
    assert len(frame) < MAX_FRAME_BYTES
    assert len(frame) > CHUNK_BYTES * 4 // 3


@pytest.mark.parametrize("complete", [False, True])
@pytest.mark.parametrize("received", [0, 1, CHUNK_BYTES * 3])
def test_an_ack_round_trips(complete: bool, received: int) -> None:
    ack = Ack(sha256=_SHA, received_bytes=received, complete=complete, reason="ok")

    assert ack_from_wire(ack_to_wire(ack)) == ack


@pytest.mark.parametrize("code", list(RefusalCode))
def test_a_refusal_round_trips_for_every_code(code: RefusalCode) -> None:
    refuse = Refuse(sha256=_SHA, code=code, reason="because")

    wire = refuse_to_wire(refuse)

    assert refuse_from_wire(wire) == refuse
    assert wire.code is BasketRefusal[code.name]


def test_the_two_refusal_enums_name_the_same_seven_codes() -> None:
    assert {c.name for c in RefusalCode} == {c.name for c in BasketRefusal}


def test_a_long_peer_reason_is_clipped_not_rejected() -> None:
    wire = BasketAck(sha256=_SHA, received_bytes=0, complete=False, reason="x" * 900)

    assert len(ack_from_wire(wire).reason) == 200
