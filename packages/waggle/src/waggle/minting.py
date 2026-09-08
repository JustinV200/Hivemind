"""Mint typed ids: one new_<kind>_id wrapper per IdKind.

Every Waggle message (the Hive's shared bee-to-bee wire protocol) and nearly every record the
Hive keeps is identified by a prefixed ULID from ``waggle.ids``; the wrappers below are how the
rest of the workspace mints one, because each returns the ``NewType`` for its kind (a ``TaskId``,
never a bare ``str``) so mypy catches an id handed to the wrong parameter. They live in their own
module rather than beside ``new_id`` because ``waggle/ids.py`` sits at the codingrules 5.1 file
limit, and the split follows responsibility, not line count: ``ids.py`` defines the kinds, the
types and the ULID generator, this module is the set of typed constructors every caller actually
uses. It is the same precedent as ``waggle.ulid`` (the raw encoding) being split out of ``ids.py``.
Each wrapper takes the injected Clock (the source of time every component reads instead of the
system clock, so tests get deterministic timestamps) and delegates to ``waggle.ids.new_id``.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Called by every subsystem that creates a Hive record
    or a Waggle message; calls into waggle.ids.new_id only.

Key invariants:
    - Exactly one wrapper per IdKind member, named new_<member>_id and returning that member's
      NewType, so this module and waggle.ids.IdKind never drift apart (tests/test_minting.py
      checks it).
    - Every id a wrapper returns satisfies waggle.ids.parse_id for its kind, and
      waggle.ids.timestamp_of recovers the clock's time at millisecond resolution.

See Also:
    - waggle.ids for IdKind, the NewTypes and the generic new_id these wrappers delegate to.
    - waggle.ulid for the encoding underneath, split out of ids.py for the same size reason.
    - docs/adr/0003-ids-clock-and-loop-live-in-waggle.md for why ids live in waggle.
"""

from __future__ import annotations

from waggle.clock import Clock
from waggle.ids import (
    AlarmId,
    CellId,
    DeviceId,
    EventId,
    GrantId,
    HiveId,
    IdKind,
    LeaseId,
    MessageId,
    NodeId,
    TaskId,
    ToolId,
    WardenId,
    WorkerId,
    new_id,
)

__all__ = [
    "new_alarm_id",
    "new_cell_id",
    "new_device_id",
    "new_event_id",
    "new_grant_id",
    "new_hive_id",
    "new_lease_id",
    "new_message_id",
    "new_node_id",
    "new_task_id",
    "new_tool_id",
    "new_warden_id",
    "new_worker_id",
]


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
