# ADR-0004: Waggle transport: JSON envelopes over WebSocket

- Status: Accepted
- Date: 2026-09-07

## Context

Waggle (the shared messaging protocol between bees, named after the honeybee's waggle dance) is the
one channel on which the Queen (the central orchestrator), every Warden (the always-on supervisor
of one Cell, a Cell being a unit of compute a task runs on), every Worker (a subagent a Warden
spawns) and every Pollen Packet (the thin gateway installed on an enrolled external device) talk to
each other; codingrules section 8.6 keeps it strictly apart from the second channel, the HTTP calls
a model adapter makes to whichever provider serves a model, so where a model runs never changes how
bees talk. Phase 1 has to pick the encoding those messages take on the wire and the transport that
carries them, and codingrules section 8.1 lists `Transport` among the mandatory protocols that get
their own ADR when first implemented, because both choices are hard to reverse once every
subsystem, the Hive Entrance (the authenticated gateway that is the only door into the Hive) and
the phase 11.7a gateway contract for gateways written in other languages depend on them. Pollen
must install on constrained hardware such as a Raspberry Pi with only `pydantic`, `websockets` and
`cryptography` installed (phase 1's exit criterion), which rules out any encoding that needs a
schema compiler, generated stubs or a native library. Codingrules section 15 and Appendix A.1 give
a new Virtual Cell (a VM or container the Hive, the on-demand Virtual Cell fleet, provisions for
one task) no inbound ports, and CLAUDE.md allows no public exposure mode at all: a Cell's Warden
and a device's Pollen Packet connect out to the Hive Stand (the machine the Queen runs on), never
the reverse, so the transport must work with exactly one listener in the whole system. Codingrules
section 8.8 requires a disconnected Warden to keep working within what it owns, queue its results
and Alarms (issues a bee cannot resolve, escalated up the chain) and retry reconnection with
backoff, and section 15 forbids it from growing while offline, so the transport has to make a
dropped link visible to the caller rather than paper over it, and durability has to live in the
caller's outbox (ADR-0005), not in the link. Codingrules section 11 requires everything to be
`asyncio`-native with structured concurrency, a named timeout constant on every external await, and
every sleep routed through the injected `Clock` from ADR-0003, so that reconnect logic can be
tested at simulated speed. The Pheromone Trail (the system's append-only audit log) and every log
line are better for a frame a human can read without a decoder, since the log is the first thing
anyone opens when two bees disagree. Finally, section 15 carries every remote link over a VPN
overlay or mutual TLS, and the README routes a Night Veil Cell's Waggle link (the strictest Comb
Shield security tier, all traffic through Tor) to a hidden service, so whatever is chosen must ride
an ordinary TCP stream that those overlays can wrap without the protocol knowing.

## Decision

Waggle's wire format is JSON and its inter-process transport is WebSocket, through the `websockets`
library's `asyncio` API. One WebSocket frame carries exactly one envelope (the fixed outer record
every message rides in, ADR-0005): a single UTF-8 JSON object whose keys are the envelope fields
and whose `payload` is the message's `model_dump(mode="json")`, never split across frames and never
batched. JSON wins over msgpack, CBOR and protobuf because a frame is readable in a log line and on
the Pheromone Trail as it is, because pydantic serialises and validates it natively with no extra
code, and because it needs no schema compiler, generated stubs or native extension on a Raspberry
Pi, so Pollen's three dependencies stay three. Frames are WebSocket binary frames only; a text
frame is a protocol error (`MalformedFrameError`, close code 1002), so there is exactly one frame
kind to reason about and no way for two peers to disagree about encoding by accident.
`MAX_FRAME_BYTES` is 1 MiB, matching the `websockets` default `max_size`; a session file chunk plus
envelope overhead fits, anything larger is chunked by the sender (a chunk carries `offset` and
`final`) and never sent as a bigger frame, and a receiver rejects an oversized frame with
`FrameTooLargeError` and close code 1009. Keepalive is WebSocket ping/pong at the transport
(`PING_INTERVAL_S` and `PING_TIMEOUT_S`, both 20 seconds and commented), which says only whether
the link is alive; it is distinct from the bee-level `Heartbeat` message, which carries
`ContextTelemetry` (tokens used, current goal, blockers, spend) and belongs to supervision, so a
live link with a stuck bee and a dead link with a healthy bee are told apart. A
`WebSocketTransport` wraps one connection, client or server side;
`WebSocketClientTransport.connect()` dials out with capped exponential backoff through the injected
`Clock` (`RECONNECT_INITIAL_S` 0.5 seconds, `RECONNECT_FACTOR` 2.0, `RECONNECT_MAX_S` 30 seconds
and an `open_timeout` per attempt, each a commented constant) and raises `ConnectFailedError` after
`max_attempts`. There is no automatic reconnect inside `receive()`: it is an async generator that
ends normally on a clean close and raises `ConnectionLostError` on a dropped link, `send()` raises
`TransportClosedError` after `close()` and `ConnectionLostError` while disconnected, and `close()`
is idempotent; after a drop the caller's loop calls `connect()` again and then replays its outbox,
so the delivery guarantee at this layer is at-most-once with ordering preserved within one
connection, and every retry and every durable copy is explicit in the caller's code rather than
hidden in the link. `WebSocketServer` binds `127.0.0.1` and an OS-assigned port by default, owns
its handler tasks under structured concurrency, and is the only listener in the system: every Cell
and every device connects out to the Hive Stand, so a Virtual Cell keeps its no-inbound-ports
default and a Real Cell (an existing device borrowed for a task and left as it was found) opens
nothing. A frame that fails to decode, whether malformed, oversized, of an unsupported major
version, of an unregistered kind or badly signed, makes `receive()` raise the `CodecError` or
`SignatureError` and the transport closes the connection (1002 for malformed, 1008 for a signature
policy failure, 1009 for too large, 1000 for a normal close), because a bad frame from a peer is a
bug or an attack, never a flaky link, and reconnect plus outbox replay handles the aftermath.
`MemoryTransport.pair()` moves bytes, not objects, through the same codec, so the size, malformed
and signature checks are real in-process and the memory transport is a faithful stand-in for the
WebSocket one in the conformance suite that is parametrised over both; it ships `inject_frame()`
and `drop()` as documented test hooks. Transports are policy-free carriers of frames: which frames
must be signed is the codec's decision (ADR-0005), never the transport's.

## Consequences

Positive: a frame is a JSON object, so a log line, a Pheromone Trail entry or a captured packet is
readable by a human and by `jq` with no decoder, and the phase 11.7a gateway contract can be
implemented in any language that has a JSON library and a WebSocket client. Pydantic does the
serialising and the validating, so there is no second schema to keep in step with the models, and
Pollen's dependency set stays at the three phase 1 allows. One frame per envelope and binary-only
frames mean the conformance suite has exactly one framing rule to test and a peer has exactly one
way to be wrong about it. Because the memory transport pushes the same bytes through the same
codec, every test that runs in-process exercises the real size, malformed and signature paths, and
a Warden and its Workers running inside the Queen's process in phase 3 use a transport whose
behaviour matches the one they will use across a machine boundary in phase 11. Keeping reconnect
out of `receive()` puts the at-most-once contract and the outbox replay side by side in the
caller's loop, where a reader can see which sends were lost and what is done about them, and lets
the `Clock` from ADR-0003 drive the backoff in tests without a real timer. A loopback default and
outbound-only connections mean no Cell ever listens on a port for Waggle, which is the blast-radius
rule from codingrules section 15 falling out of the design rather than being enforced on top of it,
and an ordinary TCP stream is what the VPN overlay, mutual TLS and the Night Veil Tor route already
know how to carry. Closing on the first bad frame keeps a buggy or hostile peer from wedging a
connection in a half-understood state, and separating WebSocket ping/pong from the `Heartbeat`
message keeps link liveness out of supervision telemetry.

Negative: JSON is larger and slower to encode than a binary format, and every `bytes` field
(session output, file chunks) pays base64's one-third overhead on the wire, which matters most on
exactly the constrained devices Pollen targets. The 1 MiB cap pushes chunking logic into the
session family and into every future message that might carry a large body, and a sender that
forgets to chunk learns it only from `FrameTooLargeError`. No automatic reconnect means every
long-running caller has to write the reconnect-then-replay sequence, and although the loop base
class from ADR-0003 is where it will be written once, it is a contract a new contributor has to
learn before a Warden or a Pollen Packet survives a dropped link correctly. One envelope per frame
rules out batching, so a burst of small messages pays per-frame overhead each time. `websockets`
becomes a hard dependency whose `asyncio` API HiveMind tracks across major versions, and its
default `max_size` is now load-bearing for the frame cap. Closing on any undecodable frame means
two nodes on incompatible major versions drop and redial each other on every attempt until one is
upgraded, which is deliberate but noisy on the trail, and binary-only frames mean a hand-driven
WebSocket client has to be told to send binary before it can talk to a Hive at all.

## Alternatives considered

A binary encoding, msgpack, CBOR or protobuf: each is smaller and faster than JSON, but msgpack and
CBOR add a dependency Pollen may not have and lose the readable log line, and protobuf adds a
schema compiler and generated stubs to a codebase whose models already live in pydantic.

gRPC: a mature request/reply and streaming transport, but it brings protobuf, a code generator and
`grpcio` wheels that are heavy on a Raspberry Pi, and its frames are opaque in a log where JSON
over WebSocket is not.

HTTP long-polling: works through any proxy and needs no persistent connection, but it turns one
ordered bidirectional stream into two half-duplex request loops with no ordering guarantee across
polls and no way for the Hive Stand to push a message the moment it exists.

ZeroMQ or nanomsg: excellent messaging patterns and framing, but each is a native library plus a
Python binding that Pollen may not install, neither speaks to a browser or rides a Tor hidden
service as plainly as WebSocket does, and their socket patterns fit one listener with many dial-out
clients no better than WebSocket already does.

Raw TCP with a length prefix: the fewest layers, but it reinvents the framing, keepalive, close
handshake and TLS handling that WebSocket already provides, and it cannot share the Hive Entrance's
listener or pass through an HTTP-aware proxy without more code than the layer it saves.

MQTT: designed for exactly the constrained devices Pollen targets, but it needs a broker as a third
process, its topic-based publish/subscribe model does not fit point-to-point request/reply with
correlation ids, and its client library is a fourth dependency.

An auto-reconnecting transport that redials inside `receive()`: friendlier to callers, but it hides
link loss from the very code that has to decide what was lost, so the at-most-once contract becomes
unknowable, the outbox replay becomes implicit, and neither can be tested deterministically through
the injected `Clock`.
