# Waggle protocol specification

Protocol version `1.1`. This document is the source of truth for every message the Hive's bees
exchange; the pydantic models in `packages/waggle/src/waggle/` implement it and a drift test
(section 11) keeps the two in step. Every bee term is defined in plain English where it first
appears; the README's terminology table is the longer reference.

## 1. Purpose and scope

Waggle is the Hive's bee-to-bee wire protocol, named after the waggle dance honeybees use to pass
directions to each other. It is the single vocabulary spoken by the Queen (the central
orchestrator, the only component with a global view), every Warden (the always-on supervisor of
one Cell), every Worker (a subagent a Warden spawns to do work) and every Pollen Packet (the thin
gateway installed on an enrolled device, with no brain of its own). A Cell is a unit of compute a
Worker runs in or on: a Virtual Cell (a VM or container the Hive provisions and later destroys) or
a Real Cell (an existing device borrowed for a task and left exactly as found). The Hive Stand is
the machine the Queen runs on; the Swarm is the mesh of enrolled Real Cells.

This specification covers:

- the envelope every message travels in (section 2) and the three message shapes (section 3);
- how the protocol is versioned (section 4), framed and bounded (section 5) and signed (section 6);
- the error message and its stable code table (section 7);
- the complete message catalogue, ten families and sixty-six kinds (section 8);
- the transports (section 9), the offline outbox (section 10) and conformance (section 11);
- the design questions the catalogue settled and why (section 12).

Out of scope: model calls (they travel separately to whichever provider serves the model and never
ride on Waggle), the Landing Board's HTTP contract (`docs/entrance/openapi.json`), the Pheromone
Trail export format (the audit log's segments cross Waggle as opaque bytes, section 8.10), and the
enrolment ceremony around `swarm.enrol_request` (`docs/waggle/enrolment.md`, phase 11).

The `waggle` package is its own layer: `hivemind` and `pollen` both import it and it imports
neither. Its only third-party dependencies are `pydantic`, `websockets` and `cryptography`, so a
Pollen Packet can speak the protocol with nothing else installed.

## 2. Envelope

Every frame on the wire is one `Envelope`: a frozen pydantic model with `extra="forbid"`, whose
fields appear in this order.

| Field | Type | Rule |
|---|---|---|
| `id` | `MessageId` | `msg_<ULID>`, unique per message; a reply's `correlation_id` points here |
| `correlation_id` | `MessageId \| None` | Set by shape; see section 3 |
| `sender` | `str` | A bee address (below) |
| `recipient` | `str` | A bee address (below) |
| `kind` | `str` | `<family>.<snake_name>`, a registered kind matching the payload's class |
| `version` | `str` | `"<major>.<minor>"`, pattern `^\d+\.\d+$`, default `"1.1"` |
| `sent_at` | `datetime` | Timezone-aware UTC; a naive datetime is rejected |
| `node_id` | `NodeId` | `node_<ULID>` of the process that sent it; signing keys are per node |
| `payload` | `SerializeAsAny[WaggleMessage]` | The typed message; the subclass is serialised in full |
| `signature` | `str \| None` | Padded base64 Ed25519 signature, or null when unsigned |

Validation rules:

- **Ids.** Every id is a lowercase prefix, an underscore and a 26-character Crockford ULID
  (`waggle.ids.IdKind` lists the prefixes). `id` is minted by `wrap()` through
  `new_message_id(clock)`, so it sorts by creation time. `correlation_id`, when set, is a
  `MessageId` and refers to an envelope the receiver has seen or may legitimately have missed
  (section 3).
- **Bee addresses.** `sender` and `recipient` are each a well-formed id whose kind is `hive`
  (the Queen), `warden`, `worker` or `device` (a Pollen Packet gateway or another enrolled
  device). This check lives in `waggle/envelope.py` only, built from `IdKind` and
  `waggle.ulid.decode_ulid`; no other module defines an address pattern. Message payloads that
  name a bee use the matching id NewType(s) and validate them with `waggle.ids.parse_id` against
  the kinds they may hold.
- **Kind.** `kind` must be registered in `waggle.messages.registry` and must equal
  `registry.kind_for(type(payload))`; message classes never carry their own kind.
- **Correlation.** A model validator looks up `spec_for(kind).shape`: `REQUEST` requires
  `correlation_id` None, `REPLY` requires it set, `EVENT` accepts either. A decoded frame that
  breaks the rule is `waggle.codec.invalid_payload`.
- **Version.** `version` follows section 4; `PROTOCOL_VERSION = "1.1"`, `PROTOCOL_MAJOR = 1` and
  `PROTOCOL_MINOR = 1` are constants in `waggle/envelope.py`.
- **Time.** `sent_at` is stamped from the injected `Clock` by `wrap()`; it is transported as an
  ISO 8601 string with an explicit offset. An aware datetime with a non-zero offset is
  normalised to UTC on validation (canonical bytes are computed over the raw wire dict, so
  this never breaks a signature); a naive datetime is rejected. `messages/base.py` provides the
  `UtcDatetime` alias that `sent_at` and every `_at` field use.
- **Payload.** `payload` is an instance of a `WaggleMessage` subclass (frozen, `extra="forbid"`,
  every field described). It is declared `SerializeAsAny` so the subclass's fields, not the base
  class's, are written to the wire.
- **Signature.** `signature` is filled in by the codec at send time when a signer is configured
  (section 6); the envelope model itself never signs.
- **Factory.** `wrap(payload, hop, *, clock, correlation_id=None)` mints the id, stamps
  `sent_at`, looks the kind up in the registry and returns an unsigned envelope. `Hop` is a frozen
  dataclass of `sender`, `recipient` and `node_id` (the three values that change at every hop of
  a relayed message, section 3), grouped so `wrap` stays under the five-parameter limit.
  `envelope.py` imports the registry; the registry never imports the envelope.

## 3. Shapes

Every registered kind has exactly one shape, fixed in the registry as `MessageSpec.shape`, and a
`replies_to` kind (or none). The shape decides what `correlation_id` must hold.

- **request.** Asks the recipient for something and expects exactly one answer. `correlation_id`
  MUST be null. The answer is the registered reply kind where one exists; otherwise the
  correlated event its catalogue entry names (`task.result`, `cell.lease_opened`,
  `cell.lease_released`, `cell.wax_written`, `session.exit`, `tool.promoted`); or a
  `control.error`. Never both, never more than one. A request that draws no answer is the
  sender's problem to time out (every await on the wire has a timeout).
- **reply.** Answers one request. `correlation_id` MUST be set and equal the request's `id`.
  `replies_to` names the request kind, except `control.error`, which may answer any kind
  (`replies_to` none, `correlation_id` still required). A receiver that cannot match a reply to a
  request it made records the fact at debug level and drops the reply. Replies whose payload
  carries a durable id (`question_id`, `proposal_id`) are the exception: they are matched by
  that id against the Brood Chamber (the task store) or the Capping table, never dropped as
  unmatched, and delivered to whichever bee currently holds the task, which learns of the
  pending question or proposal from its Handoff (the document a bee writes before its context
  is reset).
- **event.** Tells the recipient something happened. `correlation_id` is optional: when set it
  names the message the event follows from, and the registry's `replies_to` records the usual
  antecedent so tooling can link them.

Registered correlations for events (the `replies_to` column of the catalogue):

| Event | Follows from | When set |
|---|---|---|
| `task.progress`, `task.result` | `task.assign` | Always |
| `session.output`, `session.exit` | `session.exec` | Always; or the request or transfer they end |
| `cell.lease_opened` | `cell.request` | Always when answering a request |
| `cell.lease_released` | `cell.teardown_request` | Always when answering a request |
| `tool.promoted` | `tool.request` | When a request exists |
| `capping.check_result` | `capping.proposal_submitted` | Always |
| `capping.postcondition_result` | `capping.proposal_submitted` | When it belongs to a proposal |
| `capping.rollback_done` | `capping.proposal_submitted` | Always |
| `cell.wax_written`, `cell.wax_cleared` | `cell.wax_proposed` | When a proposal exists |

Every other event has no registered antecedent and normally travels with `correlation_id` null;
the payload carries the identity that links it (`task_id`, `alarm_id`, `lease_id`, `grant_id`).

Session rule: every session request (`session.open`, `session.exec`, `session.get_file`, and a
`session.put_file` transfer, whose identity is its `transfer_id`, the first chunk's envelope id)
ends in exactly one `session.exit` correlated to it or one `control.error`; `session.output`
chunks may precede that terminal message and never follow it. A non-final `session.put_file`
chunk draws no answer unless it fails. `session.stdin` carries the exec's id in its payload and
sets `correlation_id` to it.

Relaying: when a message crosses more than one hop (`Queen -> Warden -> Worker`), each hop is a
fresh envelope with its own `id`, `sender`, `recipient` and `node_id`; the relaying bee re-wraps
the payload. Identity therefore lives in the payload: a task is its `task_id`, an Alarm (an issue
a bee cannot resolve, escalated up the chain) its `alarm_id`, a question its `question_id`, a
proposal its `proposal_id`, a session its `lease_id`. Correlation is always per hop.

First-hop identity (receiver rule): a payload field that names the sender (`proposer`,
`asked_by`, `device_id`, `warden_id`, `worker_id`, `requester`, `origin` and `holder` on the
hop that creates the message) must equal the envelope `sender`; a relaying bee preserves it.
Kinds only the Queen sends (`forage.grant_issued`, `forage.grant_revoked`,
`forage.hosting_decided`, `forage.ceilings_set`, `forage.plan_written`, `swarm.nuc_promote`,
`control.cluster`, `control.wake`, `control.shutdown`, `control.mask_override` and
`control.queen_moved`) are accepted only from a `hive` sender, and a receiver drops a frame
whose `recipient` is not its own address or one it relays for. The guard (the policy engine,
phase 10) and the Pollen Packet (phase 11) enforce these; they are listed here so the rules
have one home.

Duplicates and freshness (receiver rules): a `send` that raised `waggle.transport.connection_lost`
after the frame reached the peer stays in the outbox and is replayed, so the same envelope can
arrive twice. Every receiver keeps the `id` of each envelope it processed for
`MAX_MESSAGE_AGE_S = 604_800` (seven days) per sender and silently drops a duplicate,
re-sending the cached reply for a request whose reply it still holds. A receiver drops an
envelope whose `sent_at` is older than that window or more than `MAX_CLOCK_SKEW_S = 300`
seconds ahead of its own clock, and the outbox expires pending entries older than the same
window. An order (`forage.grant_revoked`, `forage.hosting_decided`, `control.cluster`,
`control.wake`, `control.mask_override`) whose `sent_at` precedes the last applied order of the
same kind and scope (`cell_id`, `provider`, `grant_id`) is ignored; grants and ceilings carry a
`revision` for the same purpose. These constants live in `messages/base.py`; the checks are
the inbox's, not the codec's, so the codec stays a pure function of the frame.

## 4. Versioning

The protocol version travels on every envelope as `version = "<major>.<minor>"`.

- An **additive** change (a new kind, a new optional field with a default, a new enum member, a
  loosened bound) bumps the **minor** version.
- A **breaking** change (a removed or renamed kind or field, a changed type, a tightened bound, a
  changed id prefix, a changed canonical form) bumps the **major** version.
- A receiver **rejects an unknown major** (`waggle.version.unsupported_major`, closing the
  connection, section 9) and **accepts any minor** of a major it knows.
- Adding an enum member is a minor change for the sender but an older receiver still rejects the
  unknown member at payload validation. A sender therefore MUST NOT use a member, kind or field
  introduced in minor `n` toward a peer that has not advertised minor `n` or later. Peers learn
  each other's minor from the `version` of the first envelope they receive on a connection (the
  `control.ping` / `control.pong` exchange at connect time is the conventional place); until a
  peer has advertised, a sender assumes minor 0 of the known major.
- Kind strings, field names, enum member values and error codes are never renamed once they have
  been sent on the wire; a rename is a removal plus an addition, which is a major bump.

## 5. Wire format and size limit

- One frame carries one envelope as one JSON object, UTF-8 encoded, whose keys are exactly the
  envelope fields of section 2. `payload` is the message's `model_dump(mode="json")`. The
  wire encoding is compact (`separators=(",", ":")`, sorted keys, `ensure_ascii=False`); the
  canonical bytes of section 6 are recomputed from the parsed object, so the wire and the
  canonical encodings are allowed to differ. Character bounds in this document are code
  points; a sender sizes by the encoded frame.
- Frames are WebSocket **binary** frames. A text frame is a malformed frame
  (`waggle.codec.malformed`) and closes the connection with code 1002.
- `MAX_FRAME_BYTES = 1_048_576` (1 MiB, the `websockets` default `max_size`). A sender whose
  encoded frame would exceed it raises `waggle.codec.too_large` locally and never sends; a
  receiver that gets a bigger frame rejects it and closes with code 1009. Nothing is ever chunked
  at the frame layer: a bigger payload is split by the sender into messages. Bounds are per
  field; a message whose encoded frame still exceeds `MAX_FRAME_BYTES` fails at encode with
  `waggle.codec.too_large`, is never sent, and is refused by the outbox (section 10).
- JSON conventions: `bytes` fields are base64 as pydantic emits them (URL-safe alphabet, no
  padding); a receiver accepts standard or URL-safe, padded or not. `datetime` fields are ISO
  8601 with an explicit offset; enums serialise as their member value (short upper-case strings,
  or `C0`/`C1`/`C2` for clearance); tuples are arrays; `None` is `null`; unknown keys anywhere are
  rejected (`extra="forbid"` on every model).
- **Chunking rule.** Every `bytes` field on a message is at most `MAX_CHUNK_BYTES = 262_144`
  (256 KiB; base64 inflates it by a third and the rest of the envelope fits comfortably under
  1 MiB). Content larger than that is sent as a sequence of messages, each carrying `chunk`,
  `offset` (the byte offset of this chunk within the whole) and `final` (true on the last chunk),
  plus a group key that ties the chunks together: the request's `MessageId` for session output
  streams, the `transfer_id` for put-file transfers, the envelope `sender` plus the content
  `sha256` for Nectar deposits, `node_id` plus `first_event_id` for trail segments. Receivers
  reassemble by group key in `offset` order and verify the whole against `sha256` where the
  family carries one. Text fields that could be large carry an explicit maximum length
  (section 8); nothing on the wire is unbounded.
- **Reassembly (receiver rules).** A receiver holds at most `MAX_OPEN_CHUNK_GROUPS = 8`
  incomplete groups per sender; it rejects a first chunk whose `offset` is not 0 or whose
  `total_bytes` exceeds the family's cap (`MAX_NECTAR_BYTES`, `MAX_TRAIL_SEGMENT_BYTES` and
  `MAX_PUT_FILE_BYTES`, manifest values whose defaults live in the family file), rejects a
  chunk whose `offset` is not the running length, and discards a group idle longer than
  `CHUNK_GROUP_TIMEOUT_S = 60`. Every rejection is a `control.error` with `is_retryable` false,
  and a partial file on a device is deleted.

## 6. Signing

- The signed bytes are `canonical_bytes(wire)`: the raw wire dict **minus** `signature`,
  serialised with `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=True)` and
  UTF-8 encoded. Signing and verification both operate on the raw wire dict exactly as sent,
  never on a re-validated model, so a default filled in by validation can never break a signature.
- The algorithm is Ed25519 (`cryptography`). The signature travels as standard padded base64 in
  `signature`.
- Keys are **per node**: the `node_id` on the envelope names the key that signed it. A verifier
  maps `node_id -> 32-byte raw public key`. Public keys travel as raw 32 bytes, hex encoded,
  wherever a human, a manifest or a payload field sees them.
- Policy: a codec with a verifier **requires** a valid signature on every frame it decodes
  (unsigned -> `waggle.signature.missing`, unknown `node_id` -> `waggle.signature.unknown_node`,
  bad -> `waggle.signature.invalid`, all closing the connection with code 1008). A codec with no
  verifier ignores signatures; that is the in-process case only. Anything that crosses a machine
  boundary, every message to a Pollen Packet included, is signed and verified.
- The **Hive key** is the key that signs `control.queen_moved`, the relocation notice a device
  trusts above everything else. In phase 1 the Hive key **is the Hive Stand's node key**; a
  separate Hive keypair may arrive with Supersedure (moving the Hive Stand to another machine,
  phase 13) and will be recorded in an ADR. The Hive keypair itself never crosses Waggle.
- **Enrolment bootstrap.** A Pollen Packet's key is unknown until its `swarm.enrol_request` is
  accepted. The Queen's enrolment listener therefore decodes that one frame with no verifier,
  validates the one-time token, then verifies the same raw frame with the `public_key_hex` and
  `node_id` inside the signed payload (which must equal the envelope's `node_id`) before pinning
  that key for the node; every later frame on the connection is verified normally. A key is
  trusted on first use only because the token bound it to an operator's `hive swarm invite`.
  Before a key is pinned, the only kind accepted on the connection is `swarm.enrol_request`;
  anything else closes with code 1008. The invite carries the Hive Stand's node public key
  alongside the token: the packet pins it at install, verifies the `swarm.enrol_accept` frame
  and every later frame against it, and refuses an accept whose `hive_key_hex` differs from
  the pinned key, so whoever answers the dial cannot enrol the device into another Hive.
  `trusted_nodes` in the accept is trusted only because it arrives under that signature.
- **Address binding (receiver rule).** A node key signs only for the bee addresses the Queen
  registered to that node (a device's `device_id`; a colonized Cell's `warden_id` and its
  Workers; the Hive Stand's `hive_id` and everything that runs in its process). A receiver
  that holds the registry rejects a frame whose `sender` is not bound to the signing `node_id`,
  and a connection is pinned to the `node_id` of its first verified frame (a different
  `node_id` closes with 1008). The verifier's key table may carry the bound addresses when the
  guard lands (phase 10); that is an additive change to `Ed25519Verifier`.
- **Confidentiality is the link's.** Waggle provides authenticity and integrity, never
  confidentiality: the link does (TLS on `wss://`, the tier's VPN, or Tor for a `NIGHT_VEIL`
  link). A `ws://` endpoint is valid only on a loopback host.

## 7. Error message shape and the stable code table

Errors that cross Waggle are `control.error` (`ErrorMessage`, section 8.11): a reply correlated to
the message that failed, carrying a stable `code`, a full-sentence `message` with the identifiers
needed to debug, the `failed_kind` when known, and `is_retryable`. A traceback never crosses the
wire. A frame the receiver cannot decode at all (malformed, too large, wrong major, bad signature)
gets no `control.error`, because there may be no readable id to correlate to: the transport closes
the connection with the WebSocket close code listed in section 9 and the sender's outbox replays
what was pending after it reconnects. A frame that parses with a readable `id` but whose
envelope or payload fails validation (`waggle.codec.invalid_payload`) does get a `control.error`
correlated to that id, with `is_retryable` false, and the connection stays open. `message` names
ids (lease, task, exec, proposal), never a path, hostname or content, because paths on a Real
Cell are `C2`.

Codes are lowercase, dotted and stable: once a code has been sent it is never renamed. Protocol
failures use the `waggle.*` codes below, which are the `code` class attribute of the matching
class in `waggle/errors.py`.

| Code | Class | Raised when |
|---|---|---|
| `waggle.error` | `WaggleError` | Root; never raised directly |
| `waggle.ids.invalid` | `InvalidIdError` | An id has the wrong prefix, length or alphabet |
| `waggle.codec.error` | `CodecError` | Root; never raised directly |
| `waggle.codec.malformed` | `MalformedFrameError` | Bad JSON, bad envelope keys, or a text frame |
| `waggle.codec.too_large` | `FrameTooLargeError` | A frame over `MAX_FRAME_BYTES` |
| `waggle.version.unsupported_major` | `UnsupportedVersionError` | Unknown major version |
| `waggle.codec.unknown_kind` | `UnknownKindError` | `kind` is not in the registry |
| `waggle.codec.invalid_payload` | `InvalidPayloadError` | Readable `id`, but a field, id or shape rule fails |
| `waggle.signature.error` | `SignatureError` | Root; never raised directly |
| `waggle.signature.missing` | `MissingSignatureError` | Verifier configured, frame unsigned |
| `waggle.signature.unknown_node` | `UnknownSignerError` | No key pinned for the `node_id` |
| `waggle.signature.invalid` | `InvalidSignatureError` | Fails to verify over canonical bytes |
| `waggle.transport.error` | `TransportError` | Root; never raised directly |
| `waggle.transport.closed` | `TransportClosedError` | `send` after `close` |
| `waggle.transport.connection_lost` | `ConnectionLostError` | The link dropped mid-transfer |
| `waggle.transport.connect_failed` | `ConnectFailedError` | Gave up after `max_attempts` dials |
| `waggle.outbox.error` | `OutboxError` | Root; never raised directly |
| `waggle.outbox.corrupt` | `OutboxCorruptError` | An unreadable non-trailing outbox record |

Subsystem failures use their own `hive.<subsystem>.<failure>` codes, defined once as the `code`
of an error class in that subsystem's `errors.py` under `hivemind/`, never in `waggle`. Examples
the catalogue relies on: `hive.memory.wax_rejected` (the Queen declines a Cell Wax proposal),
`hive.swarm.enrol_denied` (a bad, expired or reused invite token), `hive.session.lease_not_open`
(session traffic for a lease the packet does not hold open), `hive.guard.capability_denied` (a
tool call outside the caller's capabilities), `hive.tool.os_unsupported` and
`hive.cell.provision_failed`. A `pollen` error uses `pollen.<failure>` in the same style.

`is_retryable` is the sender's hint to the outbox and to autopilot (deterministic handling by
rules, with no model call): true for a transient condition where resending the same message may
succeed, false for a message that was malformed, refused or already superseded.

## 8. Message catalogue

Conventions. Every message is a frozen pydantic model subclassing `WaggleMessage`, with every
field described and every closed set an `Enum`. Ids use the `waggle.ids` NewTypes, and every id
field, single or union, is validated with `parse_id` against the kind(s) its NewType(s) name:
`messages/base.py` provides one `Annotated` alias per `IdKind` (`TaskIdField`, `CellIdField`,
...) and `id_validator(*kinds)` for a union field (`Annotated[WorkerId | WardenId,
id_validator(IdKind.WORKER, IdKind.WARDEN)]`), so families declare the alias and write no
validator. A rule marked (validator) is a pydantic validator on the model; a rule marked
(receiver rule) compares
against another message or the receiver's state and is enforced by the receiver, never by the
model.
Reason fields are always named `reason` (a decision that crosses the wire always says why, so
the Pheromone Trail, the append-only audit log, can record it); their bound is the shared
`MAX_REASON_CHARS = 1_000`. Timestamps are timezone-aware UTC and end in `_at`; durations are
seconds as `float` and end in `_s`; sizes in bytes end in `_bytes`. Chunked content is `chunk`
(`bytes`, at most `MAX_CHUNK_BYTES`) plus `offset` and `final` (section 5). Booleans read as
predicates (`is_`, `has_`, `can_`); `final` is the one fixed exception. Datetimes end in `_at`;
`grace_until` is the one fixed exception. Digests are lowercase hex sha256 in fields named
`sha256` or ending in `_sha256`, pattern `^[0-9a-f]{64}$` (`base.py`). Every count of sub-bees
on the wire is at most `MAX_SUB_BEES_ON_WIRE = 64` (`base.py`), so a cap and a heartbeat can
never disagree. A proposal (an action a bee wants to take, awaiting the Capping
gate) is identified by the `MessageId` of its `capping.proposal_submitted` envelope; a question by
a `MessageId` the asker mints; a session by its `LeaseId`; a tool by its `ToolId`; a Cell Wax note
(a Queen-written caution about one Cell) by a `wax_`-prefixed ULID string until an `IdKind` for it
exists. Model slots (the named role a model is bound to, `ModelSlot` in `hivemind.forage`) travel
as `str`, at most `MAX_SLOT_CHARS = 32`, pattern `^[A-Z][A-Z_]*$`. Paths are `str` of at most
`MAX_PATH_CHARS = 4_096`. Spend is a `float` in the manifest's currency with no currency field.
The shared constants named here live in `messages/base.py`; every other bound below is a named,
commented constant in its family file, and the number, not the name, is normative. Tool schemas
and arguments are the one sanctioned use of JSON text on the wire (section 8.8). Each row's
Direction names the sending and receiving bees; a comma separates the hops a relayed message
takes, each a fresh envelope. Shape and Replies to are the registry's values (section 3). Terms
the table uses before their family defines them: Forage (capacity as data), Honey and Nectar
(distilled and raw knowledge), a Handoff (the document a bee writes before its context is
reset), Clustering (pause and preserve while a model provider is down), a Pheromone Mask (a
short-lived write-or-move-like-a-human tactic), Cell Wax (a Queen-written caution about one
Cell), the Landing Board (the Entrance's HTTP contract), the flight recorder (the Exoskeleton's
record of GUI actions), the Forage map (every source that can serve a model), and a checker
(the Warden's own check runner, a judge on its own slot, or a human, reporting through the
Warden).

| Kind | Class | Shape | Replies to | Direction | Summary |
|---|---|---|---|---|---|
| `task.assign` | `TaskAssign` | request | none | Queen -> Warden, Warden -> Worker | Hand a placed task to the Cell's Warden, then to its Worker. |
| `task.progress` | `TaskProgress` | event | `task.assign` | Worker -> Warden, Warden -> Queen | Report a stage change or progress on a task. |
| `task.result` | `TaskResult` | event | `task.assign` | Worker -> Warden, Warden -> Queen | Close an attempt with its outcome, artifacts and spend. |
| `task.cancel` | `TaskCancel` | event | none | Queen -> Warden, Warden -> Worker | Stop a task after a grace period to checkpoint. |
| `task.pause` | `TaskPause` | event | none | Queen -> Warden, Warden -> Worker | Checkpoint a task and hold it in place. |
| `task.resume` | `TaskResume` | event | none | Queen -> Warden, Warden -> Worker | Resume a paused task in place or from its Handoff. |
| `supervision.heartbeat` | `Heartbeat` | event | none | Worker -> Warden, Warden -> Queen | Report liveness, state and context telemetry. |
| `supervision.alarm_raised` | `AlarmRaised` | event | none | Worker -> Warden, Warden -> Queen | Escalate an issue the sender cannot resolve. |
| `supervision.alarm_resolved` | `AlarmResolved` | event | none | Queen -> Warden, Warden -> Worker or Queen | Close an Alarm at every level that saw it. |
| `supervision.inspect` | `Inspect` | request | none | Queen -> Warden, Warden -> Worker | Ask for a compacted view of a bee's context. |
| `supervision.inspect_reply` | `InspectReply` | reply | `supervision.inspect` | Warden -> Queen, Worker -> Warden | Return the compacted view and telemetry. |
| `supervision.intervene` | `Intervene` | event | none | Queen -> Warden, Warden -> Worker | Pull a supervisor lever on a bee. |
| `supervision.question` | `Question` | request | none | Worker -> Warden, Warden -> Queen | Raise a question that blocks the task. |
| `supervision.answer` | `Answer` | reply | `supervision.question` | Queen -> Warden, Warden -> Worker | Deliver the answer so the task resumes. |
| `forage.capacity_report` | `CapacityReport` | event | none | Warden or Pollen Packet -> Queen | Report a Cell's capacity snapshot. |
| `forage.grant_issued` | `GrantIssued` | event | none | Queen -> Warden | Issue or revise a Forage grant. |
| `forage.grant_revoked` | `GrantRevoked` | event | none | Queen -> Warden | Take a grant back entirely. |
| `forage.request` | `ForageRequest` | request | none | Warden -> Queen | Ask for shared Forage beyond the grant. |
| `forage.reply` | `ForageReply` | reply | `forage.request` | Queen -> Warden | Grant, partly grant or deny a Forage request. |
| `forage.hosting_decided` | `HostingDecided` | event | none | Queen -> Warden | Record where a Cell's bees get their models. |
| `forage.ceilings_set` | `CeilingsSet` | event | none | Queen -> Warden | Set or change a Warden's local-pool ceilings. |
| `forage.plan_written` | `PlanWritten` | event | none | Queen -> Warden | Write a Cell's hosting plan. |
| `cell.ready` | `CellReady` | event | none | Warden -> Queen | Announce a Cell as probed, attested and ready. |
| `cell.heartbeat` | `CellHeartbeat` | event | none | Warden -> Queen | Report a Cell's mode, leases and shield state. |
| `cell.teardown_request` | `CellTeardownRequest` | request | none | Warden -> Queen, Queen -> Pollen Packet or Warden | Ask to release a lease or retire a Cell. |
| `cell.request` | `CellRequest` | request | none | Warden -> Queen, Queen -> Pollen Packet or Warden | Ask for a Cell, or for a lease on a named Cell. |
| `cell.lease_opened` | `LeaseOpened` | event | `cell.request` | Pollen Packet or Warden -> Queen, Queen -> Warden | Announce a lease as open. |
| `cell.lease_released` | `LeaseReleased` | event | `cell.teardown_request` | Pollen Packet or Warden -> Queen, Queen -> Warden | Announce a lease as released. |
| `cell.wax_proposed` | `CellWaxProposed` | request | none | any bee -> Queen | Propose a Cell Wax caution about a Cell. |
| `cell.wax_written` | `CellWaxWritten` | event | `cell.wax_proposed` | Queen -> Warden | Deliver a written Cell Wax note. |
| `cell.wax_cleared` | `CellWaxCleared` | event | `cell.wax_proposed` | Queen -> Warden | Announce a Cell Wax note as cleared. |
| `session.open` | `SessionOpen` | request | none | Warden -> Pollen Packet | Open the terminal session for a lease. |
| `session.exec` | `SessionExec` | request | none | Warden -> Pollen Packet | Run one program inside the session. |
| `session.stdin` | `SessionStdin` | event | none | Warden -> Pollen Packet | Feed bytes or a signal to a running exec. |
| `session.output` | `SessionOutput` | event | `session.exec` | Pollen Packet -> Warden | Stream one chunk of output or file content. |
| `session.exit` | `SessionExit` | event | `session.exec` | Pollen Packet -> Warden | End a session request with its outcome. |
| `session.put_file` | `SessionPutFile` | event | none | Warden -> Pollen Packet | Write one chunk of a file onto the device. |
| `session.get_file` | `SessionGetFile` | request | none | Warden -> Pollen Packet | Read a file from the device. |
| `session.close` | `SessionClose` | event | none | Warden -> Pollen Packet | Close the lease's session. |
| `honey.nectar_deposit` | `NectarDeposit` | event | none | Worker or Warden -> Queen | Deposit one chunk of raw Nectar. |
| `honey.query` | `HoneyQuery` | request | none | any bee -> Queen | Search Honey within scopes and a budget. |
| `honey.response` | `HoneyResponse` | reply | `honey.query` | Queen -> any bee | Return the hits that survived filtering. |
| `tool.request` | `ToolRequest` | request | none | Worker -> Warden, Warden -> Queen | Ask for a tool the requester lacks. |
| `tool.promoted` | `ToolPromoted` | event | `tool.request` | Queen -> Warden, Warden -> Worker | Announce a tool version as promoted. |
| `tool.invoke` | `ToolInvoke` | request | none | Worker -> Warden, Queen -> Warden | Run one tool version on a Cell. |
| `tool.result` | `ToolResult` | reply | `tool.invoke` | Warden -> Worker, Warden -> Queen | Return a tool call's outcome. |
| `capping.proposal_submitted` | `ProposalSubmitted` | request | none | Worker -> Warden | Propose an action with its postconditions. |
| `capping.check_result` | `CheckResult` | event | `capping.proposal_submitted` | checker -> Warden, Warden -> Queen | Report one rung of the check ladder. |
| `capping.verdict` | `Verdict` | reply | `capping.proposal_submitted` | Warden -> Worker | Give the gate's final word on a proposal. |
| `capping.postcondition_result` | `PostconditionResult` | event | `capping.proposal_submitted` | Warden -> Queen, Warden -> Worker | Report one postcondition or acceptance check. |
| `capping.rollback_done` | `RollbackDone` | event | `capping.proposal_submitted` | Warden -> Queen, Warden -> Worker | Report a rollback of an applied proposal. |
| `swarm.enrol_request` | `EnrolRequest` | request | none | Pollen Packet -> Queen | Present a token and key to join the Swarm. |
| `swarm.enrol_accept` | `EnrolAccept` | reply | `swarm.enrol_request` | Queen -> Pollen Packet | Admit the device with its keys and limits. |
| `swarm.device_heartbeat` | `DeviceHeartbeat` | event | none | Pollen Packet -> Queen | Report the gateway's liveness and leases. |
| `swarm.nuc_promote` | `NucPromote` | request | none | Queen -> Warden | Start or stop a local model server. |
| `swarm.nuc_promoted` | `NucPromoted` | reply | `swarm.nuc_promote` | Warden -> Queen | Report the new runtime level and models. |
| `swarm.trail_segment_sync` | `TrailSegmentSync` | event | none | Warden -> Queen | Ship one chunk of an offline trail segment. |
| `control.ping` | `Ping` | request | none | any -> any | Probe a peer's liveness. |
| `control.pong` | `Pong` | reply | `control.ping` | any -> any | Answer a Ping with the arrival time. |
| `control.error` | `ErrorMessage` | reply | none | receiver -> sender | Report that a message could not be processed. |
| `control.shutdown` | `Shutdown` | event | none | Queen -> Warden, Warden -> Worker | Order the recipient to stop. |
| `control.cluster` | `Cluster` | event | none | Queen -> Warden | Start Clustering for one provider or all. |
| `control.wake` | `Wake` | event | none | Queen -> Warden | End Clustering and resume paused bees. |
| `control.human_message` | `HumanMessage` | event | none | Device -> Queen, Queen -> Warden | Carry a human's message into the Hive. |
| `control.mask_override` | `MaskOverride` | event | none | Queen -> Warden | Force or clear a Pheromone Mask at Cell scope. |
| `control.queen_moved` | `QueenMoved` | event | none | Queen -> every Warden and Pollen Packet | Announce that the Hive Stand has moved. |

### 8.1 Shared labels and value models (`messages/labels.py`)

Closed sets and small value models that ride on messages of more than one family live here, so
that no family file imports another. These are the wire forms; `hivemind` mirrors the enums in
`cell.tiers`, `forage.tempo` and `supervision`, and a test there keeps the two in sync.

Enums:

- `HoneyClearance`: `C0` (Wildflower: public), `C1` (Apiary: internal, non-personal), `C2`
  (Royal: personal or sensitive; anything describing the operator or a Real Cell). The data
  sensitivity label every memory tier and every message that quotes data carries.
- `AccessLevel`: `READ_ONLY`, `SCRATCH`, `FULL`. How much of a Real Cell the Hive may touch;
  Virtual Cells are always `FULL`. `HoneyClearance` is totally ordered `C0` < `C1` < `C2` and
  `AccessLevel` `READ_ONLY` < `SCRATCH` < `FULL`; each exposes a `rank` property so validators
  compare ranks, never values.
- `CombShieldLevel`: `MEADOW` (tier 0 baseline), `PROPOLIS` (tier 1, VPN-only), `NIGHT_VEIL`
  (tier 2, virtual-only, VPN plus Tor). A Cell's security tier; a Real Cell is never
  `NIGHT_VEIL`.
- `AlarmSeverity`: `INFO`, `WARNING`, `CRITICAL`.
- `Urgency`: `GRACEFUL` (bees may checkpoint and release first), `IMMEDIATE` (kill now, no
  checkpoint: Sting Cut, the human's per-Cell emergency disconnect, and Absconding, the
  human-only mass teardown). Used by `cell.teardown_request` and `control.shutdown`.
- `AccuracyBar`: `LOW`, `NORMAL`, `HIGH`, `CRITICAL`. The accuracy half of a Tempo.
- `OsFamily`: `LINUX`, `WINDOWS`, `MACOS`. Adding a member later (a phone gateway) is a minor.
- `PostconditionKind`: `FILE_EXISTS`, `FILE_ABSENT`, `COMMAND_EXITS_ZERO`, `TEST_PASSES`,
  `HTTP_STATUS`, `ELEMENT_TEXT`, `JUDGE_RUBRIC`. The machine-checkable assertions of the Capping
  gate (the quality gate: propose, check, apply, verify, roll back) plus the judge's rubric for
  what no machine can check.

Value models:

- `Tempo`: a task's speed-against-accuracy setting.
  - `latency_budget_s` (`float | None`): the latency the task can tolerate; None means no budget.
    Greater than 0 when set.
  - `accuracy` (`AccuracyBar`): the accuracy bar; sets the minimum model grade and how much
    checking the task gets.
- `Postcondition`: one assertion a bee states before acting, or one acceptance criterion a
  planner attaches to a task; the same shape serves both (`task.assign` and
  `capping.proposal_submitted`).
  - `kind` (`PostconditionKind`): what is asserted.
  - `subject` (`str`): the path, URL, selector or test id the assertion is about, or a short
    label for `JUDGE_RUBRIC`. Min 1, max `MAX_PATH_CHARS` chars.
  - `argv` (`tuple[str, ...]`): the command as an argument list, never a shell string. Max 32
    items, each max 1024 chars; non-empty only for `COMMAND_EXITS_ZERO` and `TEST_PASSES`
    (validator).
  - `expected` (`str | None`): the HTTP status, the element text or the rubric text; None for
    kinds with nothing to compare. Max 4000 chars; required for `HTTP_STATUS`, `ELEMENT_TEXT`
    and `JUDGE_RUBRIC` (validator).
- `HandoffRef`: a reference to a Handoff (the structured document a bee writes before its
  context is reset, which the next bee resumes from). No Handoff id kind exists, so the
  reference is the trail event that recorded it.
  - `event_id` (`EventId`): the `memory.checkpoint` trail event, the lookup key in Bee Bread
    (the warm memory tier).
  - `written_at` (`datetime`): when the Handoff was written.
  - `clearance` (`HoneyClearance`): the Handoff's label, visible before it is fetched so a bee
    never resumes from a Handoff above its own clearance.
- `PlatformReport`: what a device or Cell runs, the one home for OS and architecture on the wire.
  - `os` (`OsFamily`): the operating system family.
  - `distribution` (`str | None`): distribution or edition name; None when not applicable.
    Max 64.
  - `architecture` (`str`): CPU architecture (`x86_64`, `aarch64`). Max 32.
  - `package_manager` (`str | None`): the package manager available, if any. Max 32.
  - `shell` (`str`): the login shell the session runs commands through. Max 64.
  - `python_version` (`str | None`): the Python available to tools; None when none. Max 32.
- `CellCapabilitiesReport`: the wire form of `CellCapabilities`, what a Cell can do; placement
  and Workers branch on these flags, never on the Cell's kind.
  - `has_display`, `has_audio`, `has_browser`, `can_start_display`, `can_host_model` (`bool`).
  - `network_scopes` (`tuple[str, ...]`): network scopes reachable from the Cell (capability
    syntax, e.g. `net:internet`). Max 32 items, each max 256 chars.
- `GpuReport`: one GPU on a host.
  - `name` (`str`): the device name. Max 128.
  - `vram_bytes` (`int`): total VRAM. At least 0.
  - `vram_free_bytes` (`int`): free VRAM at report time. At least 0, at most `vram_bytes`.
- `HostCapacityReport`: the wire form of `HostCapacity`, static and live figures for one host;
  always a full snapshot so a report replayed from an outbox is self-contained.
  - `cores` (`int`): logical cores. At least 1.
  - `memory_bytes`, `memory_free_bytes` (`int`): total and free memory. Free at most total.
  - `disk_bytes`, `disk_free_bytes` (`int`): total and free disk where the Hive works. Free at
    most total.
  - `cpu_load` (`float`): one-minute load average divided by `cores`. At least 0.
  - `gpus` (`tuple[GpuReport, ...]`): every GPU. Max 16.

### 8.2 task (`messages/task/`)

The lifecycle of one task: assigned by the Queen to the Warden of the placed Cell, run by a
Worker, reported on, and closed. A task reaches `SUCCEEDED` only after its Warden, never the
Worker that did the work, has run its acceptance checks.

Family enums and value models:

- `WorkerRole`: `DRONE`, `FORAGER`, `SCOUT`, `GUARD_BEE`, `UNDERTAKER`, `HOUSE_BEE`. Which Worker
  role the Warden spawns for a task; the six roles that all implement `Worker`.
- `TaskStage`: `STARTED`, `WORKING`, `CHECKPOINTED`, `PAUSED`, `RESUMED`. What a `TaskProgress`
  reports; `STARTED` is the Warden's evidence for the `ASSIGNED -> RUNNING` edge.
- `TaskOutcome`: `CLAIMED` (the Worker's hop: work done, acceptance not yet run), `SUCCEEDED`,
  `FAILED`, `CANCELLED`. The Worker may only claim; the Warden's hop carries a terminal state.
- `ArtifactRef`: an output the task produced, by reference; contents never travel.
  - `path` (`str`): the scratch or capped-and-applied path. Max `MAX_PATH_CHARS`.
  - `size_bytes` (`int`): size. At least 0.
  - `sha256` (`str`): digest of the file.

#### TaskAssign

Hand a placed task, with its acceptance criteria, Tempo, clearance, grant and optional Handoff to
resume from, to the Warden that owns the chosen Cell, which re-issues it to the Worker it spawns.

- `task_id` (`TaskId`): the task being assigned.
- `goal_id` (`TaskId`): the root of the task graph this task belongs to; equals `task_id` for a
  root goal. Spend is metered per goal.
- `cell_id` (`CellId`): the Cell placement chose; a Warden rejects an assign for a Cell it does
  not own (`control.error`).
- `role` (`WorkerRole`): the Worker role the Warden spawns.
- `slot` (`str`): the model slot the bee runs on; a rebind re-assigns with a stronger one. Slot
  rules of the conventions.
- `objective` (`str`): what the task must achieve, as the planner wrote it. Min 1, max 8000.
- `acceptance` (`tuple[Postcondition, ...]`): the criteria the Warden checks before the task may
  reach `SUCCEEDED`; a `JUDGE_RUBRIC` entry stands in where nothing is machine-checkable. Min 1,
  max 32 items; total characters across all criteria at most 65,536 (validator).
- `tempo` (`Tempo`): the task's latency budget and accuracy bar.
- `clearance` (`HoneyClearance`): the highest label the task's bee may read, resume from or
  write.
- `grant_id` (`GrantId`): the Forage grant (Queen to Warden) or the slice the Warden carved
  (Warden to Worker) that the task's seats and spend are charged to. Forage is capacity as data:
  cores, memory, GPU, model seats, spend.
- `attempt` (`int`): 1 for the first try; incremented on every retry, respawn, rebind or
  reassignment so late reports from an earlier attempt are recognisable. At least 1. The Queen
  owns the number on Queen-to-Warden hops; a Warden-local respawn or rebind keeps it and
  reports it on the next `task.progress`.
- `resume_from` (`HandoffRef | None`): the Handoff to resume from after a rebind, takeover, wake
  or reassignment; None for a fresh start. Its `clearance` must not exceed `clearance`
  (validator).
- `reason` (`str`): why this task was placed here, on this role and slot, now.

#### TaskProgress

Report a stage change or periodic progress on an assigned task, carrying the Handoff reference
when the stage is a checkpoint.

- `task_id` (`TaskId`): the task reported on.
- `attempt` (`int`): echo of `TaskAssign.attempt`; a report from a superseded attempt is
  ignored. At least 1.
- `stage` (`TaskStage`): what happened.
- `summary` (`str`): one paragraph of what changed since the last report; never a transcript.
  Min 1, max 2000.
- `clearance` (`HoneyClearance`): the label of `summary`.
- `fraction_done` (`float | None`): estimated completion; None when the bee cannot estimate.
  Between 0 and 1 when set.
- `handoff` (`HandoffRef | None`): the Handoff just written. Required when `stage` is
  `CHECKPOINTED`, None otherwise (validator).

#### TaskResult

Close an attempt with its outcome, artifacts, spend and reason, on two hops: an unverified claim
from the Worker, then the Warden's verified result after the acceptance checks.

- `task_id` (`TaskId`): the task concluded.
- `attempt` (`int`): echo of `TaskAssign.attempt`. At least 1.
- `outcome` (`TaskOutcome`): `CLAIMED` on the Worker's hop, a terminal state on the Warden's.
  `checked_by` is set exactly when `outcome` is not `CLAIMED` (validator). The Warden rejects a
  `task.result` from a Worker sender whose `outcome` is not `CLAIMED` (`control.error`,
  `hive.task.self_verified`) and the Queen rejects one whose `checked_by` is not the envelope
  `sender` (receiver rules).
- `summary` (`str`): what was done, in prose; never a transcript. Min 1, max 2000.
- `clearance` (`HoneyClearance`): the label of `summary` and of the artifact paths.
- `artifacts` (`tuple[ArtifactRef, ...]`): outputs produced, by path, size and digest. Max 64;
  may be empty.
- `checked_by` (`WardenId | None`): the Warden that ran the acceptance checks; None on the hop
  from the Worker, which may only claim. Per-criterion results travel as
  `capping.postcondition_result` events with no proposal id.
- `handoff` (`HandoffRef | None`): the bee's last Handoff, so a failed or cancelled task can be
  retried from it.
- `spend` (`float`): the attempt's total spend. At least 0.
- `reason` (`str`): why this outcome: the failure cause, the cancellation cause, or the
  acceptance summary.

#### TaskCancel

Order a task stopped, with a grace period to checkpoint before the bee is killed.

- `task_id` (`TaskId`): the task to cancel.
- `grace_s` (`float`): seconds the bee gets to checkpoint and release before it is killed; 0
  kills at once (Sting Cut, the human's per-Cell emergency disconnect, and Absconding, the
  human-only mass teardown). At least 0.
- `reason` (`str`): why the task is cancelled.

#### TaskPause

Order a running task to checkpoint and hold in place, moving it to `PAUSED`, as Clustering (the
pause-and-preserve protocol while a model provider is unavailable) and the Supersedure freeze
require. A pause always checkpoints; the structured cause rides on `control.cluster`.

- `task_id` (`TaskId`): the task to pause.
- `reason` (`str`): why it is paused.

#### TaskResume

Resume a paused task, either in place while the bee is still warm or from its Handoff on a fresh
bee, optionally on a different slot.

- `task_id` (`TaskId`): the task to resume.
- `attempt` (`int`): the attempt number the resumed work reports under: the paused attempt when
  resuming in place, a fresh one for a fresh bee. At least 1.
- `resume_from` (`HandoffRef | None`): the Handoff a fresh bee resumes from; None means the
  paused bee is still warm and continues in place.
- `slot` (`str | None`): a different model slot to resume on when the original provider is
  still down; None keeps the current binding. Slot rules of the conventions.
- `reason` (`str`): why it resumes now.

### 8.3 supervision (`messages/supervision/`)

The one `Supervisor` protocol at every level of the tree (human, Queen, Wardens, sub-bees):
heartbeats with context telemetry, Alarms escalated upward, inspection, intervention and
questions that block a task until answered. Full transcripts never travel up the tree.

Family enums and value models:

- `ContextTelemetry`: what every bee reports on heartbeat, bounded so it can never smuggle a
  transcript.
  - `tokens_used` (`int`): tokens in the bee's context. At least 0.
  - `context_window` (`int`): the bound model's window. Greater than 0.
  - `goal` (`str`): the current goal in one line. Max 1000.
  - `last_actions` (`tuple[str, ...]`): the most recent actions. Max 10 items, each max 200.
  - `blockers` (`tuple[str, ...]`): what the bee is stuck on. Max 10 items, each max 500.
  - `spend` (`float`): spend so far. At least 0.
- `WorkerState`: `SPAWNED`, `RUNNING`, `HANDING_OFF`, `PAUSED`, `DONE`, `FAILED`, `KILLED`.
- `WardenState`: `STARTING`, `ACTIVE`, `WATCH`, `OFFLINE`, `CLUSTERED`, `MIGRATING`, `STOPPED`.
- `ChildTelemetry`: one sub-bee's row in a Warden's aggregate heartbeat.
  - `worker_id` (`WorkerId`), `task_id` (`TaskId | None`), `state` (`WorkerState`),
    `telemetry` (`ContextTelemetry`).
- `AlarmKind`: `WORKER_FAILED`, `WORKER_CRASHED`, `WORKER_STALLED`, `ACCEPTANCE_FAILED`,
  `POSTCONDITION_FAILED`, `CONTEXT_OVERFLOW`, `GRANT_EXCEEDED`, `PROVIDER_UNAVAILABLE`,
  `AUDIT_FAILED`, `CELL_UNREACHABLE`, `QUOTA_EXCEEDED` (a lease's scratch directory outgrew its
  configured quota; added in a minor 1 bump, roadmap step 3.11), `OTHER`. The closed set the
  escalation policy keys on; `OTHER` carries anything new until a minor bump names it.
- `AlarmContext`: typed references to what an Alarm is about.
  - `task_id` (`TaskId | None`), `cell_id` (`CellId | None`), `worker_id` (`WorkerId | None`:
    the bee concerned, which may differ from `origin` when a Warden raises about a sub-bee),
    `event_id` (`EventId | None`: the trail event that best explains it), `handoff`
    (`HandoffRef | None`: the stuck bee's last Handoff so a takeover can resume from it).
- `AlarmResolution`: `RETRIED`, `RESPAWNED`, `REBOUND`, `TAKEN_OVER`, `CANCELLED`, `HUMAN`,
  `SELF_CLEARED`.
- `CompactView`: the compacted view `inspect` returns, shaped after a Handoff's sections.
  - `goal` (`str`, max 1000), `progress` (`str`, max 2000), `decisions` (`tuple[str, ...]`, max
    16 items each max 500), `open_threads` (`tuple[str, ...]`, max 16 items each max 500);
    total characters at most 8000 (validator).
- `InterventionAction`: `COMPACT`, `CHECKPOINT`, `HANDOFF`, `REBIND`, `TAKEOVER`, `CANCEL`.
- `AnswerSource`: `HUMAN`, `QUEEN`, `WARDEN`. Who answered; the human is not a bee address.

#### Heartbeat

Report the sender's liveness, state and context telemetry, a Warden's per-sub-bee telemetry, and
renew the Warden's grant.

- `telemetry` (`ContextTelemetry`): the sender's own telemetry.
- `task_id` (`TaskId | None`): the sender's current task; None while idle or in `WATCH`.
- `worker_state` (`WorkerState | None`): the sender's state when it is a Worker.
- `warden_state` (`WardenState | None`): the sender's state when it is a Warden. Exactly one of
  `worker_state` and `warden_state` is set (validator).
- `children` (`tuple[ChildTelemetry, ...]`): a Warden's per-sub-bee rows; empty for a Worker.
  Max `MAX_SUB_BEES_ON_WIRE`, worker ids unique.
- `grant_id` (`GrantId | None`): the grant this heartbeat renews; None for a Worker or a Warden
  without a shared grant.
- `grant_spend` (`float | None`): total spend charged to that grant so far. At least 0; requires
  `grant_id`.
- `interval_s` (`float`): the sender's configured heartbeat interval, so the receiver's watchdog
  can size its timeout. Greater than 0.

#### AlarmRaised

Escalate an issue the sender cannot resolve to its supervisor, keeping the same alarm id, origin
and attempt count at every level; the human is always last and only the Queen reaches them.

- `alarm_id` (`AlarmId`): identical at every level so no level handles it twice.
- `kind` (`AlarmKind`): what went wrong, as the policy table keys it.
- `severity` (`AlarmSeverity`): how bad.
- `origin` (`WorkerId | WardenId`): the bee that first raised it; survives forwarding.
- `attempts` (`int`): resolution attempts made so far across levels. At least 0; 0 on the
  raising hop, incremented once per level (receiver rule).
- `raised_at` (`datetime`): when it was first raised; its age feeds the Attendant's score (the
  Attendant is a supervisor's inbox triage).
- `context` (`AlarmContext`): typed references to the task, Cell, bee, trail event and Handoff.
- `detail` (`str`): the failing assertion, error sentence or observation; never a transcript.
  Min 1, max 2000.
- `clearance` (`HoneyClearance`): the label of `detail`.
- `reason` (`str`): why the sender escalates instead of handling it.

#### AlarmResolved

Close an Alarm at every level that saw it, saying how and why it was resolved, so no level keeps
re-escalating it.

- `alarm_id` (`AlarmId`): the Alarm closed.
- `resolution` (`AlarmResolution`): how it was resolved.
- `resolved_by` (`WardenId | HiveId`): the supervisor that resolved it (the Queen when the human
  did).
- `reason` (`str`): why this resolution.

#### Inspect

Ask a child, or one of its sub-bees, for a compacted view of its context under a size cap; the
Queen never addresses a Worker directly, so reaching one goes through its Warden.

- `subject` (`WorkerId | None`): a sub-bee of the recipient to inspect; None means the recipient
  itself.
- `max_chars` (`int`): the cap the reply's view must honour. Between 256 and 8000, default 8000.

#### InspectReply

Return the requested bee's telemetry and compacted view, labelled with its clearance.

- `subject` (`WorkerId | WardenId`): the bee described.
- `task_id` (`TaskId | None`): the subject's current task, if any.
- `telemetry` (`ContextTelemetry`): the subject's telemetry at reply time.
- `view` (`CompactView`): the compacted view. Total size at most the request's `max_chars`
  (receiver rule).
- `clearance` (`HoneyClearance`): the label of the view, derived from the hot state it
  summarises.
- `is_truncated` (`bool`): whether the view was cut to fit the cap.

#### Intervene

Pull one of the supervisor's levers on a child or one of its sub-bees: compact, checkpoint,
handoff, rebind to a slot, takeover or cancel. Wardens hold the same levers over their sub-bees
minus takeover with the Queen's slot.

- `action` (`InterventionAction`): the lever pulled.
- `subject` (`WorkerId | None`): the recipient's sub-bee the action targets; None means the
  recipient itself.
- `task_id` (`TaskId | None`): the task concerned when the subject holds more than one.
- `slot` (`str | None`): the model slot to rebind to. Required when `action` is `REBIND`, None
  otherwise (validator). Slot rules of the conventions.
- `alarm_id` (`AlarmId | None`): the Alarm this intervention answers, so the trail links the two.
- `reason` (`str`): why the supervisor intervenes.

#### Question

Raise a question up the chain that blocks the task until an `Answer` with the same question id
comes back; only the Queen decides whether it goes to the human.

- `question_id` (`MessageId`): the id the question is known by everywhere, minted by the asker
  with `new_message_id` so it survives re-wrapping at each hop. It is not any envelope's id.
- `task_id` (`TaskId`): the task that blocks on the answer.
- `asked_by` (`WorkerId | WardenId`): the bee that asked; survives forwarding.
- `text` (`str`): the question. Min 1, max 4000.
- `options` (`tuple[str, ...]`): closed choices, when the asker can offer them. Max 16 items,
  each max 200; may be empty.
- `clearance` (`HoneyClearance`): the label of the question text, which may quote Real Cell or
  personal data.
- `asked_at` (`datetime`): when it was first asked; survives forwarding.

#### Answer

Deliver the answer to a blocked question back down the chain so the task resumes.

- `question_id` (`MessageId`): matches `Question.question_id` at every hop.
- `task_id` (`TaskId`): the task that resumes.
- `text` (`str`): the answer. Min 1, max 4000.
- `chosen_option` (`int | None`): index into `Question.options` when one was chosen. Between 0
  and 15; a valid index for that question (receiver rule).
- `source` (`AnswerSource`): who answered.
- `clearance` (`HoneyClearance`): the label of the answer; a human's answer is `C2` by
  provenance (`source` `HUMAN` requires `C2`, validator).

### 8.4 forage (`messages/forage/`)

Forage is the Hive's capacity in several dimensions, never one number. The Queen divides what is
shared by grant; a Warden divides what is physically on its own Cell (its local pool) under
ceilings the Queen set once; every model call passes through the Fanner (the seat meter), which
follows a hosting plan of primary and fallback sources.

Family enums and value models:

- `CapacityTrigger`: `PROVISIONED`, `ENROLLED`, `CHANGED`, `RECONNECTED`, `PERIODIC`. Why a
  capacity report was sent.
- `LocalSourceReport`: one model loaded on a model server on the Cell, the local half of a
  Forage map source.
  - `model` (`str`): the Forage map model id. Max 128.
  - `seats_total`, `seats_free` (`int`): concurrent requests the server allows and has free.
    At least 0; free at most total.
  - `context_window` (`int`): the model's window. At least 0.
  - `vram_bytes` (`int`): VRAM the loaded model occupies. At least 0.
  - `tokens_per_s` (`float | None`): measured generation speed; None until measured. At least 0.
- `ModelServerReport`: a model server running on the Cell with the models it serves.
  - `server_id` (`str`): the manifest key of the local model server definition, which the
    receiver resolves to a provider and address; no URL crosses the wire. Max 128.
  - `sources` (`tuple[LocalSourceReport, ...]`): the models it serves. Max 64.
- `LocalPoolUsage`: the Warden's usage against its ceilings plus the seats it has exported;
  every field mirrors one `CeilingsReport` dimension.
  - `sub_bees_active`, `model_vram_bytes`, `model_disk_bytes`, `seats_exported` (`int`). At
    least 0.
- `Effort`: `LOW`, `MEDIUM`, `HIGH`. A model's effort setting, per binding.
- `SourceRef`: names one Forage map source without its live figures.
  - `source_id` (`str`): the map entry key. Max 128.
  - `provider` (`str`): the manifest provider name. Max 64.
  - `model` (`str`): the model id. Max 128.
  - `host_cell_id` (`CellId | None`): the Cell whose server serves it; None for a hosted
    provider.
- `AllowedBinding`: one entry of a grant's allowed bindings.
  - `slot` (`str`): slot rules of the conventions. `source` (`SourceRef`). `max_effort`
    (`Effort`).
- `SeatReservation`: seats reserved on one shared server or hosted provider; the hosted
  equivalent of a seat is requests and tokens per minute.
  - `source_id` (`str`, max 128), `seats` (`int`, at least 0), `requests_per_minute`
    (`int | None`, at least 0), `tokens_per_minute` (`int | None`, at least 0).
- `RevocationCause`: `EXPIRED`, `HOLDER_OFFLINE`, `RECLAIMED`, `RELEASED`, `STING_CUT`.
- `ForageRequestKind`: `SHARED_SEATS`, `SPEND`, `BINDING`, `SUB_BEES`.
- `ForageDelta`: the delta wanted or granted; one model serves both so a partial grant has the
  shape of the ask.
  - `seats` (`int`, default 0), `source_id` (`str | None`: which shared source the seats are
    on; max 128, as `SourceRef.source_id`), `spend` (`float`, default 0.0), `tokens` (`int`,
    default 0), `sub_bees` (`int`, default 0, at most `MAX_SUB_BEES_ON_WIRE`), `slot`
    (`str | None`: the slot a `BINDING` request is for; slot rules of the conventions),
    `minimum_grade` (`int | None`: between 1 and 5). Counts and `spend` at least 0.
- `ForageOutcome`: `GRANTED`, `PARTIAL`, `DENIED`.
- `HostingMode`: `SHARED_ONLY`, `LOCAL_FIRST`, `LOCAL_ONLY`.
- `CeilingsReport`: the wire form of `Ceilings`.
  - `max_sub_bees` (`int`, at most `MAX_SUB_BEES_ON_WIRE`), `model_vram_bytes` (`int`),
    `model_disk_bytes` (`int`), `loadable_sources` (`tuple[str, ...]`: source ids, max 64,
    each max 128), `exportable_seats` (`int`). Counts at least 0.
- `SourceChain`: a primary source and its fallback chain, the unit the Fanner spills along.
  - `primary` (`SourceRef`), `fallbacks` (`tuple[SourceRef, ...]`, max 8).
- `SlotPlan`: one named slot's entry in a hosting plan.
  - `slot` (`str`: slot rules of the conventions), `chain` (`SourceChain`).

#### CapacityReport

A Cell's capacity snapshot: host compute, the model servers on it, its sub-bee cap and the
Warden's usage against its ceilings, sent at provision or enrolment, on change, on reconnection
and on a timer. Live capacity travels only here, never on heartbeats.

- `cell_id` (`CellId`): the Cell this capacity belongs to.
- `trigger` (`CapacityTrigger`): why this report was sent.
- `host` (`HostCapacityReport`): static and live host figures, a full snapshot.
- `model_servers` (`tuple[ModelServerReport, ...]`): every model server on the Cell; empty for
  a Cell that hosts no models. Max 8.
- `max_sub_bees` (`int`): the Cell's own cap on concurrent sub-bees. Between 0 and
  `MAX_SUB_BEES_ON_WIRE`.
- `usage` (`LocalPoolUsage`): what the Warden uses of its local pool; all zeros for a Cell with
  no Warden yet.

#### GrantIssued

The Queen issues or re-issues (grows, shrinks, tops up) the shared half of a Warden's Forage as a
lease with an expiry; the same `grant_id` with a higher revision replaces the previous terms.

- `grant_id` (`GrantId`): the grant's identity, stable across revisions.
- `holder` (`WardenId`): the Warden that holds it.
- `cell_id` (`CellId`): the Cell the holder runs.
- `task_id` (`TaskId | None`): the goal or task whose caps sized the budgets; None for a
  standing grant.
- `revision` (`int`): 0 on first issue, incremented on every change so the holder discards
  stale replays. At least 0.
- `allowed` (`tuple[AllowedBinding, ...]`): bindings the holder may use. Max 32.
- `seats` (`tuple[SeatReservation, ...]`): reservations the Fanner enforces. Max 32.
- `token_budget` (`int`): total tokens the grant allows. At least 0.
- `spend_budget` (`float`): total spend the grant allows. At least 0.
- `tokens_spent` (`int`): tokens already consumed at issue time. At least 0.
- `spent` (`float`): spend already consumed at issue time. At least 0.
- `max_sub_bees` (`int`): sub-bees the holder may run under this grant. Between 0 and
  `MAX_SUB_BEES_ON_WIRE`.
- `expires_at` (`datetime`): when the grant returns to the pool unless renewed by heartbeat.
- `reason` (`str`): why these terms: the allocator's decision.

#### GrantRevoked

The Queen takes a grant back entirely; the holder must stop drawing on shared Forage at once.
Shrinking is a `GrantIssued` revision, not a revocation.

- `grant_id` (`GrantId`): the revoked grant.
- `holder` (`WardenId`): the Warden that held it.
- `revision` (`int`): the grant revision revoked; a holder whose revision is higher ignores
  the message as a stale replay (receiver rule). At least 0.
- `cause` (`RevocationCause`): which rule revoked it.
- `reason` (`str`): the Queen's reason.

#### ForageRequest

A Warden asks for shared Forage beyond its standing grant: more shared seats, spend, a
higher-grade binding, or more sub-bees, with a reason and the task's Tempo. Answered only while
connected; offline it waits in the outbox.

- `grant_id` (`GrantId`): the grant the Warden wants extended.
- `kind` (`ForageRequestKind`): which dimension is asked for.
- `wanted` (`ForageDelta`): the delta wanted. At least one non-zero field (validator).
- `task_id` (`TaskId | None`): the task the extra Forage serves; None for the Warden's own needs.
- `tempo` (`Tempo`): the requesting task's Tempo, an allocator input.
- `reason` (`str`): why the Warden needs it.

#### ForageReply

The Queen grants, partly grants or denies a `ForageRequest` with a reason; a granted delta is
folded into the next `GrantIssued` revision.

- `grant_id` (`GrantId`): the grant the request was against.
- `outcome` (`ForageOutcome`): granted in full, in part, or denied.
- `granted` (`ForageDelta`): what was actually granted; all zeros when denied.
- `revision` (`int | None`): the grant revision that now carries the delta; None exactly when
  denied (validator). At least 0.
- `expires_at` (`datetime | None`): the grant's expiry after this change; None exactly when
  denied (validator).
- `reason` (`str`): why: headroom, contest, reserve, cost cap.

#### HostingDecided

The Queen records where a Cell's bees get their models (shared only, local first, or local
only) with the reason; the detailed plan follows as `PlanWritten`.

- `cell_id` (`CellId`): the Cell the decision is about.
- `revision` (`int`): 0 for the first decision about this Cell, incremented on each change, so
  a replayed older decision never overrides a newer one (receiver rule). At least 0.
- `mode` (`HostingMode`): the decision. `LOCAL_ONLY` is mandatory for a `NIGHT_VEIL` Cell
  (receiver rule: the Warden of such a Cell refuses anything else).
- `task_id` (`TaskId | None`): the task that prompted it, if any.
- `reason` (`str`): why: free VRAM, seat pressure, distance against Tempo, need to survive
  disconnection, cost.

#### CeilingsSet

The Queen sets or changes a device Warden's or Nuc's ceilings once (a Nuc is a colonized Real Cell
that also runs its own model server); within them the Warden never asks.

- `cell_id` (`CellId`): the Cell whose local pool the ceilings bound.
- `holder` (`WardenId`): the Warden they apply to.
- `revision` (`int`): 0 when first set, incremented on each change. At least 0.
- `ceilings` (`CeilingsReport`): the ceilings.
- `reason` (`str`): why these ceilings; changing one is a Queen decision on the trail.

#### PlanWritten

The Queen writes a Cell's hosting plan: for each slot a primary source and a fallback chain, plus
a default chain for slots not named, with the reason.

- `cell_id` (`CellId`): the Cell the plan is for.
- `revision` (`int`): 0 for the first plan, incremented on each rewrite. At least 0.
- `slots` (`tuple[SlotPlan, ...]`): per named slot, its chain. Max 16, slot names unique.
- `default` (`SourceChain`): the chain for any slot not named.
- `reason` (`str`): why this plan.

### 8.5 cell (`messages/cell/`)

The life of a Cell and of the leases on it: a Cell reports ready and alive, a Warden asks the
Queen for a Cell or to retire one, leases open and close (a lease is one Warden's tenancy on a
Real Cell, with a scratch directory and the processes it started), and Cell Wax notes are
proposed, written and cleared. A Real Cell is borrowed and left exactly as found.

Family enums and value models:

- `AttestationCheck`: one readiness check, pass or fail.
  - `name` (`str`, max 64), `has_passed` (`bool`), `detail` (`str`, max 1000).
- `CellMode`: `ACTIVE` (bees running), `IDLE` (a Virtual Cell eligible for Overwintering, the
  dormant pool), `WATCH` (a Real Cell's Warden observing read-only).
- `IsolationNeed`: `REQUIRED`, `PREFERRED`, `NONE`.
- `TaskNeedsReport`: the wire form of `TaskNeeds`, what placement decides on.
  - `isolation` (`IsolationNeed`), `needs_exoskeleton` (`bool`: needs the virtual peripherals
    bundle),
    `os` (`OsFamily | None`), `network_scopes` (`tuple[str, ...]`, max 32, each max 256),
    `is_disposable` (`bool`), `comb_shield` (`CombShieldLevel`), `tempo` (`Tempo`).
- `ReleaseCause`: `COMPLETED`, `CANCELLED`, `STING_CUT`, `DEAD_MAN`, `ORPHAN_SWEEP`,
  `HOLDER_LOST`. Sting Cut is the human's per-Cell emergency disconnect; the dead-man switch
  is what a device with no Warden on it runs when its link is lost too long.
- `WaxSeverity`: `NOTE`, `CAUTION`, `BLOCK`. Placement treats `BLOCK` as exclusion and
  `CAUTION` as a penalty.
- `WaxOrigin`: `BEE`, `PATROL`, `HUMAN`. Who noticed the caution (the Patrol is a watching
  Warden's scheduled review).
- `WaxDecision`: `AUTOPILOT`, `AWAKE`. Whether the Queen wrote the note by rule or by a model
  episode.
- `WaxClearCause`: `CLEARED`, `EXPIRED`, `CELL_RETIRED`.

#### CellReady

A Cell has booted, been probed and, where its tier requires it, attested; it reports its platform,
capabilities, access level, Comb Shield level and check results so placement can consider it.

- `cell_id` (`CellId`): the Cell that is ready.
- `warden_id` (`WardenId`): the Warden that owns this Cell from now on.
- `platform` (`PlatformReport`): OS, distribution, architecture, package manager, shell, Python.
- `capabilities` (`CellCapabilitiesReport`): what the Cell can do, as probed.
- `access_level` (`AccessLevel`): how much of the Cell the Hive may touch; `FULL` for every
  Virtual Cell.
- `comb_shield` (`CombShieldLevel`): the Cell's security tier, bound to the Cell not the task.
- `attestation` (`tuple[AttestationCheck, ...]`): per-check results; empty exactly when
  `comb_shield` is `MEADOW` (validator). Max 32. A `NIGHT_VEIL` Cell with any failed check is
  recorded on the trail but never schedulable, and the Queen checks `comb_shield` and
  `access_level` against its provisioning or enrolment record for `cell_id` (receiver rules).
- `runtime_version` (`str`): the HiveMind runtime version in the Cell. Max 64.

#### CellHeartbeat

Liveness of the Cell itself, distinct from the bee heartbeat: its mode, open leases, active
Workers, whether its tier's tunnel checks still hold, and the cadence to watchdog against. It
carries no capacity figures.

- `cell_id` (`CellId`): the Cell reporting.
- `mode` (`CellMode`): the Cell's current mode.
- `lease_ids` (`tuple[LeaseId, ...]`): every lease open on the Cell, for orphan reconciliation.
  Max 64.
- `worker_count` (`int`): sub-bees running on the Cell. At least 0.
- `is_shield_verified` (`bool`): whether the tier's runtime checks held at this beat; always
  true for `MEADOW` (receiver rule).
- `interval_s` (`float`): the sender's heartbeat cadence. Greater than 0.

#### CellTeardownRequest

Ask the recipient to end a tenancy: release a lease on a Real Cell, or retire a Virtual Cell,
gracefully or immediately. A Warden asks the Queen; the Queen asks the device's gateway or Warden.
The answer is a correlated `LeaseReleased` or a `control.error`.

- `cell_id` (`CellId`): the Cell concerned.
- `lease_id` (`LeaseId | None`): the lease to release; None asks for the whole Virtual Cell to be
  retired (overwinter or destroy is the Queen's choice).
- `urgency` (`Urgency`): graceful or immediate.
- `cause` (`ReleaseCause`): why the tenancy ends, so a gateway behaves differently for a Sting
  Cut (invalidate session keys, delete lease-local traces) without deciding anything itself.
  Only `COMPLETED`, `CANCELLED` or `STING_CUT` on a request (validator); the answering
  `LeaseReleased.cause` echoes it.
- `reason` (`str`): why.

#### CellRequest

Ask for a Cell: a Warden states task needs and the Queen places; or the Queen names a Real Cell
and asks its gateway or Warden to open a lease for a holder at an access level. The answer is a
correlated `LeaseOpened` or a `control.error`.

- `cell_id` (`CellId | None`): None means any Cell that fits `needs`; set means open a lease on
  exactly this Cell.
- `needs` (`TaskNeedsReport | None`): what the work needs. Required when `cell_id` is None
  (validator).
- `holder` (`WardenId | None`): the Warden that will hold the resulting lease. Required when
  `cell_id` is set, None when it is None (validator): every placed Cell gets its own Warden,
  which the Queen names in the correlated `LeaseOpened`.
- `task_id` (`TaskId | None`): the task the Cell is for; None for a sandbox or other internal use.
- `access_level` (`AccessLevel`): the most the holder needs; the operator's enrolled level caps
  it.
- `lifetime_s` (`float | None`): expected tenancy length; None means until released. Greater
  than 0 when set.
- `reason` (`str`): why the Cell is needed.

#### LeaseOpened

A lease is open: the holder, task, access and tier, the scratch root, the paths allowed outside
scratch and the dead-man limit, with the placement reason. The opener tells the Queen; the Queen
tells the holder.

- `lease_id` (`LeaseId`): minted by the opener.
- `cell_id` (`CellId`): the Cell leased.
- `holder` (`WardenId`): the Warden that owns the tenancy.
- `task_id` (`TaskId | None`): the task it serves, if any.
- `access_level` (`AccessLevel`): the level actually granted.
- `comb_shield` (`CombShieldLevel`): the Cell's tier the task inherits.
- `scratch_root` (`str`): the lease's scratch directory; everything outside it needs a
  capability. Min 1, max `MAX_PATH_CHARS`.
- `allowed_paths` (`tuple[str, ...]`): paths outside scratch the lease may touch. Max 64, each
  max `MAX_PATH_CHARS`.
- `dead_man_s` (`float | None`): if the link is lost longer than this and no Warden runs on the
  device, the gateway kills what the lease started and releases; None when a Warden lives on
  the Cell. Greater than 0 when set.
- `reason` (`str`): why this Cell, any Cell Wax that weighed on it included.

#### LeaseReleased

A lease is released: why, whether the device was left as found, how many started processes were
killed, and any paths that could not be restored. A gateway may send it unprompted (dead-man).

- `lease_id` (`LeaseId`): the lease closed.
- `cell_id` (`CellId`): the Cell it was on.
- `holder` (`WardenId`): the Warden that held it.
- `cause` (`ReleaseCause`): which rule released it.
- `is_restored` (`bool`): true when the scratch directory is gone and every touched path was
  restored.
- `killed_processes` (`int`): processes the lease started that were terminated on release. At
  least 0.
- `residual_paths` (`tuple[str, ...]`): paths outside scratch that could not be restored; empty
  exactly when `is_restored` (validator). Max 64, each max `MAX_PATH_CHARS`.
- `reason` (`str`): the releaser's reason.

#### CellWaxProposed

Propose a Cell Wax note about one Cell: a caution with severity, text, reason, clearance and
optional expiry. Only the Queen writes it; a rejection is a `control.error` with code
`hive.memory.wax_rejected`.

- `cell_id` (`CellId`): the Cell the note is about.
- `severity` (`WaxSeverity`): every `BLOCK` is an awake decision.
- `text` (`str`): the caution itself. Min 1, max 4000; the manifest may cap lower.
- `reason` (`str`): why the proposer believes it.
- `clearance` (`HoneyClearance`): anything describing an operator's own machine is `C2`.
- `expires_at` (`datetime | None`): when the note expires on its own; None for standing wax.
- `origin` (`WaxOrigin`): who noticed it.
- `proposer` (`WorkerId | WardenId | None`): the proposing bee, so a Worker's proposal relayed
  by its Warden still routes to an awake decision; None exactly when `origin` is `HUMAN`
  (validator). Equals the envelope `sender` on the first hop (receiver rule).
- `task_id` (`TaskId | None`): the task during which it was noticed, for provenance; None when
  no task is involved (a Patrol, the human, or the Warden's own work).

#### CellWaxWritten

The Queen has written a Cell Wax note; the full note travels so the Cell's Warden holds it even
when offline.

- `wax_id` (`str`): the note's id, minted by the Queen. Pattern `^wax_[0-9A-HJKMNP-TV-Z]{26}$`.
- `cell_id` (`CellId`): the Cell marked.
- `severity` (`WaxSeverity`): as written; the Queen may downgrade a proposal.
- `text` (`str`): the caution as written. Min 1, max 4000.
- `reason` (`str`): why the Queen wrote it.
- `clearance` (`HoneyClearance`): the label carried into hot state and later Honey.
- `expires_at` (`datetime | None`): expiry, if any.
- `origin` (`WaxOrigin`): who proposed it.
- `proposer` (`WorkerId | WardenId | None`): the proposing bee; None exactly when `origin` is
  `HUMAN` (validator).
- `decided_by` (`WaxDecision`): by rule or by a model episode.

#### CellWaxCleared

A Cell Wax note has left the written state: cleared by the Queen, expired on the sweep, or retired
with its destroyed Virtual Cell.

- `wax_id` (`str`): the note cleared. Pattern as above.
- `cell_id` (`CellId`): the Cell it marked.
- `cause` (`WaxClearCause`): why it left.
- `reason` (`str`): the decision text; expiry names the sweep.

### 8.6 session (`messages/session/`)

The terminal-over-Waggle family: a Warden on the Hive Stand drives a remote Real Cell through its
Pollen Packet exactly as it would a local terminal (exec with streaming output, stdin, put and get
file, close). A session is identified by its `LeaseId`, one session per lease; concurrency lives
at the exec level, each exec keyed by its request's `MessageId`. Command arguments are always
argument lists, never a shell string.

Family enums and value models:

- `EnvVar`: one environment variable overlaid for an exec.
  - `name` (`str`, max 256, pattern `^[A-Za-z_][A-Za-z0-9_]*$`), `value` (`str`, max 4096; a
    possible secret: redacted from logs and the trail, which record names only).
- `SessionStream`: `STDOUT`, `STDERR`, `FILE`.
- `SessionOutcome`: `EXITED` (ended on its own), `SIGNALED` (ended by a signal), `TIMED_OUT`
  (killed at `timeout_s`), `KILLED` (killed on a stdin signal, an output cap or session close),
  `COMPLETED` (an open or a file transfer finished).
- `ProcessSignal`: `INTERRUPT`, `TERMINATE`, `KILL`. Platform-neutral; the packet maps them.
- `SessionRequestKind`: `OPEN`, `EXEC`, `PUT_FILE`, `GET_FILE`. Which request a `SessionExit`
  ends.

#### SessionOpen

Open the terminal session for one open lease on the device so exec, file and close traffic may
follow. Success is a `SessionExit` with outcome `COMPLETED`.

- `lease_id` (`LeaseId`): the open lease whose scratch directory and process list this session
  works inside; the session's identity from here on. A lease the packet does not hold open
  draws `hive.session.lease_not_open`.
- `reason` (`str`): why the Warden opens the session now.

#### SessionExec

Run one program on the device inside the lease, streaming its output back as `SessionOutput`
events and ending with a `SessionExit`.

- `lease_id` (`LeaseId`): the session to run in.
- `argv` (`tuple[str, ...]`): the program and its arguments; `argv[0]` is the executable. Min 1,
  max 128 items, each max 4096 chars, at most 131,072 characters in total (validator).
- `cwd` (`str | None`): working directory; None means the lease's scratch directory. The packet
  refuses paths outside scratch or the lease's allowed roots. Max `MAX_PATH_CHARS`.
- `env` (`tuple[EnvVar, ...]`): variables overlaid for this process only. Max 64, names unique.
- `timeout_s` (`float`): wall-clock seconds before the packet kills the process and reports
  `TIMED_OUT`. Greater than 0.
- `has_stdin` (`bool`): true when the Warden will stream `SessionStdin` and close it with
  `final`; false closes stdin at start. Default false.
- `max_output_bytes` (`int`): cap on total stdout plus stderr bytes; past it the packet kills
  the process (`KILLED`). At least 1; default `DEFAULT_MAX_OUTPUT_BYTES = 16_777_216` (16 MiB),
  so a looping device can never stream unbounded output into a Warden.
- `task_id` (`TaskId | None`): the task this command serves; None for the Warden's own
  diagnostics or Patrol probes.
- `worker_id` (`WorkerId | None`): the sub-bee on whose behalf the Warden runs it.

#### SessionStdin

Feed bytes or a signal to a running exec's standard input, exactly as a terminal would; a signal
is how one exec is cancelled without closing the session.

- `lease_id` (`LeaseId`): the session the exec belongs to.
- `exec_id` (`MessageId`): the envelope id of the `SessionExec` this input targets; also the
  envelope's `correlation_id`.
- `chunk` (`bytes`): raw stdin bytes; a possible secret, redacted from logs and the trail. Max
  `MAX_CHUNK_BYTES`; empty when `signal` is set.
- `offset` (`int`): byte offset within the stdin stream. At least 0.
- `final` (`bool`): true closes the process's stdin after this chunk. Default false.
- `signal` (`ProcessSignal | None`): deliver a signal instead of bytes. When set, `chunk` must
  be empty (validator).

#### SessionOutput

One chunk of an exec's stdout or stderr, or of a file answering `SessionGetFile`.

- `lease_id` (`LeaseId`): the session the chunk belongs to.
- `request_id` (`MessageId`): envelope id of the `SessionExec` or `SessionGetFile` this chunk
  answers; also the envelope's `correlation_id`.
- `stream` (`SessionStream`): which stream; `FILE` only for a get-file transfer (receiver
  rule). A chunk whose `request_id` is not an outstanding request of the session is dropped
  (receiver rule).
- `chunk` (`bytes`): the bytes, in stream order. Max `MAX_CHUNK_BYTES`.
- `offset` (`int`): byte offset within its stream. At least 0.
- `final` (`bool`): true on the last chunk of this stream; stdout and stderr each end with their
  own final chunk, before the `SessionExit`. Default false.

#### SessionExit

The terminal event of one session request: an exec's exit status, or the completion of an open,
put-file or get-file.

- `lease_id` (`LeaseId`): the session the request ran in.
- `request_id` (`MessageId`): envelope id of the request this ends (for a put-file, its
  `transfer_id`); also the envelope's `correlation_id`.
- `request_kind` (`SessionRequestKind`): `OPEN`, `EXEC`, `PUT_FILE` or `GET_FILE`, which
  request this ends, so the rules below are decidable on the model alone.
- `outcome` (`SessionOutcome`): how it ended; `EXITED`, `SIGNALED`, `TIMED_OUT` and `KILLED`
  only when `request_kind` is `EXEC`, `COMPLETED` only otherwise (validator).
- `exit_code` (`int | None`): the process's exit status. Set exactly when `outcome` is `EXITED`
  (validator).
- `signal_name` (`str | None`): the platform signal that ended the process. Set exactly when
  `outcome` is `SIGNALED` (validator); max 16.
- `duration_s` (`float`): seconds from start to end. At least 0.
- `size_bytes` (`int | None`): total bytes written or sent. Set exactly when `request_kind` is
  `PUT_FILE` or `GET_FILE` (validator); at least 0.
- `sha256` (`str | None`): digest of the whole file written or read. Set exactly when
  `request_kind` is `PUT_FILE` or `GET_FILE` (validator).
- `reason` (`str`): why it ended, in words.

#### SessionPutFile

Write one chunk of a file onto the device. Chunks are events keyed by `transfer_id`, the envelope
id of the chunk at offset 0 (which carries its own id there); the final chunk closes the
transfer, which is answered by one `SessionExit` with outcome `COMPLETED` correlated to
`transfer_id`, or by a `control.error` at any chunk, after which the packet deletes its
temporary file. Two Workers writing the same path therefore never interleave.

- `lease_id` (`LeaseId`): the session the write belongs to.
- `transfer_id` (`MessageId`): the group key: the first chunk's envelope id, echoed on every
  chunk of the transfer, minted by the sender before the first chunk is wrapped.
- `total_bytes` (`int`): length of the whole file, so the packet can pre-check disk and the
  lease's cap before writing. At least 0; `offset` plus the chunk length never exceeds it
  (validator).
- `path` (`str`): destination, absolute or relative to scratch; outside scratch the packet checks
  the lease's allowed roots and access level and records the touch. Max `MAX_PATH_CHARS`.
- `chunk` (`bytes`): file bytes. Max `MAX_CHUNK_BYTES`.
- `offset` (`int`): byte offset in the file. At least 0.
- `final` (`bool`): true on the last chunk; the packet then verifies the digest and renames its
  temporary file into place. Default false.
- `sha256` (`str | None`): digest of the whole file. Required when `final`, None otherwise
  (validator).
- `is_executable` (`bool`): set the executable bit on completion (ignored on Windows). Default
  false.
- `task_id` (`TaskId | None`): the task this write serves.
- `worker_id` (`WorkerId | None`): the sub-bee on whose behalf it is written.

#### SessionGetFile

Read a file from the device; the packet streams it back as `SessionOutput` `FILE` chunks and ends
with a `SessionExit` `COMPLETED` carrying size and digest.

- `lease_id` (`LeaseId`): the session the read belongs to.
- `path` (`str`): source, absolute or relative to scratch; checked against the access level's
  readable roots. Max `MAX_PATH_CHARS`.
- `max_bytes` (`int`): refuse (`control.error`) rather than stream a file larger than this. At
  least 1; default `DEFAULT_MAX_OUTPUT_BYTES`.
- `task_id` (`TaskId | None`): the task this read serves.
- `worker_id` (`WorkerId | None`): the sub-bee on whose behalf it is read.

#### SessionClose

Close the lease's session: the packet kills every exec still running in it (each gets a
`SessionExit` `KILLED`) and stops accepting session traffic for that lease. The lease itself is
released by `cell.lease_released`, not here.

- `lease_id` (`LeaseId`): the session to close.
- `reason` (`str`): why the session closes.

### 8.7 honey (`messages/honey/`)

Knowledge in and out of the Honey Store, the persistent knowledge base: Nectar (raw, unprocessed
findings a bee brings back) goes in with provenance and clearance; Honey (distilled, indexed
knowledge) comes out on query, filtered by scope and clearance. Nothing retrieved is ever
executed; it reaches a model delimited and labelled as untrusted content.

Family enums and value models:

- `NectarKind`: `FINDING`, `TRANSCRIPT`, `TOOL_RESULT`, `PATROL_SUMMARY`, `AUDIT_FINDING`,
  `FLIGHT_RECORDING`, `HANDOFF` (a Handoff document, so every checkpoint reaches the Queen's
  Bee Bread and a task can resume on any Cell), `RIPENED_HONEY` (Honey a Cell ripened on its
  own local slots, the one kind that survives a `NIGHT_VEIL` teardown). Ripening (turning
  Nectar into Honey) picks its pipeline by kind. `honey.nectar_deposit` is the one kind
  permitted to carry transcript bytes; the test that no transcript travels up the tree
  whitelists it.
- `HoneyProvenance`: where a hit came from.
  - `task_id` (`TaskId | None`), `cell_id` (`CellId | None`), `bee`
    (`WorkerId | WardenId | None`), `observed_at` (`datetime`).
- `HoneyHit`: one retrieval result.
  - `honey_ref` (`str`): the store's row key, also its browser path. Max 256.
  - `title` (`str`, max 200), `excerpt` (`str`, max 2000), `score` (`float`, 0 to 1).
  - `scope` (`str`): pattern `^(hive|cell:[^:]+|bee:[^:]+|task:[^:]+)$`, max 64.
  - `clearance` (`HoneyClearance`), `origin_tier` (`CombShieldLevel`: `NIGHT_VEIL` marks Honey
    that crossed that boundary), `provenance` (`HoneyProvenance`).

#### NectarDeposit

Deposit raw Nectar with mandatory provenance and clearance, chunked and content-addressed by the
sha256 of the whole content so intake can hash, dedupe and reassemble.

- `sha256` (`str`): digest of the complete content; with the envelope `sender` it is the key
  every chunk of one deposit shares, and alone it is the dedupe key of the completed row at
  intake.
- `kind` (`NectarKind`): what sort of finding.
- `media_type` (`str`): MIME type of the content. Min 1, max 128.
- `title` (`str`): a one-line label for the browser and the ripener. Max 200.
- `task_id` (`TaskId | None`): the task it came from; None for a Patrol or watch summary.
- `cell_id` (`CellId`): the Cell where it was gathered.
- `worker_id` (`WorkerId | None`): the Worker that gathered it; None when a Warden deposits.
- `observed_at` (`datetime`): when the finding was observed, not when this chunk was sent.
- `clearance` (`HoneyClearance`): assigned from provenance; intake may raise it, never lower it.
- `origin_tier` (`CombShieldLevel`): the tier of the Cell it came from as the sender believes
  it; intake sets the stored tier from the Queen's record for `cell_id`, never from this field
  (receiver rule). From a `NIGHT_VEIL` Cell intake accepts only `RIPENED_HONEY` at `C0` or
  `C1`, keys every other deposit to the Cell's ephemeral segment, and refuses
  `swarm.trail_segment_sync` entirely (`hive.trail.night_veil_segment`), so the Night Veil
  boundary of codingrules section 12 holds on the wire. `RIPENED_HONEY` with `origin_tier`
  `NIGHT_VEIL` requires `clearance` `C0` or `C1` (validator).
- `event_id` (`EventId | None`): for `HANDOFF`, the `memory.checkpoint` trail event the
  Handoff was recorded under; None for every other kind (validator).
- `chunk` (`bytes`): this slice of the content. Max `MAX_CHUNK_BYTES`.
- `offset` (`int`): byte offset within the content. At least 0.
- `total_bytes` (`int`): length of the complete content, so intake can pre-check its cap. At
  least 1.
- `final` (`bool`): true on the last chunk; intake then verifies `sha256`.

#### HoneyQuery

Search Honey by text within scopes, under a hit count, a token budget and a clearance ceiling.

- `text` (`str`): the query. Min 1, max 2000.
- `requester` (`WorkerId | WardenId`): the bee asking, so scope, clearance and tier filtering
  key on it and not on the Warden that relays the query. Equals the envelope `sender` on the
  first hop (receiver rule).
- `scopes` (`tuple[str, ...]`): scopes to search; empty means every scope the caller may read.
  Max 16, each max 64, matching the scope pattern (as `HoneyHit.scope`).
- `max_hits` (`int`): upper bound on hits. Between 1 and 50, default 10.
- `max_tokens` (`int`): result token budget. At least 1.
- `max_clearance` (`HoneyClearance`): the highest label the caller wants back; the store caps
  further by the caller's capabilities and tier policy.
- `task_id` (`TaskId | None`): the task the query serves; None when no task is involved (a
  Patrol, or the Queen's or Warden's own work).

#### HoneyResponse

The hits that survived scope, clearance and budget filtering, with counts and a reason so the
reader knows what was withheld without seeing it.

- `hits` (`tuple[HoneyHit, ...]`): ranked results. Max 50.
- `token_count` (`int`): tokens the hits occupy. At least 0.
- `is_truncated` (`bool`): true when the budget or `max_hits` cut ranked results.
- `filtered_count` (`int`): ranked results withheld by scope, clearance or tier policy. At
  least 0.
- `reason` (`str`): why the result is what it is.

### 8.8 tool (`messages/tool/`)

Tools from request to call: a bee asks for a capability it lacks, the Royal Jelly Lab (the
tool-authoring pipeline) scaffolds and quarantines it, the Comb Registry (the catalogue of
promoted tools) announces the promotion, and calls run through the Cell's Warden. Tool schemas,
arguments and outputs are inherently open JSON, so this family is the one sanctioned use of JSON
text on the wire: each such field is bounded and must parse as a JSON object (or value, for
output), and is validated against the tool's schema at the edge, never handled as a mapping in
transit. No tool code or package ever crosses Waggle; only a digest does.

Family enums:

- `ToolScope`: `HIVE` (promoted by the Queen, visible everywhere), `CELL` (promoted by a Warden
  for its own Cell).
- `QuarantinePath`: `SANDBOX_CELL` (fully exercised in the sandbox Virtual Cell), `REAL_CELL`
  (static checks in the sandbox, tests in a lease's scratch on a Real Cell of the target
  platform).
- `ToolInvokePurpose`: `WORK` (an ordinary granted call), `QUARANTINE` (a test run under the
  quarantine capability with the flight recorder on).
- `ToolOutcome`: `SUCCEEDED`, `FAILED`, `DENIED` (schema, capability or OS re-validation refused
  it before it ran), `TIMED_OUT`.

#### ToolRequest

Ask for a capability the requester lacks, so the Royal Jelly Lab scaffolds, quarantines and
promotes a tool for the target Cell's platform. A refusal is a `control.error`.

- `tool_id` (`ToolId`): minted by the first requester; names the tool through every stage and
  on the eventual `ToolPromoted`.
- `warden_id` (`WardenId`): the Warden responsible for the requesting Cell.
- `worker_id` (`WorkerId | None`): the Worker whose task needs it, when a Worker started the
  request.
- `task_id` (`TaskId | None`): the task blocked on the missing capability.
- `cell_id` (`CellId`): the Cell the tool must run on.
- `scope` (`ToolScope`): requested visibility.
- `name` (`str`): desired tool name, the capability string `tool:<name>` once promoted. Pattern
  `^[a-z][a-z0-9_]{0,63}$`.
- `description` (`str`): what the tool must do, the scaffolder's brief. Min 1, max 4000.
- `required_capabilities` (`tuple[str, ...]`): capability strings the tool will need; anything
  beyond the requester's own set is rejected. Max 32, each max 128.
- `platform` (`PlatformReport`): the target Cell's platform.
- `reason` (`str`): why the capability is needed now.

#### ToolPromoted

Announce that a tool version passed the Quarantine Comb (the sandbox every new tool must pass) and
is now in the Comb Registry at a scope, so Workers load it and the requester unblocks. A rollback
is a promotion of an earlier version naming the one it displaces.

- `tool_id` (`ToolId`): the promoted tool.
- `name` (`str`): the registry name. Pattern as `ToolRequest.name`.
- `version` (`str`): the immutable version promoted. Pattern `^\d+\.\d+\.\d+$`, max 32.
- `scope` (`ToolScope`): visibility, set at promotion.
- `cell_id` (`CellId | None`): the one Cell whose bees may see a `CELL`-scope tool. Required
  exactly when `scope` is `CELL` (validator).
- `description` (`str`): the model-facing description. Max 4000.
- `input_schema_json` (`str`): the input schema as JSON Schema text. Max 16384; must parse as a
  JSON object (validator).
- `required_capabilities` (`tuple[str, ...]`): capabilities a caller must hold. Max 32, each
  max 128.
- `supported_os` (`tuple[OsFamily, ...]`): platforms the tool was written and quarantined for.
  Min 1, members unique.
- `quarantine_path` (`QuarantinePath`): which route proved it.
- `quarantined_on` (`CellId`): the Cell the quarantine report came from.
- `quarantine_passed_at` (`datetime`): when the report passed.
- `package_sha256` (`str`): digest of the immutable package in the registry, so a loader
  verifies what it installs by whatever channel delivers it. The sha256 pattern.
- `package_size_bytes` (`int`): size of that package. At least 1.
- `promoted_by_warden` (`WardenId | None`): the promoting Warden for a `CELL`-scope tool; None
  when the Queen promoted at `HIVE` scope. Required exactly when `scope` is `CELL` (validator).
- `supersedes_version` (`str | None`): the version this promotion replaces as current. Pattern
  as `version`.
- `reason` (`str`): why it was promoted, the Queen's review note included.

#### ToolInvoke

Run one promoted (or quarantining) tool version on a Cell with a JSON argument document, through
that Cell's Warden and its session.

- `tool_id` (`ToolId`): the tool to run.
- `version` (`str`): the exact version. Pattern as `ToolPromoted.version`.
- `cell_id` (`CellId`): the Cell whose Warden runs it; must be the recipient Warden's Cell
  (receiver rule).
- `task_id` (`TaskId | None`): the task the call serves; None for a quarantine run.
- `worker_id` (`WorkerId | None`): the calling Worker, whose capability set is checked; None
  when the Queen drives a quarantine run. When the envelope `sender` is a Worker, `worker_id`
  must equal it and `purpose` must be `WORK`; `QUARANTINE` and a None `worker_id` are accepted
  only from a `hive` sender (receiver rule), so no Worker can borrow the quarantine capability.
- `purpose` (`ToolInvokePurpose`): work or quarantine.
- `arguments_json` (`str`): the call's input as a JSON object document matching the tool's
  schema. Max 65536; must parse as a JSON object (validator).
- `timeout_s` (`float`): seconds before the run is killed and reported `TIMED_OUT`. Greater
  than 0.
- `reason` (`str`): why this tool is called now.

#### ToolResult

The outcome of one `ToolInvoke`: its bounded JSON output or a stable error code, with duration.

- `tool_id` (`ToolId`): the tool that ran.
- `version` (`str`): the version that ran. Pattern as `ToolPromoted.version`, max 32.
- `outcome` (`ToolOutcome`): the result.
- `output_json` (`str`): the output as JSON text; empty when there is none. Max 131072; the
  Warden truncates with `is_truncated` until the frame fits.
- `is_truncated` (`bool`): true when the output was cut at the cap; the Warden keeps the whole
  as Nectar. Default false.
- `clearance` (`HoneyClearance`): the label of `output_json`, from the task's clearance.
- `error_code` (`str | None`): a stable `hive.*` code for a failure or denial. Set exactly when
  `outcome` is not `SUCCEEDED` (validator); max 128.
- `duration_s` (`float`): wall-clock seconds. At least 0.
- `reason` (`str`): the explanation of a non-success, or a one-line summary on success.

### 8.9 capping (`messages/capping/`)

The Capping gate: every action with a side effect outside a lease's scratch directory is proposed
with its risk tier and the postconditions the bee expects, checked cheapest-first, applied,
verified and rolled back on failure. The proposer never verifies its own work; its Warden runs
the gate. A proposal is identified by the `MessageId` of its `capping.proposal_submitted`
envelope.

Family enums and value models:

- `RiskTier`: `READ_ONLY`, `SCRATCH_WRITE`, `OUTSIDE_SCRATCH_WRITE`, `NETWORK_EGRESS`, `SPEND`,
  `DEVICE_COMMAND`, `IRREVERSIBLE`. Which check ladder applies and whether a snapshot precedes
  apply.
- `ActionKind`: `DIFF`, `COMMAND`, `ACTION_SEQUENCE`.
- `ProposedAction`: the action, in a shape deterministic checks can read.
  - `kind` (`ActionKind`), `summary` (`str`, max 1000), `diff` (`str | None`, max 131072; a
    larger diff is written to scratch with the session and referenced by `paths` plus
    `diff_sha256`), `diff_sha256` (`str | None`: the sha256 pattern; set exactly when `diff` is
    None for a `DIFF` action, validator), `command` (`tuple[str, ...]`: argv, max 64 items
    each max 4096; non-empty only for `COMMAND`), `cwd` (`str | None`, max `MAX_PATH_CHARS`),
    `paths` (`tuple[str, ...]`: paths touched, max 64 each max `MAX_PATH_CHARS`), `steps`
    (`tuple[str, ...]`, max 100 each max 1000; non-empty only for `ACTION_SEQUENCE`). The
    field matching `kind` must be populated, and the characters across `summary`, `diff`,
    `command`, `paths` and `steps` total at most 262,144 (validator).
- `CheckKind`: `SCHEMA`, `LINT`, `TYPES`, `ALLOWLIST`, `SIZE_CAP`, `SANDBOX_TESTS`, `JUDGE`,
  `HUMAN`. The rungs of the ladder, cheapest first.
- `CheckOutcome`: `PASSED`, `FAILED`, `CHANGES_REQUESTED` (judge and human only).
- `VerdictOutcome`: `VERIFIED`, `REJECTED`, `ROLLED_BACK`, `CHANGES_REQUESTED`.
- `RollbackMethod`: `SNAPSHOT`, `REVERSE_DIFF`, `NONE` (nothing possible: the documented no-op
  on Real Cells).

#### ProposalSubmitted

A bee proposes an action with a side effect: what it will do, its risk tier, the postconditions
it expects to hold afterwards, the task's Tempo and why. The reply is the gate's `Verdict`.

- `task_id` (`TaskId`): the task the action serves.
- `cell_id` (`CellId`): the Cell the action runs on.
- `proposer` (`WorkerId`): the bee proposing. Equals the envelope `sender` (receiver rule), and
  the Warden rejects a `capping.check_result` whose `sender` is the proposer.
- `risk_tier` (`RiskTier`): the declared tier; checks may raise it, never lower it.
- `action` (`ProposedAction`): the diff, command or sequence.
- `postconditions` (`tuple[Postcondition, ...]`): assertions stated before acting. Max 32;
  may be empty only for `READ_ONLY`; total characters at most 65,536 (validator).
- `tempo` (`Tempo`): the task's Tempo as the proposer knows it; the Warden takes the Tempo
  from its own `TaskAssign` for `task_id` and rejects a differing one (receiver rule), so a
  bee never shortens its own check ladder.
- `spend_estimate` (`float`): expected spend; non-zero exactly for the `SPEND` tier
  (validator). At least 0, default 0.
- `clearance` (`HoneyClearance`): the label of the action's text, from the task's clearance.
- `reason` (`str`): why the bee wants to do this.

#### CheckResult

One rung of the check ladder finished for a proposal: which check, its outcome, why, and how long
it took. Checkers report to the Warden running the gate, which forwards to the Queen for the
trail.

- `proposal_id` (`MessageId`): the proposal this check belongs to.
- `task_id` (`TaskId`): the task, so the trail links without the proposal record.
- `cell_id` (`CellId`): the Cell the proposal runs on, so the Queen's trail writer can key the
  event to a `NIGHT_VEIL` Cell's ephemeral segment without a lookup.
- `check` (`CheckKind`): which rung ran.
- `outcome` (`CheckOutcome`): the result.
- `reason` (`str`): the checker's one-line reason.
- `detail` (`str`): bounded findings: lint output, failing test names, the judge's structured
  reasons. Max 8000, default empty.
- `clearance` (`HoneyClearance`): the label of `detail`.
- `duration_s` (`float`): wall-clock seconds. At least 0.

#### Verdict

The gate's final word on a proposal: verified (applied and postconditions held), rejected, rolled
back, or changes requested, with the reason and what failed.

- `proposal_id` (`MessageId`): the proposal.
- `task_id` (`TaskId`): the task.
- `cell_id` (`CellId`): the Cell the proposal ran on.
- `outcome` (`VerdictOutcome`): the terminal state the proposal reached.
- `reason` (`str`): why, in one line.
- `failing_check` (`CheckKind | None`): the rung that rejected or requested changes. Set exactly
  when `outcome` is `REJECTED` or `CHANGES_REQUESTED` (validator).
- `failing_postcondition` (`int | None`): index of the first postcondition that did not hold.
  Set exactly when `outcome` is `ROLLED_BACK` (validator); between 0 and 31.

#### PostconditionResult

One postcondition, or one of a task's acceptance criteria, was checked after apply by someone
other than the proposer: did it hold, and what was observed. With `proposal_id` None this is the
acceptance-check result for `task.assign`.

- `proposal_id` (`MessageId | None`): the proposal; None for an acceptance criterion.
- `task_id` (`TaskId`): the task whose proposal or acceptance is checked.
- `cell_id` (`CellId`): the Cell the check ran on.
- `index` (`int`): position in the proposal's postconditions or the task's acceptance list.
  Between 0 and 31.
- `kind` (`PostconditionKind`): repeated so the trail row stands alone.
- `has_held` (`bool`): whether the assertion held.
- `observed` (`str`): what was actually found. Max 4000, default empty.
- `clearance` (`HoneyClearance`): the label of `observed`.
- `reason` (`str`): the checker's reason; on failure, the failing assertion attached to the
  Alarm.

#### RollbackDone

An applied proposal was rolled back after a postcondition failed: by which method, whether
completely, and what residue remains.

- `proposal_id` (`MessageId`): the proposal.
- `task_id` (`TaskId`): the task.
- `cell_id` (`CellId`): the Cell rolled back.
- `method` (`RollbackMethod`): how.
- `snapshot_id` (`str | None`): the snapshot restored. Set exactly when `method` is `SNAPSHOT`
  (validator); max 128.
- `is_complete` (`bool`): true when the Cell is back to its pre-apply state.
- `residual_paths` (`tuple[str, ...]`): paths that could not be restored; empty exactly when
  `is_complete` (validator). Max 64, each max `MAX_PATH_CHARS`.
- `reason` (`str`): why the rollback happened and, if incomplete, why.

### 8.10 swarm (`messages/swarm/`)

Devices joining and living in the Swarm: enrolment with a one-time token, the gateway's heartbeat,
promotion of a colonized device (one whose Warden lives on it) to a Nuc and back, and the merge of
an offline Warden's local Pheromone Trail segment on reconnection.

Family enums and value models:

- `NodeKey`: one trusted signer.
  - `node_id` (`NodeId`), `public_key_hex` (`str`: raw 32-byte Ed25519 key, pattern
    `^[0-9a-f]{64}$`).
- `RuntimeLevel`: `GATEWAY` (level 0: packet only, Warden on the Hive Stand), `COLONIZED`
  (level 1: Warden and sub-bees on the device), `NUC` (level 2: plus a model server).
- `NucAction`: `PROMOTE`, `DEMOTE`. Demotion rides `swarm.nuc_promote`; there is no separate
  kind.
- `NucOutcome`: `PROMOTED`, `DEMOTED`, `FAILED`.

#### EnrolRequest

Present a one-time invite token, the packet's signing key and the device's platform,
capabilities and capacity to join the Swarm as a Real Cell. The token is sent exactly once, here,
and never echoed; a denial is a `control.error` with code `hive.swarm.enrol_denied`.

- `invite_token` (`str`): the single-use token from `hive swarm invite`. A secret: redacted from
  logs and the trail, and this message is never appended to an outbox (it is sent only on a
  live connection; a failed enrolment is retried by minting a new invite). Min 16, max 128.
- `device_id` (`DeviceId`): minted by the packet at install; its bee address for life. The
  receiver rejects a mismatch with the envelope's `sender`.
- `node_id` (`NodeId`): the packet's node, whose key follows. The receiver rejects a mismatch
  with the envelope's `node_id`.
- `public_key_hex` (`str`): the packet's raw 32-byte Ed25519 public key, hex. Pattern
  `^[0-9a-f]{64}$`.
- `hostname` (`str`): the device's hostname. Max 253.
- `runtime_version` (`str`): the `pollen` package version. Max 32.
- `platform` (`PlatformReport`): the report every later decision about the device reads.
- `capabilities` (`CellCapabilitiesReport`): what the device can do.
- `capacity` (`HostCapacityReport`): the device's host capacity.
- `max_sub_bees` (`int | None`): an operator-configured cap on concurrent sub-bees; the Queen's
  ceilings may only lower it. At least 0 when set.
- `requested_access` (`AccessLevel`): the level the packet asks for; the operator may grant
  less. Default `FULL`.

#### EnrolAccept

Admit the device: its Cell id, the Hive key and trusted node keys, its Warden, the granted access
level and Comb Shield tier, its capability set and the limits it must persist. The packet keeps
dialling the address it enrolled through; only a `QueenMoved` changes it.

- `device_id` (`DeviceId`): the device admitted.
- `cell_id` (`CellId`): the Real Cell this device now is.
- `hive_id` (`HiveId`): the Queen's bee address, the recipient of everything the packet sends up.
- `hive_key_hex` (`str`): the Hive key (raw 32-byte Ed25519 public key, hex) that signs
  `QueenMoved`. Pattern `^[0-9a-f]{64}$`.
- `trusted_nodes` (`tuple[NodeKey, ...]`): node ids and keys whose signatures the packet
  accepts; seeds its verifier. Min 1, max 16, node ids unique.
- `warden_id` (`WardenId`): the Cell's Warden; the packet accepts session and lease traffic only
  from it or the Queen.
- `access_level` (`AccessLevel`): the level granted. At most `requested_access` (receiver
  rule).
- `comb_shield` (`CombShieldLevel`): the operator-set tier. Never `NIGHT_VEIL` (validator).
- `granted_capabilities` (`tuple[str, ...]`): the node's capability strings, checked locally on
  every request. Max 64, each max 128.
- `offline_limit_s` (`float`): the dead-man limit for a device with no Warden on it. Greater
  than 0.
- `heartbeat_interval_s` (`float`): how often the packet sends `DeviceHeartbeat`. Greater than
  0.
- `reason` (`str`): why admitted at this level and tier.

#### DeviceHeartbeat

The gateway's liveness report: its runtime level, open leases, Hive-started process count and
outbox depth. Live capacity never rides here; a changed figure is a `forage.capacity_report`
with `trigger` `CHANGED`.

- `device_id` (`DeviceId`): the reporting device.
- `level` (`RuntimeLevel`): which rung the device is on, so the Queen knows whether the
  dead-man switch or an on-device Warden handles link loss.
- `lease_ids` (`tuple[LeaseId, ...]`): leases the packet holds open. Max 64.
- `hive_started_processes` (`int`): processes its leases started and still track. At least 0.
- `outbox_pending` (`int`): frames waiting in the packet's outbox. At least 0.
- `uptime_s` (`float`): seconds since the packet started, so a restart is visible. At least 0.

#### NucPromote

Order a colonized device's Warden to start (or stop) a local model server within the ceilings and
hosting plan already set, making the device a Nuc (or an ordinary colonized Cell again).

- `cell_id` (`CellId`): the Cell promoted or demoted; must be the recipient Warden's Cell.
- `device_id` (`DeviceId`): the device behind that Cell.
- `action` (`NucAction`): promote or demote.
- `server_id` (`str | None`): the manifest key of the local model server definition to start;
  its kind and address come from the manifest, never the wire. Required for `PROMOTE`, None
  for `DEMOTE` (validator); max 128, as `ModelServerReport.server_id`.
- `initial_models` (`tuple[str, ...]`): Forage map model ids to load first, each within the
  ceilings' allowlist and VRAM. Max 32, each max 128; empty for `DEMOTE` (validator).
- `deadline_s` (`float`): seconds to bring the server up (or down) before replying `FAILED`.
  Greater than 0.
- `reason` (`str`): the Queen's decision.

#### NucPromoted

Report the outcome of a promotion or demotion: the device's new level, its server and the models
now loaded. The detailed sources follow as a `forage.capacity_report`.

- `cell_id` (`CellId`): the Cell.
- `device_id` (`DeviceId`): the device behind it.
- `action` (`NucAction`): echo of the request.
- `outcome` (`NucOutcome`): `PROMOTED` only with `PROMOTE`, `DEMOTED` only with `DEMOTE`
  (validator).
- `level` (`RuntimeLevel`): the rung after the attempt.
- `server_id` (`str | None`): the server now running. Set exactly when `outcome` is `PROMOTED`
  (validator); max 128.
- `loaded_models` (`tuple[str, ...]`): model ids now served. Max 32, each max 128; empty unless
  `outcome` is `PROMOTED` (validator).
- `reason` (`str`): what happened, especially why a promotion failed.

#### TrailSegmentSync

Ship one chunk of an offline Warden's local Pheromone Trail segment so the Queen merges it
idempotently on reconnection. The bytes are the trail's own export format, opaque to Waggle.

- `node_id` (`NodeId`): the node whose segment this is. Must equal the envelope's `node_id`,
  and `warden_id` the envelope's `sender` (receiver rule, answered with `control.error`), so
  no Warden can sync a segment under another node's identity; the Queen dedupes by the
  envelope `node_id`, `first_event_id`, `last_event_id` and `sha256` together.
- `cell_id` (`CellId`): the Cell the Warden owns.
- `warden_id` (`WardenId`): the Warden that wrote it.
- `from_at` (`datetime`): timestamp of the segment's first event.
- `to_at` (`datetime`): timestamp of its last event. At least `from_at` (validator).
- `first_event_id` (`EventId`): first event in the segment; with `last_event_id` and `node_id`
  it identifies the segment so a replayed sync is deduplicated.
- `last_event_id` (`EventId`): last event in the segment.
- `event_count` (`int`): events in the whole segment, checked after merge. At least 1.
- `segment_format_version` (`int`): the export format version, so the merge can refuse an
  unknown layout. At least 1.
- `chunk` (`bytes`): a slice of the exported segment. Max `MAX_CHUNK_BYTES`.
- `offset` (`int`): byte offset within the export. At least 0.
- `total_bytes` (`int`): size of the whole export. At least 1.
- `final` (`bool`): true on the last chunk; the Queen then verifies and merges. Default false.
- `sha256` (`str | None`): digest of the whole export. Required when `final`, None otherwise
  (validator).

### 8.11 control (`messages/control/`)

`Ping`, `Pong`, `ErrorMessage`, `Shutdown`, `Cluster` and `Wake` live in `control/protocol.py`;
`HumanMessage`, `MaskOverride` and `QueenMoved`, the Hive-wide orders, in `control/hive.py`, a
split by responsibility that keeps each file under the size limit.

Protocol housekeeping and Hive-wide orders: liveness probes, the error reply, shutdown,
Clustering and waking, the human's messages, Pheromone Mask overrides (short-lived behaviour
overlays that make a bee write or move like a human) and the Hive Stand's relocation notice.

Family enums:

- `ClusterCause`: `PROVIDER_DOWN`, `COST_CAP`, `HUMAN`, `SUPERSEDURE`, `OFFLINE_LIMIT`.
- `MaskOverrideAction`: `FORCE`, `CLEAR`.
- `MaskTactic`: `WRITE_LIKE_HUMAN`, `MOUSE_LIKE_HUMAN`.

#### Ping

Probe a peer's liveness and round trip; it carries nothing because the envelope's `id` and
`sent_at` and the Pong's correlation give the measurement, and its `version` advertises the
sender's minor.

No fields.

#### Pong

Answer a Ping with the responder's arrival time so the pinger can estimate round trip and clock
skew.

- `received_at` (`datetime`): the responder's clock when the Ping arrived.

#### ErrorMessage

Report that a received message could not be processed, with a stable code, a full-sentence
message and whether the sender may retry (section 7).

- `code` (`str`): the stable code: `waggle.*` for protocol failures, `hive.*` or `pollen.*` for
  subsystem failures. Max 128, pattern `^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$`.
- `message` (`str`): a full sentence with the identifiers needed to debug; never a traceback.
  Min 1, max 2000.
- `failed_kind` (`str | None`): the kind of the message that failed, for when the sender no
  longer holds the correlated envelope. Pattern `^[a-z_]+\.[a-z_]+$`, max 64.
- `is_retryable` (`bool`): whether resending the original may succeed.

#### Shutdown

Order the recipient to stop, either gracefully by a deadline or immediately.

- `urgency` (`Urgency`): graceful (checkpoint, release leases, stop by the deadline) or
  immediate (kill now, no checkpoint).
- `deadline_s` (`float`): seconds allowed to checkpoint, release leases and exit. At least 0;
  must be 0 when `urgency` is `IMMEDIATE` (validator).
- `reason` (`str`): why the recipient is stopped.

#### Cluster

Tell a Warden to run the Clustering protocol for one provider or for all: checkpoint affected
sub-bees, pause their tasks, keep leases alive and heartbeats running. The provider is data here,
not a branch.

- `provider` (`str | None`): the manifest provider name whose bindings are unavailable; None
  means every provider (a full freeze). Max 64, pattern `^[a-z][a-z0-9_]*$`.
- `cause` (`ClusterCause`): why the Hive clusters.
- `reason` (`str`): the decision in prose.

#### Wake

End Clustering for one provider or for all so the Warden resumes its paused sub-bees from their
Handoffs.

- `provider` (`str | None`): the provider that returned; None means every provider. Max 64,
  pattern as `Cluster.provider`.
- `reason` (`str`): why the Hive wakes.

#### HumanMessage

Carry a free-text message from the human into the Queen's inbox, or on to the Warden of the task
it concerns, labelled `C2` by provenance.

- `text` (`str`): the message, typed or transcribed. Min 1, max 16000.
- `task_id` (`TaskId | None`): the task it concerns; None addresses the Queen at large.
- `device_id` (`DeviceId`): the enrolled device the human spoke from; kept in the payload so a
  relayed copy still names it.
- `clearance` (`Literal[HoneyClearance.C2]`): always `C2`: a human's words are personal by
  provenance, and a program device cannot launder them lower.

#### MaskOverride

Force a Pheromone Mask tactic at Cell scope with a reason and expiry, or clear it, for the Warden
to enforce on the Cell's sub-bees while active.

- `cell_id` (`CellId`): the Cell the override applies to.
- `action` (`MaskOverrideAction`): force or clear.
- `tactics` (`tuple[MaskTactic, ...]`): the tactics forced. Max 2, unique; non-empty exactly
  when `action` is `FORCE` (validator).
- `expires_at` (`datetime | None`): when the forced override ends on its own. Required exactly
  when `action` is `FORCE` (validator).
- `reason` (`str`): why the Queen forces or clears it.

#### QueenMoved

Announce that the Hive Stand has moved: the new address and signing node, when it takes effect and
how long both addresses are honoured. Signed with the Hive key (section 6); a recipient replaces
the Queen's address only on a notice that key verifies. This is the one address that crosses
Waggle.

- `sequence` (`int`): strictly increasing per Hive across every notice, rollbacks included; a
  recipient persists the highest accepted value and ignores a lower or equal one, so a
  replayed or stale notice can never redirect it. At least 1.
- `new_address` (`str`): the new Queen's Waggle endpoint, a WebSocket URI (an onion host for a
  Night Veil link). Max 512, pattern `^wss?://\S+$`; `ws://` only with a loopback host
  (validator).
- `new_node_id` (`NodeId`): the new Hive Stand's node id, whose key signs every envelope from
  the new Queen.
- `new_node_public_key_hex` (`str`): the new node's raw 32-byte Ed25519 public key, hex.
  Pattern `^[0-9a-f]{64}$`. On accepting a notice the recipient pins it for `new_node_id`,
  treats it as the Hive key from `grace_until`, and drops the old key then, so the machine
  that steps down keeps no authority.
- `effective_at` (`datetime`): when recipients start connecting to the new address.
- `grace_until` (`datetime`): until when both addresses are honoured. At least `effective_at`
  (validator).
- `is_rollback` (`bool`): true when the old Queen points recipients back at itself after a
  failed handover. Default false.
- `reason` (`str`): why the move, or the rollback cause.

## 9. Transports

A transport is a policy-free carrier of frames: it moves the bytes the codec produced and
consumed, and every check (size, shape, version, kind, signature) happens in the codec.

The `Transport` protocol (`waggle/transport/base.py`):

- `async connect()`, `async send(envelope)`, `receive() -> AsyncIterator[Envelope]` (an async
  generator: `async for envelope in transport.receive()`), `async close()`.
- **Delivery guarantee: at-most-once** at this layer, with ordering preserved within one
  connection. Retries and durability belong to the caller through the outbox (section 10).
- `send` after `close` raises `waggle.transport.closed`; `send` or `receive` on a dropped link
  raises `waggle.transport.connection_lost`; `receive` ends normally on a clean close.
- A frame that fails to decode makes `receive` raise the `CodecError` or `SignatureError` and
  the transport closes the connection: a bad frame from a peer is a bug or an attack, never a
  flaky link, and reconnect plus outbox replay handles the aftermath.
- The one exception is `waggle.codec.invalid_payload` (a readable `id`, section 7): `receive`
  raises it, the connection stays open, and calling `receive()` again continues on the same
  connection, so the inbox can answer with a `control.error` correlated to that id.
- `close` is idempotent.

`MemoryTransport` (`waggle/transport/memory.py`) is the in-process pair used by tests and by the
Warden and Workers that run inside the Queen's process. `MemoryTransport.pair(codec_a, codec_b)`
returns two connected ends that exchange **bytes** through their codecs, not objects, so size and
malformed-frame checks are real and the memory transport is a faithful stand-in for the WebSocket
one. `inject_frame(frame)` delivers a raw frame to one end as if the peer had sent it (a
documented test hook that ships); `drop()` simulates link loss on both ends.

`WebSocketTransport` (`waggle/transport/websocket.py`) wraps exactly one `websockets`
connection, client or server side, binary frames only; a text frame is `waggle.codec.malformed`.

- `WebSocketClientTransport(uri, codec, clock, *, max_attempts, ...)` dials with capped
  exponential backoff through the injected clock: `RECONNECT_INITIAL_S = 0.5`,
  `RECONNECT_FACTOR = 2.0`, `RECONNECT_MAX_S = 30.0`, plus an `open_timeout`; it raises
  `waggle.transport.connect_failed` after `max_attempts`. After a drop the caller calls
  `connect()` again and then replays its outbox.
- `WebSocketServer(host="127.0.0.1", port=0, codec, ...)` offers `start()`, a `port` property,
  `connections() -> AsyncIterator[WebSocketTransport]` and `close()`; it owns its handler tasks
  (structured concurrency, no dropped tasks).
- Keepalive is the WebSocket ping: `PING_INTERVAL_S = 20.0`, `PING_TIMEOUT_S = 20.0`.
- Close codes: `1000` normal; `1002` protocol error (malformed frame, unsupported major, unknown
  kind); `1008` policy violation (missing, unknown or invalid signature); `1009` message too big.
- Where a Cell's tier routes its link through a VPN or Tor (a `NIGHT_VEIL` Cell reaches the Hive
  Stand through a hidden-service endpoint), only the URI and the network path differ; the
  transport and the protocol are unchanged.

## 10. Outbox and offline replay

The outbox (`waggle/outbox/`) is a durable, ordered queue of envelopes a node could not send,
replayed on reconnection. Offline Wardens and every Pollen Packet use it; it is what lets a
disconnected Warden keep working within what it owns and report everything afterwards.

- `Outbox(path, codec=Codec())` with `append(envelope)`, `pending() -> tuple[Envelope, ...]`
  (FIFO), `ack(message_id)` and `__len__`. Storage is one append-only JSONL file of records
  `{"op": "put", "frame": "<UTF-8 JSON of the UNSIGNED wire dict>"}` and
  `{"op": "ack", "id": "msg_..."}`, flushed and `fsync`ed per append. A torn trailing line is
  ignored on open; any other unreadable record is `waggle.outbox.corrupt`. The file is compacted
  to pending records on open. Appending an id already pending is a no-op. `append` encodes the
  envelope first and refuses one whose frame would exceed `MAX_FRAME_BYTES` minus
  `SIGNATURE_OVERHEAD_BYTES` (the bytes a signature adds) with `waggle.codec.too_large`, so a
  poison entry can never wedge the queue. `swarm.enrol_request` is never appended (section 8.10).
- The outbox stores **unsigned** envelopes. Replay re-encodes each through the sending
  transport's codec, so signing always happens at send time with the node's current key; the
  outbox is the node's own durable memory, not a trust boundary.
- `replay_outbox(outbox, transport) -> int` sends pending envelopes in order, acks each after a
  successful send, and stops at the first transport error leaving the rest pending. An entry
  whose encode raises a `CodecError` (a kind unregistered after an upgrade, a frame grown past
  the limit) is acked and reported through the replay's returned report rather than replayed
  forever. Replaying twice sends nothing the second time; a crash mid-replay leaves exactly the
  unsent tail. A client closed with code 1002 for `waggle.version.unsupported_major` stops
  reconnecting: no replay can fix a major mismatch.
- Replay preserves the original `id` and `sent_at`, so a receiver can tell a late message from a
  fresh one and dedupe by id. Requests replayed after their answer became moot draw a
  `control.error` with `is_retryable` false; the application drops them.
- The outbox acks on successful send, never on reply. Whether to keep waiting for a reply, or to
  drop a request whose `control.error` said `is_retryable` false, is the application's decision.
- What may wait in an outbox is bounded by policy above Waggle: a disconnected Warden queues
  results, Alarms, Nectar and Forage requests, but never grows (no new Cells, no new shared
  Forage, no human-bound questions) until it reconnects.

## 11. Conformance

- **Transport contract suite** (`packages/waggle/tests/contracts/test_transport_contract.py`),
  parametrised over `MemoryTransport` and `WebSocketTransport`: ordering within a connection,
  close semantics, oversized frame rejection, malformed frame rejection, signature rejection,
  and outbox replay after a dropped link.
- **Codec property tests** (hypothesis): encode/decode round trip for every registered kind with
  generated payloads; canonical bytes stable across key order; any single-byte mutation of a
  signed frame fails verification.
- **Outbox property tests**: FIFO order; idempotent replay; crash mid-replay leaves exactly the
  tail pending.
- **Spec drift test** (`tests/test_spec_drift.py`) parses the catalogue table of section 8 (every
  row `| \`kind\` | \`Class\` | shape | replies_to | ... |`) and asserts, in both directions, that
  the set of kinds equals `registry.all_kinds()`, that each row's class name equals
  `model_for(kind).__name__`, and that each row's shape and replies-to match the registry's
  `MessageSpec`. A kind added to either side without the other fails CI.
- **Dependency test** (`tests/test_dependencies.py`) AST-scans every module under `src/waggle`
  and asserts every non-stdlib import is one of `pydantic`, `websockets`, `cryptography` or
  `waggle`, so `pollen` can depend on `waggle` with nothing else installed.
- **Label sync tests** in `hivemind`: `cell.tiers` and `forage.tempo` mirror the enums of
  section 8.1 member for member, and every `ModelSlot` name matches the slot pattern of the
  conventions.
- **Echo demo** (`scripts/waggle_echo.py`): a server in one process and a client in another
  exchange `Ping`/`Pong` and a signed `TaskAssign`, reject a tampered envelope, and replay the
  client's outbox after the server is killed and restarted; `scripts/tests/test_waggle_echo.py`
  runs it as a slow test.

## 12. Resolved design questions

Each question the family designers raised, and how this specification settles it, followed by
the review findings applied before the catalogue was implemented.

Review outcomes (applied in the revision that first shipped the code):

- `swarm.node_updated` (Queen -> Pollen Packet: `access_level`, `comb_shield`,
  `granted_capabilities`, `trusted_nodes`, `warden_id`, `is_revoked`, `reason`, verified
  against `hive_key_hex` like `QueenMoved`) is reserved as an additive minor for phase 11, so a
  lowered access level, a revocation or a new on-device Warden node reaches the device; packets
  persist those values as replaceable. It is not registered in 1.0.
- The Night Veil boundary on the wire: for a message that originates on a `NIGHT_VEIL` Cell the
  Queen's trail writer keeps only ids (`task_id`, `alarm_id`, `proposal_id`, `cell_id`) and
  outcomes for `task.progress`, `task.result`, `supervision.alarm_raised`,
  `supervision.inspect_reply`, `capping.check_result`, `capping.verdict` and
  `capping.postcondition_result`, never their text fields, and derives the one
  `capping.summary` per tier from the Cell's ephemeral segment at teardown. `cell_id` was added
  to the three capping events so the writer needs no lookup to do it.
- `TaskOutcome.CLAIMED`, `SessionExit.request_kind`, `SessionPutFile.transfer_id` and
  `total_bytes`, `QueenMoved.sequence`, `CellTeardownRequest.cause`, `CellWaxProposed.proposer`,
  `HoneyQuery.requester`, `NectarDeposit.event_id`, `TaskResume.attempt`, `revision` on
  `GrantRevoked` and `HostingDecided`, `clearance` on every message that quotes data, the
  `Urgency` label, and the aggregate character bounds were all added on review so that no
  legal payload can exceed a frame and no lower level can widen its own authority.

Identity and ids:

1. **Question identity.** No question id kind exists. `Question.question_id` is a `MessageId`
   the asker mints with `new_message_id`, never an envelope's id, so it survives re-wrapping at
   each hop. Re-wrapping is allowed everywhere; relays are never forbidden.
2. **Proposal identity.** A proposal is the `MessageId` of its `capping.proposal_submitted`
   envelope; the submission carries no id field and every later capping message carries
   `proposal_id`. No `IdKind.PROPOSAL` in phase 1.
3. **Cell Wax id.** `wax_id` is a `str` with a `wax_` prefix and ULID pattern until an `IdKind`
   exists; adding one later is wire-transparent.
4. **Session identity.** A session is its `LeaseId`, one per lease; concurrency lives at the exec
   level, each exec keyed by its request's `MessageId`. No `SessionId` kind.
5. **Nectar identity.** `NectarDeposit` groups and dedupes by the content `sha256`; two identical
   deposits, `TRANSCRIPT` included, collapse into one Nectar row, which is exactly what intake's
   dedupe would do anyway.
6. **`HoneyHit.honey_ref`** stays a `str` row key; a typed id can follow in phase 7 without a wire
   change.
7. **Bee addresses in payloads.** The address check lives in `envelope.py` only. Payload fields
   that name a bee are typed with the id NewType(s) they may hold and validated with `parse_id`
   against those kinds; no family defines an address regex. `CellWaxWritten.proposer` and
   `HoneyProvenance.bee` were retyped from `str` to id unions for the same reason.

Shared shapes:

8. **Postconditions and acceptance criteria are one model.** `Postcondition` (`kind`, `subject`,
   `argv`, `expected`) and `PostconditionKind` live in `labels.py` and serve `task.assign` and
   `capping.proposal_submitted`. The two designers' kind sets are merged (`FILE_ABSENT` kept,
   the rubric member named `JUDGE_RUBRIC`).
9. **`PostconditionResult` is also the acceptance-check result** (`proposal_id` None). The task
   family therefore carries no per-criterion outcome list: `TaskResult.acceptance` and
   `AcceptanceOutcome` were dropped in favour of `checked_by` plus those events.
10. **`HandoffRef`** (task and supervision) lives in `labels.py`.
11. **`OsFamily`, `PlatformReport`, `CellCapabilitiesReport`, `GpuReport`,
    `HostCapacityReport`** (tool, swarm, cell, forage) live in `labels.py`. `PlatformReport` is
    the one home for OS and architecture; `CellCapabilitiesReport` carries neither, and
    `cell.ready` and `swarm.enrol_request` carry both models. `HostCapacityReport` takes the
    forage designer's shape (all figures required, a full snapshot).
12. **`LocalSourceReport` stays in `forage/capacity.py`.** `swarm.nuc_promoted` reports loaded model ids
    only; the seats, VRAM and measured speed follow in the `forage.capacity_report` a promotion
    always triggers, so no model-server value model crosses families.
13. **`TaskNeedsReport` and `IsolationNeed` stay in `cell/status.py`** because `task.assign` carries no
    needs in phase 1; a task inherits its tiers from the placed Cell.
14. **Shared bounds** live in `messages/base.py`: `MAX_REASON_CHARS`, `MAX_CHUNK_BYTES`,
    `MAX_PATH_CHARS`, `MAX_SLOT_CHARS` with the slot pattern, and the sha256 pattern. The
    designers' three names for the reason bound are unified as `MAX_REASON_CHARS`.
15. **One chunk size.** Every `bytes` field is capped at `MAX_CHUNK_BYTES` (256 KiB); the Nectar
    designer's 512 KiB was lowered to it so one rule fits every frame.
16. **Booleans read as predicates** (`is_`, `has_`, `can_`), so `truncated`, `restored`,
    `complete`, `held`, `passed`, `executable`, `stdin_open` and `shield_verified` became
    `is_truncated`, `is_restored`, `is_complete`, `has_held`, `has_passed`, `is_executable`,
    `has_stdin` and `is_shield_verified`. `final` is the fixed exception of the chunking rule.
17. **Slots travel as bounded `str`** (`MAX_SLOT_CHARS`, `^[A-Z][A-Z_]*$`), not as an enum
    mirror of `ModelSlot`; a `hivemind` test keeps the names in sync.
18. **Spend is a `float` in the manifest's currency** with no currency field; a multi-currency
    Hive would add one as an additive minor.
19. **`TaskOutcome` keeps its name** although `brood_chamber` will have a model of the same name:
    different packages, and the vocabulary is used literally by rule.

Registry and shapes:

20. **`replies_to` follows the brief's list exactly.** `task.cancel`, `task.pause`,
    `task.resume`, `supervision.alarm_resolved`, `control.wake` and `session.stdin` register no
    antecedent; their payloads carry the linking id and their envelopes may still set
    `correlation_id`.
21. **Every session request ends in exactly one `session.exit` or one `control.error`**,
    `session.open` and file transfers included; there are no `session.opened` or file-done
    kinds. `session.output` and `session.exit` register `session.exec` as their usual antecedent.
22. **`cell.request` and `cell.teardown_request` serve both directions** (Warden to Queen for
    placement and retirement, Queen to a gateway or device Warden for lease open and release);
    `cell.lease_opened` and `cell.lease_released` are their correlated answers.
23. **A Virtual Cell's tenancy is a lease on the wire too**, so `cell.lease_opened` serves both
    Cell kinds; the hivemind-side machine name is a naming matter, not a wire one.
24. **Demotion rides `swarm.nuc_promote`** with `action = DEMOTE`; there is no separate kind.
25. **Wax rejection is a `control.error`** with code `hive.memory.wax_rejected`; no
    `cell.wax_rejected` kind.
26. **`TaskAssign` carries no capabilities field**; the Warden attenuates its own set locally in
    phase 3, and a `capabilities` field is an additive minor later.
27. **`ForageRequestKind` keeps `SUB_BEES`**; the phase 4 exit criterion needs a Warden to ask
    for more sub-bees and be denied with a reason.
28. **`CellHeartbeat` carries no capacity figures**; live capacity travels only in
    `forage.capacity_report`, so one message owns capacity.
29. **`Heartbeat.children` is per-sub-bee, capped at 64.** The Queen never sets a
    `max_sub_bees` ceiling above the wire cap.

Wire, JSON and addresses:

30. **Tool schemas, arguments and outputs travel as bounded JSON text**, the one sanctioned use
    of open JSON on the wire, validated to parse as a JSON object (or value, for output) and
    against the tool's schema at the edge. A typed parameter model would lose nested schemas.
31. **URLs never cross Waggle** except the Queen's own address in `control.queen_moved`, which
    the brief fixes. `ModelServerReport.base_url` became `server_id`, `NucPromote.server_name`
    became `server_id` (a manifest key the receiver resolves), and `EnrolAccept.queen_address`
    was dropped: the packet keeps the address it enrolled through.
32. **The invite token is sent exactly once**, in `swarm.enrol_request`, and never echoed.
33. **Trail segments are opaque bytes** in the Pheromone Trail's own export format with a
    `segment_format_version`; Waggle cannot define a trail event record (layer rule), and typed
    records would reintroduce open JSON.
34. **Tool packages do not cross Waggle**; `tool.promoted` carries the digest and size, and phase
    9 may add a chunked `tool.package` kind or reuse `session.put_file` additively.
35. **Tool retirement has no kind**; a rollback is a `tool.promoted` of the earlier version with
    `supersedes_version`, and a plain retire is an additive `tool.retired` for phase 9.

Signing and versioning:

36. **Enrolment bootstrap** is trust on first use bound to the one-time token, as section 6
    describes: the enrolment frame is decoded without a verifier, then verified with the key
    inside its own signed payload once the token checks, and that key is pinned for the node.
37. **`EnrolAccept.warden_id` is assumed stable across migration** in phase 1. If phase 11 mints a
    new `WardenId` when a Warden moves onto the device, an additive swarm event carries it; the
    packet must not hard-pin one id.
38. **Adding an enum member is a minor bump**, with the sender-side rule of section 4: an older
    receiver rejects the member at validation, so a sender never uses a new member toward a
    peer that has not advertised the newer minor.
39. **The Hive key is the Hive Stand's node key** in phase 1; `EnrolAccept` still carries
    `hive_key_hex` and `trusted_nodes` separately so a later Hive keypair changes nothing on the
    wire.
