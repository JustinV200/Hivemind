# ADR-0027: A Virtual Cell exposes no inbound port; its entry point boots a Warden that connects out to the Queen

- Status: Accepted
- Date: 2026-09-21

## Context

Every Cell has a Warden (codingrules 8.8), and a Worker's runtime runs inside a Virtual Cell so
that isolation covers the model's actions, not only its commands (8.7, "brain and hands"). Two
questions follow. Where does a Virtual Cell's Warden run, and who dials whom? A Cell runs
model-directed code and is the least trusted machine in the Hive, so anything listening inside it
is attack surface the task itself can reach. Later phases add constraints in the same direction: a
Night Veil Cell must reach the Hive Stand through a Tor hidden service (5.7a), a cloud Cell sits
behind NAT the Hive does not control (5.12), and a Swarm device dials out through its gateway
(phase 11).

## Decision

**The image's entry point starts a Warden, and the Warden dials out.** `images/base-ubuntu`
runs the in-Cell Warden composition root as its `ENTRYPOINT`. It reads the Queen's Waggle URL, its
own Cell id, the Hive id and its key material from `HIVEMIND_*` environment variables (read only in
`manifest/env.py`), connects out over the WebSocket transport, sends a signed `CellReady` carrying
the platform, capabilities and `ForageCapacity` it probed, then heartbeats. The image declares no
`EXPOSE`, the backend publishes no port, and nothing in the Cell listens.

**A backend's `provision` returns only after `CellReady` and the first `Heartbeat`.** "The
container started" is not readiness; a Warden the Queen can supervise is.

**Sub-bees run where their Warden runs.** Inside the Cell the Warden uses the `in_cell` spawn
strategy and an `InCellSession`: commands are local subprocesses in the Cell, scratch is a
directory in the Cell, and there is no lease restore path, because the whole Cell is disposable
and destroying it is the cleanup. Role code cannot see the difference (8.7).

**Each Cell gets its own signing key, minted at provision.** The backend passes it into the Cell,
the Queen's codec verifies it by `node_id`, and it dies with the Cell. Signing is mandatory because
the link crosses a machine boundary (roadmap 1.7).

## Consequences

Positive: one connection direction for Virtual Cells, Night Veil Cells, cloud Cells and Swarm
devices, so the Queen has one listener and one attach path. A Cell with `network = none` still
works with only the control link allowed. A compromised Cell has nothing to listen on and one
peer to talk to. Reconnect, backoff and outbox replay from phase 1 apply unchanged.

Negative: the Queen needs a reachable listener, which on Docker Desktop means the host gateway
address and on Night Veil a hidden service. The Queen cannot reach into a Cell whose Warden is
dead; the only remedy is the backend (`destroy`), which is the Undertaker's job anyway. Provision
latency includes a Python start and a WebSocket handshake, which is what the Overwintering pool
(ADR-0029) exists to hide.

## Alternatives considered

The Queen dials into each Cell: needs a published port per Cell, fails behind NAT and cannot work
for Night Veil. The Warden stays on the Hive Stand and drives the container through `docker exec`:
the model loop would then run outside the isolation boundary, which 8.7 rules out for Virtual
Cells, and it would put `subprocess` and Docker calls in the Warden. SSH into the Cell: an inbound
daemon plus key distribution, for a worse version of what Waggle already does.
