# ADR-0055: Every receiver deduplicates within a bounded replay window, and a connection has a frame ceiling

- Status: Accepted
- Date: 2026-10-13

## Context

This ADR amends ADR-0005, and it adds one close code to the list in ADR-0004 and in spec section 9.
ADR-0005 and ADR-0004 stay exactly as they were accepted, because an accepted ADR is never edited
(codingrules 7.7); read each of them together with the two additions below.

ADR-0005 signed the envelope (the fixed outer record every Waggle message, a message of the protocol
the bees use to talk to each other, rides in) and named a gap it left open: a signature
authenticates a frame but does not stop it arriving twice, so "receivers that care must deduplicate
by `id`, and nothing in this ADR obliges them to yet". For a node reached across a network that
stops being optional. A node (a process with a signing key of its own) that reconnects after a lost
link replays its outbox (the durable queue of what it could not send), which is the design, so an
envelope whose acknowledgement was lost arrives a second time as a matter of course. A duplicate
`session.exec` would run a command twice on a borrowed device, and any event that changes the
Queen's tables would be applied twice. Spec section 3 already asks every receiver to keep the id of
each envelope it processed and drop a duplicate, but it places that rule in the inbox, with seven
days of ids, no bound a small device can promise to keep and no record when it fires. Roadmap step
11.3b makes the obligation concrete and gives it a home in the transport, so that a duplicate never
reaches a handler.

The second gap is in ADR-0004. The WebSocket transport bounds the size of a frame, 1 MiB, but not
how many arrive. The Queen (the central orchestrator, running on the Hive Stand, the machine she
lives on) is one process with one event loop, and a faulty or hostile node can send small, valid,
correctly signed frames as fast as its link allows. Phase 11 puts devices the Hive does not control
on that listener, and roadmap step 13.4a wants the Guard Bee (the bee that watches for attacks) to
count such events as a pattern; isolating a Cell keeps its socket open, and only a Sting Cut (the
human's per-Cell emergency disconnect) invalidates a node's keys, so something at the transport must
be able to stop a flood by itself.

## Decision

**A receiver deduplicates within a bounded replay window.** `waggle/transport/replay.py` holds
`ReplayWindow`, a pure value over an injected clock. It remembers the envelope ids seen from each
node for `replay_window_s` (default 600) and keeps at most 50,000 ids per connection (default),
forgetting the oldest first, so its memory is bounded whatever the peer sends. The server half of
the WebSocket transport asks it about every frame, drops a duplicate without handing it to the
receiver and reports it through a callback. The Pollen Packet (the thin gateway installed on a
device) keeps the same window on its side of the link. The defaults live in `ServerLimits`, which
`WebSocketServer` takes as an optional argument.

**What a duplicate means depends on what just happened.** A duplicate inside the reconnection grace
that follows a `warden.reconnected` (the trail event for a Warden, a Cell's always-on supervisor,
coming back) is expected, because an outbox replay after a lost acknowledgement produces them, and
it is dropped silently. Any other duplicate is a `guard.replay_refused` event carrying the node id.
`waggle` writes no trail (the Pheromone Trail, the Hive's append-only audit log), so it reports each
refusal through an `on_refusal` callback that carries a `ConnectionRefusal` (the node id, the kind
`REPLAY` or `FRAME_CEILING`, the envelope id when there is one, and the time), and the Hive Stand's
listener wrapper records the event.

**A connection has a frame ceiling.** `waggle/transport/ceiling.py` holds `FrameCeiling`, a token
bucket over an injected clock: `max_frames_per_minute` (default 6,000, a hundred a second and far
above any Warden's heartbeat and result traffic) with a burst allowance (default 600). A connection
over it is closed by the server with code `4029` and the reason `waggle.transport.frame_ceiling`;
the code is in the range RFC 6455 reserves for private use. The peer reconnects with its usual
backoff, so a short burst costs a reconnection and a persistent flood shows as repeated closes. The
listener wrapper records `guard.frame_ceiling` with the node id on each close. Below the ceiling
nothing changes. Closing is a narrowing action the transport takes alone; the Guard Bee only counts
the events, and repeated ceilings from one node in a window are a dire pattern for it.

**The gateway contract carries both obligations.** A gateway written in another language (ADR-0051,
`docs/waggle/gateway-contract.md`) is held to the replay window and treats a `4029` close like any
other, a reason to back off and redial, so neither protection depends on what runs on the device.
`waggle/errors.py` gains `FrameCeilingError` with the code `waggle.transport.frame_ceiling`, and the
spec's code table and its list of close codes gain the new entries.

## Consequences

Positive: a repeated command cannot run twice, and an outbox replay stays idempotent without any
receiver having to remember to make it so. A flood of small frames costs the Queen a bounded amount
and leaves a record with the node id on it. Both rules are small pure values over an injected clock,
so they are tested without sockets and without sleeping, and the transport has one place for each.
They sit below the codec and the inbox, so a duplicate or an excess frame is refused before any
handler or model-facing code sees it. A compiled gateway inherits the same protections by following
the contract.

Negative: the window is shorter than the seven days spec section 3 asks inboxes to remember, so a
duplicate that arrives more than a window after the original is not caught here; the inbox's own
record of what it processed stays the second line for receivers that keep one, and this ADR does not
extend it. The cap bounds memory at a price: at the ceiling's sustained rate, 50,000 ids is about
500 seconds, so under a flood the cap and not the clock sets the window. A close loses the frames in
flight on that connection; the outbox and the request timeouts already cover that, and a replay cut
short by the ceiling resumes from its unsent tail without losing or repeating anything. A node with
a very long outbox must pace its replay under the ceiling or it will be closed part way. Closing a
connection is a blunt tool: it also interrupts the legitimate traffic of a node that merely
misbehaved once.

## Alternatives considered

Deduplicating only in each inbox: it is the rule spec section 3 already states, and it leaves the
choice, the bound and the record to every receiver, so a new implementation could omit it, and a
duplicate would reach a handler before anyone noticed. An unbounded or seven-day set of ids in the
transport: a constrained device cannot promise that, and a hostile peer could grow it. A persistent
id store in SQLite: far more machinery than a window of recent ids needs, and ADR-0005 turned the
same engine down for the outbox for the same reason. A per-sender sequence number: it needs a new
envelope field, and a receiver rejects any frame whose keys are not exactly the envelope's, so
adding one is a major version change; it would also have to survive reconnection and replay. Making
individual commands idempotent at the packet only: it protects the device but not the Queen.
Dropping excess frames silently instead of closing: the sender cannot tell, and the transport is
at-most-once, so a silent drop looks like loss. Limiting bytes instead of frames: bytes are already
bounded by the frame size and the chunk rules, and the cost to the event loop is the number of
frames. A per-node limit set by the Guard Bee: the Guard Bee only requests, and the transport must
be able to protect itself on its own.
