# ADR-0005: Envelope signing with Ed25519 and the file-backed outbox

- Status: Accepted
- Date: 2026-09-07

## Context

Every message that crosses Waggle (the shared messaging protocol between bees, named after the
honeybee's waggle dance) rides in an envelope, the fixed outer record that says who sent what to
whom, when, in which version of the protocol, and whether it is a request, the reply to one, or an
event nobody asked for; roadmap step 1.1 fixes the envelope's fields before any code depends on
them and step 1.7 signs it. Codingrules section 15 requires every Waggle message to a Pollen Packet
(the thin gateway installed on an enrolled external device) to carry a signature over the envelope
that the packet verifies before executing anything, because a device command has the largest blast
radius in the system, and ADR-0004's transport is a plain WebSocket that authenticates nothing by
itself: the link can be carried by a VPN overlay, mutual TLS, the Hive Entrance (the authenticated
gateway that is the only door into the Hive) or a Tor hidden service, and none of those hops can
vouch for who composed a message at the far end. The same section requires a disconnected Warden
(the always-on supervisor of one Cell, a Cell being a unit of compute a task runs on) to keep
working within what it owns and to queue its results and Alarms (issues a bee cannot resolve,
escalated up the chain) until it reconnects, and roadmap 11.3 gives the Pollen Packet an outbox of
its own, so a node needs a durable, ordered place to keep what it could not send, one that survives
a crash and replays without duplicating; ADR-0004 gives the transport an at-most-once guarantee and
no reconnect inside `receive()` precisely so that this outbox is where retries live. Pollen must
run with only `pydantic`, `websockets` and `cryptography` installed, on hardware such as a
Raspberry Pi where signature verification cost and SD-card write cost both matter; codingrules
section 10 requires errors that cross Waggle to be an `ErrorMessage` with a stable `code`, and
section 8.5 requires data to flow as immutable values. Later phases already lean on what is decided
here: roadmap 10.4 keeps an Ed25519 keypair for every Waggle-side principal, 10.5b signs webhooks
with the Hive key (the key that vouches for Hive-wide notices), 13.2a sends a `QueenMoved`
relocation notice signed with the Hive key and moves the Hive keypair only through the secret
store, never over Waggle, and 11.7a needs a canonical form that a gateway in another language can
reproduce byte for byte.

## Decision

An envelope is a frozen pydantic model with `extra="forbid"` and exactly ten fields, in this order.
`id` is a `msg_<ULID>` minted through ADR-0003's `new_message_id(clock)`, unique per message and
sortable by creation time; it is what a reply points at and what the outbox deduplicates on.
`correlation_id` ties a message to the one it answers or follows from: the registry records each
kind's shape as `REQUEST`, `REPLY` or `EVENT`, a reply must carry its request's `id` here, a
request must carry `None`, and an event may carry the message it follows from (`task.progress`
points at its `task.assign`, `session.output` at its `session.exec`), with `control.error` a reply
to any request. `sender` and `recipient` are bee addresses, ids whose kind is HIVE (the Queen, the
central orchestrator), WARDEN, WORKER (a subagent a Warden spawns) or DEVICE (a Pollen Packet), so
routing and the Pheromone Trail (the system's append-only audit log) know who spoke to whom without
opening the payload. `kind` is `<family>.<snake_name>`, must be registered and must equal
`registry.kind_for(type(payload))`, so a receiver picks the model before validating. `version` is
`"<major>.<minor>"`, `PROTOCOL_VERSION = "1.0"` today; additive changes bump minor, breaking
changes bump major, and a receiver rejects an unknown major with `waggle.version.unsupported_major`
while accepting any minor. `sent_at` is a timezone-aware UTC `datetime` from the injected `Clock`,
naive values rejected, for ordering on the trail and for staleness checks by receivers. `node_id`
names the process that sent the envelope, which is not the same thing as the bee: a Warden and the
Workers running inside the Queen's process share one node, and signing keys are per node. `payload`
is the typed message, `SerializeAsAny[WaggleMessage]` so the subclass survives. `signature` is the
standard padded base64 of an Ed25519 signature over the canonical bytes, or `None` when unsigned.
The canonical bytes are the wire dictionary minus `signature`, dumped as compact ASCII JSON with
sorted keys (`json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=True)`), and both
signing and verification run over the raw wire dictionary that was actually sent, never over a
re-validated model, so a default filled in by validation, a float re-rendered or a key reordered
can never break a signature that was good on the wire. Signatures are Ed25519 through
`cryptography`: 32-byte keys, 64-byte signatures, verification fast enough for a Raspberry Pi, and
deterministic signing that needs no per-signature nonce, so a weak random source on a device cannot
leak a private key. `Ed25519Signer` holds one node's private key and `Ed25519Verifier` maps
`node_id` to a raw 32-byte public key, immutably (`with_key` returns a new verifier); public keys
are hex-encoded wherever a human or a manifest sees them. In phase 1 the Hive Stand's node key (the
Hive Stand being the machine the Queen runs on) doubles as the Hive key, the key that signs
`QueenMoved`; phase 10 or 13 may introduce a separate Hive keypair held in the secret store, and
nothing here prevents it, because verification is keyed by `node_id`, not by role. Policy lives in
the codec and nowhere else: a `Verifier` configured means every frame must be signed
(`MissingSignatureError` for an unsigned frame, `UnknownSignerError` for a node without a key,
`InvalidSignatureError` for a bad one, all under `SignatureError`), no `Verifier` means signatures
are ignored, which is the in-process memory transport's case, and the transports carry frames with
no opinion either way; the codec declares `Signer` and `Verifier` as structural protocols so it
never imports the signing module. The outbox is `Outbox(path, codec)`: one append-only JSONL file
of unsigned envelopes, each record either `{"op": "put", "frame": ...}` carrying the unsigned wire
dictionary or `{"op": "ack", "id": ...}`, flushed and `fsync`ed on every append, compacted to
pending-only records on open, with a torn trailing line (a crash mid-write of a record whose
`append()` never returned) ignored on open and any other malformation raised as
`OutboxCorruptError`; appending an id already pending is a no-op, `pending()` returns FIFO order,
and `replay_outbox(outbox, transport)` sends pending in order, acks each after a successful send
(the `ack` under `asyncio.to_thread`), and stops at the first transport error leaving the tail
pending. Replay re-encodes through the sending transport's codec, so signing always happens at send
time with the node's current key: the outbox is the node's own durable memory, never a trust
boundary, and a key rotated between a crash and its replay still yields valid signatures.

## Consequences

Positive: every envelope that crosses a machine boundary is bound to the node that composed it, and
a Pollen Packet verifies a command with one dictionary lookup and one Ed25519 check before it
touches the device, which is codingrules section 15's rule made mechanical. The canonical form is
sorted, compact, ASCII JSON, which any language's JSON library can reproduce, so the phase 11.7a
gateway contract inherits a signature scheme with no HiveMind-specific canonicalisation to port.
Verifying over the raw wire dictionary means the bytes that were signed are the bytes that are
checked, so a pydantic default or a minor-version field addition can never turn a good signature
bad. Putting the policy in the codec means no transport can forget to sign or forget to verify, and
the in-process case costs nothing because it configures no verifier. The correlation rules are
enforced at validation time from the registry's shape, so a reply without a `correlation_id` or a
request with one is rejected before it is sent, and the trail can reconstruct every request/reply
pair and every event's lineage from envelopes alone. The outbox's single log file has one ordering,
the file's, and one `fsync` per append, and a crash loses at most the record whose `append()` never
returned; replay is idempotent by construction (an acked id is gone, a pending id is never appended
twice) and is property-tested for FIFO order, for a second replay sending nothing, and for a crash
mid-replay leaving exactly the tail. Storing unsigned envelopes means a stolen outbox file yields
nothing a peer would accept, and a rotated key needs no rewrite of what was queued.

Negative: per-node keys mean per-node key distribution: a verifier must know a node's public key
before the first frame, which in phase 1 is manual (the demo script generates keypairs and hands
them across), and only the phase 11.1 enrolment handshake makes it automatic; there is no
revocation in phase 1 beyond dropping a key from the verifier. A signature authenticates but does
not encrypt, so confidentiality of a Waggle link comes entirely from the transport wrapping it, and
the envelope layer does not itself reject a replayed signed frame; receivers that care must
deduplicate by `id`, and nothing in this ADR obliges them to yet. The canonical form is tied to the
exact rendering rules of Python's `json.dumps`, so the spec must pin number formatting and forbid
non-finite floats, and the gateway contract needs a signed test vector to prove another language
matches byte for byte. An `fsync` per append costs milliseconds on an SD card, which is what a
Raspberry Pi writes to, and compaction only on open means a long-running node's outbox grows with
acks until restart. While the Hive Stand's node key doubles as the Hive key, rotating one rotates
the other, so the eventual split in phase 10 or 13 is a migration, not a drop-in. A single
`Verifier` per codec means a node that talks to many peers holds all their keys in one map, which
is right for the Hive Stand and for a device that only ever talks to the Hive Stand, but any future
many-to-many topology would need the verifier composed differently.

## Alternatives considered

HMAC with a shared secret: cheaper to compute and no key distribution problem, but every node would
hold the same secret, so one compromised Pollen Packet could forge commands from the Queen to every
other device, and nothing could later prove which node composed a message.

TLS only, with no message signatures: mutual TLS authenticates the connection but not the message;
an envelope replayed from an outbox after reconnection, relayed through the Hive Entrance or
carried over a Tor hidden service arrives on a different connection from the one its author made,
and a `QueenMoved` has to stay verifiable after being stored on a device, so the signature has to
travel with the message.

JWS/JOSE: a standard with libraries, but it is built around tokens with header machinery Waggle
does not need, its compact serialisation base64url-encodes the payload so a frame is no longer
readable in a log, and the library is a fourth dependency Pollen may not have.

RSA or ECDSA: RSA keys and signatures are an order of magnitude larger and slow to generate on a
Raspberry Pi, and ECDSA needs a fresh high-quality nonce per signature so a weak random source
leaks the private key, a failure mode Ed25519's deterministic signing does not have.

A SQLite outbox: transactional and indexed, but it is far more machinery than a queue of a few
hundred envelopes needs, its file is opaque to `tail`, and its crash guarantees depend on
journal-mode and synchronous settings that would have to be got right on every OS, where a single
append-only file with one `fsync` has exactly one behaviour.

One file per envelope: simple to write, but the order then depends on file names and directory
listing, durability needs a directory `fsync` per file on Linux, and an ack becomes a delete with a
race of its own, where a single log has one ordering and one append.

Storing signed frames in the outbox: saves re-encoding at replay, but a signature made before a
crash may be under a key that has since rotated, a peer's verifier would then reject the whole
tail, and the file becomes worth stealing, where storing unsigned envelopes keeps the outbox a
private memory and signing at send time keeps every replayed frame current.
