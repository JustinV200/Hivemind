# Enrolling a device in the Swarm

This document specifies the enrolment ceremony end to end: how a device becomes a Real Cell of the
Swarm. The Swarm is the mesh of enrolled Real Cells, existing devices the Hive borrows for a task
and leaves exactly as found. A device joins through the Pollen Packet, the thin gateway installed on
it, which speaks Waggle (the protocol every bee in the Hive uses) to the Queen (the Hive's central
orchestrator) on the Hive Stand (the machine the Queen runs on). The ceremony is one invite, one
signed request and one signed accept. It ends with the device holding the Hive's identity and a set
of limits, and the Hive holding exactly one public key for the device. A node is a process that
signs Waggle frames with a key of its own, and a device is exactly one node. A newly enrolled device
starts on Level 0, the first rung of the runtime ladder of
[ADR-0052](../adr/0052-runtime-ladder-warden-moves-first.md): only the packet on the device, with
its Warden on the Hive Stand.

The message fields are defined in [`spec.md`](spec.md) section 8.10 and the signing rules in section
6; this document says in what order the messages move, what each side checks and what each side
keeps. The decisions behind it are
[ADR-0051](../adr/0051-pollen-packet-is-a-gateway-and-the-trust-model.md) (the trust model) and
[ADR-0055](../adr/0055-waggle-replay-window-and-frame-ceiling.md) (the replay window and frame
ceiling). The minimal contract any other implementation of the packet must meet is
[`gateway-contract.md`](gateway-contract.md). The ceremony is the Swarm's counterpart of the device
enrolment of the Landing Board, the Hive's HTTP door for enrolled clients
([ADR-0041](../adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md)): a short-lived,
single-use invite minted at the Hive Stand, a key the device generates and keeps, and nothing
trusted until the invite is redeemed. It differs because a Swarm device is not a human's client. It
has no password and never reaches the Landing Board; it is driven by a Warden (the always-on
supervisor of one Cell, a unit of compute) and acts for nobody.

## Contents

1. [What enrolment establishes](#1-what-enrolment-establishes)
2. [Before the first invite: the swarm listener](#2-before-the-first-invite-the-swarm-listener)
3. [Step 1: the operator mints an invite](#3-step-1-the-operator-mints-an-invite)
4. [Step 2: the device prepares](#4-step-2-the-device-prepares)
5. [Step 3: the enrolment request](#5-step-3-the-enrolment-request)
6. [Step 4: the door validates the token](#6-step-4-the-door-validates-the-token)
7. [Step 5: the accept](#7-step-5-the-accept)
8. [Step 6: the first connection after enrolment](#8-step-6-the-first-connection-after-enrolment)
9. [TLS pinning](#9-tls-pinning)
10. [Changing a device later: `swarm.node_update`](#10-changing-a-device-later-swarmnode_update)
11. [Revoking a device](#11-revoking-a-device)
12. [When the Hive Stand moves: `QueenMoved`](#12-when-the-hive-stand-moves-queenmoved)
13. [What can go wrong](#13-what-can-go-wrong)
14. [Trail events and error codes](#14-trail-events-and-error-codes)

```text
  Operator            Hive Stand (the Queen)               Device (Pollen Packet)
     |                         |                                    |
     |--- hive swarm invite -->| store SHA-256(token), cap,         |
     |<-- pollen1.<...> -------| tier and expiry                    |
     |                         |                                    |
     |--------------------------- the string, by a trusted channel >| pollen enrol
     |                         |                                    | mints ids and key
     |                         |<== wss, certificate pinned ========|
     |                         |<-- swarm.enrol_request (signed) ---|
     |                         | proof of possession, then token;   |
     |                         | node created, key pinned           |
     |                         |---- swarm.enrol_accept (signed) -->| checks the pins,
     |                         | (or control.error: denied)         | persists, closes
     |                         |                                    |
     |                         |<== wss, certificate pinned ========| pollen run
     |                         |<-- swarm.device_heartbeat ---------| first frame, signed
     |                         | node ONLINE; Level 0 Warden starts |
```

## 1. What enrolment establishes

Four things, each a piece of trust that did not exist before.

- **The Hive learns which device the operator meant.** The first public key the Hive pins for a
  `device_id` arrives in a frame that carries a token only the operator was shown.
- **The device learns which Hive it joined.** The invite carries the Hive Stand's node key and the
  fingerprint of the listener's certificate, and the device refuses anything else, so whoever
  answers its dial cannot enrol it into another Hive.
- **The device learns its limits.** The access level (capped by the operator), the Comb Shield tier
  (the Cell's security tier, chosen by the operator), the capability strings it will enforce locally
  on every request, and the timings for heartbeats and for the dead-man switch (the rule that
  releases a device's lease, the Hive's temporary hold on it, when the link to the Queen has been
  lost too long).
- **Both sides share the identities.** The device's `device_id` and `node_id`, and the `cell_id` and
  `warden_id` the Queen mints for it.

What each party holds afterwards, and what it never does with it:

| Item | Made by | Held by | Never |
|---|---|---|---|
| Invite token and invite string | `hive swarm invite` | the operator, shown once; then the device until it is used | logged, stored in the clear, put on the trail, echoed back, queued in an outbox (the durable queue of frames a node could not send) |
| SHA-256 of the token | the Hive Stand | the swarm registry | used for anything but matching a redemption |
| Node private key | the packet, at first run | the device, in `<data_dir>/node.ed25519`, mode 0600 | sent anywhere |
| Node public key | the packet | the Hive Stand's swarm registry, pinned at enrolment | replaced without a new enrolment |
| Hive key (the Hive Stand's node public key) and its node id | the Hive Stand | the device, pinned from the invite | trusted from the accept frame alone: the accept must match the pin |
| Listener certificate fingerprint | the operator's certificate | the device, pinned from the invite | trusted in place of a certificate authority's say-so |

## 2. Before the first invite: the swarm listener

Devices reach the Hive Stand on the swarm listener, a WebSocket listener that serves Waggle and
nothing else; it is not the Landing Board. It is configured in the `[swarm]` section of the Hive
Manifest, the Hive's TOML configuration file.

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | Whether the Hive Stand listens for devices at all. |
| `listen_host`, `listen_port` | `"127.0.0.1"`, `8720` | Where it binds. |
| `advertise_url` | `""` | The address devices dial. It goes into every invite, so it must be the name or address the device can reach. |
| `tls_cert`, `tls_key` | none | The listener's certificate and key. Required when `listen_host` is not a loopback address, which then serves `wss://`. A self-signed certificate is enough. |
| `invite_ttl_minutes` | `15` | How long an invite lives. |
| `heartbeat_interval_s`, `heartbeat_miss_limit` | `15`, `4` | How often a packet reports, and how many missed reports make a node unreachable. |
| `dead_man_s` | `120` | The longest a Level 0 device keeps a lease after losing the link. |
| `default_access` | `"full"` | The access level an invite carries when `--access` is not given. |

On loopback the listener may serve plain `ws://`, which lets a device on the same machine, or a
test, enrol without a certificate. Anywhere else it serves `wss://` or does not start; plain `ws://`
stays loopback-only, as `waggle.uris` rules.

## 3. Step 1: the operator mints an invite

### 3.1 The command

On the Hive Stand: `hive swarm invite --access LEVEL --shield meadow|propolis [--label NAME]`.

- `LEVEL` is the most the device may be granted, one of `READ_ONLY`, `SCRATCH` and `FULL` (how much
  of the device the Hive may touch; Virtual Cells are always `FULL`). The device asks for an access
  level when it enrols and receives the lower of what it asked for and this cap.
- `--shield` is the Comb Shield tier the operator vouches for. `meadow` is the baseline. `propolis`
  says the device already runs a VPN, which the Hive verifies before each lease and never installs,
  starts or reroutes. There is no way to ask for `NIGHT_VEIL`, the strictest tier, on a Real Cell:
  it exists only for Virtual Cells.
- `--label` is the name the operator will see in `hive swarm list`.

Minting an invite is an operator act on the Hive Stand: it is a command, not a route, so no
listener, local or remote, serves it. The command writes the invite row straight to the registry,
prints the invite string once and exits.

### 3.2 What the Hive Stand stores

One invite row: `code_hash` (the SHA-256 of the token exactly as it travels), `label`,
`access_level` (the cap), `comb_shield`, `created_at`, `expires_at` (`created_at` plus
`invite_ttl_minutes`), `state` and, once redeemed, the `device_id`. The token and the invite string
are not stored anywhere, and `swarm.invited` on the Pheromone Trail (the Hive's append-only audit
log) records the label, the cap, the tier and the expiry and nothing else.

An invite is in exactly one state. It starts `ISSUED`. A successful enrolment moves it to
`REDEEMED`, in the same transaction that creates the node. It becomes `EXPIRED` when its time runs
out unredeemed, and `REVOKED` when the operator cancels it. The three later states are final. An
invite is therefore **short-lived** (default 15 minutes) and **single use**: the second redemption
of a token finds it `REDEEMED` and is denied.

The access cap and the tier live on this row and never on the wire. A device can ask for an access
level, but it cannot raise the ceiling it is asked against, and it cannot choose its tier at all.

### 3.3 The invite string

`pollen1.<base64url(JSON)>`: the prefix, a dot, and the URL-safe base64 (RFC 4648 section 5) of a
compact UTF-8 JSON object. Padding is not written; a reader accepts it either way, as the spec's
JSON conventions do for every `bytes` field. The object has exactly eight keys.

| Key | Value |
|---|---|
| `token` | 16 random bytes from the system's CSPRNG written as RFC 4648 base32 without padding, in groups of four joined by hyphens: 26 characters and six hyphens, 32 in all, the same shape as the Landing Board's invite code. The secret. |
| `url` | The swarm listener's advertised URL (`wss://...`, or `ws://` on loopback). |
| `hive_id` | The Queen's bee address (`hive_<ULID>`), the `recipient` of the first frame. |
| `hive_node_id` | The Hive Stand's node id (`node_<ULID>`). |
| `hive_key` | The Hive key: the Hive Stand's node public key, 64 lowercase hex characters (a raw 32-byte Ed25519 key). In this protocol version the Hive key is the Hive Stand's node key. |
| `tls_sha256` | The SHA-256 fingerprint of the listener's certificate (the DER encoding), 64 lowercase hex characters; `null` when the URL is `ws://`. |
| `expires_at` | When the invite stops being accepted, ISO 8601 with an explicit offset. |
| `label` | The operator's label for the device, or `null`. |

The prefix is the format's version. A reader rejects any other prefix, and the set of keys never
changes under `pollen1.`: adding or removing one is a `pollen2.` string. A worked example (the
values are illustrative; the string is shown wrapped, the real one has no line breaks):

```text
pollen1.eyJ0b2tlbiI6Ikg3UUstM01aUC1XRDJGLVhOVkEtUjVURy1DNFlCLUo2IiwidXJsIjoi
d3NzOi8vaGl2ZS5leGFtcGxlLm5ldDo4NzIwIiwiaGl2ZV9pZCI6ImhpdmVfMDFNNFRKNEdHMDI0
OEgyNDhIMjQ4SDI0OEoiLCJoaXZlX25vZGVfaWQiOiJub2RlXzAxTTRUSjRHRzAyNDhIMjQ4SDI0
OEgyNDhIIiwiaGl2ZV9rZXkiOiIwM2ExMDdiZmYzY2UxMGJlMWQ3MGRkMThlNzRiYzA5OTY3ZTRk
NjMwOWJhNTBkNWYxZGRjODY2NDEyNTUzMWI4IiwidGxzX3NoYTI1NiI6IjQyYjM0MWJlZjczYWNh
ZDlkNWRjYjU0NDU0YTlhZmU1MDViMjIzMjZhOWUwNDQxYjZhM2FhYjFhM2Q3MTkxOTUiLCJleHBp
cmVzX2F0IjoiMjAyNi0xMC0xM1QxMjoxNTowMFoiLCJsYWJlbCI6ImtpdGNoZW4tcGkifQ
```

It decodes to:

```json
{
  "token": "H7QK-3MZP-WD2F-XNVA-R5TG-C4YB-J6",
  "url": "wss://hive.example.net:8720",
  "hive_id": "hive_01M4TJ4GG0248H248H248H248J",
  "hive_node_id": "node_01M4TJ4GG0248H248H248H248H",
  "hive_key": "03a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8",
  "tls_sha256": "42b341bef73acad9d5dcb54454a9afe505b22326a9e0441b6a3aab1a3d719195",
  "expires_at": "2026-10-13T12:15:00Z",
  "label": "kitchen-pi"
}
```

The invite string is a secret until it is used or expires. Whoever reads it in that window can enrol
a device up to the cap and tier it carries, so it travels by a channel the operator trusts: typed
in, copied through a local file, or shown on a screen only the operator can see. It is never logged.

## 4. Step 2: the device prepares

The operator gives the invite string to `pollen enrol` on the device. The packet:

1. **Reads the invite.** It checks the prefix, decodes the payload and requires every key to be
   present and well formed. It refuses a `wss://` URL whose `tls_sha256` is null (there would be
   nothing to verify the listener against), and a `ws://` URL whose host is not a loopback host. It
   may refuse an invite whose `expires_at` has already passed by its own clock; the Queen's clock
   decides in the end.
2. **Mints its identity.** A `device_id` (`device_<ULID>`), a `node_id` (`node_<ULID>`) and an
   Ed25519 key pair. The private key is written to `<data_dir>/node.ed25519` with mode 0600 and is
   never sent anywhere; this happens before the first dial, so a crash can never leave the Hive
   holding a key the device lost.
3. **Probes itself.** The `PlatformReport` (operating system, distribution, architecture, package
   manager, shell, Python), the `CellCapabilitiesReport` (display, audio, browser, network scopes)
   and the `HostCapacityReport` (cores, memory, disk, GPUs). Everything the Hive later decides about
   the device, from placement to tool eligibility, reads these.
4. **Pins what the invite gave it.** The Queen's URL, the Hive Stand's node id and public key, and
   the certificate fingerprint. They stay in force until a later notice replaces them under the
   rules of sections 9 and 12.

## 5. Step 3: the enrolment request

### 5.1 Connecting

The packet dials `url`. For `wss://` it completes the TLS handshake and compares the SHA-256 of the
certificate the listener presents with the pinned fingerprint before it sends a byte of Waggle; on a
mismatch it closes the connection and fails with `waggle.transport.tls_pin` (section 9). The invite
token is therefore never sent to a server that cannot prove it holds the certificate the operator
pinned.

### 5.2 The first frame

The first frame on the connection is one `swarm.enrol_request`, signed with the node key the packet
just minted. The envelope (the fixed outer record every Waggle message rides in) names the packet as
sender (`device_id`) and node (`node_id`). Its `recipient` is the invite's `hive_id`, the Queen's bee
address, and the door refuses an enrolment request addressed to any other Hive (the receiver rule of
spec section 3: a frame whose `recipient` is not the receiver's own address is dropped). `EnrolAccept`
repeats `hive_id`; a packet that finds the two different refuses the accept.

The payload (spec section 8.10):

| Field | Rule |
|---|---|
| `invite_token` | The token from the invite, exactly as the invite carries it. 16 to 128 characters. Sent once, here, and never echoed. This message is never appended to an outbox: it goes only on a live connection, and a failed enrolment is retried with a new invite. |
| `device_id`, `node_id` | The packet's own. The receiver rejects a `device_id` that differs from the envelope's `sender` and a `node_id` that differs from its `node_id`. |
| `public_key_hex` | The packet's raw 32-byte Ed25519 public key, 64 lowercase hex characters. |
| `hostname` | The device's hostname. Text the device wrote: it is shown to the operator and trusted for nothing. |
| `runtime_version` | The `pollen` version. |
| `platform`, `capabilities`, `capacity` | The reports of step 2. |
| `max_sub_bees` | An optional operator-set cap on concurrent sub-bees (the Workers a Warden spawns); the Queen's ceilings (limits she sets on a Warden's own capacity) may only lower it. |
| `requested_access` | The access level the packet asks for, `FULL` by default; the operator's cap may lower it. |

### 5.3 The first-frame rule

The packet's key is unknown to the Queen until this frame is accepted, so the frame cannot be
verified in the ordinary way. The rule is:

1. On a connection whose node has no pinned key, **the only kind accepted is
   `swarm.enrol_request`.** Any other first frame closes the connection with code 1008, with no
   reply.
2. The listener decodes that one frame with no verifier. It requires the payload's identities to
   match the envelope's (section 5.2).
3. It then verifies the signature over the same raw frame against the `public_key_hex` and `node_id`
   inside the payload. This is proof of possession: only the holder of the private key could have
   produced it. A frame that fails here closes the connection with `waggle.signature.invalid` and
   code 1008.
4. Only then does the door validate the token (section 6). The key is trusted on first use only
   because the token bound it to an operator's `hive swarm invite`.
5. Once a key is pinned for the node, every later frame on the connection is verified against it in
   the ordinary way, and the connection is pinned to the node id of its first verified frame; a
   frame signed by a different node closes it with 1008.

## 6. Step 4: the door validates the token

The door (`swarm/enrolment/door.py`) hashes the received token with SHA-256, finds the invite with
that `code_hash` and redeems it in one atomic registry operation. The redemption succeeds only if
the invite exists, is `ISSUED`, has not expired by the Hive Stand's clock and has not been revoked.
The same transaction creates the node, pins its key and moves the invite to `REDEEMED`, so two
devices presenting the same token can never both succeed. The token is 128 bits of randomness, so
guessing it is not a realistic attack.

A denial is two things. The packet receives a `control.error` correlated to its request, with code
`hive.swarm.enrol_denied` and `is_retryable` false; its message is a full sentence that names ids
and never the token. The trail receives `swarm.enrol_refused` with the device and node ids and a
reason code (an unknown token, an expired invite, a revoked one, one already used, an identity
mismatch), never the token and never the invite string. After a denial no key is pinned, so the
connection can carry nothing but another `swarm.enrol_request`, and the packet does not send one: a
failed enrolment is retried by minting a new invite.

## 7. Step 5: the accept

### 7.1 What the Queen decides

On a successful redemption the Queen answers with `swarm.enrol_accept`, a reply correlated to the
request and signed with the Hive Stand's node key. In the same transaction as the redemption she
records `swarm.enrolled`. The accept carries:

| Field | Where it comes from |
|---|---|
| `device_id` | Echoed from the request. |
| `cell_id` | Minted by the Queen: the Real Cell this device now is. |
| `hive_id` | The Queen's bee address, the recipient of everything the packet sends up. |
| `hive_key_hex` | The Hive key. It must equal the key pinned from the invite. |
| `trusted_nodes` | The node ids and keys whose signatures the packet will accept (at least one, at most sixteen): the Hive Stand's node. It seeds the packet's verifier. |
| `warden_id` | Minted by the Queen and kept for the life of the node: the device's Warden at every rung of the runtime ladder (ADR-0052). The packet accepts session and lease traffic only from the Queen or this Warden. |
| `access_level` | The lower of `requested_access` and the invite's cap. |
| `comb_shield` | The invite's tier: `MEADOW` or `PROPOLIS`, and never `NIGHT_VEIL`. |
| `granted_capabilities` | The access level's ceiling in the capability grammar of the guard (the Hive's policy engine), restricted to the families the packet understands (below). Checked locally on every request. |
| `offline_limit_s` | The dead-man limit for a device with no Warden on it (`[swarm] dead_man_s`). |
| `heartbeat_interval_s` | How often the packet sends `swarm.device_heartbeat` (`[swarm] heartbeat_interval_s`). |
| `reason` | Why it was admitted at this level and tier. |

The packet enforces five capability families, and the literal `{scratch}` in a scope stands for each
lease's scratch root:

| Access level | `granted_capabilities` |
|---|---|
| `READ_ONLY` | `fs:read:**` and `watch:<cell_id>`. A read-only device runs no command at all. |
| `SCRATCH` | the above, plus `fs:write:{scratch}/**` and `exec:*`. |
| `FULL` | the above, plus `fs:write:**` and `cell:outside_scratch:**`. |

These are the guard's per-level grants (`guard/access.py`) cut down to the families a gateway can
enforce on the machine itself: the others (the network, devices, the peripherals of the Exoskeleton,
the virtual display, input and audio a task may ask for) are enforced by the Hive Stand and never
reach the packet. The `watch` capability bounds what the Warden may observe on the Cell (ADR-0054),
and the packet checks it again on every `swarm.observe`. Which directories the idle Warden asks
about is not decided here: the operator names them after enrolment (`hive swarm watch`, section
10), none until then, and the packet answers for a directory only if the device's `fs:read`
grants it.

### 7.2 What the packet checks

The accept is the first frame the packet verifies, and it has no trusted nodes yet, so it checks it
against what the invite pinned: the signature must verify under the pinned Hive key for the pinned
`hive_node_id`, and the envelope's `node_id` must be that node. It requires `correlation_id` to be
its request's id, `device_id` to be its own and `hive_key_hex` to equal the pinned key. It also
requires the accept's own validity rules to hold: an access level no higher than it requested, a
tier that is not `NIGHT_VEIL`, and the bounds of spec section 8.10. If anything fails it discards
the accept and persists nothing, so a device never holds half an enrolment.

### 7.3 What the packet persists

Everything that must survive a restart, in the state directory:

- the node key, in its own file (`node.ed25519`, mode 0600);
- `PacketState`, a JSON document replaced atomically, so a crash leaves the old or the new document
  and never a torn one: the `device_id` and `node_id`; the Queen's URL and the certificate pin; the
  Hive node id and key and the Queen's `hive_id`; the trusted nodes; the `cell_id` and `warden_id`;
  the runtime level (`GATEWAY`); the access level, the tier and the granted capabilities; the
  offline limit and the heartbeat interval; and the highest `QueenMoved` sequence accepted so far
  (section 12);
- the lease table and the outbox (the gateway contract describes both).

It does not keep the invite token or the invite string; it discards them as soon as the accept is
persisted. When the persist succeeds the packet closes the enrolment connection.

## 8. Step 6: the first connection after enrolment

`pollen run`, the service `pollen install` sets up, dials the persisted URL with the same pin. **The
first frame of every connection after enrolment is a signed `swarm.device_heartbeat`**: the
`device_id`, the runtime `level` (`GATEWAY`), the open `lease_ids`, the count of processes its
leases started, the outbox depth and the uptime. The listener looks the packet's key up by
`node_id`, verifies the frame, pins the connection to that node and marks the node `ONLINE`
(`swarm.online`). The Queen's swarm gate then starts the device's Level 0 Warden on the Hive Stand,
bound to the device through `PollenSession` (the session over Waggle by which a Warden drives the
device), and the device appears in `hive cells list` as a `REAL` Cell from source `swarm`.

After that the packet sends a heartbeat every `heartbeat_interval_s`. The Queen marks the node
`UNREACHABLE` (`swarm.unreachable`) when the link is lost or `heartbeat_miss_limit` heartbeats in a
row are missing, and `ONLINE` again on the next verified heartbeat. A heartbeat never carries
capacity figures: live capacity travels only in `forage.capacity_report`.

## 9. TLS pinning

Waggle gives authenticity and integrity and never confidentiality, so a link that crosses a network
not under the operator's control is wrapped in TLS. The swarm listener presents one certificate. The
invite carries its fingerprint: the SHA-256 of the certificate's DER encoding, in lowercase hex. The
packet does not validate a certificate chain or a name. It compares the fingerprint of the
certificate the listener presents with the pin, and a connection that differs is closed before any
Waggle frame is sent. That is why a self-signed certificate is enough and the device needs neither a
trust store nor a DNS name.

The pin is a property of the invite and then of the device's state. Nothing in this protocol version
rotates it, so a certificate change breaks every device that pinned the old one until it is given a
new invite. The `QueenMoved` notice (section 12) carries an address and a node key but no
fingerprint, so a Supersedure (moving the Hive Stand to another machine) keeps working only if the
new Hive Stand presents the certificate the devices pinned; how a move carries a new pin is for
roadmap step 13.2a to decide.

A packet that cannot connect because of a pin mismatch does not give up: it treats the failure like
any failed dial, backs off and tries again, and it should report the cause in `pollen status` so the
operator can see why it cannot reach the Hive.

## 10. Changing a device later: `swarm.node_update`

When the operator changes a node's access level (`hive swarm access`) or its tier (`hive swarm
shield`), or when the node moves a rung of the runtime ladder, the Queen sends it a
`swarm.node_update`. It is an event, sent only by the Queen, that carries the node's current
`device_id`, `cell_id`, `warden_id`, `level`, `access_level`, `comb_shield` (never `NIGHT_VEIL`),
`granted_capabilities`, `offline_limit_s`, `heartbeat_interval_s` and `watch_roots`, and a reason.
The packet replaces the stored values with these, persists them, and applies them **from the next
request**: a
request that arrives after the update is checked against the new values, including a request inside
a lease opened before it; a process that is already running is not interrupted. The Queen also sends
the node's current values when the node comes online, so a change made while the device was away
still reaches it. The packet never takes an update from anyone but the Queen: like every Queen-only
kind it is accepted only from `hive_id`.

The directories the operator names for the device's Warden to watch (`hive swarm watch <node>
<directory>...`, or `--clear` for none) are a term too, but the packet reads none of them: a
Warden on the Hive Stand (Level 0) names them itself in every `swarm.observe` (`roots`, at most
sixteen of at most 256 characters) and the packet judges each against its own `fs:read` grant
before it reads a file. They ride the update (`watch_roots`) for the Warden that lives on the
device, which is local to its files and so has to be told where to look: the packet keeps them and
passes them in `HIVEMIND_WATCH_ROOTS` to the Warden it starts, and the Warden says in its
`cell.ready` which it runs with (`watch_roots`), so a list that was changed while it ran is found
and the Warden is restarted under it, as for a new level or tier. The Hive Stand refuses a
directory that is not absolute in the device's own spelling (POSIX or Windows) or that the node's
granted capabilities do not cover, sends the packet only the ones they cover, and rebuilds the
node's Level 0 Warden when the list changes.

## 11. Revoking a device

`hive swarm revoke <node>` marks the node `REVOKED`, a final state. The Queen's swarm gate then
stops the device's Warden, which releases its lease while the link is still up and leaves the
device as found, closes the packet's connection, and removes the node's key from her verifier. The
registry keeps the row, marked revoked, for the audit. From then on a dial from that device fails at
its first frame, which is signed by a key nobody knows (`waggle.signature.unknown_node`, close code
1008), and the packet treats it like a link that never returns: any lease it still holds is cleaned
by the dead-man switch after `offline_limit_s`. The operator removes the packet with `pollen
uninstall`. Enrolling the same machine again takes a new invite, and the old node stays on record as
revoked. Cancelling an invite that has not been used moves the invite to `REVOKED`.

## 12. When the Hive Stand moves: `QueenMoved`

Supersedure (moving the Hive Stand to another machine, codingrules 8.16) is a phase 13 step, so the
Queen that sends a `control.queen_moved` is not built yet; the packet that honours one is. The
packet:

1. **Persists the Queen's address** from enrolment and replaces it only on a `control.queen_moved`
   that the current Hive key verifies.
2. **Rejects a stale or replayed notice.** The notice's `sequence` must be above the highest the
   packet has persisted. It persists the new highest value, so a replayed or older notice, a
   rollback included, can never redirect it.
3. **Pins the new node's key** (`new_node_public_key_hex` for `new_node_id`) and keeps the old Hive
   key as well. From `effective_at` it dials the new address. Until `grace_until` it accepts frames
   signed by either node and may dial either address.
4. **Drops the old key and address at `grace_until`**, so the machine that stepped down keeps no
   authority. A notice with `is_rollback` set points the packet back at the old Queen under the same
   rules.

The `QueenMoved` frame is subject to the ordinary rules before any of this: it is signed by a
trusted node, addressed to the packet, sent by the Queen, fresh and not a duplicate.

## 13. What can go wrong

| What the operator sees | Cause | What happens | What to do |
|---|---|---|---|
| `pollen enrol` reports a pin mismatch | The listener's certificate is not the one in the invite: a replaced certificate, the wrong host, or something answering that should not | The connection is closed before any Waggle frame, so the token is never sent | Check the address and the certificate; mint a new invite if the certificate changed |
| `hive.swarm.enrol_denied` | The token is unknown, expired, revoked or already used, or the identities in the request did not match | Nothing is pinned and the device persists nothing | Mint a new invite |
| The device reports a Hive key mismatch | The invite came from a different Hive than the listener it reached | The accept is discarded | Use an invite from the Hive that owns that listener |
| `pollen enrol` times out without an accept, yet `hive swarm list` shows the node, which never heartbeats | The accept was lost after the Queen redeemed the invite, so the device never persisted it | The invite is spent and the node exists but is `UNREACHABLE` | `hive swarm revoke` the node, then mint a new invite |
| Frames are dropped though the link is up | The device's clock is more than `MAX_CLOCK_SKEW_S` (300 s) ahead of the Hive Stand's, or the sent time is older than `MAX_MESSAGE_AGE_S` | Both sides drop the frames silently (spec section 3) | Fix the device's clock |
| The device disappears after a Supersedure | The new Hive Stand presents a different certificate than the pin | Pin mismatch on every dial | See section 9: the move must keep the certificate, or the device needs a new invite |
| An enrolled device's leases vanish after an outage | The link was down longer than `offline_limit_s` and no Warden is on the device | The dead-man switch released them (cause `DEAD_MAN`) and the Warden's work was re-placed | Nothing is wrong; see the gateway contract |

## 14. Trail events and error codes

Trail events the ceremony writes, all in the `swarm` family. None carries a token, an invite string
or a key, and none a path but `swarm.watch_changed`, which lists the directories the operator named.

| Event | When |
|---|---|
| `swarm.invited` | An invite is minted. |
| `swarm.enrol_refused` | The door denies a request. |
| `swarm.enrolled` | A redemption succeeds: the node is created and its key pinned. |
| `swarm.online` | A device's link is established: its first connection after enrolment, a reconnection and a connection after the Hive Stand restarted, whatever status it was in. |
| `swarm.unreachable` | The node's status becomes `UNREACHABLE`: its link is lost, its heartbeats stop, or (for a colonized device) its Warden's link is lost. |
| `swarm.node_held` | The Queen holds a lost device's tasks for the grace period (ids, the rung and the reason a link was lost; never an address). |
| `swarm.promoted`, `swarm.demoted` | A device moves a rung of the runtime ladder, or a move is undone, with `from_level`, `to_level` and a reason word. |
| `swarm.access_changed`, `swarm.shield_changed` | The operator changes the node's level or tier. |
| `swarm.watch_changed` | The operator changes the directories its Warden watches (`hive swarm watch`): how many there were and the new list. |
| `swarm.revoked` | The node is revoked. |

Error codes a device meets during the ceremony:

| Code | Meaning |
|---|---|
| `hive.swarm.enrol_denied` | The request was refused (section 6). `is_retryable` is false. |
| `waggle.transport.tls_pin` | The listener's certificate did not match the pin. |
| `waggle.signature.invalid`, `waggle.signature.unknown_node` | A frame failed verification; the connection is closed with code 1008. |
| `waggle.codec.invalid_payload` | The request had a readable id but a payload or envelope rule failed; the connection stays open. |
