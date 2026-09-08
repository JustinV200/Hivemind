"""Provide the Hive's shared wire protocol and shared primitives: the waggle package.

Message envelopes, the codec that puts them on the wire, Ed25519 signing, the transports, the
offline outbox, the message catalogue (waggle.messages), ids, the clock and the standard
long-running loop shape live here, so a process has what it needs before it can be trusted to
speak to another. It is deliberately dependency-light (pydantic, websockets, cryptography
only) and never imports anything from
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
    - docs/waggle/spec.md is the source of truth for the wire: the registry in waggle.messages
      is checked against its catalogue table by tests/test_spec_drift.py, and
      tests/test_dependencies.py proves no module imports beyond pydantic, websockets and
      cryptography, so pollen can depend on this package alone.

See Also:
    - .claude/codingrules.md section 4 for why ids, the clock and the loop shape live here and
      not in hivemind.common.
    - docs/adr/0003-ids-clock-and-loop-live-in-waggle.md for the decision behind this step.
    - docs/adr/0004-waggle-transport-websocket-json.md and
      docs/adr/0005-waggle-envelope-signing-and-offline-outbox.md for the phase 1 decisions.
    - docs/waggle/spec.md for the envelope, versioning, error and catalogue contract.
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
    - Envelope, Hop, wrap and PROTOCOL_VERSION / PROTOCOL_MAJOR / PROTOCOL_MINOR
      (waggle.envelope): the outer wrapper every message travels in and its factory.
    - Codec, canonical_bytes, MAX_FRAME_BYTES and the Signer / Verifier protocols
      (waggle.codec): frames in and out, with signature policy.
    - Ed25519Signer, Ed25519Verifier (waggle.signing), public_key_hex, public_key_from_hex
      (waggle.key_encoding): per-node signing keys and their manifest form.
    - check_waggle_uri, is_loopback_host (waggle.uris): the one rule for a dialable endpoint.
    - Outbox (waggle.outbox), replay_outbox, ReplayReport, expire_older_than
      (waggle.outbox_replay): the durable queue of unsent envelopes and its replay.
    - waggle.messages (the catalogue and registry) and waggle.transport (the transports) are
      sub-packages with their own public API.
    - WaggleError and the protocol error tree (waggle.errors): InvalidIdError; CodecError with
      MalformedFrameError, FrameTooLargeError, UnsupportedVersionError, UnknownKindError,
      InvalidPayloadError;
      SignatureError with MissingSignatureError, UnknownSignerError, InvalidSignatureError;
      TransportError with TransportClosedError, ConnectionLostError, ConnectFailedError;
      OutboxError with OutboxCorruptError.
"""

from waggle.clock import Clock, FakeClock, SystemClock
from waggle.codec import (
    MAX_FRAME_BYTES,
    Codec,
    Signer,
    Verifier,
    canonical_bytes,
)
from waggle.envelope import (
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    PROTOCOL_VERSION,
    Envelope,
    Hop,
    wrap,
)
from waggle.errors import (
    CodecError,
    ConnectFailedError,
    ConnectionLostError,
    FrameTooLargeError,
    InvalidIdError,
    InvalidPayloadError,
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
from waggle.key_encoding import public_key_from_hex, public_key_hex
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
from waggle.outbox import Outbox
from waggle.outbox_replay import ReplayReport, expire_older_than, replay_outbox
from waggle.signing import Ed25519Signer, Ed25519Verifier
from waggle.uris import check_waggle_uri, is_loopback_host

__all__ = [
    "MAX_FRAME_BYTES",
    "PROTOCOL_MAJOR",
    "PROTOCOL_MINOR",
    "PROTOCOL_VERSION",
    "AlarmId",
    "CellId",
    "Clock",
    "Codec",
    "CodecError",
    "ConnectFailedError",
    "ConnectionLostError",
    "DeviceId",
    "Ed25519Signer",
    "Ed25519Verifier",
    "Envelope",
    "EventId",
    "FakeClock",
    "FrameTooLargeError",
    "GrantId",
    "HiveId",
    "Hop",
    "IdKind",
    "InvalidIdError",
    "InvalidPayloadError",
    "InvalidSignatureError",
    "LeaseId",
    "MalformedFrameError",
    "MessageId",
    "MissingSignatureError",
    "NodeId",
    "Outbox",
    "OutboxCorruptError",
    "OutboxError",
    "ReplayReport",
    "SignatureError",
    "Signer",
    "SystemClock",
    "TaskId",
    "TickLoop",
    "ToolId",
    "TransportClosedError",
    "TransportError",
    "UnknownKindError",
    "UnknownSignerError",
    "UnsupportedVersionError",
    "Verifier",
    "WaggleError",
    "WardenId",
    "WorkerId",
    "canonical_bytes",
    "check_waggle_uri",
    "expire_older_than",
    "is_loopback_host",
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
    "public_key_from_hex",
    "public_key_hex",
    "replay_outbox",
    "timestamp_of",
    "wrap",
]
