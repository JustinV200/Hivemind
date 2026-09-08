"""Tests for waggle.outbox_log: the record shapes, the torn-line rule, folding and compaction.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Drives the pure functions (encode, parse, fold)
    over hand-built byte logs, and the two file operations over a real temporary directory, so
    every reading rule of spec section 10 (one torn trailing line ignored, anything else
    corrupt) is pinned independently of the Outbox that composes them.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.outbox_log for the module under test.
    - test_outbox.py for the same rules exercised through Outbox open.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope
from waggle.errors import OutboxCorruptError
from waggle.ids import MessageId
from waggle.minting import new_message_id
from waggle.outbox_log import (
    TEMP_SUFFIX,
    AckRecord,
    PutRecord,
    append_record,
    encode_ack,
    encode_put,
    parse_log,
    pending_after,
    rewrite_log,
)

MakeEnvelope = Callable[..., Envelope]

CORRUPT_CODE = "waggle.outbox.corrupt"


def _put_line(codec: Codec, envelope: Envelope) -> bytes:
    """The put record the outbox would write for ``envelope``."""
    return encode_put(envelope.id, codec.encode(envelope).decode("utf-8"))


# ──────────────────────────────────────────────────────────────────────────────
# Records
# ──────────────────────────────────────────────────────────────────────────────


def test_put_and_ack_lines_are_one_json_object_each_and_read_back(
    plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    envelope = make_envelope()
    frame = plain_codec.encode(envelope).decode("utf-8")

    put = encode_put(envelope.id, frame)
    ack = encode_ack(envelope.id)

    assert put.endswith(b"\n") and put.count(b"\n") == 1
    assert json.loads(put) == {"op": "put", "id": envelope.id, "frame": frame}
    assert json.loads(ack) == {"op": "ack", "id": envelope.id}
    assert parse_log(put + ack) == (PutRecord(envelope.id, frame), AckRecord(envelope.id))


def test_an_empty_or_missing_log_parses_to_no_records() -> None:
    assert parse_log(b"") == ()
    assert pending_after(()) == {}


# ──────────────────────────────────────────────────────────────────────────────
# Torn and corrupt lines
# ──────────────────────────────────────────────────────────────────────────────


def test_a_torn_trailing_line_is_ignored(plain_codec: Codec, make_envelope: MakeEnvelope) -> None:
    kept = make_envelope()
    torn = _put_line(plain_codec, make_envelope())[:-40]  # cut mid-frame, no newline

    records = parse_log(_put_line(plain_codec, kept) + torn)

    assert [record.message_id for record in records] == [kept.id]


def test_a_torn_line_cut_inside_a_multibyte_character_is_still_only_torn(
    plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    kept = make_envelope()
    # A frame is UTF-8; a crash can cut it between the bytes of one character, which must read
    # as a torn line, not as a decoding crash of the whole log.
    torn = b'{"op":"put","id":"msg_x","frame":"\xc3'

    records = parse_log(_put_line(plain_codec, kept) + torn)

    assert [record.message_id for record in records] == [kept.id]


def test_a_complete_last_record_without_its_newline_is_kept(
    plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    envelope = make_envelope()

    records = parse_log(_put_line(plain_codec, envelope).rstrip(b"\n"))

    assert [record.message_id for record in records] == [envelope.id]


def test_an_unreadable_line_before_the_last_is_corrupt(
    plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    first, third = make_envelope(), make_envelope()
    log = _put_line(plain_codec, first) + b"not json\n" + _put_line(plain_codec, third)

    with pytest.raises(OutboxCorruptError, match="line 2") as caught:
        parse_log(log)

    assert caught.value.code == CORRUPT_CODE


@pytest.mark.parametrize(
    "line",
    [
        b"[1, 2]\n",
        b'{"op": "put", "frame": "x"}\n',
        b'{"op": "put", "id": 7, "frame": "x"}\n',
        b'{"op": "put", "id": "cell_01ARZ3NDEKTSV4RRFFQ69G5FAV", "frame": "x"}\n',
        b'{"op": "put", "id": "msg_not-a-ulid", "frame": "x"}\n',
        b'{"op": "put", "id": "msg_01ARZ3NDEKTSV4RRFFQ69G5FAV"}\n',
        b'{"op": "put", "id": "msg_01ARZ3NDEKTSV4RRFFQ69G5FAV", "frame": 5}\n',
        b'{"op": "drop", "id": "msg_01ARZ3NDEKTSV4RRFFQ69G5FAV"}\n',
    ],
)
def test_json_that_is_not_a_record_is_corrupt_even_on_the_last_line(line: bytes) -> None:
    # Valid JSON of the wrong shape cannot be a torn append (a cut record never parses), so it
    # is damage wherever it sits, the last line included.
    with pytest.raises(OutboxCorruptError, match="line 1") as caught:
        parse_log(line)

    assert caught.value.code == CORRUPT_CODE


# ──────────────────────────────────────────────────────────────────────────────
# Folding
# ──────────────────────────────────────────────────────────────────────────────


def test_pending_after_keeps_fifo_order_and_applies_acks(fake_clock: FakeClock) -> None:
    first, second, third = (MessageId(new_message_id(fake_clock)) for _ in range(3))
    records = (
        PutRecord(first, "f1"),
        PutRecord(second, "f2"),
        AckRecord(first),
        PutRecord(third, "f3"),
        AckRecord(MessageId(new_message_id(fake_clock))),  # never put: a no-op
        PutRecord(second, "f2-again"),  # already pending: keeps its place and its frame
    )

    assert pending_after(records) == {second: "f2", third: "f3"}


# ──────────────────────────────────────────────────────────────────────────────
# Files
# ──────────────────────────────────────────────────────────────────────────────


def test_append_record_creates_the_file_and_appends_in_order(tmp_path: Path) -> None:
    log = tmp_path / "outbox.jsonl"

    append_record(log, b"one\n")
    append_record(log, b"two\n")

    assert log.read_bytes() == b"one\ntwo\n"


def test_rewrite_log_writes_only_the_given_puts_and_leaves_no_temporary_file(
    tmp_path: Path, plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    log = tmp_path / "outbox.jsonl"
    stale = log.with_name(log.name + TEMP_SUFFIX)
    stale.write_bytes(b"left by a crash mid-compaction")
    first, second = make_envelope(), make_envelope()
    frames = {
        first.id: plain_codec.encode(first).decode("utf-8"),
        second.id: plain_codec.encode(second).decode("utf-8"),
    }

    rewrite_log(log, frames)

    assert not stale.exists()
    assert parse_log(log.read_bytes()) == (
        PutRecord(first.id, frames[first.id]),
        PutRecord(second.id, frames[second.id]),
    )


def test_rewrite_log_fails_when_the_directory_is_missing(tmp_path: Path) -> None:
    with pytest.raises(OSError, match=r"No such file|cannot find"):
        rewrite_log(tmp_path / "missing" / "outbox.jsonl", {})
