# Waggle protocol specification

Protocol version `1.15`. This document is the source of truth for every message the Hive's bees
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
- the complete message catalogue, eleven families and eighty-nine kinds (section 8);
- the transports (section 9), the offline outbox (section 10) and conformance (section 11);
- the design questions the catalogue settled and why (section 12).

Out of scope: model calls (they travel separately to whichever provider serves the model and never
ride on Waggle), the Landing Board's HTTP contract (`docs/entrance/openapi.json`), the Pheromone
Trail export format (the audit log's segments cross Waggle as opaque bytes, section 8.10), and the
enrolment ceremony around `swarm.enrol_request` ([`enrolment.md`](enrolment.md)).

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
| `version` | `str` | `"<major>.<minor>"`, pattern `^\d+\.\d+$`, default `"1.15"` |
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
- **Version.** `version` follows section 4; `PROTOCOL_VERSION = "1.15"`, `PROTOCOL_MAJOR = 1` and
  `PROTOCOL_MINOR = 15` are constants in `waggle/envelope.py`.
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
| `cell.lease_released` | `cell.teardown_request`, or the `supervision.intervene` (`RELEASE_LEASE`) it answers | Always when answering one |
| `tool.promoted` | `tool.request` | When a request exists |
| `capping.check_result` | `capping.proposal_submitted` | Always |
| `capping.postcondition_result` | `capping.proposal_submitted` | When it belongs to a proposal |
| `capping.rollback_done` | `capping.proposal_submitted` | Always |
| `cell.wax_written`, `cell.wax_cleared` | `cell.wax_proposed` | When a proposal exists |

Every other event has no registered antecedent and normally travels with `correlation_id` null;
the payload carries the identity that links it (`task_id`, `alarm_id`, `lease_id`, `grant_id`).

Session rule: every session request (`session.open`, `session.exec`, `session.get_file`,
`session.delete_file`, and a `session.put_file` transfer, whose identity is its `transfer_id`, the
first chunk's envelope id) ends in exactly one `session.exit` correlated to it or one
`control.error`; `session.output` chunks may precede that terminal message and never follow it.
A non-final `session.put_file` chunk draws no answer unless it fails. `session.stdin` carries the
exec's id in its payload and sets `correlation_id` to it. `session.process` is the one session
request that ends in no `session.exit`: its single answer is a `session.process_state` or a
`control.error`.

Relaying: when a message crosses more than one hop (`Queen -> Warden -> Worker`), each hop is a
fresh envelope with its own `id`, `sender`, `recipient` and `node_id`; the relaying bee re-wraps
the payload. Identity therefore lives in the payload: a task is its `task_id`, an Alarm (an issue
a bee cannot resolve, escalated up the chain) its `alarm_id`, a question its `question_id`, a
proposal its `proposal_id`, a session its `lease_id`. Correlation is always per hop.

First-hop identity (receiver rule): a payload field that names the sender (`proposer`,
`asked_by`, `device_id`, `warden_id`, `worker_id`, `requester`, `origin` and `holder` on the
hop that creates the message) must equal the envelope `sender`; a relaying bee preserves it.
Kinds only the Queen sends (`forage.grant_issued`, `forage.grant_revoked`,
`forage.hosting_decided`, `forage.ceilings_set`, `forage.plan_written`, `forage.source_evict`,
`swarm.nuc_promote`, `swarm.node_update`, `swarm.colonize`, `control.cluster`, `control.wake`,
`control.shutdown`, `control.mask_override` and `control.queen_moved`) are accepted only from a
`hive` sender, and a receiver drops a frame
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

Minor-version history of major 1 (each entry additive, so a peer at an older minor still
validates every message a newer one sends it, as long as the sender honours the rule above):

- **1.1**: `AlarmKind.QUOTA_EXCEEDED` (roadmap step 3.11).
- **1.2**: `Intervene.binding`, the `[llm.slots]` key a Queen-sent REBIND names.
- **1.3**: `TaskAssign.leaves` (roadmap step 5.0b, the plan declares what stays).
- **1.4**: `ActionKind.COPY` and `ProposedAction.copy_sha256`/`copy_size` (roadmap step 5.0e).
- **1.5**: the snapshot relay (`cell.snapshot_request`/`_reply`, `cell.rollback_request`/`_reply`)
  and `InterventionAction.RELEASE_LEASE` (roadmap steps 5.10 and 5.13).
- **1.6**: the Exoskeleton (roadmap phase 6, ADR-0031): `ActionKind.GUI` with its typed
  `GuiStep`, `PostconditionKind.URL_MATCHES`/`REGION_CHANGED`, `RollbackMethod.GUI_STATE`,
  `AlarmKind.EXOSKELETON_FAILED`, `CellCapabilitiesReport.real_display_allowed`,
  `TaskAssign.exoskeleton`/`network_scopes`/`recon` and `TaskResult.scout_report`.
- **1.7**: `TaskAssign.honey` (roadmap step 7.9, the Queen's Honey pre-check).
- **1.8**: `TaskAssign.capabilities` (roadmap step 10.3, ADR-0039): a goal's capability set
  reaches the Warden that attenuates its Worker's set to it and to the task's network needs
  (`TaskAssign.network_scopes`, already carried since 1.6).
- **1.9**: `AlarmKind.SECURITY`, `InterventionAction.QUARANTINE` and `Intervene.suspect_episode_id`
  (roadmap steps 10.6 and 10.6c, ADR-0043): a security Alarm always goes up to the Queen and on
  to the human, and the Queen may order a Warden to quarantine one of its sub-bees from the
  episode its memory is suspect from. A receiver that meets an `InterventionAction` it does not
  know refuses the whole frame (`waggle.codec.invalid_payload`); it never reads an unknown lever
  as a cancel, or as any other lever.
- **1.10** (both additive, roadmap steps 10.6 and 10.6a, ADR-0043):
  - `GrantIssued.audit_raises` and its value model `RaisedAuditRate`: every Capping audit-rate
    raise the Guard Bee has in force when a grant is issued travels on it, so the Warden holding
    the grant, a Virtual Cell's in-Cell Warden included, samples that tier at the raised rate
    until the raise lapses. A tier travels by name, not as an enum, so a receiver that does not
    know a tier ignores that raise rather than refusing the grant.
  - `cell.taint_order` (`CellTaintOrder`): when the Queen isolates a Cell she orders its Warden
    to run the taint setter on the memory store it keeps inside the Cell (the bees and tasks she
    names, from the instant she names, caused by her `cell.isolated` event), which her own label
    on the Hive's tables cannot reach; the Warden then refuses to resume a bee from a Handoff so
    labelled.
- **1.11** (roadmap step 8.3, ADR-0044): `forage.source_added` (`SourceAdded`),
  `forage.source_removed` (`SourceRemoved`, with the new enum `SourceRemovalCause`) and
  `forage.source_evict` (`SourceEvict`). A Warden that starts a model server on its own Cell and
  loads a source within its ceilings tells the Queen with `source_added` (and with `source_removed`
  when the source goes), so she enters it in her Forage map and seat ledger; the Queen may order
  `source_evict` only to reclaim seats she exported. All three are new kinds, so a peer below
  minor 11 must not be sent them (section 4's rule).
- **1.12** (all additive, roadmap steps 9.2a and 9.5, ADR-0046 to ADR-0048):
  - The `basket` family (section 8.12): `basket.offer`, `basket.chunk`, `basket.ack` and
    `basket.refuse`. A Basket blob (a content-addressed, signed, inert byte string: an archive, a
    wheel, a dataset, a tool package) is offered by its sha256 and then streamed in chunks of at
    most 256 KiB, resumable by byte offset, and the receiver refuses it by rule (clearance, taint,
    room, signer, platform) before the first chunk moves.
  - `tool.quarantine`, `tool.quarantine_report` and `tool.retired` (section 8.8): the order to run
    the Quarantine Comb on a package already delivered as a blob, where the sandbox is; its
    report, back to whoever asked; and a version leaving the registry (a rollback stays a
    `tool.promoted` of the earlier version). A sender uses none of them toward a peer that has not
    advertised minor 12.
- **1.13** (all additive, roadmap phase 11, ADR-0051 to ADR-0056):
  - Device control (section 8.6): `session.delete_file`, with `SessionRequestKind.DELETE_FILE` for
    the `session.exit` that ends it, and `session.process` with its reply `session.process_state`,
    which start, stop or query a background process that outlives the request that started it.
  - The Swarm ladder and watch mode (section 8.10): `swarm.node_update` (the Queen amends an
    enrolled device's rung, access level, Comb Shield tier, capabilities and limits),
    `swarm.colonize` and its reply `swarm.colonized` (prepare, start or withdraw the Warden on a
    device), and `swarm.observe` with its reply `swarm.observation` (a Warden reads a device
    read-only). `swarm.node_update` and `swarm.colonize` are Queen-only (section 3).
  - `TaskAssign.handoff` (section 8.2) with the value models `HandoffDocument` and
    `HandoffDecision`: the Handoff itself, for a receiver that cannot read the sender's memory
    store (a Warden migrating onto a device, a task reassigned across nodes).
  - `TrailSegmentSync.segment_signature` (section 8.10), with the helper `segment_signing_bytes`:
    a Swarm node's signature over the identity and digest of its offline trail segment.
  - `CellRequest.scratch_quota_bytes` (section 8.5) and `AlarmKind.PATROL_ANOMALY` (section 8.3).
  - `NodeUpdate.watch_roots` (section 8.10) and `CellReady.watch_roots` (section 8.5): the
    directories a colonized device's Warden watches, sent to the packet that starts it and
    reported back by the Warden that runs with them.

  A sender uses none of these toward a peer that has not advertised minor 13 (section 4's rule).
- **1.14** (additive): `TaskResult.cause` and its `ResultCause` enum (section 8.2, ADR-0060): a
  typed cause an attempt ended as it did, carried from the Worker's claim onto the Warden's result.
  `CELL_RUNS_NO_COMMANDS` says the Cell refused the attempt's commands, and the Queen re-places the
  task once on a Cell that runs them. The field defaults to None, so a peer below minor 14 still
  validates every result that leaves it unset.
- **1.15** (additive): the Nurse Bee, a fresh-context reviewer of an attempt that passed its
  machine checks (ADR-0061). `TaskResult.review` (a `NurseReview`, with its `NurseVerdict` and
  `NurseFinding`s, section 8.2) on the Warden's verified result; `TaskAssign.review_round` and
  `TaskAssign.review_findings`, which hand a fresh Worker the findings to fix forward on the same
  Cell; and `AlarmKind.NURSE_UNRESOLVED` (section 8.3), raised by the Queen when the Nurse still
  asks for changes after her last round. Every field defaults (None, 0 or empty), so a peer below
  minor 15 still validates every message that leaves them unset; a sender uses none of them, nor
  the new Alarm kind, toward a peer that has not advertised minor 15.

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
  link). A `ws://` endpoint is valid only on a loopback host, or on a Tor v3 onion service
  (`<56 base32 characters>.onion`, its version and checksum verified): an onion address is the
  service's own public key, and Tor encrypts the connection end to end and authenticates the
  service by that key, which is what TLS would otherwise add. An onion service is dialled only
  through a SOCKS proxy on the dialler's own loopback that resolves the name itself (`socks5h`
  or `socks4a`, section 9); its name is never looked up locally. (A Virtual Cell may also dial a
  documented host-gateway alias or a private address, an opt-in carve-out, `waggle.uris`.)

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
| `waggle.transport.proxy_failed` | `ProxyFailedError` | The SOCKS proxy could not be reached, refused, closed early or did not answer in time |
| `waggle.transport.frame_ceiling` | `FrameCeilingError` | A connection sent frames faster than the listener's per-connection ceiling; the listener closed it with code `4029` and this code as the close reason, never as a `control.error` (section 9) |
| `waggle.transport.tls_pin` | `TlsPinMismatchError` | A pinned `wss://` dial found a certificate other than its `tls_pin_sha256`; a `ConnectFailedError` raised at once on the first attempt and never retried (section 9) |
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
(`bytes`, at most `MAX_CHUNK_BYTES`) plus `offset` and `final` (section 5); the one exception is
`basket.chunk` (section 8.12), whose `data` is base64 text that the model itself decodes and
bounds, with `is_last` for `final`. Booleans read as
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
the table uses before their family defines them: the Basket (the Hive's store of content-addressed,
signed blobs), the Quarantine Comb (the sandbox every new tool must pass), Forage (capacity as
data), Honey and Nectar (distilled and raw knowledge), a Handoff (the document a bee writes before
its context is reset), Clustering (pause and preserve while a model provider is down), a Pheromone
Mask (a short-lived write-or-move-like-a-human tactic), Cell Wax (a Queen-written caution about
one Cell), the Landing Board (the Entrance's HTTP contract), the flight recorder (the
Exoskeleton's record of GUI actions), the Forage map (every source that can serve a model), a
checker (the Warden's own check runner, a judge on its own slot, or a human, reporting through
the Warden), and a device's level (which of three rungs of the Swarm ladder it is on: only its
Pollen Packet, a Warden and sub-bees on it too, or a model server of its own as well).

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
| `forage.source_added` | `SourceAdded` | event | none | Warden -> Queen | Announce a model source as loaded on a Cell's server. |
| `forage.source_removed` | `SourceRemoved` | event | none | Warden -> Queen | Announce a model source as gone from a Cell's server. |
| `forage.source_evict` | `SourceEvict` | event | none | Queen -> Warden | Order a Warden to give back seats the Queen exported. |
| `cell.ready` | `CellReady` | event | none | Warden -> Queen | Announce a Cell as probed, attested and ready. |
| `cell.heartbeat` | `CellHeartbeat` | event | none | Warden -> Queen | Report a Cell's mode, leases and shield state. |
| `cell.teardown_request` | `CellTeardownRequest` | request | none | Warden -> Queen, Queen -> Pollen Packet or Warden | Ask to release a lease or retire a Cell. |
| `cell.request` | `CellRequest` | request | none | Warden -> Queen, Queen -> Pollen Packet or Warden | Ask for a Cell, or for a lease on a named Cell. |
| `cell.lease_opened` | `LeaseOpened` | event | `cell.request` | Pollen Packet or Warden -> Queen, Queen -> Warden | Announce a lease as open. |
| `cell.lease_released` | `LeaseReleased` | event | `cell.teardown_request` | Pollen Packet or Warden -> Queen, Queen -> Warden | Announce a lease as released. |
| `cell.wax_proposed` | `CellWaxProposed` | request | none | any bee -> Queen | Propose a Cell Wax caution about a Cell. |
| `cell.wax_written` | `CellWaxWritten` | event | `cell.wax_proposed` | Queen -> Warden | Deliver a written Cell Wax note. |
| `cell.wax_cleared` | `CellWaxCleared` | event | `cell.wax_proposed` | Queen -> Warden | Announce a Cell Wax note as cleared. |
| `cell.snapshot_request` | `CellSnapshotRequest` | request | none | Warden -> Queen | Ask the Queen to snapshot the sender's own Cell. |
| `cell.snapshot_reply` | `CellSnapshotReply` | reply | `cell.snapshot_request` | Queen -> Warden | Return the new snapshot's id, or why it failed. |
| `cell.rollback_request` | `CellRollbackRequest` | request | none | Warden -> Queen | Ask the Queen to roll the sender's own Cell back to a snapshot. |
| `cell.rollback_reply` | `CellRollbackReply` | reply | `cell.rollback_request` | Queen -> Warden | Report whether the rollback succeeded. |
| `cell.taint_order` | `CellTaintOrder` | event | none | Queen -> Warden | Order an isolated Cell's Warden to taint its own memory store. |
| `session.open` | `SessionOpen` | request | none | Warden -> Pollen Packet | Open the terminal session for a lease. |
| `session.exec` | `SessionExec` | request | none | Warden -> Pollen Packet | Run one program inside the session. |
| `session.stdin` | `SessionStdin` | event | none | Warden -> Pollen Packet | Feed bytes or a signal to a running exec. |
| `session.output` | `SessionOutput` | event | `session.exec` | Pollen Packet -> Warden | Stream one chunk of output or file content. |
| `session.exit` | `SessionExit` | event | `session.exec` | Pollen Packet -> Warden | End a session request with its outcome. |
| `session.put_file` | `SessionPutFile` | event | none | Warden -> Pollen Packet | Write one chunk of a file onto the device. |
| `session.get_file` | `SessionGetFile` | request | none | Warden -> Pollen Packet | Read a file from the device. |
| `session.close` | `SessionClose` | event | none | Warden -> Pollen Packet | Close the lease's session. |
| `session.delete_file` | `SessionDeleteFile` | request | none | Warden -> Pollen Packet | Delete one file on the device. |
| `session.process` | `SessionProcess` | request | none | Warden -> Pollen Packet | Start, stop or query one background process. |
| `session.process_state` | `SessionProcessState` | reply | `session.process` | Pollen Packet -> Warden | Report a background process's pid and whether it runs. |
| `honey.nectar_deposit` | `NectarDeposit` | event | none | Worker or Warden -> Queen | Deposit one chunk of raw Nectar. |
| `honey.query` | `HoneyQuery` | request | none | any bee -> Queen | Search Honey within scopes and a budget. |
| `honey.response` | `HoneyResponse` | reply | `honey.query` | Queen -> any bee | Return the hits that survived filtering. |
| `tool.request` | `ToolRequest` | request | none | Worker -> Warden, Warden -> Queen | Ask for a tool the requester lacks. |
| `tool.promoted` | `ToolPromoted` | event | `tool.request` | Queen -> Warden, Warden -> Worker | Announce a tool version as promoted. |
| `tool.invoke` | `ToolInvoke` | request | none | Worker -> Warden, Queen -> Warden | Run one tool version on a Cell. |
| `tool.result` | `ToolResult` | reply | `tool.invoke` | Warden -> Worker, Warden -> Queen | Return a tool call's outcome. |
| `tool.quarantine` | `ToolQuarantine` | request | none | Warden -> Queen, Queen -> Warden | Request a sandbox for a tool, or order a Comb run in one. |
| `tool.quarantine_report` | `ToolQuarantineReport` | reply | `tool.quarantine` | Warden -> Queen, Queen -> Warden | Return a Comb run's verdict and report to whoever asked. |
| `tool.retired` | `ToolRetired` | event | none | Queen -> Warden, Warden -> Worker | Announce a tool version as withdrawn from the registry. |
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
| `swarm.node_update` | `NodeUpdate` | event | none | Queen -> Pollen Packet | Amend an enrolled device's level, access, tier and limits. |
| `swarm.colonize` | `Colonize` | request | none | Queen -> Pollen Packet | Order one step of putting a Warden on the device. |
| `swarm.colonized` | `Colonized` | reply | `swarm.colonize` | Pollen Packet -> Queen | Report how a colonization step ended. |
| `swarm.observe` | `Observe` | request | none | Warden -> Pollen Packet | Ask a device what it looks like, read-only. |
| `swarm.observation` | `Observation` | reply | `swarm.observe` | Pollen Packet -> Warden | Return the device's processes, resource use, logs and file changes. |
| `basket.offer` | `BasketOffer` | request | none | Queen -> Warden, Warden -> Queen | Offer a blob by digest, with the claims a receiver checks. |
| `basket.chunk` | `BasketChunk` | request | none | Queen -> Warden, Warden -> Queen | Carry one slice of an accepted blob, ordered by offset. |
| `basket.ack` | `BasketAck` | reply | `basket.chunk` | Warden -> Queen, Queen -> Warden | Acknowledge an offer or a chunk with the bytes held so far. |
| `basket.refuse` | `BasketRefuse` | reply | `basket.offer` | Warden -> Queen, Queen -> Warden | Refuse a blob with a stable code before or after its bytes move. |
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
  `HTTP_STATUS`, `ELEMENT_TEXT`, `JUDGE_RUBRIC`, and from `PROTOCOL_MINOR` 6 (roadmap step 6.5)
  `URL_MATCHES` (the attached browser's page URL equals `expected`, or starts with it when
  `expected` ends in `*`) and `REGION_CHANGED` (the pixels of the display rectangle `subject`
  differ after the action from before it). The machine-checkable assertions of the Capping gate
  (the quality gate: propose, check, apply, verify, roll back) plus the judge's rubric for what no
  machine can check. `ELEMENT_TEXT` holds when the text of the element `subject` names contains
  `expected`; `subject` is `role=<role>` optionally followed by `;name=<accessible name>`,
  `label=<label>`, `text=<visible text>`, `selector=<css>`, or a bare CSS selector (the 1.0
  reading, still accepted, so no existing subject is refused).

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
    label for `JUDGE_RUBRIC`. Min 1, max `MAX_PATH_CHARS` chars. For `REGION_CHANGED` it is
    `x,y,width,height` in display pixels, width and height at least 1 (validator).
  - `argv` (`tuple[str, ...]`): the command as an argument list, never a shell string. Max 32
    items, each max 1024 chars; non-empty only for `COMMAND_EXITS_ZERO` and `TEST_PASSES`
    (validator).
  - `expected` (`str | None`): the HTTP status, the element text, the URL or the rubric text;
    None for kinds with nothing to compare. Max 4000 chars; required for `HTTP_STATUS`,
    `ELEMENT_TEXT`, `JUDGE_RUBRIC` and `URL_MATCHES` (validator).
- `HandoffRef`: a reference to a Handoff (the structured document a bee writes before its
  context is reset, which the next bee resumes from). No Handoff id kind exists, so the
  reference is the trail event that recorded it.
  - `event_id` (`EventId`): the `memory.checkpoint` trail event, the lookup key in Bee Bread
    (the warm memory tier).
  - `written_at` (`datetime`): when the Handoff was written.
  - `clearance` (`HoneyClearance`): the Handoff's label, visible before it is fetched so a bee
    never resumes from a Handoff above its own clearance.
- `PlannedLeaving` (roadmap step 5.0b, `PROTOCOL_MINOR` 3): one path a task's plan declares
  should stay on its Cell once the lease is released; the same shape rides on a `TaskDraft`, a
  stored `Task` and a `task.assign`, unchanged.
  - `pattern` (`str`): an absolute (`/...`, `C:\...`, `\\server\share\...`) or `~`-rooted path.
    Min 1, max `MAX_PATH_CHARS` chars; never a bare root, drive or home, never a `..` segment
    (validator).
  - `reason` (`str`): one line, why the goal itself needs this path to remain. Min 1, max
    `MAX_REASON_CHARS` chars.
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
  - `real_display_allowed` (`bool`, `PROTOCOL_MINOR` 6): whether the operator allows the Hive to
    drive the display already running on the Cell (their own screen); never implied by
    `has_display`. Defaults to False.
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
- `ExoskeletonNeed` (`PROTOCOL_MINOR` 6, `messages/task/needs.py`): the Exoskeleton a task needs,
  so its Warden attaches exactly that.
  - `browser_only` (`bool`): the browser fast path alone is enough. Defaults to False.
  - `audio` (`bool`): the task needs to hear or speak through the Cell. Defaults to False; never
    together with `browser_only` (validator).
- `ScoutReport` (`PROTOCOL_MINOR` 6, `messages/task/recon.py`): what a Scout found; untrusted
  model prose.
  - `feasible` (`bool`): whether the Scout recommends committing Foragers as planned; False holds
    the dependent tasks back.
  - `summary` (`str`): one paragraph. Min 1, max 1000.
  - `findings`, `suggested_steps` (`tuple[str, ...]`): max 16 items each; `risks`, `targets`
    (`tuple[str, ...]`): max 8 items each. Every item min 1, max 500 chars.
  - A Scout writes its report as JSON to `scout-report.json` (`SCOUT_REPORT_FILE`) in its lease's
    scratch; the planner makes `FILE_EXISTS` on that path a Scout task's acceptance.
- `HandoffDocument` (`PROTOCOL_MINOR` 13, `messages/task/handoff.py`): a Handoff as it crosses
  Waggle, the wire form of hivemind's `Handoff` field for field with the same bounds, minus the
  taint label (a tainted Handoff is one nobody may resume from, so it never travels). Every field
  but `task_id` is mandatory, so a bee resuming from it never has to guess what an absent field
  meant. A test in hivemind keeps the two in step.
  - `goal` (`str`): what it is working toward. Min 1, max 2000.
  - `progress` (`str`): what has been done so far. Min 1, max 4000.
  - `decisions` (`tuple[HandoffDecision, ...]`): decisions made so far, each with its reason. Max
    64.
  - `tried_and_failed`, `constraints`, `open_threads`, `next_steps`, `do_not_redo`,
    `pinned_facts` (`tuple[str, ...]`): approaches that did not work, constraints discovered,
    loose ends, what the resuming bee does next, what must not be repeated, and facts copied
    verbatim from a pin and never summarised. Max 64 items each, every item max 500.
  - `notes` (`str`): freeform notes. Max 2000.
  - `clearance` (`HoneyClearance`): the document's data-sensitivity label.
  - `written_by` (`str`): the bee id or role that wrote it. Max 128.
  - `task_id` (`TaskId | None`): the task it concerns, if any. Default None.
- `HandoffDecision` (`PROTOCOL_MINOR` 13): one decision with its reason. `what` (`str`, min 1,
  max 500) and `why` (`str`, min 1, max 500).
- The Nurse Bee's review (`PROTOCOL_MINOR` 15, `messages/task/review.py`, ADR-0061): what a
  fresh-context reviewer said about an attempt that had already passed its machine checks. Every
  `problem`, `fix` and `notes` is untrusted model prose, written from untrusted evidence; a
  receiver delimits it as such before it reaches another model.
  - `NurseOutcome`: `PASS`, `CHANGES_REQUESTED`.
  - `NurseReviewStatus`: `REVIEWED` (a Nurse returned a verdict), `UNAVAILABLE` (no model was
    eligible; the attempt is accepted "not nursed", never escalated), `ERROR` (a Nurse was chosen
    but its call or output failed).
  - `NurseFinding`: one problem, anchored. `criterion_index` (`int | None`, 0 to 31: the
    acceptance criterion it fails), `path` (`str | None`, min 1, max `MAX_PATH_CHARS`), `line`
    (`int | None`, at least 1), `problem` and `fix` (`str`, min 1, max 300 each). It names a
    `criterion_index` or a `path`, and a `line` only with a `path` (validator).
  - `NurseVerdict`: `outcome` (`NurseOutcome`); `findings` (`tuple[NurseFinding, ...]`, max 8,
    default empty), empty exactly when `outcome` is `PASS` (validator); `rubric_id` (`str`, min 1,
    max 64), stamped by the Nurse's implementation, never by the model; `notes` (`str`, max 1000,
    default empty).
  - `NurseReview`: `status` (`NurseReviewStatus`); `round` (`int`, 1 to 8: which review of the
    task this is, `TaskAssign.review_round` plus one); `verdict` (`NurseVerdict | None`), set
    exactly when `status` is `REVIEWED` (validator); `nurse_grade` (`int | None`, 1 to 5: the
    reviewing model's effective grade), None exactly when `status` is `UNAVAILABLE` and never
    below `worker_grade` (validators); `worker_grade` (`int`, 1 to 5: the highest grade over the
    Worker's bindings and the models its calls actually reached).

#### TaskAssign

Hand a placed task, with its acceptance criteria, Tempo, clearance, grant, its goal's capability
set (`PROTOCOL_MINOR` 8) and optional Handoff to resume from, to the Warden that owns the chosen
Cell, which re-issues it to the Worker it spawns.

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
- `leaves` (`tuple[PlannedLeaving, ...]`, `PROTOCOL_MINOR` 3): what the plan declared should stay
  on this Cell once the lease is released, carried unchanged from the plan. Max 16 items;
  defaults to empty, so an envelope from before this field existed still validates. A Drone
  cannot widen this set, only raise a `Question`.
- `honey` (`tuple[HoneyHit, ...]`, `PROTOCOL_MINOR` 7): what the Queen's pre-check found for this
  task (roadmap step 7.9): Honey about its objective and its chosen Cell, plus that Cell's live
  Cell Wax, as hits (section 8.7). Max 16 items; defaults to empty, so an envelope from before this
  field existed still validates. No hit's `clearance` may exceed `clearance` (validator). Each hit
  reaches the bee's model delimited and labelled as untrusted retrieved content.
- `capabilities` (`tuple[str, ...] | None`, `PROTOCOL_MINOR` 8): the capability set of the goal
  this task was planned from (ADR-0039), as sorted capability strings (`family` or
  `family:scope`); the Warden gives the task's Worker only what its own set and this set both
  allow. Max 64 entries of 1 to 1,024 characters each; opaque to the codec, parsed by the
  receiving Warden's Guard. Defaults to None, which means no goal ceiling travels (the operator's
  own local submission, or a peer older than minor 8); an empty tuple is a goal allowed nothing.
- `tempo` (`Tempo`): the task's latency budget and accuracy bar.
- `exoskeleton` (`ExoskeletonNeed | None`, `PROTOCOL_MINOR` 6): the Exoskeleton the task needs;
  None for a terminal-only task and by default.
- `network_scopes` (`tuple[str, ...]`, `PROTOCOL_MINOR` 6): outbound hosts the task's capability
  set must allow reaching, so the Warden grants exactly those. Max 32 items, each min 1, max 253
  chars; defaults to empty.
- `recon` (`tuple[ScoutReport, ...]`, `PROTOCOL_MINOR` 6): reports from the Scout tasks this one
  depended on, for the Worker's brief. Max 4; defaults to empty.
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
- `handoff` (`HandoffDocument | None`, `PROTOCOL_MINOR` 13): the Handoff itself, for a receiver
  that may not hold it: a Warden migrating onto a colonized device cannot read the Hive Stand's
  memory store, and a task reassigned across nodes finds the sender's store out of reach. The
  receiver stores it under `resume_from.event_id` before it spawns the bee. Set only with
  `resume_from`, and `handoff.clearance` must equal `resume_from.clearance` (validators), so the
  label every check ran against is the label of the document. A tainted Handoff never travels.
  A frame that would exceed `MAX_FRAME_BYTES` with it raises `waggle.codec.too_large` at the
  sender, which falls back to a fresh start rather than send a partial document. The Queen sends
  `resume_from` without it when she holds no copy: the bees of a Virtual Cell or a colonized
  device wrote their Handoffs in their Warden's own store, which is where the receiver resumes
  from, and a receiver whose store lacks the one named starts the bee fresh (ADR-0024). Defaults
  to None, so an envelope from before this field existed still validates.
- `review_round` (`int`, `PROTOCOL_MINOR` 15): Nurse Bee reviews this task has already had, as
  the Queen counts them; 0 to 7, default 0. The Warden stamps its own review one higher.
- `review_findings` (`tuple[NurseFinding, ...]`, `PROTOCOL_MINOR` 15): what the last Nurse review
  asked to change, for the fresh Worker that fixes forward on the same Cell; untrusted model
  prose. Max 8; set only when `review_round` is at least 1 (validator); default empty.
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
- `scout_report` (`ScoutReport | None`, `PROTOCOL_MINOR` 6): what a Scout found, carried
  unchanged from the Worker's claim through the Warden's result; None for every other role and
  by default.
- `cause` (`ResultCause | None`, `PROTOCOL_MINOR` 14): a typed cause the attempt ended as it did,
  set on the Worker's claim and carried onto the Warden's result, so the Queen can act on it
  without reading `reason`; None by default. `CELL_RUNS_NO_COMMANDS`: the Cell refused the
  attempt's commands under the access-level rule (ADR-0060), so the Queen re-places the task once
  on a Cell that runs them instead of retrying it where they will be refused again.
- `review` (`NurseReview | None`, `PROTOCOL_MINOR` 15): the Nurse Bee's review of this attempt
  (ADR-0061), set by the Warden on its verified result; None when no review ran and by default.
  Never on a Worker's claim, and a `CHANGES_REQUESTED` verdict only with outcome `FAILED`
  (validators), so a rejected attempt never reaches `SUCCEEDED`.

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
  configured quota; added in a minor 1 bump, roadmap step 3.11), `EXOSKELETON_FAILED` (attach
  could not equip the Cell with the Exoskeleton its task needs, or a lease-started peripheral
  process would not stop; added in minor 6, ADR-0031), `SECURITY` (a security event, such as a
  bee its Warden quarantined; it always goes up to the Queen and on to the human, never retried
  or rebound; PROTOCOL_MINOR 9, roadmap step 10.6), `PATROL_ANOMALY` (a watching Warden's Patrol,
  its scheduled read-only review of an idle Cell, found something worth a human's eyes; it
  escalates to the Queen, who posts it to the human; PROTOCOL_MINOR 13, roadmap phase 11,
  ADR-0054), `NURSE_UNRESOLVED` (the Nurse Bee still asked for changes after the last
  fix-forward round the Queen allows; she escalates it to the human, never retries it;
  PROTOCOL_MINOR 15, ADR-0061), `OTHER`. The closed set the escalation policy keys on; `OTHER` carries anything new
  until a minor bump names it.
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
- `InterventionAction`: `COMPACT`, `CHECKPOINT`, `HANDOFF`, `REBIND`, `TAKEOVER`, `CANCEL`,
  `RELEASE_LEASE` (PROTOCOL_MINOR 5: Queen -> Warden only, stop every sub-bee and release the
  recipient's own lease, idempotently, then report `LeaseReleased`, whose envelope names the
  order's in `correlation_id`; `subject` is always None. The Queen also sends it as the
  Overwintering scrub, before she pauses a Virtual Cell for reuse, and pauses the Cell only once
  that answer arrives within her bound with `is_restored` true, its scratch gone; otherwise the
  Cell is destroyed. A Warden holding no lease has nothing to release and answers nothing),
  `QUARANTINE` (PROTOCOL_MINOR 9, roadmap step 10.6c: Queen -> Warden only; the Warden carries it
  out on the sub-bee `subject` or `task_id` names, and never relays it to a Worker: it checkpoints
  the bee, stops and kills it, revokes its slice of the grant, taints the bee's memory from
  `suspect_episode_id` on, holds the task paused (a `task.progress` at stage `PAUSED` to the
  Queen) and tells the Queen with a `SECURITY` Alarm (receiver rule)).
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
  can size its timeout; on the Heartbeat a sender sends just before it is frozen on purpose (a
  relayed Cell snapshot pauses the whole Cell), the longest that freeze may last instead. Greater
  than 0.

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
handoff, rebind to a slot, takeover, cancel, (PROTOCOL_MINOR 5, Queen -> Warden only) release
the recipient's own lease, or (PROTOCOL_MINOR 9, Queen -> Warden only) quarantine one of its
sub-bees. Wardens hold the same levers over their sub-bees minus takeover with the Queen's slot;
a Warden quarantines its own sub-bee by its own escalation policy, never by sending this.

- `action` (`InterventionAction`): the lever pulled.
- `subject` (`WorkerId | None`): the recipient's sub-bee the action targets; None means the
  recipient itself.
- `task_id` (`TaskId | None`): the task concerned when the subject holds more than one.
- `slot` (`str | None`): the model slot to rebind to. Required when `action` is `REBIND`, None
  otherwise (validator). Slot rules of the conventions.
- `binding` (`str | None`, PROTOCOL_MINOR 2): a `[llm.slots]` manifest key the sender already
  resolved for a REBIND (e.g. the Queen's own fallback-chain lookup), so the receiver can respawn
  on it directly instead of searching its own grant. Optional even when `action` is `REBIND`; max
  64 characters.
- `alarm_id` (`AlarmId | None`): the Alarm this intervention answers, so the trail links the two.
- `reason` (`str`): why the supervisor intervenes.
- `suspect_episode_id` (`EventId | None`, PROTOCOL_MINOR 9): the episode from which the bee's
  memory is suspect (an episode record's id, EventId-shaped, or the trail event that best marks
  it); everything the bee wrote from then on is tainted. Required when `action` is `QUARANTINE`,
  None otherwise (validator); a `QUARANTINE` names its bee by `subject` or `task_id`, never
  neither (validator).

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
- `RaisedAuditRate` (`PROTOCOL_MINOR` 10): one Capping tier's sampled-audit rate, raised Hive-wide
  by the Guard Bee until it lapses.
  - `tier` (`str`): the Capping risk tier by name, pattern `^[A-Z][A-Z0-9_]*$`, max 64. A receiver
    ignores a tier it does not know (receiver rule).
  - `rate` (`float`): the raised sampled-audit rate. Above 0, at most 1.
  - `until` (`datetime`): when the raise lapses; a receiver ignores one already past (receiver
    rule).
- `SourceRemovalCause` (`PROTOCOL_MINOR` 11): `UNLOADED`, `SERVER_STOPPED`, `SERVER_DIED`,
  `EVICTED`, `LEASE_RELEASED`. Why a source left a Cell's model server.
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
- `audit_raises` (`tuple[RaisedAuditRate, ...]`, `PROTOCOL_MINOR` 10): every Capping audit-rate
  raise in force when the grant was issued. Max 16; at most one per tier (validator). Defaults to
  empty, so an envelope from before this field existed still validates. The holder's Capping gate
  samples each terminal proposal of a raised tier at the higher of its own tier table's rate and
  the raise, until `until`, keeping per tier the highest raise in force among every grant issued
  to it (receiver rule).

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

#### SourceAdded

`PROTOCOL_MINOR` 11. A Warden announces a model source it loaded on a model server on its own
Cell, within the ceilings the Queen set (autonomous: loading is reported, not requested). The
Queen enters the source in her Forage map with `host_cell_id` set to `cell_id` and adds its seats
to her ledger. Warden -> Queen, an event; the Queen does not reply.

- `cell_id` (`CellId`): the Cell whose server now serves the source.
- `server_id` (`str`): the manifest key (`[llm.servers]`) of the model server, as
  `ModelServerReport.server_id`; no URL crosses the wire. Max 128.
- `source` (`SourceRef`): the source that was loaded.
- `grade` (`int`): the hand-set grade, 1 to 5, the Warden read from its manifest entry; never a
  measurement.
- `context_window` (`int`): the model's window, in tokens. Greater than 0.
- `seats_total` (`int`): concurrent requests the server allows for it. At least 0.
- `vram_bytes` (`int`): VRAM the loaded model occupies. At least 0.
- `capabilities` (`tuple[str, ...]`): the source's capability flags, as the manifest names them.
  Max 16, each max 32.
- `reason` (`str`): why it was loaded, within which ceilings.

#### SourceRemoved

`PROTOCOL_MINOR` 11. A Warden announces that a source is gone from its Cell's model server; the
Queen drops it from her Forage map and releases the seats she had reserved on it. Warden ->
Queen, an event.

- `cell_id` (`CellId`): the Cell whose server served the source.
- `server_id` (`str`): the manifest key of the model server. Max 128.
- `source` (`SourceRef`): the source that was removed.
- `cause` (`SourceRemovalCause`): which event removed it.
- `reason` (`str`): why, in the Warden's words.

#### SourceEvict

`PROTOCOL_MINOR` 11. The Queen orders a Warden to give back seats on one of its sources. Queen ->
Warden, an event (the catalogue has no order shape; `cell.taint_order` and the `control.*` orders
are events too), accepted only from a `hive` sender.

- `cell_id` (`CellId`): the Cell whose server serves the source.
- `source` (`SourceRef`): the source to evict.
- `seats_to_reclaim` (`int`): seats the Queen needs back. Greater than 0.
- `reason` (`str`): why the Queen needs the seats back.

Receiver rule: a Warden evicts only when the named source is one whose seats it exported
(`Ceilings.exportable_seats`, section 8.4's `CeilingsReport`); for any other source it ignores
the order and sends nothing, so the `forage.source_removed` (cause `EVICTED`) that follows a real
eviction never arrives. The Queen sends the order only to reclaim seats she exported
(codingrules 8.10), never to unload a model the Warden loaded for its own bees.

### 8.5 cell (`messages/cell/`)

The life of a Cell and of the leases on it: a Cell reports ready and alive, a Warden asks the
Queen for a Cell or to retire one, leases open and close (a lease is one Warden's tenancy on a
Real Cell, with a scratch directory and the processes it started), Cell Wax notes are
proposed, written and cleared, and an isolated Cell's Warden is ordered to taint its own memory.
A Real Cell is borrowed and left exactly as found.

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
- `watch_roots` (`tuple[str, ...]`, default `()`): the directories the Warden reads logs and file
  changes from while it watches an idle Cell, as it runs with them (`PROTOCOL_MINOR` 13). Max 16,
  each 1 to 256 characters. A Virtual Cell's Warden and a Level 0 device's name none; a colonized
  device's names those its packet handed it at start, which the Queen compares with the list the
  operator set for the device and restarts the Warden when they differ (receiver rule).

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
- `scratch_quota_bytes` (`int | None`, `PROTOCOL_MINOR` 13): the lease's scratch quota in bytes,
  taken from the holder's `Ceilings.scratch_disk_bytes_per_lease` (the Hive Stand fills in its
  configured default, `[swarm] scratch_quota_mb`, for a Warden whose ceilings name no figure),
  which the device's packet enforces on the lease's scratch directory; None leaves the packet's
  own default. At least 0 when set (0 allows no scratch bytes). Defaults to None, so an envelope
  from before this field existed still validates.
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

#### The snapshot relay (PROTOCOL_MINOR 5)

A Virtual Cell's own Warden runs inside the Cell (ADR-0027) and so cannot reach the host's Docker
daemon or QEMU process to snapshot or roll back its own Cell before a risky Capping proposal
(ADR-0018); only the Queen, on the Hive Stand, holds that backend. These four messages are the
relay: a snapshot or rollback request travels Warden -> Queen and its reply Queen -> Warden. Both
replies carry their own outcome rather than falling back to `control.error`, so a timed-out or
failed request always resolves to `hivemind.cell.SnapshotUnsupportedError` on the asking Warden's
own `RelaySnapshotter`, which makes the Capping gate fall back to REVERSE_DIFF (ADR-0018) exactly
as it would for a Real Cell's `NoopSnapshotter`. The receiver rule holds here too: the Queen answers
a request only about the Cell the sender's link proved at its handshake. One naming another Cell
never reaches her snapshotter; it is answered with `error` set (`ok` false for a rollback) and
recorded as `guard.relay_refused` about the sender's own Cell, which the Guard Bee's
`relay_forgery` rule counts.

##### CellSnapshotRequest

Ask the Queen to snapshot the sender's own Cell.

- `cell_id` (`CellId`): the Cell to snapshot; always the sender's own Cell.
- `purpose` (`str`): why: the Capping tier or proposal kind this snapshot precedes. Min 1, max
  `MAX_REASON_CHARS`.

##### CellSnapshotReply

Answer with a new snapshot id, or why one could not be taken.

- `cell_id` (`CellId`): the Cell the request named.
- `snapshot_id` (`str | None`): the new snapshot's id; set exactly when `error` is None (validator).
  Min 1, max 256 characters; no `IdKind` exists for it yet.
- `error` (`str | None`): why no snapshot was taken -- an unknown Cell, or one whose backend
  cannot snapshot at all; set exactly when `snapshot_id` is None (validator). Min 1, max
  `MAX_REASON_CHARS`.

##### CellRollbackRequest

Ask the Queen to roll the sender's own Cell back to a snapshot it already took.

- `cell_id` (`CellId`): the Cell to roll back; always the sender's own Cell.
- `snapshot_id` (`str`): an id a prior `CellSnapshotReply` on this Cell returned. Min 1, max 256
  characters.

##### CellRollbackReply

Report whether the rollback succeeded.

- `cell_id` (`CellId`): the Cell the request named.
- `ok` (`bool`): whether the Cell was restored to the named snapshot.
- `error` (`str | None`): why the rollback failed -- an unknown Cell, an unknown snapshot id, or a
  backend failure; set exactly when `ok` is False (validator). Min 1, max `MAX_REASON_CHARS`.

#### CellTaintOrder (PROTOCOL_MINOR 10)

The Queen has isolated the receiver's Cell (roadmap step 10.6a, ADR-0043) and orders its Warden to
label the memory it keeps in its own store tainted, so no later prompt or resumed bee reads what
was written under suspicion there: every Handoff, episode record and checkpoint deposit written at
or after `suspect_at` by one of `authors` or about one of `task_ids`. The Warden runs the same
setter the Queen runs on the Hive's tables, with `TaintSource.ISOLATION` and `cause_event_id` as
every label's cause, and from then on refuses to resume a bee from a Handoff its store labels
tainted until a judge clears it. The order only narrows: it labels and refuses, never widens.
Idempotent: a label already standing is left as it is, so a Queen resends it when the Warden's
link reattaches while the isolation stands.

- `cell_id` (`CellId`): the isolated Cell; always the receiver's own (receiver rule: an order
  naming another Cell is ignored).
- `cause_event_id` (`EventId`): the Queen's `cell.isolated` event.
- `suspect_at` (`datetime`): memory written at or after this instant is covered: the isolation's
  first cited event, or when the isolation began when it cites none.
- `authors` (`tuple[WorkerId | WardenId, ...]`): the bees that ran on the Cell. Max 256.
- `task_ids` (`tuple[TaskId, ...]`): the tasks placed on the Cell. Max 1024. At least one of
  `authors` and `task_ids` is non-empty (validator).
- `reason` (`str`): why, naming ids only. Min 1, max `MAX_REASON_CHARS`.

### 8.6 session (`messages/session/`)

The terminal-over-Waggle family: a Warden on the Hive Stand drives a remote Real Cell through its
Pollen Packet exactly as it would a local terminal (exec with streaming output, stdin, put, get
and delete file, start, stop and query a background process, close). A session is identified by
its `LeaseId`, one session per lease; concurrency lives
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
- `SessionRequestKind`: `OPEN`, `EXEC`, `PUT_FILE`, `GET_FILE`, `DELETE_FILE` (`PROTOCOL_MINOR`
  13). Which request a `SessionExit` ends. A `DELETE_FILE` exit follows an `OPEN` exit's rules for
  its outcome, size and digest: it completes, and it moved no file.
- `ProcessAction` (`PROTOCOL_MINOR` 13, `messages/session/process.py`): `START`, `STOP`, `QUERY`.
  What a `SessionProcess` asks of a background process.

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
put-file, get-file or delete-file.

- `lease_id` (`LeaseId`): the session the request ran in.
- `request_id` (`MessageId`): envelope id of the request this ends (for a put-file, its
  `transfer_id`); also the envelope's `correlation_id`.
- `request_kind` (`SessionRequestKind`): `OPEN`, `EXEC`, `PUT_FILE`, `GET_FILE` or `DELETE_FILE`,
  which request this ends, so the rules below are decidable on the model alone.
- `outcome` (`SessionOutcome`): how it ended; `EXITED`, `SIGNALED`, `TIMED_OUT` and `KILLED`
  only when `request_kind` is `EXEC`, `COMPLETED` only otherwise, a `DELETE_FILE` included
  (validator).
- `exit_code` (`int | None`): the process's exit status. Set exactly when `outcome` is `EXITED`
  (validator).
- `signal_name` (`str | None`): the platform signal that ended the process. Set exactly when
  `outcome` is `SIGNALED` (validator); max 16.
- `duration_s` (`float`): seconds from start to end. At least 0.
- `size_bytes` (`int | None`): total bytes written or sent. Set exactly when `request_kind` is
  `PUT_FILE` or `GET_FILE` (validator), so never for a `DELETE_FILE`; at least 0.
- `sha256` (`str | None`): digest of the whole file written or read. Set exactly when
  `request_kind` is `PUT_FILE` or `GET_FILE` (validator), so never for a `DELETE_FILE`.
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

#### SessionDeleteFile

Delete one file on the device (`PROTOCOL_MINOR` 13, roadmap phase 11). The packet removes it and
ends the request with one `SessionExit` whose `request_kind` is `DELETE_FILE` and `outcome`
`COMPLETED`, with `size_bytes` and `sha256` None. A path that does not exist is a `control.error`
`pollen.not_found`, and one outside what the lease may touch, or not a file, is
`pollen.path_refused`; neither deletes anything. One message removes one file: there is no glob,
no recursion and no force flag, so a single request can never remove more than the file it names.

- `lease_id` (`LeaseId`): the session the delete belongs to.
- `path` (`str`): the file to remove, absolute or relative to scratch; resolved with symlinks
  followed and checked against the lease's allowed roots and access level, and a touch outside
  scratch is recorded with the file's prior bytes so the release can restore it. Min 1, max
  `MAX_PATH_CHARS`.
- `task_id` (`TaskId | None`): the task this delete serves.
- `worker_id` (`WorkerId | None`): the sub-bee on whose behalf it is deleted.

#### SessionProcess

Start, stop or query one background process in the lease (`PROTOCOL_MINOR` 13). An exec runs a
program to its end; this is for a program that must outlive the request that started it (a virtual
display server, an audio daemon, a browser the Exoskeleton needs running for the whole task). The
packet records every process it starts with the lease before it answers, so a release or the
dead-man switch kills it and the device is left as found. The answer is one `SessionProcessState`,
or a `control.error` (`pollen.start_failed` for a `START` that could not launch the program,
`hive.session.lease_not_open` for an unknown lease). One message does exactly one thing: the launch
fields belong to `START` alone, the pid to `STOP` and `QUERY` alone.

- `lease_id` (`LeaseId`): the open lease the process belongs to; its scratch directory is the
  default working directory.
- `action` (`ProcessAction`): start a process, stop one, or ask about one.
- `argv` (`tuple[str, ...]`): the program and its arguments, as `SessionExec.argv` (never a shell
  string; max 128 items, each max 4096 chars, at most 131,072 characters in total, validator).
  Non-empty exactly when `action` is `START` (validator).
- `cwd` (`str | None`): working directory for a `START`; None means the lease's scratch
  directory. The packet refuses a path outside scratch or the lease's allowed roots. Max
  `MAX_PATH_CHARS`; None for `STOP` and `QUERY` (validator).
- `env` (`tuple[EnvVar, ...]`): variables overlaid on the new process only. Max 64, names unique;
  empty for `STOP` and `QUERY` (validator).
- `log_path` (`str | None`): where the packet redirects the process's stdout and stderr, absolute
  or relative to scratch; None discards them. Max `MAX_PATH_CHARS`; None for `STOP` and `QUERY`
  (validator).
- `pid` (`int | None`): the process to stop or query, as a `SessionProcessState` reported it.
  Required for `STOP` and `QUERY`, None for `START` (validator); at least 1. A pid the lease did
  not start draws a `control.error` `pollen.not_found`, so one lease can never signal another's
  process or the device's own; a pid it started that has since exited is answered normally
  (`is_running` false).
- `task_id` (`TaskId | None`): the task this process serves.
- `worker_id` (`WorkerId | None`): the sub-bee on whose behalf it is started, stopped or queried.

#### SessionProcessState

Report one background process's state: the answer to a `SessionProcess` (`PROTOCOL_MINOR` 13),
correlated to it.

- `lease_id` (`LeaseId`): the lease the process belongs to.
- `action` (`ProcessAction`): echo of the request's action.
- `pid` (`int`): the process's id on the device: the new pid after a `START`, the request's pid
  after `STOP` or `QUERY`. At least 1.
- `argv` (`tuple[str, ...]`): the command line as started, bounded as `SessionProcess.argv`;
  non-empty exactly when `action` is `START`, so a Warden sees what ran (validator).
- `log_path` (`str | None`): the resolved absolute path of the redirected output; only after a
  `START`, and None there when the request named no log (validator). Max `MAX_PATH_CHARS`.
- `is_running` (`bool`): whether the process is running now; after a `START`, false means it
  exited before the packet could answer, and the log says why.
- `was_running` (`bool | None`): whether the process was running when the `STOP` arrived, so a
  stop of an already-dead process is told apart from a kill. Set exactly when `action` is `STOP`
  (validator).
- `reason` (`str`): what happened, in words, especially why a stop failed.

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
  `C1` and keys every other deposit to the Cell's ephemeral segment, where the Queen also holds
  every `swarm.trail_segment_sync` the Cell ships, never merging it into her trail, so the Night
  Veil boundary of codingrules section 12 holds on the wire. `RIPENED_HONEY` with `origin_tier`
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
transit. No tool code or package ever rides a `tool.*` message; a package crosses Waggle only as
a Basket blob (section 8.12), named by its digest.

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

#### ToolQuarantine

Order or request a Quarantine Comb run over a package already delivered as a Basket blob. The Comb
runs where its sandbox is, driven by that Cell's Warden, because no Warden answers `session.*`
messages before phase 11 and so the Queen cannot drive a sandbox Cell's session from her own
process. From a Warden to the Queen it is that Warden's request for a sandbox for a cell-scope
tool: the Queen provisions one, ships the package to it, runs the Comb there on the requester's
behalf and relays the report back. From the Queen to the Warden of a sandbox Cell it is the order
to run the Comb there. A refusal is a `control.error`; a successful run is a
`tool.quarantine_report`. Lives in `tool/quarantine.py`.

- `tool_id` (`ToolId`): the tool under test, as minted by its requester.
- `name` (`str`): the registry name. Pattern as `ToolRequest.name`.
- `version` (`str`): the immutable version under test. Pattern as `ToolPromoted.version`, max 32.
- `scope` (`ToolScope`): the visibility the tool would be promoted at.
- `cell_id` (`CellId | None`): the one Cell a `CELL`-scope tool is for. Required exactly when
  `scope` is `CELL` (validator).
- `spec_json` (`str`): the `ToolSpec` as JSON text. Max 32768; must parse as a JSON object
  (validator).
- `package_sha256` (`str`): digest of the package, a Basket blob already resident on the
  recipient. The sha256 pattern.
- `package_size_bytes` (`int`): size of that package. At least 1.
- `path` (`QuarantinePath`): which route through the Comb the sender chose.
- `limits_json` (`str`): the `QuarantineLimits` (CPU seconds, memory, wall time, output, fuzz
  cases) as JSON text. Max 16384; must parse as a JSON object (validator).
- `requested_by_warden` (`WardenId`): the Warden whose Worker or own work needs the tool.
- `reason` (`str`): why the tool is being quarantined now.

Receiver rules. A Warden accepts a `tool.quarantine` only from a `hive` sender, and only when
`package_sha256` names a blob resident on its own Cell whose size is `package_size_bytes`; it
re-validates `spec_json`, checks the spec and the package agree, and refuses with a
`control.error` otherwise. The Queen accepts one only from a Warden, only when
`requested_by_warden` equals the envelope `sender`, and for `CELL` scope only when `cell_id` is
that Warden's own Cell; a sandbox is never handed to the requester, it stays the Queen's and is
torn down after the report. Neither side runs the Comb on a package whose digest it has not
verified.

#### ToolQuarantineReport

The verdict of one `ToolQuarantine`. It is a reply: `correlation_id` is the request's `id`, and
the message goes back to whoever sent the request (the sandbox's Warden to the Queen, or the Queen
to the Warden that asked for a sandbox). The registry promotes only on a passing report whose
`spec_sha256` and `package_sha256` match what it was offered, so the full report travels as JSON
text for the promoter to re-check. Lives in `tool/quarantine.py`.

- `tool_id` (`ToolId`): the tool that was quarantined.
- `version` (`str`): the version that was quarantined. Pattern as `ToolPromoted.version`.
- `passed` (`bool`): true when every gate passed.
- `path` (`QuarantinePath`): which route proved, or failed to prove, the tool.
- `sandbox_cell_id` (`CellId`): the Cell the Comb ran in; the Hive Stand's Cell for the
  development subprocess sandbox.
- `failing_gate` (`str | None`): the first gate that failed (`static`, `tests`, `schema`, `fuzz`,
  `resources`, `capabilities`). Pattern `^[a-z][a-z0-9_]{0,63}$`. None exactly when `passed` is
  true (validator).
- `report_json` (`str`): the full `QuarantineReport` as JSON text. Max 65536; must parse as a JSON
  object (validator).
- `reason` (`str`): a one-line summary of the verdict for the trail.

#### ToolRetired

Announce that a tool version left the Comb Registry, so Wardens and Workers stop offering it. A
retired version is never deleted: its row and package stay as evidence, and a rollback makes an
earlier version current again by a `tool.promoted` naming `supersedes_version`, not by this
message. An event with no registered antecedent (a promotion is an event, and a registered
antecedent is always a request): the version is identified by `tool_id` and `version`, and
`correlation_id` is normally null. Lives in `tool/quarantine.py`.

- `tool_id` (`ToolId`): the retired tool.
- `name` (`str`): the registry name. Pattern as `ToolRequest.name`.
- `version` (`str`): the immutable version retired. Pattern as `ToolPromoted.version`.
- `scope` (`ToolScope`): the visibility the version had.
- `cell_id` (`CellId | None`): the one Cell a `CELL`-scope tool was visible on. Required exactly
  when `scope` is `CELL` (validator).
- `retired_by` (`HiveId | WardenId`): the Queen's address, or the address of the Warden that owns a
  `CELL`-scope tool.
- `reason` (`str`): why the version was withdrawn.

Receiver rules. A Warden accepts a `tool.retired` only from a `hive` sender, or for its own Cell's
`CELL`-scope tool from the Warden named in `retired_by`; a Worker never sends one. `retired_by`
equals the first-hop envelope `sender`.

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
- `ActionKind`: `DIFF`, `COMMAND`, `ACTION_SEQUENCE` (prose steps, for a device), `COPY`
  (`PROTOCOL_MINOR` 4, roadmap step 5.0e: the `keep` tool moves a scratch file outside it; a diff
  cannot carry a binary, so this moves the bytes by digest instead of inline), `GUI`
  (`PROTOCOL_MINOR` 6, roadmap step 6.5: typed Exoskeleton steps the gate applies through the
  attached Exoskeleton, never prose).
- `GuiOp` (`PROTOCOL_MINOR` 6, `messages/capping/gui.py`): `MOVE`, `CLICK`, `DOUBLE_CLICK`,
  `TYPE`, `PRESS`, `SCROLL` (desktop); `NAVIGATE`, `BROWSER_CLICK`, `BROWSER_FILL`,
  `BROWSER_PRESS` (browser); `SAY` (audio). `MouseButton`: `LEFT`, `MIDDLE`, `RIGHT`.
- `ElementTarget` (`PROTOCOL_MINOR` 6): one element on a page, named exactly one way (validator):
  `role` (optionally with `name`, its accessible name), `label`, `text` or `selector`, each min 1,
  max 512 chars; `name` only beside `role`.
- `GuiStep` (`PROTOCOL_MINOR` 6): one typed step; the fields its `op` needs are required and every
  other field is None (validator). `x`, `y` (`int | None`, 0 to 16384: display pixels; MOVE,
  CLICK, DOUBLE_CLICK, optional for SCROLL); `button` (`MouseButton | None`, CLICK and
  DOUBLE_CLICK, None meaning LEFT); `text` (`str | None`, min 1, max 4000: TYPE, BROWSER_FILL);
  `secret` (`bool`, default False, only on TYPE and BROWSER_FILL: the text is redacted wherever
  the step is recorded or rendered); `keys` (`str | None`, max 64, pattern
  `^[A-Za-z0-9_]+(\+[A-Za-z0-9_]+)*$`: one chord, PRESS and BROWSER_PRESS); `dx`, `dy` (`int |
  None`, -100 to 100 wheel steps: SCROLL, at least one non-zero); `url` (`str | None`, max 2048,
  http, https, file or about:blank only: NAVIGATE); `target` (`ElementTarget | None`:
  BROWSER_CLICK, BROWSER_FILL, optional for BROWSER_PRESS); `clip` (`str | None`, max
  `MAX_PATH_CHARS`: the scratch path of a WAV clip, SAY).
- `ProposedAction`: the action, in a shape deterministic checks can read.
  - `kind` (`ActionKind`), `summary` (`str`, max 1000), `diff` (`str | None`, max 131072; a
    larger diff is written to scratch with the session and referenced by `paths` plus
    `diff_sha256`), `diff_sha256` (`str | None`: the sha256 pattern; set exactly when `diff` is
    None for a `DIFF` action, validator), `command` (`tuple[str, ...]`: argv, max 64 items
    each max 4096; non-empty only for `COMMAND`), `cwd` (`str | None`, max `MAX_PATH_CHARS`),
    `paths` (`tuple[str, ...]`: paths touched, max 64 each max `MAX_PATH_CHARS`; exactly 2 for
    `COPY`, `(source, destination)`), `steps` (`tuple[str, ...]`, max 100 each max 1000;
    non-empty only for `ACTION_SEQUENCE`), `copy_sha256` (`str | None`, `PROTOCOL_MINOR` 4: the
    sha256 pattern, set exactly for `COPY`; the gate reads the source's bytes from scratch itself
    at apply time and verifies this digest, so the proposal never carries the bytes), `copy_size`
    (`int | None`, `PROTOCOL_MINOR` 4: the source's size in bytes, set exactly for `COPY`, ge 0),
    `gui` (`tuple[GuiStep, ...]`, `PROTOCOL_MINOR` 6, max 32: non-empty exactly for `GUI`,
    applied in order). The field matching `kind` must be populated, and the characters across
    `summary`, `diff`, `command`, `paths`, `steps` and the text of every GUI step total at most
    262,144 (validator).
- `CheckKind`: `SCHEMA`, `LINT`, `TYPES`, `ALLOWLIST`, `SIZE_CAP`, `SANDBOX_TESTS`, `JUDGE`,
  `HUMAN`. The rungs of the ladder, cheapest first.
- `CheckOutcome`: `PASSED`, `FAILED`, `CHANGES_REQUESTED` (judge and human only).
- `VerdictOutcome`: `VERIFIED`, `REJECTED`, `ROLLED_BACK`, `CHANGES_REQUESTED`.
- `RollbackMethod`: `SNAPSHOT`, `REVERSE_DIFF`, `NONE` (nothing possible: the documented no-op
  on Real Cells), `GUI_STATE` (`PROTOCOL_MINOR` 6: the Exoskeleton restored its own checkpoint,
  the browser page, cookies and local storage it held before a GUI action; client state only).

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
an offline Warden's local Pheromone Trail segment on reconnection. From `PROTOCOL_MINOR` 13
(roadmap phase 11) the family also carries the steps that put a Warden on a device and amend an
enrolled device's terms (`messages/swarm/ladder.py`: `swarm.node_update`, `swarm.colonize`,
`swarm.colonized`), and a Warden's read-only watching of a device through its packet
(`messages/swarm/watch.py`: `swarm.observe`, `swarm.observation`).

Family enums and value models:

- `NodeKey`: one trusted signer.
  - `node_id` (`NodeId`), `public_key_hex` (`str`: raw 32-byte Ed25519 key, pattern
    `^[0-9a-f]{64}$`).
- `RuntimeLevel`: `GATEWAY` (level 0: packet only, Warden on the Hive Stand), `COLONIZED`
  (level 1: Warden and sub-bees on the device), `NUC` (level 2: plus a model server).
- `NucAction`: `PROMOTE`, `DEMOTE`. Demotion rides `swarm.nuc_promote`; there is no separate
  kind.
- `NucOutcome`: `PROMOTED`, `DEMOTED`, `FAILED`.
- `ColonizeAction` (`PROTOCOL_MINOR` 13): `PREPARE` (install the pinned runtime beside the
  packet, start nothing), `START` (launch the device's Warden from the installed runtime),
  `WITHDRAW` (stop the device's Warden, leaving the packet alone on the device). One message, one
  step.
- `ColonizeOutcome` (`PROTOCOL_MINOR` 13): `PREPARED`, `STARTED`, `WITHDRAWN`, `FAILED`.
- `ObservationKind` (`PROTOCOL_MINOR` 13): `PROCESS_LIST`, `RESOURCE_USE`, `LOGS`,
  `FILE_CHANGES`. What a Warden may ask a device to report: exactly the Guard's watch
  observations (network counters ride on `RESOURCE_USE`), never the screen and never input.
- `FileChangeKind` (`PROTOCOL_MINOR` 13): `CREATED`, `MODIFIED`, `DELETED`.
- `ProcessSample` (`PROTOCOL_MINOR` 13, `messages/swarm/watch.py`): one running process.
  - `pid` (`int`): the process id on the device. At least 0 (0 only for an idle task).
  - `name` (`str`): the executable's name, never its arguments, which can carry secrets. Max 256.
  - `cpu_share` (`float`): the share of the device's whole CPU capacity the process used since
    the previous sample, between 0 and 1 (one core of four busy is 0.25).
  - `rss_bytes` (`int`): resident memory. At least 0.
  - `started_at` (`datetime | None`): when the process started; None where the platform does not
    say.
- `NetworkCounter` (`PROTOCOL_MINOR` 13): cumulative traffic of one interface since boot.
  - `interface` (`str`, min 1, max 64), `rx_bytes` (`int`, at least 0), `tx_bytes` (`int`, at
    least 0).
- `ResourceSample` (`PROTOCOL_MINOR` 13): the device's resource use at one instant, always a full
  snapshot.
  - `cpu_load` (`float`): one-minute load average divided by logical cores. At least 0.
  - `memory_bytes`, `memory_free_bytes` (`int`): total memory and memory available to start work.
    At least 0; free at most total (validator).
  - `disk_bytes`, `disk_free_bytes` (`int`): total and free bytes of the filesystem the packet
    works on (its data directory's). At least 0; free at most total (validator).
  - `uptime_s` (`float`): seconds since the device booted. At least 0.
  - `network` (`tuple[NetworkCounter, ...]`): one counter per interface. Max 32, interface names
    unique (validator).
- `LogLine` (`PROTOCOL_MINOR` 13): one line from the tail of a log file inside a watched root.
  - `path` (`str`): the log file. Min 1, max 256 (`MAX_OBSERVED_PATH_CHARS`).
  - `line` (`str`): the line without its newline, cut at the bound if it was longer. Max 1000.
- `FileChange` (`PROTOCOL_MINOR` 13): one file created, changed or deleted inside a watched root
  since the cursor.
  - `path` (`str`): the file. Min 1, max 256 (`MAX_OBSERVED_PATH_CHARS`).
  - `change` (`FileChangeKind`): how it changed.
  - `at` (`datetime`): when the change was seen (a modification time).
  - `size_bytes` (`int | None`): the file's size after the change; None for a deleted file
    (validator) and where the size could not be read. At least 0.

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
A chunk the Queen does not merge is answered with a `control.error` correlated to it, with
`is_retryable` false, whose code names the reason: `hivemind.queen.segment_refused.another_node`
(the receiver rule below, or another node's segment), `.another_cell` (a Cell the link did not
prove), `.format` (an unknown `segment_format_version`) or `.corrupt` (bytes, digest or event
count that do not match what the chunk declared). A merged chunk is not answered.

- `node_id` (`NodeId`): the node whose segment this is. Must equal the envelope's `node_id`,
  and `warden_id` the envelope's `sender` (receiver rule, answered with `control.error`), so
  no Warden can sync a segment under another node's identity; the Queen dedupes by the
  envelope `node_id`, `first_event_id`, `last_event_id` and `sha256` together.
- `cell_id` (`CellId`): the Cell the Warden owns. A Queen whose link proved the Cell (a Virtual
  Cell's `CellReady`) refuses a chunk naming any other: she routes a segment by its Cell.
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
- `segment_signature` (`str | None`, `PROTOCOL_MINOR` 13): padded base64 Ed25519 signature
  (exactly 88 characters, pattern `^[A-Za-z0-9+/]{86}==$`) by the node's own key over
  `segment_signing_bytes(node_id, first_event_id, last_event_id, event_count, sha256)`. Allowed on
  the final chunk only (validator), the one that carries the digest it covers. Receiver rule: a
  Swarm node's segment without a signature that verifies against that node's registered key is
  refused, and recorded as `guard.segment_refused`; the dedupe above proves order, not
  authorship. A Virtual Cell's Warden has no registered node key to sign with, so for it the
  field is simply None. Defaults to None, so an envelope from before this field existed still
  validates.

`segment_signing_bytes(node_id, first_event_id, last_event_id, event_count, sha256) -> bytes`
(`waggle.messages.swarm`, a pure helper beside the model) is the one definition of what is
signed: the UTF-8 encoding of the line `waggle-trail-segment-v1` and then the five fields as text
(the ids as they appear on the wire, the count in decimal, the digest in lowercase hex), each on
its own line, joined by single newlines with no trailing newline:

```text
waggle-trail-segment-v1
<node_id>
<first_event_id>
<last_event_id>
<event_count>
<sha256>
```

The first line is a versioned domain tag naming this one use of the node key, so the signature
cannot be replayed as any other the key makes (an envelope's canonical bytes, section 6, start
with `{`); no field contains a newline, so the join is unambiguous.

#### NodeUpdate

Change an enrolled device's standing terms without a new enrolment (`PROTOCOL_MINOR` 13,
roadmap phase 11): an event from the Queen to the device's Pollen Packet, which persists it and
applies it from the next request it handles. It restates every term an `EnrolAccept` granted that
can change after enrolment, plus the level the device is on, so the packet never has to merge.
Queen-only (section 3): a packet accepts it only from the `hive` sender it enrolled with.

- `device_id` (`DeviceId`): the device whose terms change.
- `cell_id` (`CellId`): the Real Cell this device is.
- `warden_id` (`WardenId`): the Cell's Warden, which keeps its id across every level; the packet
  accepts session and lease traffic only from it or the Queen.
- `level` (`RuntimeLevel`): the level the device is on, so the packet knows whether the dead-man
  switch or an on-device Warden handles a lost link.
- `access_level` (`AccessLevel`): the level now granted.
- `comb_shield` (`CombShieldLevel`): the tier now set by the operator. Never `NIGHT_VEIL`
  (validator).
- `granted_capabilities` (`tuple[str, ...]`): the node's capability strings from now on, checked
  locally on every request; replaces the previous set entirely. Max 64, each max 128.
- `offline_limit_s` (`float`): the dead-man limit for a device with no Warden on it, from now on.
  Greater than 0.
- `heartbeat_interval_s` (`float`): how often the packet sends `DeviceHeartbeat`, from now on.
  Greater than 0.
- `watch_roots` (`tuple[str, ...]`, default `()`): the directories the device's Warden reads logs
  and file changes from while it watches an idle Cell, from now on; replaces the previous list
  entirely (`PROTOCOL_MINOR` 13). Max 16, each 1 to 256 characters. The packet keeps them for the
  Warden it starts (`HIVEMIND_WATCH_ROOTS`) and reads nothing itself: a `swarm.observe` names its
  own roots. The Queen sends only directories the capabilities above let the Hive read, so a
  device with none named, or none readable, gets `()`.
- `reason` (`str`): why the terms changed.

#### Colonize

Order one step of colonizing a device (`PROTOCOL_MINOR` 13): a request from the Queen to the
device's Pollen Packet. `PREPARE` installs the pinned runtime beside the packet, `START` launches
the device's Warden under the Warden id the Cell has had since enrolment, and `WITHDRAW` stops it
again. The answer is one correlated `Colonized` (`FAILED` included), or a `control.error` when the
packet refuses the order outright. Queen-only (section 3). Nothing here names a program, a path
or a command: the runtime is installed from the wheelhouse or index the operator configured when
installing the packet, never from the wire, so the wire can only start what is already there.

- `cell_id` (`CellId`): the Cell being colonized; the packet's own.
- `device_id` (`DeviceId`): the device behind that Cell.
- `action` (`ColonizeAction`): prepare, start or withdraw.
- `warden_id` (`WardenId`): the Warden the device will run: the id the Cell's Warden has had
  since enrolment, so it keeps its identity (and the trail its continuity) across the move.
- `runtime_version` (`str | None`): the HiveMind runtime version to install. Required for
  `PREPARE`; may accompany `START` to pin which prepared runtime starts; None for `WITHDRAW`
  (validator). Min 1, max 32.
- `warden_url` (`str | None`): the swarm listener's URL the started Warden dials. Required for
  `START`, None otherwise (validator). Pattern `^wss?://\S+$`, max 512; the packet checks it
  against the endpoint rule of section 6 (`waggle.uris`) before it passes it on.
- `deadline_s` (`float`): seconds the step may take before the packet answers `FAILED`. Greater
  than 0.
- `reason` (`str`): the Queen's decision.

#### Colonized

Report how a colonization step ended (`PROTOCOL_MINOR` 13): the packet's answer to a `Colonize`,
correlated to it.

- `cell_id` (`CellId`): the Cell.
- `device_id` (`DeviceId`): the device behind it.
- `action` (`ColonizeAction`): echo of the order's step.
- `outcome` (`ColonizeOutcome`): `PREPARED` only after `PREPARE`, `STARTED` only after `START`,
  `WITHDRAWN` only after `WITHDRAW`; `FAILED` after any (validator).
- `level` (`RuntimeLevel`): the level the device is on after the step.
- `runtime_version` (`str | None`): the runtime version now installed (`PREPARED`) or running
  (`STARTED`). Set exactly when `outcome` is `PREPARED` or `STARTED` (validator); min 1, max 32.
- `warden_pid` (`int | None`): the started Warden's process id on the device, for the packet's own
  supervision of it. Set exactly when `outcome` is `STARTED` (validator); at least 1.
- `reason` (`str`): what happened, especially why a step failed.

#### Observe

Ask a device's packet what the device looks like, read-only (`PROTOCOL_MINOR` 13): a request from
the Warden of a level 0 device (whose Warden lives on the Hive Stand) to the device's Pollen
Packet. The packet answers with one correlated `Observation`, built from a fixed set of collectors
it runs itself: no lease, and no command a request could shape; the request chooses among the
collectors and names the roots to look inside, nothing more. The packet checks the sender's watch
capability for the Cell (`watch:<cell_id>`) before it collects anything and refuses with a
`control.error` `pollen.capability_denied` otherwise.

- `device_id` (`DeviceId`): the device asked; the packet's own.
- `kinds` (`tuple[ObservationKind, ...]`): what to report. Min 1, max 4, each kind at most once
  (validator).
- `roots` (`tuple[str, ...]`): directories to read logs from and watch for file changes, each
  inside what the Cell's watch capability and access level allow. Max 16, each min 1 and max 256
  (`MAX_OBSERVED_PATH_CHARS`, the bound of the paths reported under a root, so no root asks for
  what could never be reported back); non-empty exactly when `kinds` includes `LOGS` or
  `FILE_CHANGES` (validator). The Hive Stand sends the directories the operator named for the
  device (`hive swarm watch`) and, until they name some, asks for neither kind; the packet's
  collectors never follow a symbolic link at any depth under a root.
- `since_at` (`datetime | None`): the file-change cursor: report only changes after this instant.
  Only with `FILE_CHANGES` (validator); None asks for the packet's own recent window.
- `max_items` (`int`): the most items to return in each of the process list, the file changes and
  the log lines (the last further capped at 200). Between 1 and 500, default 200.
- `reason` (`str`): why the Warden looks now (a scheduled sample, a Patrol).

#### Observation

Report what a device looks like (`PROTOCOL_MINOR` 13): the packet's answer to an `Observe`, correlated
to it. Only the parts that were asked for are filled; the rest are empty or None. Everything in it
describes the operator's own machine and is `C2` on arrival: the Warden stores it as Bee Bread
(the warm memory tier) at that label, and it never travels further as it is. The bounds are
chosen so that a maximal answer of ordinary text encodes to well under `MAX_FRAME_BYTES`
(about 620 KB, signature included); the packet sizes by the encoded frame (section 5), leaves out
what does not fit and says so in `is_truncated`.

- `device_id` (`DeviceId`): the device observed.
- `observed_at` (`datetime`): when the packet took the sample.
- `processes` (`tuple[ProcessSample, ...]`): the process list when `PROCESS_LIST` was asked for,
  busiest first, so a cut at `max_items` loses the least busy. Max 500.
- `resources` (`ResourceSample | None`): resource use when `RESOURCE_USE` was asked for; None
  otherwise.
- `log_lines` (`tuple[LogLine, ...]`): the newest log lines when `LOGS` was asked for, oldest of
  them first. Max 200.
- `file_changes` (`tuple[FileChange, ...]`): changes after the cursor when `FILE_CHANGES` was
  asked for, oldest first, so the last one's time is the next request's `since_at` with nothing
  missed. Max 500.
- `is_truncated` (`bool`): true when the packet left something out to stay within a bound: more
  items than `max_items` or the caps allow, a path over its bound (a cut path would name another
  file, so it is skipped), or a frame that would be too large; the Warden then knows the sample is
  a lower bound.
- `reason` (`str`): what the packet did, especially why a part is missing.

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

### 8.12 basket (`messages/basket/`)

The Basket is the Hive's store of content-addressed, signed blobs: an archive, a wheel, a dataset,
a tool package. A blob is **inert**: bytes travel and permission does not, so holding or
receiving one grants nothing, and only a Comb promotion (section 8.8) makes a blob executable, by
naming its hash as a tool's package. Honey describes a blob, the Comb permits it, the Basket
stores it. This family moves a blob between the Hive Stand (where the Queen keeps the store) and a
Cell's Warden (which keeps a bounded resident copy), always through the Queen, because nodes
connect out to her; a Warden-to-Warden shipment is two legs. `BasketOffer`, `BasketRefuse` and
`BasketRefusal` live in `basket/offer.py`; `BasketChunk` and `BasketAck` in `basket/chunk.py`.

Family enums:

- `BasketRefusal`: `CLEARANCE` (the blob's label is above what the receiving Cell may hold; a
  `NIGHT_VEIL` Cell takes nothing above `C1`, so nothing from a Real Cell), `TAINTED` (the blob
  came from an isolated or quarantined source), `NO_ROOM` (not even evicting every older blob
  would fit it under the Cell's resident-Basket ceiling), `UNKNOWN_SIGNER` (the manifest was
  signed by a node the receiver does not trust), `INTEGRITY` (the assembled bytes do not hash to
  the offered digest, or the manifest signature failed), `PLATFORM` (none of the blob's platform
  tags fits the receiving Cell), `UNKNOWN_BLOB` (the sender holds no blob by this digest).

Transfer sequence (receiver rules). The sender sends a `basket.offer`; the receiver checks every
claim against its own rules and answers with a `basket.refuse` or a `basket.ack` whose
`received_bytes` says where to start (0 for a new shipment, more when it kept a partial temp file
of the same digest). The sender then sends `basket.chunk` messages in offset order, each answered
by a `basket.ack` carrying the new contiguous length. The last chunk (`is_last`) makes the receiver
verify the sha256 of the whole and the manifest signature before keeping a byte outside its temp
file; its ack has `complete` true and the blob is then resident. A mismatch is a `basket.refuse`
with code `INTEGRITY`, the temp file is deleted, and the receiver records a
`guard.basket_integrity_failed` event. A chunk whose `offset` is not the receiver's
`received_bytes` is not kept: the receiver re-acknowledges with its real count and the sender
resumes from it, so a dropped link or a duplicate costs one round trip. A shipment is resumable by
hash: after a reconnect the sender offers again and reads `received_bytes` from the ack. A chunk
that would run past `size_bytes`, or an `is_last` chunk that does not end exactly there, is an
`INTEGRITY` refusal.

Correlation. A `basket.ack` answers either an offer or a chunk, but the registry gives a reply one
parent (section 3), so it is registered as a reply to `basket.chunk`; the ack to an offer carries
the offer's `id` as its `correlation_id` by this documented convention, and the ack to a chunk
carries that chunk's. `basket.refuse` is registered as a reply to `basket.offer`; one sent
mid-transfer (`INTEGRITY`) carries the offending chunk's `id`. Every transfer ends in exactly one
`basket.refuse` or one `basket.ack` with `complete` true, or in a sender-side timeout (every await
on the wire has one); a receiver that cannot match an ack or refusal to a transfer it made drops
it at debug level.

#### BasketOffer

Offer one blob to a Cell's Warden, or to the Queen. It carries no bytes, so the receiver can
refuse by rule before the first chunk moves.

- `sha256` (`str`): digest of the complete blob; with the envelope `sender` it is the key every
  chunk of one shipment shares. The sha256 pattern.
- `size_bytes` (`int`): length of the complete blob. Between 1 and 68719476736 (64 GiB), a sanity
  bound; every receiver's own ceiling is far lower.
- `media_kind` (`str`): a descriptive tag (`wheel`, `archive`, `dataset`, `tool_package`,
  `text`). Pattern `^[a-z][a-z0-9_]{0,63}$`. It names what the blob is for a reader and grants
  nothing.
- `media_type` (`str`): MIME type of the content. Min 1, max 128.
- `platform_tags` (`tuple[OsFamily, ...]`): platforms the blob is built for; empty means
  platform-independent. Members unique (validator).
- `clearance` (`HoneyClearance`): the blob's data-sensitivity label, set at deposit from its
  provenance as `max(declared, floor)`, the floor being `C2` for a blob from a Real Cell. A
  receiver whose ceiling is lower refuses it (receiver rule). The floor is restated for the Basket
  (it is the same rule the Honey Store applies to Nectar) so the two Layer 2 stores never import
  each other.
- `origin_cell_id` (`CellId`): the Cell the blob was first deposited from.
- `origin_task_id` (`TaskId | None`): the task it came from; None when no task was involved.
- `origin_bee` (`WorkerId | WardenId | None`): the bee that produced it; None when unknown.
- `tainted` (`bool`): true when the provenance names an isolated or quarantined source. A
  tainted blob is never shipped or loaded, and a receiver refuses it with `TAINTED` (receiver
  rule).
- `manifest_json` (`str`): the signed `BlobManifest` (the blob's record, the signer's node id and
  an Ed25519 signature over those canonical bytes, made with the Hive key at deposit) as JSON
  text. Max 8192; must parse as a JSON object (validator). The receiver verifies the signature
  against the Hive key it trusts before keeping a byte (receiver rule).
- `destination_cell_id` (`CellId`): the Cell whose resident Basket should hold the blob; the
  recipient's own Cell, or one it relays for (receiver rule).
- `reason` (`str`): why the blob is being shipped now.

Receiver rules. A Warden accepts a `basket.offer` only from a `hive` sender. The Queen accepts one
only from a Warden whose Cell is `origin_cell_id` (a deposit from a Cell) and never from a Worker;
she re-labels the blob at her own deposit, never trusting `clearance` or `tainted` downward.

#### BasketChunk

Carry one slice of a blob. A request answered by a `basket.ack` (or a `basket.refuse`).

- `sha256` (`str`): digest of the complete blob this chunk belongs to. The sha256 pattern.
- `offset` (`int`): byte offset of this slice within the blob. At least 0.
- `data` (`str`): this slice as standard padded base64 text (RFC 4648 section 4, the standard
  alphabet; a URL-safe character or a line break is refused). Max 349528 characters, and it must
  decode to between 1 and `MAX_CHUNK_BYTES = 262_144` raw bytes (validator). A 256 KiB chunk is
  341 KiB of text, so a frame always fits `MAX_FRAME_BYTES` (section 5).
- `is_last` (`bool`): true on the final chunk.

#### BasketAck

Acknowledge an offer or a chunk. A reply.

- `sha256` (`str`): the blob acknowledged. The sha256 pattern.
- `received_bytes` (`int`): contiguous bytes from offset 0 the receiver holds in its temp file;
  the sender resumes from here. At least 0.
- `complete` (`bool`): true once the whole blob is held, its sha256 matched and the manifest
  signature verified.
- `reason` (`str`): a one-line note for the trail; empty when there is nothing to add.

#### BasketRefuse

Refuse a blob, before the first chunk moves or on an integrity failure after the last. A reply.

- `sha256` (`str`): the blob refused. The sha256 pattern.
- `code` (`BasketRefusal`): the stable reason code.
- `reason` (`str`): which rule, which limit.

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
- `WebSocketServer(codec, *, host="127.0.0.1", port=0, **options)` offers `start()`, `port` and
  `uri` properties, `connections() -> AsyncIterator[WebSocketTransport]` and `close()`; it owns
  its handler tasks (structured concurrency, no dropped tasks). The optional keywords are the
  typed `ServerOptions`: `limits` (`ServerLimits`, below), `ssl_context` (serve `wss://`; `uri`
  then starts with `wss://`), `on_refusal` (an async callback handed each `ConnectionRefusal`),
  `clock` (the injected `Clock` the limits read) and `open_timeout_s`. Without `limits` and
  `ssl_context` the listener is the plain one described above, unchanged.
- Keepalive is the WebSocket ping: `PING_INTERVAL_S = 20.0`, `PING_TIMEOUT_S = 20.0`.
- Close codes: `1000` normal; `1002` protocol error (malformed frame, unsupported major, unknown
  kind); `1008` policy violation (missing, unknown or invalid signature); `1009` message too big;
  `4029` frame ceiling (the application range of RFC 6455 section 7.4.2): the peer sent frames
  faster than the listener's per-connection ceiling, with the reason
  `waggle.transport.frame_ceiling`. Only the server half sends `4029`; the peer's `receive` sees
  a lost link (`waggle.transport.connection_lost`) and reconnects with its usual backoff.
- Where a Cell's tier routes its link through a VPN or Tor (a `NIGHT_VEIL` Cell reaches the Hive
  Stand through a hidden-service endpoint), only the URI and the network path differ; the
  transport and the protocol are unchanged.
- `DialOptions.socks_proxy_url` names a SOCKS proxy on the dialler's loopback (`socks5h://` or
  `socks4a://`, never a scheme that resolves locally, never credentials). Every attempt then
  opens its TCP connection through the proxy, the destination named to it (RFC 1928 CONNECT with
  a domain-name address, no authentication; or SOCKS4a), bounded by `PROXY_CONNECT_TIMEOUT_S`
  (10 s) to reach the proxy and `PROXY_REPLY_TIMEOUT_S` (120 s, Tor's own SOCKS timeout) for its
  answer, and the WebSocket handshake runs over that socket. A refusal, an early close or a
  timeout is `waggle.transport.proxy_failed`, retried with the same backoff through the same
  proxy; a proxied transport never falls back to a direct connection. A `ws://` onion URI with no
  proxy is refused at construction.

**Receiver obligations at the listener** (`waggle/transport/replay.py`, `ceiling.py`,
`limits.py`). Receivers must deduplicate by envelope id within a bounded window, and a listener
must bound what one connection can make it do. A `WebSocketServer` given
`limits=ServerLimits(replay=ReplayLimits(...), ceiling=CeilingLimits(...))`, either of which may
be `None` to switch that check off, therefore:

- **Counts every frame** a connection delivers, valid or not, against that connection's own
  token bucket *before* anything is parsed: `max_frames_per_minute = 6000` (a hundred a second,
  well above any heartbeat and result traffic) with a `burst` of 600 by default. Over it the
  listener reports the refusal, closes the connection with code `4029` and the reason
  `waggle.transport.frame_ceiling`, and its `receive` raises `FrameCeilingError`. Below the
  ceiling nothing changes.
- **Drops a duplicate envelope id.** Once the codec has decoded (and, with a verifier, verified)
  a frame, its `(node_id, id)` is checked against one replay window shared by every connection
  the listener accepts: the ids seen over the last `window_s = 600` seconds, at most
  `max_ids = 50_000` of them, the oldest forgotten first. A duplicate is dropped without being
  handed to the receiver, and the connection stays open: a lost ack followed by an outbox replay
  (section 10) is idempotent, and a signed frame replayed on a fresh connection is caught as well
  as one replayed on the old. The same id from two nodes is two ids. This window is the
  listener's bounded first line; the inbox's longer per-sender memory (section 3) stays the
  authority on what was processed.
- **Reports, but writes no trail.** waggle has no trail, so each refusal is handed to the
  caller's `on_refusal` callback as a `ConnectionRefusal` (`node_id`, `kind` = `REPLAY` or
  `FRAME_CEILING`, `envelope_id`, `at`) before the duplicate is dropped or the connection closed;
  the Hive Stand's listener wrapper records it. A `FRAME_CEILING` names the node of the last
  envelope the connection decoded, or none when it never decoded one.

The client end and the in-process pair apply neither check. Every other receiver of Waggle
frames, a Pollen Packet's gateway included, carries the same duplicate-drop obligation on its
own side.

**TLS and certificate pinning.** Confidentiality is the link's (section 6), and off the Hive
Stand's own machine the link is `wss://`. A `WebSocketServer` given an `ssl_context`
(`waggle.transport.tls.server_ssl_context(cert, key)`, TLS 1.2 or later, asking clients for no
certificate: a node proves itself with signatures) serves `wss://`. The listener's certificate is
usually self-signed and often addressed by IP, so a dialling node does not verify it against a
certificate authority: `DialOptions.tls_pin_sha256` is the SHA-256 of the certificate's DER
bytes, 64 lowercase hexadecimal characters (anything else is a `ValueError`), and with it the
dial trusts that one certificate, checks no host name, and compares the peer's certificate with
the pin as soon as the connection opens, before any frame is sent. A mismatch (another
certificate, or none) aborts the connection and raises `waggle.transport.tls_pin`
(`TlsPinMismatchError`, a `ConnectFailedError`) at once: it is never retried, because a
different certificate is a different machine or an attacker in the path, not a flaky link. The
pin works through a SOCKS proxy too, since the TLS handshake runs over the proxied socket. A
`wss://` URI with no pin keeps the default certificate-authority verification, and a pin on a
`ws://` URI is refused at construction.

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
- A send that returned has put the frame on a connection and no more. A connection whose far end
  vanished without a close frame (a cable pulled) is not known to be dead until the keepalive gives
  up, `PING_INTERVAL_S + PING_TIMEOUT_S` (40 s) after the last traffic, and a frame sent in that
  time returns as sent, is acknowledged by the outbox (or was never queued, being sent live) and
  is lost. `waggle.transport.recent` is the sender's answer: `RecentFrames`, a pure ring of the
  envelopes a node sent, bounded by `MAX_RECENT_FRAMES` = 256, by `MAX_RECENT_BYTES` = 4 MiB and,
  when it is asked, by the time (a frame is due only if it was sent less than `UNNOTICED_S` = 80 s
  before the connection ended and less than `RESEND_HORIZON_S` = 540 s, the receiver's replay window
  less a minute, before the new connection), and `RememberingTransport`, which wraps a Transport,
  remembers each frame it sent whole and sends the due ones again, oldest first, under the ids they
  had. A node that must not lose a frame sends them after its announcement and before it replays its
  outbox, and forgets them when the peer ended the connection with a close frame, which proves it
  read everything before it. Nothing is added to the wire: a frame sent again is the envelope that
  was sent, and the receiver's replay window (section 9) drops it when the first copy arrived. The
  Warden chooses the frames by its offline policy (what would be queued or held), the Pollen Packet
  by what its outbox holds the kinds of (gateway-contract section 14).
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
- **Recent-frames tests** (`packages/waggle/tests/transport/test_recent_ring.py` and
  `test_recent_remembering.py`): the ring's bounds by count, bytes and time, its order, what `due`
  offers and when it stops, `clear`; a `RememberingTransport` that sends the due frames again under
  their ids, oldest first, remembers only what was sent whole and leaves the ring as it found it
  when a send fails.
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
    needs in phase 1; a task inherits its tiers from the placed Cell. `PROTOCOL_MINOR` 6 adds only
    `TaskAssign.exoskeleton` and `TaskAssign.network_scopes`, the needs a Warden acts on (it
    attaches the Exoskeleton and sizes the Worker's `net` capabilities); the rest of a task's
    needs stay placement's business.
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
26. **`TaskAssign` carried no capabilities field** until `PROTOCOL_MINOR` 8: the Warden attenuated
    its own set locally in phase 3, and roadmap step 10.3 added `capabilities` (the goal's set)
    as the additive minor this note anticipated.
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
34. **A tool package travels as a Basket blob, so there is no `tool.package` kind.** Resolved in
    1.12: `tool.promoted` carries the package digest and size, and the bytes follow through the
    `basket.*` messages of section 8.12 like any other blob, verified by hash and signature before
    a byte is kept. Reusing `session.put_file` was rejected because it addresses a device and not
    a content hash, and cannot be resumed by digest.
35. **Tool retirement has its own kind.** Resolved in 1.12: a rollback stays a `tool.promoted` of
    the earlier version with `supersedes_version`, and a plain retire is `tool.retired`
    (section 8.8), so a registry that withdraws a version without replacing it is not forced to
    invent a promotion.

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
