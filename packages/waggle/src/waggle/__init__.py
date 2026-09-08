"""Provide the Hive's shared wire protocol and shared primitives: the waggle package.

Message envelopes, ids, the clock and the standard long-running loop shape live here, so a
process has what it needs before it can be trusted to speak to another. It is deliberately
dependency-light (pydantic, websockets, cryptography only) and never imports anything from
hivemind, because the Pollen Packet (the lightweight agent that runs on a borrowed device, in
packages/pollen) links against it directly and must install on hardware with no room for the
full Queen stack.

Fits into the Hive:
    Its own layer, used by every layer in hivemind and by pollen alike; called by all of them
    for envelopes, ids, the clock and the loop shape. It calls into nothing in the workspace,
    and nothing from hivemind, so pollen can depend on it alone.

Key invariants:
    - No submodule under this package imports anything from hivemind or pollen (see
      waggle.errors's docstring and the "waggle imports nothing from hivemind or pollen"
      import-linter contract in the root pyproject.toml).
    - Every error waggle raises on purpose is a WaggleError subclass exported here, each with a
      stable ``code`` that is the same string an ErrorMessage carries on the wire.

See Also:
    - .claude/codingrules.md section 4 for why ids, the clock and the loop shape live here and
      not in hivemind.common.
    - docs/adr/0003-ids-clock-and-loop-live-in-waggle.md for the decision behind this step.
    - pollen for the package that links against this one alone.

Public API:
    - IdKind, HiveId, CellId, LeaseId, TaskId, WorkerId, WardenId, AlarmId, GrantId, ToolId,
      NodeId, EventId, DeviceId, MessageId: the id types every Waggle envelope and Hive record
      carries.
    - new_id (waggle.ids) and the thirteen new_<kind>_id wrappers (waggle.minting): mint a
      fresh, timestamped id of one kind.
    - parse_id, timestamp_of: validate a candidate id string and read its creation time back out.
    - Clock, SystemClock, FakeClock: the injected time source every time-reading component uses.
    - TickLoop: the standard long-running loop shape every bee and Pollen subclass.
    - WaggleError and the protocol error tree (waggle.errors): InvalidIdError; CodecError with
      MalformedFrameError, FrameTooLargeError, UnsupportedVersionError, UnknownKindError;
      SignatureError with MissingSignatureError, UnknownSignerError, InvalidSignatureError;
      TransportError with TransportClosedError, ConnectionLostError, ConnectFailedError;
      OutboxError with OutboxCorruptError.
"""

from waggle.clock import Clock, FakeClock, SystemClock
from waggle.errors import (
    CodecError,
    ConnectFailedError,
    ConnectionLostError,
    FrameTooLargeError,
    InvalidIdError,
    InvalidSignatureError,
    MalformedFrameError,
    MissingSignatureError,
    OutboxCorruptError,
    OutboxError,
    SignatureError,
    TransportClosedError,
    TransportError,
    UnknownKindError,
    UnknownSignerError,
    UnsupportedVersionError,
    WaggleError,
)
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
    parse_id,
    timestamp_of,
)
from waggle.loop import TickLoop
from waggle.minting import (
    new_alarm_id,
    new_cell_id,
    new_device_id,
    new_event_id,
    new_grant_id,
    new_hive_id,
    new_lease_id,
    new_message_id,
    new_node_id,
    new_task_id,
    new_tool_id,
    new_warden_id,
    new_worker_id,
)

__all__ = [
    "AlarmId",
    "CellId",
    "Clock",
    "CodecError",
    "ConnectFailedError",
    "ConnectionLostError",
    "DeviceId",
    "EventId",
    "FakeClock",
    "FrameTooLargeError",
    "GrantId",
    "HiveId",
    "IdKind",
    "InvalidIdError",
    "InvalidSignatureError",
    "LeaseId",
    "MalformedFrameError",
    "MessageId",
    "MissingSignatureError",
    "NodeId",
    "OutboxCorruptError",
    "OutboxError",
    "SignatureError",
    "SystemClock",
    "TaskId",
    "TickLoop",
    "ToolId",
    "TransportClosedError",
    "TransportError",
    "UnknownKindError",
    "UnknownSignerError",
    "UnsupportedVersionError",
    "WaggleError",
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
