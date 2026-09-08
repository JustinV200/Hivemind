"""Tests for waggle.outbox: append, pending, ack and reopen on a real log in a temporary directory.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Drives Outbox over a real file under pytest's
    tmp_path: the first-in-first-out round trip, what a reopen restores and compacts, the torn
    trailing line and corrupt middle line rules of spec section 10 seen through open, the
    duplicate-append and oversized-frame refusals, and a hypothesis property that checks the
    pending order against a plain Python list across generated appends, acks and reopens.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.outbox for the module under test.
    - test_outbox_log.py for the record layer these rules are built on.
    - test_outbox_replay.py for draining the queue through a transport.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from waggle.clock import FakeClock
from waggle.codec import SIGNATURE_OVERHEAD_BYTES, Codec
from waggle.envelope import Envelope, Hop, wrap
from waggle.errors import CodecError, FrameTooLargeError, OutboxCorruptError
from waggle.ids import MessageId
from waggle.messages.control import Ping
from waggle.minting import new_hive_id, new_node_id, new_warden_id
from waggle.outbox import Outbox
from waggle.outbox_log import TEMP_SUFFIX, append_record, encode_put
from waggle.signing import Ed25519Signer

MakeEnvelope = Callable[..., Envelope]
Operation = tuple[str, int]

CORRUPT_CODE = "waggle.outbox.corrupt"
TOO_LARGE_CODE = "waggle.codec.too_large"
TORN_CUT = 40  # Bytes cut from the end of a put line: mid-frame, and no newline.

# Built once at import: hypothesis forbids function-scoped fixtures inside @given, and the
# property is about ids and order, so one small pool of envelopes serves every example.
CLOCK = FakeClock()
NODE_ID = new_node_id(CLOCK)
HOP = Hop(sender=new_hive_id(CLOCK), recipient=new_warden_id(CLOCK), node_id=NODE_ID)
POOL_SIZE = 5  # Few enough ids that generated sequences re-append and re-ack the same ones.
POOL = tuple(wrap(Ping(), HOP, clock=CLOCK) for _ in range(POOL_SIZE))
MAX_OPERATIONS = 24  # Long enough to interleave acks and reopens; each costs one fsync.

indexes = st.integers(min_value=0, max_value=POOL_SIZE - 1)
operations = st.lists(
    st.one_of(
        st.tuples(st.just("append"), indexes),
        st.tuples(st.just("ack"), indexes),
        st.tuples(st.just("reopen"), st.just(0)),
    ),
    max_size=MAX_OPERATIONS,
)


@pytest.fixture
def log_path(tmp_path: Path) -> Path:
    """Where the outbox under test keeps its log; nothing exists there until it is opened."""
    return tmp_path / "outbox.jsonl"


def _lines(path: Path) -> list[dict[str, object]]:
    """Every record in the log as parsed JSON, in file order."""
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def _put_line(codec: Codec, envelope: Envelope) -> bytes:
    """The put record the outbox writes for ``envelope``."""
    return encode_put(envelope.id, codec.encode(envelope).decode("utf-8"))


def _unknown_kind_put_line(codec: Codec, envelope: Envelope) -> bytes:
    """A put record whose frame names a kind this node has no model for."""
    wire: dict[str, object] = json.loads(codec.encode(envelope))
    wire["kind"] = "control.unregistered"
    return encode_put(envelope.id, json.dumps(wire))


# ──────────────────────────────────────────────────────────────────────────────
# Round trips
# ──────────────────────────────────────────────────────────────────────────────


def test_open_creates_an_empty_log_with_nothing_pending(log_path: Path) -> None:
    outbox = Outbox(log_path)

    assert outbox.path == log_path
    assert log_path.read_bytes() == b""
    assert len(outbox) == 0
    assert outbox.pending() == ()
    assert outbox.pending_ids() == ()


def test_append_pending_and_ack_keep_first_in_first_out_order(
    log_path: Path, make_envelope: MakeEnvelope
) -> None:
    outbox = Outbox(log_path)
    first, second, third = make_envelope(), make_envelope(), make_envelope()

    for envelope in (first, second, third):
        assert outbox.append(envelope) is True
    outbox.ack(second.id)

    assert len(outbox) == 2
    assert outbox.pending_ids() == (first.id, third.id)
    assert outbox.pending() == (first, third)
    assert outbox.load(third.id) == third
    with pytest.raises(KeyError):
        outbox.load(second.id)


def test_append_stores_the_frame_unsigned_whatever_the_envelope_carried(
    log_path: Path, signed_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    # A decoded frame carries its signature; the outbox drops it, since replay signs afresh.
    signed = signed_codec.decode(signed_codec.encode(make_envelope()))
    assert signed.signature is not None
    outbox = Outbox(log_path, codec=signed_codec)

    outbox.append(signed)

    (record,) = _lines(log_path)
    frame = record["frame"]
    assert isinstance(frame, str)
    assert json.loads(frame)["signature"] is None
    assert outbox.pending()[0].signature is None
    assert outbox.pending()[0].id == signed.id


def test_ack_of_an_id_not_pending_writes_nothing(
    log_path: Path, make_envelope: MakeEnvelope
) -> None:
    outbox = Outbox(log_path)
    envelope = make_envelope()
    outbox.append(envelope)
    outbox.ack(envelope.id)
    after_one_ack = log_path.read_bytes()

    outbox.ack(envelope.id)
    outbox.ack(make_envelope().id)

    assert log_path.read_bytes() == after_one_ack
    assert len(outbox) == 0


# ──────────────────────────────────────────────────────────────────────────────
# Reopen
# ──────────────────────────────────────────────────────────────────────────────


def test_reopen_restores_pending_in_fifo_order_and_compacts_the_log(
    log_path: Path, make_envelope: MakeEnvelope
) -> None:
    first, second, third = make_envelope(), make_envelope(), make_envelope()
    outbox = Outbox(log_path)
    for envelope in (first, second, third):
        outbox.append(envelope)
    outbox.ack(first.id)
    assert [record["op"] for record in _lines(log_path)] == ["put", "put", "put", "ack"]

    reopened = Outbox(log_path)

    assert reopened.pending_ids() == (second.id, third.id)
    assert reopened.pending() == (second, third)
    assert len(reopened) == 2
    # The compacted file holds exactly the pending puts, in order, and no temporary file.
    assert [(r["op"], r["id"]) for r in _lines(log_path)] == [
        ("put", second.id),
        ("put", third.id),
    ]
    assert not log_path.with_name(log_path.name + TEMP_SUFFIX).exists()


def test_a_torn_trailing_line_is_ignored_on_open_and_compacted_away(
    log_path: Path, plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    kept, torn = make_envelope(), make_envelope()
    outbox = Outbox(log_path)
    outbox.append(kept)
    # The tail of a put whose write was cut short: its append never returned, so nothing was
    # promised for it.
    with log_path.open("ab") as handle:
        handle.write(_put_line(plain_codec, torn)[:-TORN_CUT])

    reopened = Outbox(log_path)

    assert reopened.pending_ids() == (kept.id,)
    assert log_path.read_bytes().endswith(b"\n")
    assert [record["id"] for record in _lines(log_path)] == [kept.id]
    # The torn id was never promised, so appending it now is a fresh, accepted put.
    assert reopened.append(torn) is True
    assert Outbox(log_path).pending_ids() == (kept.id, torn.id)


def test_a_corrupt_line_before_the_last_fails_open_with_outbox_corrupt(
    log_path: Path, plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    first, third = make_envelope(), make_envelope()
    append_record(log_path, _put_line(plain_codec, first))
    append_record(log_path, b"not a record\n")
    append_record(log_path, _put_line(plain_codec, third))
    before = log_path.read_bytes()

    with pytest.raises(OutboxCorruptError, match="line 2") as caught:
        Outbox(log_path)

    assert caught.value.code == CORRUPT_CODE
    # Refused before compaction: the damaged file is left for a human, never rewritten.
    assert log_path.read_bytes() == before


def test_open_fails_when_the_logs_directory_is_missing(tmp_path: Path) -> None:
    with pytest.raises(OSError, match=r"No such file|cannot find"):
        Outbox(tmp_path / "missing" / "outbox.jsonl")


# ──────────────────────────────────────────────────────────────────────────────
# Refusals
# ──────────────────────────────────────────────────────────────────────────────


def test_duplicate_append_returns_false_and_stores_once(
    log_path: Path, make_envelope: MakeEnvelope
) -> None:
    outbox = Outbox(log_path)
    envelope = make_envelope()

    assert outbox.append(envelope) is True
    assert outbox.append(envelope) is False

    assert len(outbox) == 1
    assert len(_lines(log_path)) == 1
    # Once acked the id is no longer pending, so the same envelope may be queued again.
    outbox.ack(envelope.id)
    assert outbox.append(envelope) is True
    assert Outbox(log_path).pending_ids() == (envelope.id,)


def test_an_envelope_that_would_not_fit_once_signed_is_refused_and_nothing_is_written(
    log_path: Path, plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    envelope = make_envelope()
    # One byte short of what a signature needs: the codec's own limit still admits the frame,
    # so the refusal is the outbox's signature-aware one.
    limit = len(plain_codec.encode(envelope)) + SIGNATURE_OVERHEAD_BYTES - 1
    outbox = Outbox(log_path, codec=Codec(max_frame_bytes=limit))

    with pytest.raises(FrameTooLargeError, match="outbox stores at most") as caught:
        outbox.append(envelope)

    assert caught.value.code == TOO_LARGE_CODE
    assert len(outbox) == 0
    assert log_path.read_bytes() == b""


def test_an_accepted_frame_still_fits_the_wire_limit_once_signed(
    log_path: Path, plain_codec: Codec, signer: Ed25519Signer, make_envelope: MakeEnvelope
) -> None:
    envelope = make_envelope()
    limit = len(plain_codec.encode(envelope)) + SIGNATURE_OVERHEAD_BYTES
    sending = Codec(signer=signer, max_frame_bytes=limit)
    outbox = Outbox(log_path, codec=sending)

    assert outbox.append(envelope) is True

    # The invariant the refusal exists for: the sending codec can sign what the outbox kept.
    assert len(sending.encode(outbox.pending()[0])) <= limit


def test_load_raises_codec_error_for_a_frame_this_node_no_longer_decodes(
    log_path: Path, plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    good, stale = make_envelope(), make_envelope()
    # As after an upgrade that unregistered a kind: the log is intact, one frame is not readable.
    append_record(log_path, _put_line(plain_codec, good))
    append_record(log_path, _unknown_kind_put_line(plain_codec, stale))

    outbox = Outbox(log_path)

    assert outbox.pending_ids() == (good.id, stale.id)
    assert outbox.load(good.id) == good
    with pytest.raises(CodecError):
        outbox.load(stale.id)
    with pytest.raises(CodecError):
        outbox.pending()


# ──────────────────────────────────────────────────────────────────────────────
# Property: the queue is a first-in-first-out list, on disk and in memory alike
# ──────────────────────────────────────────────────────────────────────────────


@settings(max_examples=50, deadline=None)
@given(operations=operations)
def test_pending_order_matches_a_fifo_list_model_across_acks_and_reopens(
    operations: list[Operation],
) -> None:
    # Its own directory per example: hypothesis reuses one tmp_path, and every example must
    # start from an empty log.
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "outbox.jsonl"
        outbox = Outbox(path)
        model: list[MessageId] = []
        for operation, index in operations:
            envelope = POOL[index]
            if operation == "append":
                # Idempotent while pending: the model gains only what the outbox accepts.
                is_new = envelope.id not in model
                assert outbox.append(envelope) is is_new
                if is_new:
                    model.append(envelope.id)
            elif operation == "ack":
                outbox.ack(envelope.id)
                if envelope.id in model:
                    model.remove(envelope.id)
            else:
                outbox = Outbox(path)
            assert outbox.pending_ids() == tuple(model)
            assert len(outbox) == len(model)
        assert [envelope.id for envelope in Outbox(path).pending()] == model
