"""Persist the outbox's append-only JSONL log: its two records, their reading and its compaction.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). The outbox
(``waggle.outbox``) is a node's durable, ordered queue of envelopes (the outer wrapper every
message travels in) it could not send, and this module is the file underneath it: one log of
JSON lines, each either a ``put`` that queues one UNSIGNED frame under its message id or an
``ack`` that removes one. The log is only ever appended to while the node runs, one record per
line, flushed and ``fsync``ed before ``append_record`` returns, so a crash loses at most the
record being written; on open ``parse_log`` reads it back, tolerating exactly one torn trailing
line (the tail of a record whose write was cut short: it never reached the caller as a promise),
and raising ``OutboxCorruptError`` for any other line it cannot read, because a broken record in
the middle of a log means the file was damaged, not torn. ``pending_after`` folds the records
into the pending frames in first-in-first-out order, and ``rewrite_log`` compacts the log to
those pending records atomically: written to a temporary file beside the log, ``fsync``ed, then
``os.replace``d over it, so every reader ever sees either the old log or the new one, never a
half-written file. The parsing and folding are pure functions over bytes and records; only
``append_record`` and ``rewrite_log`` touch the disk (codingrules 8.3).

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Called by waggle.outbox.Outbox only; calls into
    waggle.ids for the message-id check and waggle.errors for the corrupt-log failure. Latency
    class: one ``fsync`` per append, milliseconds on an SSD and tens of milliseconds on an SD
    card; blocking, so an async caller wraps the outbox in ``asyncio.to_thread``.

Key invariants:
    - Every line append_record writes is one complete JSON object followed by exactly one
      newline, so a log that was never torn always ends in a newline.
    - parse_log ignores at most one line, and only the last; a line it cannot read anywhere
      else is OutboxCorruptError, never silently skipped.
    - rewrite_log leaves either the previous log or the new one on disk, never a mix.

See Also:
    - waggle.outbox for the Outbox that owns this log and the only module that calls it.
    - docs/waggle/spec.md section 10 and docs/adr/0005-waggle-envelope-signing-and-offline-outbox.md
      for the record shapes and the torn-line rule.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from waggle.errors import InvalidIdError, OutboxCorruptError
from waggle.ids import IdKind, MessageId, parse_id

OP_PUT = "put"  # The record that queues one unsigned frame under its message id.
OP_ACK = "ack"  # The record that removes a queued frame: sent, expired or poisoned.
TEMP_SUFFIX = ".tmp"  # Appended to the log's name for the compaction file os.replace swaps in.

__all__ = [
    "OP_ACK",
    "OP_PUT",
    "TEMP_SUFFIX",
    "AckRecord",
    "LogRecord",
    "PutRecord",
    "append_record",
    "encode_ack",
    "encode_put",
    "parse_log",
    "pending_after",
    "rewrite_log",
]


@dataclass(frozen=True, slots=True)
class PutRecord:
    """One ``put`` line read back from the log: a frame queued under its message id."""

    message_id: MessageId  # The envelope's id; what an ack later names.
    frame: str  # The unsigned wire JSON text exactly as encode_put stored it.


@dataclass(frozen=True, slots=True)
class AckRecord:
    """One ``ack`` line read back from the log: the message id no longer pending."""

    message_id: MessageId  # The id of the put this removes.


LogRecord = PutRecord | AckRecord


def encode_put(message_id: MessageId, frame: str) -> bytes:
    """Render the line that queues ``frame`` under ``message_id``.

    Args:
        message_id: The envelope's id, stored beside the frame so open never decodes a frame
            to learn what is pending.
        frame: The unsigned wire JSON text the codec produced for the envelope.

    Returns:
        One JSON object and a trailing newline, UTF-8: ``{"op":"put","id":...,"frame":...}``.
    """
    return _encode_line({"op": OP_PUT, "id": message_id, "frame": frame})


def encode_ack(message_id: MessageId) -> bytes:
    """Render the line that removes ``message_id`` from the pending set.

    Args:
        message_id: The id of a put record written earlier.

    Returns:
        One JSON object and a trailing newline, UTF-8: ``{"op":"ack","id":...}``.
    """
    return _encode_line({"op": OP_ACK, "id": message_id})


def parse_log(data: bytes) -> tuple[LogRecord, ...]:
    """Read every record in ``data``, ignoring a torn last line and refusing any other bad one.

    Args:
        data: The whole log file as read from disk; empty for a log that does not exist yet.

    Returns:
        The records in file order, the torn trailing line (if any) left out.

    Raises:
        OutboxCorruptError: A line other than the last is not UTF-8 JSON, or any line is JSON
            but not a put or ack record with a well-formed message id.
    """
    lines = data.split(b"\n")
    # A log that was never torn ends in a newline, so the split's last element is empty and is
    # not a line; when it is not empty it is the tail of a record whose write was cut short.
    if lines[-1] == b"":
        lines.pop()
    records: list[LogRecord] = []
    # Every line but the last must read; the last may be torn (unparseable) and is then
    # dropped, because its append() never returned and so never promised durability.
    for number, line in enumerate(lines, start=1):
        record = _parse_line(line, number, is_last=number == len(lines))
        if record is not None:
            records.append(record)
    return tuple(records)


def pending_after(records: tuple[LogRecord, ...]) -> dict[MessageId, str]:
    """Fold the records into the frames still pending, keyed by id in first-in-first-out order.

    Args:
        records: What parse_log returned, in file order.

    Returns:
        Message id to unsigned frame text, insertion-ordered: the first put of an id fixes its
        place in the queue, as Outbox.append does, and an ack removes it wherever it was.
    """
    pending: dict[MessageId, str] = {}
    for record in records:
        match record:
            # setdefault keeps the first put's position: a second put of a pending id is what
            # Outbox.append refuses, so one in a log can only be a hand edit, and it is ignored.
            case PutRecord(message_id=message_id, frame=frame):
                pending.setdefault(message_id, frame)
            # An ack for an id not pending is a no-op, exactly as Outbox.ack treats it.
            case AckRecord(message_id=message_id):
                pending.pop(message_id, None)
    return pending


def append_record(path: Path, line: bytes) -> None:
    """Append one encoded line to the log and force it to disk before returning.

    Args:
        path: The log file; created when it does not exist.
        line: What encode_put or encode_ack returned.

    Raises:
        OSError: The file could not be opened, written or synced.
    """
    # Opened per record rather than held open: the outbox then needs no close() and no
    # finaliser, and the fsync, not the open, is what the cost is. Binary mode keeps the bytes
    # exactly as encoded on every OS (no newline translation).
    with path.open("ab") as handle:
        handle.write(line)
        handle.flush()
        # PERF: one fsync per record is the durability promise (a returned append survives a
        # crash); it is the whole cost of an append and why callers wrap it in a thread.
        os.fsync(handle.fileno())


def rewrite_log(path: Path, pending: Mapping[MessageId, str]) -> None:
    """Replace the log with put records for ``pending`` only, atomically.

    Args:
        path: The log file; its directory must exist.
        pending: What pending_after returned (or the outbox's live view of it), in order.

    Raises:
        OSError: The temporary file could not be written, synced or swapped into place.
    """
    temporary = path.with_name(path.name + TEMP_SUFFIX)
    with temporary.open("wb") as handle:
        for message_id, frame in pending.items():
            handle.write(encode_put(message_id, frame))
        handle.flush()
        os.fsync(handle.fileno())
    # os.replace is atomic on every supported OS, so a crash here leaves either the old log or
    # the new one in place, both of which read back consistently; a stale .tmp from an earlier
    # crash is simply overwritten by the next open.
    os.replace(temporary, path)


def _encode_line(record: Mapping[str, str]) -> bytes:
    """Serialise a record as compact UTF-8 JSON on one line."""
    # A JSON string escapes its own newlines, so one record is always exactly one line;
    # ensure_ascii=False keeps non-ASCII prose as UTF-8, a third the size of escapes.
    return (json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _parse_line(line: bytes, number: int, *, is_last: bool) -> LogRecord | None:
    """Read one line into a record; None for a torn last line, OutboxCorruptError otherwise."""
    # UnicodeDecodeError and JSONDecodeError are both ValueErrors: either means the bytes are
    # not a whole record, which is torn at the end of the log and damage anywhere else.
    try:
        parsed: object = json.loads(line.decode("utf-8"))
    except ValueError as exc:
        if is_last:
            return None
        raise OutboxCorruptError(
            f"Outbox log line {number} is not valid UTF-8 JSON and is not the last line, so it "
            "cannot be a torn append."
        ) from exc
    return _record_from(parsed, number)


def _record_from(parsed: object, number: int) -> LogRecord:
    """Check the parsed JSON has a record's shape and a well-formed id, and build the record."""
    if not isinstance(parsed, dict) or not isinstance(parsed.get("id"), str):
        raise OutboxCorruptError(
            f"Outbox log line {number} is not an object with a string 'id'; the log was damaged."
        )
    # A malformed id could never have been written by append(), which takes a validated
    # envelope; refusing it here keeps every id the outbox hands out well-formed.
    try:
        message_id = MessageId(parse_id(parsed["id"], IdKind.MESSAGE))
    except InvalidIdError as exc:
        raise OutboxCorruptError(
            f"Outbox log line {number} carries an id that is not a message id: {exc}."
        ) from exc
    operation = parsed.get("op")
    if operation == OP_ACK:
        return AckRecord(message_id)
    if operation == OP_PUT and isinstance(parsed.get("frame"), str):
        return PutRecord(message_id, parsed["frame"])
    raise OutboxCorruptError(
        f"Outbox log line {number} has op {operation!r}, which is not a put with a text frame or "
        "an ack; the log was damaged."
    )
