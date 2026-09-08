"""Define every id type the Hive uses, generated as prefixed, sortable ULIDs.

Every Waggle message (the Hive's shared bee-to-bee wire protocol) carries an id, and so does
nearly everything else the Hive tracks -- one IdKind member per family, listed below. Each id is a
lowercase prefix plus a 26-char ULID (``waggle.ulid``): self-describing in logs, sortable by
creation time, and its own ``NewType`` so mypy catches a mismatched id type. This module holds the
kinds, the types, the generic generator and the two parsers; the typed ``new_<kind>_id``
constructors callers actually reach for live in ``waggle.minting``, split out by responsibility so
each file stays under the codingrules 5.1 size limit (the same reason ``waggle.ulid`` is separate).

Fits into the Hive:
    Its own layer, used by every layer in hivemind and by pollen. Called by waggle.minting (to
    mint typed ids) and by anything that validates an id read back from storage or an envelope.

Key invariants:
    - Ids sort by creation time within their kind; parse_id/timestamp_of reject bad ones.
    - Every IdKind member has exactly one NewType here and one typed wrapper in waggle.minting.

See Also:
    - docs/adr/0003-ids-clock-and-loop-live-in-waggle.md for why ids live here, not in hivemind.
    - waggle.minting for the thirteen typed new_<kind>_id wrappers built on new_id.
    - waggle.ulid for the encoding underneath.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from enum import Enum
from typing import NewType

from waggle.clock import Clock
from waggle.errors import InvalidIdError
from waggle.ulid import RANDOMNESS_BYTES, ULID_LENGTH, decode_ulid, encode_ulid

__all__ = [
    "AlarmId",
    "CellId",
    "DeviceId",
    "EventId",
    "GrantId",
    "HiveId",
    "IdKind",
    "LeaseId",
    "MessageId",
    "NodeId",
    "TaskId",
    "ToolId",
    "WardenId",
    "WorkerId",
    "new_id",
    "parse_id",
    "timestamp_of",
]


class IdKind(Enum):
    """Every kind of id waggle mints; each member's value is its lowercase, log-visible prefix."""

    HIVE = "hive"
    CELL = "cell"
    LEASE = "lease"
    TASK = "task"
    WORKER = "worker"
    WARDEN = "warden"
    ALARM = "alarm"
    GRANT = "grant"
    TOOL = "tool"
    NODE = "node"
    EVENT = "event"
    DEVICE = "device"
    MESSAGE = "msg"  # The envelope's own id; "msg" is in codingrules 6.2's accepted abbreviations.


HiveId = NewType("HiveId", str)
CellId = NewType("CellId", str)
LeaseId = NewType("LeaseId", str)
TaskId = NewType("TaskId", str)
WorkerId = NewType("WorkerId", str)
WardenId = NewType("WardenId", str)
AlarmId = NewType("AlarmId", str)
GrantId = NewType("GrantId", str)
ToolId = NewType("ToolId", str)
NodeId = NewType("NodeId", str)
EventId = NewType("EventId", str)
DeviceId = NewType("DeviceId", str)
MessageId = NewType("MessageId", str)


def new_id(kind: IdKind, clock: Clock) -> str:
    """Generate a new prefixed ULID id of the given kind, timestamped by ``clock``.

    Args:
        kind: Which kind of id to mint; determines the prefix.
        clock: Injected clock so the id's timestamp is deterministic in tests.

    Returns:
        A "<prefix>_<26-char ULID>" string, sortable by creation time among same-kind ids.
    """
    # Milliseconds, not seconds: waggle.ulid's encoding assumes millisecond resolution.
    timestamp_ms = int(clock.now().timestamp() * 1000)
    randomness = secrets.token_bytes(RANDOMNESS_BYTES)
    return f"{kind.value}_{encode_ulid(timestamp_ms, randomness)}"


def parse_id(value: str, kind: IdKind) -> str:
    """Validate that ``value`` is a well-formed id of ``kind`` and return it unchanged.

    Args:
        value: Candidate id string, e.g. read back from storage or a Waggle envelope.
        kind: The IdKind ``value`` is expected to be.

    Returns:
        ``value``, unchanged, now known well-formed.

    Raises:
        InvalidIdError: Wrong prefix, wrong ULID length, or a non-alphabet character.
    """
    prefix = f"{kind.value}_"
    if not value.startswith(prefix):
        raise InvalidIdError(
            f"id {value!r} does not start with prefix {prefix!r} for kind {kind.name}"
        )

    ulid_part = value[len(prefix) :]
    if len(ulid_part) != ULID_LENGTH:
        raise InvalidIdError(
            f"id {value!r} has {len(ulid_part)}-char ulid part, expected {ULID_LENGTH}"
        )

    # Wrap decode_ulid's ValueError so every waggle id failure raises the same typed error.
    try:
        decode_ulid(ulid_part)
    except ValueError as exc:
        raise InvalidIdError(f"id {value!r} is not a valid ulid: {exc}") from exc
    return value


def timestamp_of(id_value: str) -> datetime:
    """Extract the creation timestamp encoded in any id from new_id or a waggle.minting wrapper.

    Args:
        id_value: Any id of the shape ``"<prefix>_<26-char ULID>"``.

    Returns:
        The UTC creation time, at millisecond resolution.

    Raises:
        InvalidIdError: No ULID_LENGTH-character ulid part, or that part is not a valid ULID.
    """
    # Any well-formed <prefix>_<ulid> id carries a decodable timestamp regardless of its kind.
    _, _, ulid_part = id_value.rpartition("_")
    if len(ulid_part) != ULID_LENGTH:
        raise InvalidIdError(f"id {id_value!r} has no {ULID_LENGTH}-character ulid part")

    try:
        timestamp_ms, _ = decode_ulid(ulid_part)
    except ValueError as exc:
        raise InvalidIdError(f"id {id_value!r} is not a valid ulid: {exc}") from exc
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
