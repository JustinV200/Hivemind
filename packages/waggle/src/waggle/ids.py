"""Define every id type the Hive uses, generated as prefixed, sortable ULIDs, and mint them.

Every Waggle message (the Hive's shared bee-to-bee wire protocol) carries an id, and so does
nearly everything else the Hive tracks -- one IdKind member per family, listed below. Each id is a
lowercase prefix plus a 26-char ULID (``waggle.ulid``): self-describing in logs, sortable by
creation time, and its own ``NewType`` so mypy catches a mismatched id type. This module holds the
kinds, the types, the generic generator ``new_id``, the two parsers, and the thirteen typed
``new_<kind>_id`` constructors callers actually reach for: each returns the ``NewType`` for its
kind (a ``TaskId``, never a bare ``str``) so mypy catches an id handed to the wrong parameter,
takes the injected Clock (the source of time every component reads instead of the system clock,
so tests get deterministic timestamps) and delegates to ``new_id``. Only the raw Crockford
encoding lives apart, in ``waggle.ulid``, because it is a concept of its own.

Fits into the Hive:
    Its own layer, used by every layer in hivemind and by pollen. Called by every subsystem that
    creates a Hive record or a Waggle message, and by anything that validates an id read back
    from storage or an envelope; calls into waggle.ulid and the injected Clock only.

Key invariants:
    - Ids sort by creation time within their kind; parse_id/timestamp_of reject bad ones.
    - Every IdKind member has exactly one NewType and one typed wrapper here, named
      new_<member>_id and returning that NewType, so the three never drift apart
      (tests/test_ids_minting.py checks it).
    - Every id a wrapper returns satisfies parse_id for its kind, and timestamp_of recovers the
      clock's time at millisecond resolution.

See Also:
    - docs/adr/0003-ids-clock-and-loop-live-in-waggle.md for why ids live here, not in hivemind.
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
    "new_alarm_id",
    "new_cell_id",
    "new_device_id",
    "new_event_id",
    "new_grant_id",
    "new_hive_id",
    "new_id",
    "new_lease_id",
    "new_message_id",
    "new_node_id",
    "new_task_id",
    "new_tool_id",
    "new_warden_id",
    "new_worker_id",
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
    """Extract the creation timestamp encoded in any id from new_id or a waggle.ids wrapper.

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


def new_hive_id(clock: Clock) -> HiveId:
    """Generate a new HiveId.

    Args:
        clock: Injected clock for a deterministic timestamp.

    Returns: A hive_-prefixed id.
    """
    return HiveId(new_id(IdKind.HIVE, clock))


def new_cell_id(clock: Clock) -> CellId:
    """Generate a new CellId.

    Args:
        clock: Injected clock for a deterministic timestamp.

    Returns: A cell_-prefixed id.
    """
    return CellId(new_id(IdKind.CELL, clock))


def new_lease_id(clock: Clock) -> LeaseId:
    """Generate a new LeaseId.

    Args:
        clock: Injected clock for a deterministic timestamp.

    Returns: A lease_-prefixed id.
    """
    return LeaseId(new_id(IdKind.LEASE, clock))


def new_task_id(clock: Clock) -> TaskId:
    """Generate a new TaskId.

    Args:
        clock: Injected clock for a deterministic timestamp.

    Returns: A task_-prefixed id.
    """
    return TaskId(new_id(IdKind.TASK, clock))


def new_worker_id(clock: Clock) -> WorkerId:
    """Generate a new WorkerId.

    Args:
        clock: Injected clock for a deterministic timestamp.

    Returns: A worker_-prefixed id.
    """
    return WorkerId(new_id(IdKind.WORKER, clock))


def new_warden_id(clock: Clock) -> WardenId:
    """Generate a new WardenId.

    Args:
        clock: Injected clock for a deterministic timestamp.

    Returns: A warden_-prefixed id.
    """
    return WardenId(new_id(IdKind.WARDEN, clock))


def new_alarm_id(clock: Clock) -> AlarmId:
    """Generate a new AlarmId.

    Args:
        clock: Injected clock for a deterministic timestamp.

    Returns: An alarm_-prefixed id.
    """
    return AlarmId(new_id(IdKind.ALARM, clock))


def new_grant_id(clock: Clock) -> GrantId:
    """Generate a new GrantId.

    Args:
        clock: Injected clock for a deterministic timestamp.

    Returns: A grant_-prefixed id.
    """
    return GrantId(new_id(IdKind.GRANT, clock))


def new_tool_id(clock: Clock) -> ToolId:
    """Generate a new ToolId.

    Args:
        clock: Injected clock for a deterministic timestamp.

    Returns: A tool_-prefixed id.
    """
    return ToolId(new_id(IdKind.TOOL, clock))


def new_node_id(clock: Clock) -> NodeId:
    """Generate a new NodeId.

    Args:
        clock: Injected clock for a deterministic timestamp.

    Returns: A node_-prefixed id.
    """
    return NodeId(new_id(IdKind.NODE, clock))


def new_event_id(clock: Clock) -> EventId:
    """Generate a new EventId.

    Args:
        clock: Injected clock for a deterministic timestamp.

    Returns: An event_-prefixed id.
    """
    return EventId(new_id(IdKind.EVENT, clock))


def new_device_id(clock: Clock) -> DeviceId:
    """Generate a new DeviceId.

    Args:
        clock: Injected clock for a deterministic timestamp.

    Returns: A device_-prefixed id.
    """
    return DeviceId(new_id(IdKind.DEVICE, clock))


def new_message_id(clock: Clock) -> MessageId:
    """Generate a new MessageId.

    Args:
        clock: Injected clock for a deterministic timestamp.

    Returns: A msg_-prefixed id.
    """
    return MessageId(new_id(IdKind.MESSAGE, clock))
