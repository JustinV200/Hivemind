# ADR-0051: The Pollen Packet is a gateway, a device has one node key, and trust starts at an invite

- Status: Accepted
- Date: 2026-10-13

## Context

The Swarm (the mesh of enrolled Real Cells: existing devices the Hive borrows for a task and leaves
exactly as found) reaches a device through the Pollen Packet, the small program the operator
installs on it. The packet is the Hive's door into someone's machine, which makes it the machine's
door into the Hive, and its design is hard to reverse for two reasons: it ships to devices the Hive
does not control, and roadmap step 11.7a expects it to be reimplemented in other languages for
phones and constrained boards. Codingrules 15 already fixes the outline: every Waggle message
(Waggle is the protocol the bees use to talk to each other) sent to a packet is signed and verified
before anything executes, enrolment uses one-time tokens, and a Real Cell's access level (how much
of the device the Hive may touch: `READ_ONLY`, `SCRATCH` or `FULL`) caps what can be done to it.
Roadmap steps 11.1 to 11.4 leave the rest open. How much does the packet decide for itself: if it
supervises, plans or calls a model, a device the Hive cannot inspect holds a second brain. How many
keys does a device have: the packet and a Warden (the always-on supervisor of one Cell, a unit of
compute) that later moves onto the device (ADR-0052) could each hold one, and the Queen (the central
orchestrator, running on the Hive Stand, the machine she lives on) would then pin, rotate and revoke
two. How does a device that has never spoken to the Hive prove it is the one the operator invited,
and how does it know the Hive that answers its dial is the Hive that invited it. Where does
confidentiality come from, when Waggle provides authenticity and integrity only (spec section 6).
What does the packet do when the Queen asks for more than the operator granted. And how does it
behave when the Hive Stand moves to another machine (Supersedure, codingrules 8.16), when a frame
repeats (ADR-0005 obliged no receiver to notice) and when the link is lost with no Warden on the
device to decide anything.

## Decision

**The packet is a gateway and nothing else.** `pollen` holds no supervision logic, no model client
and no Pheromone Trail (the Hive's append-only audit log). It executes only what a signed request
from a trusted node asks, inside a lease (the Hive's temporary hold on the device: a scratch
directory, the processes it started, a restore on release), after a local capability check, and it
reports what happened. It depends on `waggle` (and so on pydantic, websockets and cryptography) and
the standard library only, so it installs on a Raspberry Pi with no Docker, no SQLite extension and
no LLM SDK. Supervision of the work on a device is a Warden's job (ADR-0052); the record of what the
Hive did to the device is written by the Hive Stand from its side of the link (`swarm.command_sent`,
the lease events), so a packet that lies or is silent cannot edit the audit.

**A device has one node, and one key.** A node is a process that signs Waggle frames with a key of
its own. At first run (`pollen enrol`) the packet mints its `device_id` (`device_<ULID>`), its
`node_id` (`node_<ULID>`) and an Ed25519 key. The private half stays in the packet's state directory
as `<data_dir>/node.ed25519`, mode 0600, and is never sent anywhere; the public half goes to the
Queen in the enrolment frame. A colonized device's Warden (Level 1 and above of the ladder in
ADR-0052) signs as the same node, reading the key file locally. The spec's address binding (section
6) already lets one node key speak for the device's `device_id`, its Cell's `warden_id` and that
Warden's Workers, and for nothing else, so no second key is needed. The Queen knows exactly one key
per device, pinned in the swarm registry at enrolment, and revoking the device is one act.

**An invite binds the first key to the operator's decision.** `hive swarm invite --access LEVEL
--shield meadow|propolis [--label NAME]` prints one invite string, `pollen1.<base64url(JSON)>`, with
seven fields. `token` is 16 random bytes as RFC 4648 base32 in groups of four (26 characters and six
hyphens, 32 in all), the same shape as the invite code of the Landing Board, the Hive's HTTP door
for enrolled clients (ADR-0041); only its SHA-256 is stored. `url` is the swarm listener's
advertised URL. `hive_node_id` and `hive_key` (64 hex characters) are the Hive Stand's node and its
public key. `tls_sha256` is the fingerprint of the listener's certificate, or null on loopback.
`expires_at` and `label` complete it. The string is a secret: it is printed once and never logged.
Invites expire (`[swarm] invite_ttl_minutes`, default 15) and are single use. The access cap and the
Comb Shield tier (the Cell's security tier: Meadow, the baseline, or Propolis, which needs a VPN;
never Night Veil, the strictest tier, on a Real Cell) the operator chose ride on the invite row in
the registry and never on the wire, so a device can ask for an access level but cannot raise the
ceiling it is asked against.

**Enrolment is one signed frame, and no key is trusted before it.** The first frame on a fresh
connection may be only `swarm.enrol_request`, signed with the packet's new key. The listener's
verifier checks that frame against the `public_key_hex` and `node_id` inside its own payload, which
proves possession of the key, and only then does the door validate the token. Every later frame is
verified against the key that is now pinned. In the other direction the packet pinned the Hive key
from the invite: it verifies the accept and every later frame against it and refuses an accept whose
`hive_key_hex` differs, so whoever answers the dial cannot enrol the device into another Hive. A
denial is a `control.error` with code `hive.swarm.enrol_denied` and a `swarm.enrol_refused` event on
the trail carrying ids and a reason code, never the token. The whole ceremony is specified in
`docs/waggle/enrolment.md`.

**Every frame is checked for who sent it, and every request for what it may do.** The packet accepts
a frame only if it is signed by a trusted node (`EnrolAccept.trusted_nodes`, later amended by a
`QueenMoved`), addressed to the packet's own `device_id`, and sent by the Queen (`hive_id`) or by
the Cell's current Warden (`warden_id`, from the accept or the last `swarm.node_update`). Kinds only
the Queen sends are accepted only from `hive_id`. Then the packet checks the request itself, before
any executor runs, against `EnrolAccept.granted_capabilities`: the access level's ceiling written in
the capability grammar of the guard, the Hive's policy engine (ADR-0039), and restricted to the
families the packet understands, `exec:*`, `fs:read:...`, `fs:write:...`, `cell:outside_scratch:...`
and `watch:<cell_id>`, where the literal `{scratch}` in a scope stands for each lease's scratch
root. `READ_ONLY` grants `fs:read:**` and `watch:<cell>` only, so a read-only device runs no command
at all. A request outside the grant is refused with a `control.error` whose code is
`pollen.capability_denied`. The Hive Stand checks the same request at its own enforcement point
first, which is why the packet's check is a second, independent wall and not the first.

**Confidentiality is the link's, and the pin is the invite's.** The swarm listener serves `wss://`
with a certificate from `[swarm] tls_cert` and `tls_key` whenever it binds a non-loopback address.
The packet pins the certificate's SHA-256 from the invite instead of trusting a certificate
authority, so a self-signed certificate is enough and the device needs neither a trust store nor a
DNS name. Plain `ws://` stays loopback-only, as `waggle.uris` already rules.

**Replay, relocation and loss of the link are the packet's business too.** Both ends drop a
duplicate envelope id inside the replay window (ADR-0055) and a frame older than `MAX_MESSAGE_AGE_S`
or more than `MAX_CLOCK_SKEW_S` ahead of their clocks (spec section 3). The packet persists the
Queen's address and replaces it only on a `control.queen_moved` that the current Hive key verifies
and whose `sequence` is above the highest it has persisted; it pins `new_node_public_key_hex` for
`new_node_id`, honours both addresses until `grace_until`, then drops the old key. Sending a
`QueenMoved` is phase 13 (13.2a); honouring one is here. With no Warden on the device (Level 0) and
the link lost for longer than `offline_limit_s`, the packet runs a dead-man switch: it kills every
process its leases started, removes their scratch, restores what it recorded and queues a
`cell.lease_released` with cause `DEAD_MAN`. What must reach the Queen after a reconnect, the
`cell.lease_released` and the terminal `session.exit` of an exec that outlived the link, waits in a
`waggle.outbox` under the state directory; `session.output` chunks are never queued, because the
Warden that wanted them has already given up on them.

## Consequences

Positive: the packet is small enough to reimplement, and the gateway contract
(`docs/waggle/gateway-contract.md`) can state all of it as a short list of obligations. It cannot be
talked into more than the operator granted, because it enforces the grant itself before any executor
runs, whatever the Hive Stand believes. One key per device means one pin, one rotation and one
revocation, and a colonized Warden needs no enrolment of its own. The invite authenticates in both
directions: the Hive learns which device the operator meant, because the first key is bound to a
token only the operator was shown, and the device learns which Hive it joined, because the Hive key
and the certificate pin come from the same string. A replayed enrolment frame gains an attacker
nothing: the token is single use, the key it carries is the device's own, and `swarm.enrol_request`
is never put in an outbox. Confidentiality works on a home network with no certificate authority and
no DNS name.

Negative: the node key is a bearer secret at rest on a machine the Hive does not control. Mode 0600
makes the operating-system account the boundary, so anyone who reads the file can sign as that
device, and at Level 1 and above as its Warden; revocation is the remedy and it is the Queen's.
Sharing the key between the packet and an on-device Warden means one compromised process can sign
for the other, which a second key on the same machine under the same account would not have
prevented. The invite string is a bearer credential until it is used or expires, so whoever sees it
in that window can enrol a device up to the cap; it must travel over a channel the operator trusts.
The pin comes from the invite, nothing here rotates it, and a `QueenMoved` carries no new
fingerprint, so a certificate change or a Supersedure that serves a different certificate breaks
every pin until those devices are given a new invite; 13.2a decides whether the notice grows a pin.
The capability matcher exists twice, once in the guard and once in the packet, because the packet
may import nothing from `hivemind`; the pair is held together by the gateway contract suite, the
same trade ADR-0048 made for the clearance floor. The dead-man switch destroys work on purpose: a
lease killed after a long partition is lost even if the Hive Stand was healthy, and the Queen
re-places the task from its Handoff (the document a bee writes before its context is reset).

## Alternatives considered

A supervising agent on the device from the start: it needs the whole `hivemind` runtime and a Python
environment, it multiplies what an attacker gains by owning the device, and it cannot run on a phone
or a constrained board. The ladder of ADR-0052 offers it where it fits, and the packet stays the
common floor. One key per bee on a device: more pins and more revocations for no separation, since
every bee on a device runs as the same operating-system user and the address binding already limits
what the node key may speak for. Trusting the Hive Stand's checks alone: a bug in the Hive Stand's
device-command enforcement point, or a compromised Queen, would then run anything with the packet's
rights; the local check bounds the damage by the operator's choice at enrolment. A device that picks
its own access level: it could raise it. A certificate authority for the swarm listener, public or
private: it needs a DNS name, issuance and renewal on a home network and a trust store on every
device including the smallest ones, where a pin needs nothing installed. Mutual TLS in place of
signed frames: it authenticates a connection and not a message, and an outbox replay or a stored
`QueenMoved` arrives on a different connection from the one its author made (the argument of
ADR-0005). Trust on first use with no invite: any host that could reach the listener could enrol.
The Queen dialling out to devices: it needs an inbound port on every device behind a home router,
the problem ADR-0027 avoided for Virtual Cells.
