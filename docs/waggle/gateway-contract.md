# The gateway contract

This document is the minimal contract a Pollen-equivalent must meet: any program that connects a
device to the Swarm by speaking Waggle to the Queen the way the Pollen Packet does. The Swarm is the
mesh of enrolled Real Cells, existing devices the Hive borrows for a task and leaves exactly as
found. Waggle is the protocol every bee in the Hive uses to talk to the others, and the Queen is the
Hive's central orchestrator, who runs on the Hive Stand, the machine she lives on. The Pollen Packet
is the thin gateway installed on a device; it has no brain of its own. The Python packet in
`packages/pollen` is the first implementation of this contract. The reason to have a second is
reach: a phone or a constrained IoT board cannot run Python, and the Hive should still be able to
borrow it. A small compiled gateway that speaks this contract is written after Brood 1.0, the first
complete release (roadmap phase 14). The protocol is the product; the Python packet is its first
implementation.

This is a contract and not a tutorial. It lists what a conforming gateway sends, what it answers and
what it enforces, and it points at the Waggle spec ([`spec.md`](spec.md)) for every field. The spec
is the source of truth for the shape of a message; this document is the source of truth for what a
gateway does with it. The enrolment ceremony has its own document, [`enrolment.md`](enrolment.md),
and the decisions behind both are
[ADR-0051](../adr/0051-pollen-packet-is-a-gateway-and-the-trust-model.md),
[ADR-0052](../adr/0052-runtime-ladder-warden-moves-first.md),
[ADR-0054](../adr/0054-access-levels-watch-mode-and-the-patrol.md) and
[ADR-0055](../adr/0055-waggle-replay-window-and-frame-ceiling.md). The words MUST, MUST NOT, SHOULD
and MAY are used as in RFC 2119.

## Contents

1. [What a gateway is](#1-what-a-gateway-is)
2. [The conformance suite](#2-the-conformance-suite)
3. [What a gateway needs from its platform](#3-what-a-gateway-needs-from-its-platform)
4. [Identity, enrolment and persisted state](#4-identity-enrolment-and-persisted-state)
5. [The link](#5-the-link)
6. [Signing, verifying and the sender rules](#6-signing-verifying-and-the-sender-rules)
7. [Duplicates, freshness and the frame ceiling](#7-duplicates-freshness-and-the-frame-ceiling)
8. [Messages a gateway sends](#8-messages-a-gateway-sends)
9. [Messages a gateway answers](#9-messages-a-gateway-answers)
10. [Capability checks](#10-capability-checks)
11. [Exec semantics](#11-exec-semantics)
12. [Paths, scratch and left-as-found](#12-paths-scratch-and-left-as-found)
13. [The dead-man switch](#13-the-dead-man-switch)
14. [The outbox](#14-the-outbox)
15. [Errors a gateway emits](#15-errors-a-gateway-emits)
16. [What a gateway must never do](#16-what-a-gateway-must-never-do)
17. [Optional: colonization](#17-optional-colonization)
18. [A signed test vector](#18-a-signed-test-vector)

## 1. What a gateway is

A gateway is a gateway and nothing else. It connects out to the Queen, executes only what a signed
request from a trusted node asks, inside a lease (the Hive's temporary hold on the device: a scratch
directory, the processes it started, a restore on release), after checking the request against the
capabilities it was granted, and it reports what happened. It is one node (a process that signs
Waggle frames with a key of its own), with one `device_id`, one `node_id` and one Ed25519 key; it
serves one Cell (a unit of compute), the device's, with the `cell_id` and `warden_id` the Queen gave
it at enrolment.

A conforming gateway runs at Level 0 of the runtime ladder (ADR-0052): the gateway is the only part
of the Hive on the device, and the device's Warden (the always-on supervisor of one Cell, a unit of
compute) runs on the Hive Stand and drives the device through the session messages. It reports
`level: GATEWAY` in every heartbeat. Levels 1 and 2 need the `hivemind` runtime on the device and
are an optional extension (section 17). With no Warden on the device to decide anything when the
link is lost, a gateway also runs a dead-man switch: after too long without the Queen it releases
everything its leases hold (section 13).

## 2. The conformance suite

The suite lives in `packages/hivemind/tests/contracts/`. It is a black-box suite that plays the Hive
Stand. It starts a real swarm listener over a WebSocket, mints invites, starts the gateway under
test as a subprocess, lets it enrol, and then drives it with signed requests as the Queen and the
Warden would, asserting what the gateway sends back and what it leaves on the device. It never
imports the gateway. Test files are held to 400 lines, so the suite is four modules that share a
rig (the package `contracts/gateway/`): `test_gateway_contract.py` (enrolment, the link, leases and
capabilities), `test_gateway_contract_programs.py` (sessions, exec, background processes),
`test_gateway_contract_files.py` (files, observation) and `test_gateway_contract_resilience.py`
(forged and replayed frames, reconnection, the outbox, the dead-man switch, `QueenMoved`). The
sections of this document the suite checks are 4 to 16.

By default it starts the Python packet. To run the identical tests against another implementation,
set the environment variable `HIVEMIND_GATEWAY_COMMAND` to a command template: the command line that
starts your gateway, with the placeholders the suite fills in for the values a gateway needs (the
`{invite}` string, a fresh `{data_dir}` and the suite's `{python}`). What each placeholder means,
and how `&&` splits a gateway that enrols with one command and runs with another, is defined in one
place, the docstring of `test_gateway_contract.py`, and not repeated here, so that it cannot drift.
Then run it like any other contract suite:

```text
HIVEMIND_GATEWAY_COMMAND='<your command template>' \
    uv run pytest packages/hivemind/tests/contracts/test_gateway_contract*.py
```

Against the Python packet the four modules take about a minute. The gateway runs on the machine that
runs the suite, because where a clause is about the device's disk or process table the suite looks
at that machine's.

A gateway conforms when the suite passes against it. Colonization (section 17) is optional, so its
one test accepts either a `pollen.unsupported` refusal or a `swarm.colonized` answer rather than
being skipped. If the suite and this document disagree, one of them has a bug, and it is fixed in
the same change.

## 3. What a gateway needs from its platform

- A WebSocket client with **binary frames**, TLS, and a way to compare the SHA-256 fingerprint of
  the server's certificate with a pinned value instead of validating a chain (section 5).
- **Ed25519** (RFC 8032) signing and verification, **SHA-256**, and a cryptographically secure
  random source.
- A JSON reader and writer able to reproduce the canonical form of section 6, which means control
  over how numbers and non-ASCII text are written.
- Durable storage with an atomic replace and a flush to disk, for the key, the state, the lease
  table and the outbox.
- A wall clock (UTC) and a monotonic clock.
- To run programs (sections 9.2, 9.3 and 11): the ability to start a process in its own process
  group (POSIX) or Job Object (Windows), to signal or kill the whole group, and to read its output.
  A gateway on a platform with no process model implements the file, lease and observation messages
  only, is enrolled at `READ_ONLY` (which grants no `exec`), and answers `session.exec` and
  `session.process` with `pollen.unsupported`.

## 4. Identity, enrolment and persisted state

**Ids.** An id is a lowercase prefix, an underscore and a 26-character ULID in Crockford's base32
alphabet (`0123456789ABCDEFGHJKMNPQRSTVWXYZ`): a 48-bit millisecond Unix timestamp followed by 80
random bits, written most significant bit first, so the first character is never above `7`. A
gateway mints `device_<ULID>` and `node_<ULID>` once, at first run, `lease_<ULID>` for each lease it
opens and `msg_<ULID>` for each envelope (the fixed outer record every Waggle message rides in) it
sends. Ids it receives are validated against their prefix and alphabet.

**Key.** One Ed25519 key per device, generated at first run. The private key (the 32-byte seed) is
stored where only the account the gateway runs as can read it (mode 0600 on a POSIX system) and is
never sent or logged.

**Enrolment.** The gateway implements the ceremony of [`enrolment.md`](enrolment.md), steps 2 to 6,
from reading the invite string to the first heartbeat.

**State.** The gateway persists everything it needs after a restart, and a crash at any point leaves
the old state or the new state, never a torn one (write a new file, flush it, replace the old one):

- the key (its own file);
- the identity and the Hive pins: the `device_id`, `node_id`, the Queen's URL, the certificate
  fingerprint, the Hive node id and key, the Queen's `hive_id` and the trusted nodes;
- the Cell's identity and grants: the `cell_id`, `warden_id`, access level, tier,
  `granted_capabilities`, `offline_limit_s` and `heartbeat_interval_s`, as last set by the accept or
  a `swarm.node_update`;
- the highest `QueenMoved` `sequence` accepted, and any move still inside its grace window;
- the lease table, the persisted record of every open lease (section 12), the outbox, a durable
  queue of the frames it could not send (section 14), and the last moment the Queen was heard from
  (section 13).

## 5. The link

**A gateway dials; it never listens.** It opens no inbound socket and has no address a peer can
connect to. It dials the Queen's persisted URL and nothing else: no other message carries an
address, and the only way the URL changes is a verified `control.queen_moved` (enrolment.md section
12).

**Schemes and TLS.** `wss://` is the rule. Plain `ws://` is valid only for a loopback host (the
spec's one other exception, a Tor onion service, belongs to Night Veil Cells, which are never Real
Cells). For `wss://` the gateway completes the TLS handshake and compares the SHA-256 of the DER
encoding of the certificate the server presents, as lowercase hex, with the fingerprint pinned from
the invite; the pin replaces chain and name validation. A mismatch closes the connection before any
Waggle frame is sent and is the error `waggle.transport.tls_pin`. A gateway never connects over
`wss://` with no pin.

**Reconnection.** After a failed or lost connection the gateway re-dials with capped exponential
backoff: 0.5 s for the first wait, doubling each time, never more than 30 s (`RECONNECT_INITIAL_S`,
`RECONNECT_FACTOR`, `RECONNECT_MAX_S`). It does not stop trying. The only exception is a connection
closed with code 1002 for `waggle.version.unsupported_major`: no replay can fix a major mismatch, so
the gateway stops and reports it. Keepalive is the WebSocket ping, every 20 s with a 20 s timeout
(`PING_INTERVAL_S`, `PING_TIMEOUT_S`); the gateway answers pings and treats a missed pong as a lost
link.

**What a close means.**

| Code | Meaning | The gateway |
|---|---|---|
| 1000 | Normal close. | Reconnects. |
| 1002 | Protocol error: a malformed frame, an unsupported major, an unknown kind. | Reconnects, except for `waggle.version.unsupported_major`. |
| 1008 | Policy violation: a missing, unknown or invalid signature. | Reconnects with backoff. A gateway whose node was revoked meets this on every dial. |
| 1009 | Message too big. | Reconnects. |
| 4029 | The frame ceiling (section 7). | Reconnects with backoff, and slows down. |

**The first frame.** The first frame of every connection after enrolment is a signed
`swarm.device_heartbeat`. After it the gateway sends a `forage.capacity_report` with trigger
`RECONNECTED` and then replays its outbox in order (section 14). It sends heartbeats at
`heartbeat_interval_s` for as long as the connection is open, **including while a command runs**:
executing a request never blocks the heartbeat, the pongs or the reading of the next frame.

**Versions.** A gateway speaks major 1 and writes the minor it implements in every envelope's
`version`. It never uses a kind, field or enum member introduced after the minor the Queen has
advertised (spec section 4), and it recognises every kind in the catalogue of the minor it speaks,
so a kind it does not implement draws `pollen.unsupported` and not a closed connection.

## 6. Signing, verifying and the sender rules

**What is signed.** Every frame a gateway sends is signed with its node key, and `node_id` in the
envelope is the gateway's own. The signature covers the **canonical bytes**: the frame's JSON object
without its `signature` key, serialised exactly as Python's `json.dumps(sort_keys=True,
separators=(",", ":"), ensure_ascii=True)` serialises it, and encoded as UTF-8. Another language
reproduces that as follows:

- Keys of every object in ascending order of their Unicode code points; no whitespace anywhere
  (separators are `,` and `:` alone).
- Strings in double quotes. Escape `"` as `\"`, `\` as `\\`, and the control characters as `\b`,
  `\f`, `\n`, `\r`, `\t`; every other character below U+0020, U+007F and every non-ASCII character
  as `\uXXXX` with **lowercase** hex digits, a character above U+FFFF as a UTF-16 surrogate pair.
  `/` is not escaped.
- Integers as plain decimal. Floats as Python's `repr` of the parsed value: the shortest digits that
  round-trip, always with a decimal point or an exponent (`30.0`, never `30`), and an exponent only
  when the decimal exponent is below -4 or at least 16, written with a sign and at least two digits
  (`1e-05`, `1e+16`, `1.2345678901234568e+17`). NaN and infinity never appear; a frame containing
  one is malformed.
- `true`, `false` and `null` in lower case.

**A signer** writes its numbers in exactly that form, because the receiver re-renders every number
it parses: a float written as `3e1` parses to 30.0 and is signed over as `30.0`, so the sender must
have signed `30.0`. **A verifier** computes the canonical bytes from the frame as received, not from
a re-validated model, and it keeps a number's int-or-float nature as written (`30` is an integer,
`30.0` a float). The signature is the 64-byte Ed25519 signature written as standard padded base64:
88 characters. Section 18 is a frame to check an implementation against.

**Frames.** A frame is one WebSocket **binary** message holding one UTF-8 JSON object with exactly
the ten envelope keys (`id`, `correlation_id`, `sender`, `recipient`, `kind`, `version`, `sent_at`,
`node_id`, `payload`, `signature`), at most `MAX_FRAME_BYTES` = 1,048,576 bytes. A text frame, a
frame over the limit, a frame with a missing or extra key or a non-finite number is malformed and
closes the connection (1002, or 1009 for size). Every `bytes` field in a payload is at most
`MAX_CHUNK_BYTES` = 262,144 raw bytes, written as base64; content larger than that is split into
messages by the sender.

**Verifying.** Before it reads anything else from a frame, the gateway verifies the signature with
the public key registered for the envelope's `node_id` among its trusted nodes (the accept's
`trusted_nodes`, extended or replaced by a verified `QueenMoved`). Unsigned, unknown signer or
invalid signature: the gateway closes the connection (1008). It does not answer a frame it cannot
verify.

**The sender rules.** A verified frame is acted on only if all of the following hold, and is
otherwise dropped:

1. It is addressed to the gateway: the envelope's `recipient` is the gateway's own `device_id`.
2. Its `sender` is the Queen (`hive_id`) or the Cell's current Warden (`warden_id`, from the accept
   or the last `swarm.node_update`).
3. If the kind is one only the Queen sends (`swarm.enrol_accept`, `control.queen_moved`,
   `swarm.node_update` and `swarm.colonize`), its sender is `hive_id`.
4. It is fresh and not a duplicate (section 7).

## 7. Duplicates, freshness and the frame ceiling

**The replay window.** A gateway keeps the envelope ids it has seen from the Queen and the Warden
over a window and drops a duplicate without acting on it. The default is **600 seconds**, with at
most **50,000 ids** kept, forgetting the oldest first (ADR-0055). For a request whose reply it still
holds, such as a repeated `cell.request`, it may send that reply again. This is what makes an outbox
replay after a lost acknowledgement harmless: a command is never run twice because its frame arrived
twice.

**Freshness.** A gateway drops a frame whose `sent_at` is more than `MAX_MESSAGE_AGE_S` = 604,800 s
(seven days) in the past, or more than `MAX_CLOCK_SKEW_S` = 300 s ahead of its own clock. This needs
a wall clock within a few minutes of the Hive Stand's, and a gateway whose clock is wrong drops
every frame, so it SHOULD notice and say so (in its status output) and not retry silently.

**The frame ceiling.** The Queen's listener closes a connection that sends more than a rate it
allows, by default 6,000 frames a minute with a burst of 600, with code **4029** and the reason
`waggle.transport.frame_ceiling`. A gateway never needs anywhere near that rate, and it keeps well
under it by coalescing output: `session.output` carries chunks of up to `MAX_CHUNK_BYTES`, not a
frame per line. On a 4029 close it reconnects with its usual backoff; its outbox replay resumes from
the unsent tail.

**Chunk groups.** For `session.put_file` the gateway holds at most `MAX_OPEN_CHUNK_GROUPS` = 8
incomplete transfers from the same sender, discards a transfer idle longer than
`CHUNK_GROUP_TIMEOUT_S` = 60 s together with its partial file, and rejects a first chunk whose
`offset` is not 0 or whose `total_bytes` is over the family's cap, and any chunk whose `offset` is
not the length received so far (spec section 5).

## 8. Messages a gateway sends

| Kind | When | Notes |
|---|---|---|
| `swarm.enrol_request` | Once, at enrolment. | Never appended to an outbox. |
| `swarm.device_heartbeat` | First frame of each connection, then every `heartbeat_interval_s`. | `level` is `GATEWAY`; `lease_ids` the open leases (at most 64); `hive_started_processes` the processes its leases started and still track; `outbox_pending` the frames waiting; `uptime_s` since the gateway started. Carries no capacity figures. |
| `forage.capacity_report` | After each reconnection (`RECONNECTED`), when a figure changes materially (`CHANGED`), and on a timer (`PERIODIC`). It MAY send one right after the accept (`ENROLLED`). | A full snapshot, so a replayed report is self-contained. `model_servers` is empty and `usage` all zeros: a gateway hosts no models and runs no bees. Capabilities are reported at enrolment; the Hive treats a capability report that changes without re-enrolment as a dire pattern (roadmap 13.4a), so a gateway reports changed capabilities no other way. |
| `cell.lease_opened` | Answering `cell.request`. | Section 9.1. |
| `cell.lease_released` | Answering `cell.teardown_request`, and unprompted when the dead-man switch fires. | Section 9.1; queued in the outbox. |
| `session.output`, `session.exit`, `session.process_state` | Answering the session messages. | Section 9.2 and 9.3; a terminal `session.exit` is queued in the outbox, `session.output` never. |
| `swarm.observation` | Answering `swarm.observe`. | Section 9.4. |
| `control.pong` | Answering `control.ping`. | |
| `control.error` | Whenever a request is refused. | Section 15. |

## 9. Messages a gateway answers

### 9.1 Leases: `cell.request` and `cell.teardown_request`

**Opening.** A `cell.request` names the Cell, the `holder` (the Warden that will own the tenancy),
the access level the holder needs, an optional `scratch_quota_bytes` and a reason. The gateway:

1. Checks that `cell_id` is its own Cell and `holder` is the Cell's Warden; otherwise it answers
   `pollen.lease_refused`.
2. Grants the lower of the requested access level and the node's access level.
3. Creates a scratch directory unique to the lease, readable only by the gateway's account, and
   writes the lease into the persisted lease table **before** it answers.
4. Answers with a correlated `cell.lease_opened`: a new `lease_id`; the granted `access_level`; the
   node's `comb_shield` (the Cell's security tier); the `scratch_root` as an absolute path;
   `allowed_paths`, the scopes of the `cell:outside_scratch` grants the node holds (empty when it
   holds none); and `dead_man_s`, the node's `offline_limit_s`.

When `scratch_quota_bytes` is set the gateway enforces it as the Hive Stand does for its own leases:
it measures the size of the lease's scratch directory (never following a symlink, so a link cannot
hide or inflate usage) while a command runs, and when the quota is crossed it kills that command's
process group and ends the exec as `KILLED`, with a reason that names the quota. A
`session.put_file` that would take the directory over the quota is refused with
`pollen.quota_exceeded`.

**Releasing.** A `cell.teardown_request` names a lease, an urgency (`GRACEFUL` or `IMMEDIATE`) and a
cause (`COMPLETED`, `CANCELLED` or `STING_CUT`). The gateway:

1. Stops accepting session traffic for the lease and ends every exec and background process the
   lease started: a graceful release asks them to terminate and then forces them, an immediate
   release forces them at once. None survives the release.
2. Restores every change the lease recorded outside scratch (section 12).
3. Deletes the scratch directory. For `STING_CUT` (the human's per-Cell emergency disconnect) it
   also deletes every other trace the lease left and discards whatever lease-scoped secret it holds.
4. Answers with a correlated `cell.lease_released`: the `cause` echoed, `is_restored` true only if
   scratch is gone and every recorded path was restored, `killed_processes`, and `residual_paths`
   listing any path it could not restore (empty exactly when `is_restored`).

A teardown for a lease the gateway does not hold open, or has already released, draws
`hive.session.lease_not_open`. A teardown that names no lease (it asks to retire a whole Virtual
Cell, a VM or container the Hive provisions and destroys) draws `pollen.lease_refused`: a Real Cell
is never retired.

### 9.2 Sessions: `session.*`

A session is identified by its lease, one per lease; concurrency lives at the exec level, each exec
keyed by its request's envelope id. **Every session request ends in exactly one `session.exit` or
one `control.error`**; `session.output` chunks may precede that and never follow it. A session
message for a lease the gateway does not hold open draws `hive.session.lease_not_open`.

| Request | The gateway |
|---|---|
| `session.open` | Answers `session.exit`, `request_kind` `OPEN`, outcome `COMPLETED`. |
| `session.exec` | Section 11. Streams `session.output`, ends with `session.exit`. |
| `session.stdin` | Feeds the chunk to the exec it names in `offset` order; `final` closes the process's stdin; `signal` delivers `INTERRUPT`, `TERMINATE` or `KILL` (mapped to the platform) instead of bytes. Input for an exec that has ended is dropped. |
| `session.put_file` | Writes a file in chunks (below). |
| `session.get_file` | Reads a file (below). |
| `session.delete_file` | Waggle 1.13. Deletes one file; ends in one `session.exit` with `request_kind` `DELETE_FILE`, outcome `COMPLETED` and no size or digest. A missing file is `pollen.not_found`; a refused path is `pollen.path_refused`. A delete follows the write rules of section 12, including the record of prior bytes outside scratch. |
| `session.close` | Ends every exec still running in the session (each gets a `session.exit` with outcome `KILLED`) and every background process it started, and stops accepting session traffic for the lease. The lease itself stays open until it is released. |

**Put-file.** Chunks are events grouped by `transfer_id`, the envelope id of the chunk at offset 0.
The gateway checks the capability and the path and the quota against `total_bytes` on the first
chunk, writes to a temporary file, and on the final chunk verifies the length and the SHA-256 of the
whole, renames the file into place and sets the executable bit if `is_executable` (ignored on
Windows). It never runs what it wrote. It answers one `session.exit` with outcome `COMPLETED`,
`request_kind` `PUT_FILE`, `size_bytes` and `sha256`, correlated to the `transfer_id`. A failure at
any chunk is a `control.error` and the temporary file is deleted. A non-final chunk draws no answer
unless it fails.

**Get-file.** The gateway checks the capability and the path and refuses (`control.error`, code
`pollen.transfer_refused`) rather than stream a file larger than `max_bytes`. It streams
`session.output` chunks with stream `FILE`, in `offset` order, the last one `final`, and ends with a
`session.exit`, `request_kind` `GET_FILE`, outcome `COMPLETED`, `size_bytes` and `sha256`.

### 9.3 Background processes: `session.process` (Waggle 1.13)

A request has an `action`. Its answer is a `session.process_state` or a `control.error`.

- **`START`** starts the program in `argv` (the same rules as an exec, section 11, but with no
  timeout and no output stream) in its own process group or Job Object, with its output going to
  `log_path` when one is given and nowhere otherwise. The gateway **records the process in the lease
  table before it answers**, so a crash right after the start cannot leak it. It answers with the
  `pid`, the `argv` as started, the resolved `log_path` and `is_running` true. A program that cannot
  start is `control.error` `pollen.start_failed`.
- **`STOP`** terminates the process group of the `pid`, which must be a process this lease started,
  asking politely and then forcing. The answer says `is_running` false and in `was_running` whether
  it was running when asked.
- **`QUERY`** answers `is_running` for a `pid` this lease started.

A `pid` that this lease did not start draws `pollen.not_found`. A gateway never signals a process
the lease did not start. Closing the session and releasing the lease kill every background process
as the backstop.

### 9.4 Observation: `swarm.observe` (Waggle 1.13)

An observation is how a Warden watches an idle device without a lease (ADR-0054). A `swarm.observe`
names the device, the `kinds` wanted (`PROCESS_LIST`, `RESOURCE_USE`, `LOGS`, `FILE_CHANGES`), the
`roots` for logs and file changes, a `since_at` cursor for file changes and a `max_items` bound. The
gateway:

- Requires the capability `watch:<cell_id>`, and for each root `fs:read` on it; otherwise it answers
  `pollen.capability_denied`. A request that names a different device draws
  `waggle.codec.invalid_payload`.
- Answers from **fixed collectors**: reading the platform's process, resource, log and file-change
  facilities directly. It never runs a command that a request could shape, and it needs no lease.
- Observes only what the four kinds name: the process list; resource use, which includes network
  counters; log lines under the roots; file-change events under the roots. It never captures the
  screen or the input, and it never writes.
- Honours the bounds of the `swarm.observation` message (at most 500 processes, 200 log lines, 500
  file changes, `max_items`), sets `is_truncated` when it cut anything and stamps `observed_at` with
  its own clock.
- Returns a kind the platform cannot collect empty (`resources` null), and says so in `reason`.

### 9.5 Updates and housekeeping

- **`swarm.node_update`** (Queen only): the gateway persists the new values and applies them from
  the next request (enrolment.md section 10). A request already running is not interrupted.
- **`control.queen_moved`**: enrolment.md section 12. The gateway replaces the Queen's address only
  on a notice the current Hive key verifies, whose `sequence` is above the highest it has persisted.
- **`control.ping`**: answered with `control.pong` carrying the gateway's arrival time. The
  `version` of that envelope is how the peer learns the minor the gateway speaks.
- **`control.error`** received: correlated to the request it answers and otherwise ignored, except
  that a `hive.swarm.enrol_denied` ends an enrolment attempt.
- A request kind the gateway recognises but does not implement draws a `control.error` with code
  `pollen.unsupported`; an event kind it does not implement is dropped.

## 10. Capability checks

The gateway checks every request against `granted_capabilities` **before any executor runs**, and
refuses a request that is not covered with `control.error` code `pollen.capability_denied`. The Hive
Stand has already checked the same request at its own enforcement point; the gateway's check is a
second, independent wall, and it is why a read-only device runs no command at all.

**The grammar.** A capability string is `family:scope`. A gateway understands five families: `exec`,
`fs:read`, `fs:write`, `cell:outside_scratch` and `watch`. A string in any other family, or one it
cannot parse, grants nothing: it is ignored and never widens anything.

**Matching.** For `exec`, `fs:read`, `fs:write` and `cell:outside_scratch` the scope is a glob and
the held scope covers the needed one when they are equal as text, or when `fnmatch` accepts the
needed scope against the held pattern, **case-sensitively**, after replacing every backslash with a
slash in both. In `fnmatch`, `*` matches any run of characters, including `/` and including none, so
`**` needs no special case; `?` matches any one character; `[abc]` and `[!abc]` match a character
class. For `watch` the scope is a prefix: the held scope equals the needed one, or ends in `*` and
is a prefix of it.

**`{scratch}`.** The literal `{scratch}` in a held scope stands for the lease's scratch root: the
gateway replaces it with the root, written with slashes, with a trailing `/` removed and with each
`*`, `?` and `[` in the root escaped as `[*]`, `[?]` and `[[]`, so a root with an unusual name can
match only itself.

**What a request needs.** Paths are resolved first (section 12), and the needed scope is the
resolved path:

| Request | Needs |
|---|---|
| `cell.request`, `cell.teardown_request`, `session.open`, `session.close`, `session.stdin` | Nothing beyond the sender rules and an open lease. |
| `session.exec`, `session.process` `START` | `exec:<argv[0]>` exactly as given (not resolved through the search path); if `cwd` is outside scratch, `cell:outside_scratch:<cwd>`; for `START` with a `log_path`, the write rules for that path. |
| `session.process` `STOP`, `QUERY` | Nothing beyond the sender rules and that the lease started the process. |
| `session.get_file` | `fs:read:<path>`. |
| `session.put_file`, `session.delete_file` | `fs:write:<path>`, and `cell:outside_scratch:<path>` when the path is outside scratch. |
| `swarm.observe` | `watch:<cell_id>`, and `fs:read:<root>` for each root. |

## 11. Exec semantics

These are the semantics of the `CellSession` every Cell offers, carried over Waggle. A gateway
implements them exactly, because a Warden does not know which kind of Cell it is driving.

- **Argument lists only.** `argv` is the program and its arguments. The gateway starts the program
  directly and never through a shell: no expansion, globbing, redirection or quoting happens unless
  `argv` is itself a shell (`["sh", "-c", ...]`), which then needs `exec:sh`. `argv[0]` is looked up
  the platform's usual way if it has no path separator.
- **Directory and environment.** `cwd` defaults to the lease's scratch directory. The child's
  environment is the gateway's own with the request's `env` overlaid for that process only. A
  gateway that is itself a packaged program removes what its packaging added to its own environment
  first, above all a loader path that points at the libraries it unpacked for itself (the Python
  packet's single-file build restores the value that was there before, and tells a single-file
  program it starts, itself included, to run as an instance of its own): the child runs on the
  device's libraries, not the gateway's. Values in `env` and bytes in `stdin` are possible secrets
  and are never logged.
- **A program that cannot start** (not found, not executable, no permission, a bad format) is not an
  error reply: the exec ends with outcome `EXITED` and exit code **127**, after one line on the
  stderr stream saying what failed. A request that is refused for a reason of ours (capability,
  path, lease) is a `control.error` instead.
- **Output.** Standard output and standard error are read concurrently and sent as `session.output`
  chunks of at most `MAX_CHUNK_BYTES`, in order, each stream with its own `offset` counting from 0;
  each stream ends with a chunk whose `final` is true, before the `session.exit`.
- **The end of an exec.** Exactly one `session.exit` correlated to the exec, with:
  - `EXITED` and the `exit_code` when the process ended on its own;
  - `SIGNALED` and the platform's `signal_name` when a signal the gateway did not send ended it;
  - `TIMED_OUT` when `timeout_s` of wall-clock time passed: the gateway terminates the process
    group, forces it after a short grace and reports it;
  - `KILLED` when the gateway ended it: on a `session.stdin` signal, on a closed session, on a
    crossed scratch quota, or because stdout plus stderr passed `max_output_bytes`, in which case it
    kills the process group at once (the default cap is `DEFAULT_MAX_OUTPUT_BYTES` = 16 MiB, so a
    looping device can never stream without bound).
- **Process groups.** Every exec runs in its own process group (POSIX) or Job Object (Windows), and
  the gateway **records the group in the lease table before it sends the first output frame**. Every
  kill, whether a timeout, a cap, a signal, a close or a release, goes to the whole group, so a
  program's children die with it.
- **Concurrency and the link.** Several execs may run in a session at once. An exec that outlives
  the link keeps running and keeps its timeout; its terminal `session.exit` is queued in the outbox
  (section 14), and the `session.output` chunks it could not send are discarded.

## 12. Paths, scratch and left-as-found

A Real Cell is borrowed and left exactly as found.

- **Scratch.** Each lease has its own scratch directory. A relative path in a request resolves under
  it, and anything the lease creates should be there.
- **Resolve before you check.** For every path in a request the gateway removes `.` and `..`
  segments and resolves symbolic links (and, on Windows, junctions and reparse points) **before**
  deciding whether the path is inside scratch and which capability it needs. A link inside scratch
  that points outside is outside. The check is on the resolved path, and the gateway acts on the
  resolved path it checked, not on the text it was sent.
- **Inside scratch,** a path needs only the capability for what is done to it (`fs:read` to read,
  `fs:write` to write or delete).
- **Outside scratch,** a read needs `fs:read`; a write or delete needs `fs:write` and
  `cell:outside_scratch`. Before the first change to such a path the gateway records its prior state
  in the lease table, either that it did not exist or its bytes and mode, and if it cannot record it
  refuses the change (`pollen.quota_exceeded`). Nothing is changed outside scratch that was not
  recorded first.
- **On release,** in reverse order of recording, the gateway deletes what the lease created and
  writes back what it changed, then deletes scratch. The answer says whether it succeeded and lists
  any path it could not restore.
- **After a restart,** a lease found in the table in state `OPEN` or `RELEASING` is not forgotten:
  its scratch, processes and recorded paths are still the gateway's to release, and the dead-man
  switch still applies to it.

Appendix C of the coding rules records the device lease as a state machine: `OPEN`, then
`RELEASING`, then `RELEASED`, with `RELEASING` retried until every step has succeeded.

## 13. The dead-man switch

A gateway has no Warden on the device, so when it loses the Queen it cannot ask anyone what to do.
If the Queen has not been heard from for longer than `offline_limit_s` (the value from the accept or
the last `swarm.node_update`, and the `dead_man_s` it put in each `cell.lease_opened`), the gateway
releases every lease as if a teardown had arrived with cause `DEAD_MAN`: it kills every process the
leases started, deletes their scratch, restores what it recorded, and queues a `cell.lease_released`
with cause `DEAD_MAN` in its outbox. It keeps dialling.

"Heard from" means a verified frame from the Queen or a WebSocket pong. The time is measured on a
monotonic clock, so a change of the wall clock neither trips the switch nor delays it, and the last
moment the Queen was heard from is persisted (at a coarse interval, since flash storage wears) so
that restarting the gateway does not restart the clock. The switch applies only to a gateway with no
Warden on the device, which is every conforming gateway at Level 0.

## 14. The outbox

The outbox is a durable, ordered queue of the frames a gateway could not send. It holds what must
reach the Queen after a reconnection: the `cell.lease_released` of a release that happened while the
link was down (the dead-man switch included), and the terminal `session.exit` of an exec that
outlived the link. It never holds a `session.output` chunk, because the Warden that wanted it has
given up on it, and it never holds a `swarm.enrol_request`.

The behaviours are those of spec section 10; the file format is private to the gateway.

- It stores the envelopes **unsigned** and signs them when it sends them, with the key it has then,
  keeping the original `id` and `sent_at` so that a receiver can tell a late message from a fresh
  one.
- It replays in first-in, first-out order after the first heartbeat and the capacity report of a
  reconnection, acknowledging each frame once it has been sent, and stops at the first failure
  leaving the rest pending, so that replaying twice sends nothing the second time and a crash
  mid-replay leaves exactly the unsent tail.
- Appending an id already pending is a no-op, and an entry older than `MAX_MESSAGE_AGE_S` is
  dropped.
- It survives a crash: an append is flushed to disk before it returns.
- It refuses to hold a frame that could not be sent whole (over `MAX_FRAME_BYTES`), so one poison
  entry cannot wedge the queue.

## 15. Errors a gateway emits

Every refusal is a `control.error` correlated to the request, with a stable `code`, a full-sentence
`message` that names ids and never a path, a hostname or content (paths on a Real Cell are `C2`, the
highest sensitivity label), and `is_retryable`. A traceback never crosses the wire. A frame that
cannot be decoded at all gets no `control.error`: the connection is closed (section 5).

| Code | Raised when | `is_retryable` |
|---|---|---|
| `pollen.capability_denied` | A request is not covered by `granted_capabilities`. | false |
| `pollen.path_refused` | A path resolves outside scratch and the lease's allowed reach, or cannot be resolved safely. | false |
| `pollen.not_found` | A file to delete or read, or a process to stop or query, does not exist or was not started by this lease. | false |
| `pollen.start_failed` | A background program could not be started. | false |
| `pollen.quota_exceeded` | A write would pass the lease's scratch quota or the free disk, or a change outside scratch could not be recorded. | false |
| `pollen.transfer_refused` | A file transfer broke a rule: an offset out of order, a length or digest mismatch, a file over `max_bytes`, too many open transfers. | false |
| `pollen.lease_refused` | A `cell.request` or `cell.teardown_request` cannot be honoured: another Cell or holder, a whole-Cell retirement. | false |
| `pollen.unsupported` | A kind the gateway recognises but does not implement, or an exec on a platform with no process model. | false |
| `hive.session.lease_not_open` | Session or teardown traffic for a lease the gateway does not hold open. | false |
| `waggle.codec.invalid_payload` | A frame had a readable `id` but a payload or envelope rule failed (for example a `swarm.observe` naming another device). | false |

`pollen.capability_denied`, `pollen.path_refused`, `pollen.not_found`, `pollen.start_failed` and
`hive.session.lease_not_open` are fixed by the design of the Swarm and by the spec. The other four
`pollen.` codes (`quota_exceeded`, `transfer_refused`, `lease_refused` and `unsupported`) are
defined by this document so that every gateway answers the same way, and `pollen/errors.py` mirrors
the whole table. The gateway also meets, but does not emit, `hive.swarm.enrol_denied` (the Queen's
refusal of an enrolment), `waggle.transport.tls_pin` and the `waggle.*` close reasons of section 5.

## 16. What a gateway must never do

- **Hold or call a model.** No model client, no LLM SDK, no prompt. A gateway has no brain.
- **Write a trail.** The Pheromone Trail, the Hive's append-only audit log, is written on the Hive
  Stand from its side of the link. A gateway keeps no audit log of its own beyond local diagnostics,
  and those never contain a token, a key, an environment value, stdin, a file's content or a
  command's arguments.
- **Supervise.** It raises no Alarm (an issue a bee cannot resolve, escalated up the chain), runs no
  Capping gate (the Hive's quality gate for side effects), decides nothing and retries nothing on
  its own initiative. Reporting is its whole job.
- **Listen.** No inbound socket, ever.
- **Act on anything unverified.** No unsigned, mis-signed, misaddressed, stale or duplicate frame,
  and no frame from a sender the rules of section 6 do not admit, produces an effect. There is no
  auto-update, no startup task and no remote shell outside a lease.
- **Use a shell.** It never runs a command through a shell it chose, and never builds a command line
  from request text.
- **Leave the lease.** It touches nothing outside scratch without the capability, and leaves nothing
  behind after a release.
- **Widen its own grant.** Only an accept or a `swarm.node_update` from the Queen changes what it
  may do.
- **Leak a secret.** The key, the invite token and the invite string are never sent after the
  ceremony, written to a log or placed in an error message.
- **Capture the screen or the input,** or observe anything `swarm.observe` does not name.
- **Dial anyone but the Queen,** or follow an address that arrives in any message other than a
  verified `control.queen_moved`.
- **Queue what must not be queued:** a `swarm.enrol_request`, or a `session.output` chunk.

## 17. Optional: colonization

Colonization moves the device's Warden onto the device (Levels 1 and 2 of the ladder, ADR-0052). It
needs a Python environment and the `hivemind` runtime, so a phone gateway or an IoT board will not
implement it. It is outside the minimal contract, and the suite accepts either answer to it.

A gateway that does not implement it reports `level: GATEWAY` in every heartbeat, reports a platform
and capacity that make the Queen's colonization decision refuse the device, and answers a
`swarm.colonize` with `control.error` `pollen.unsupported`. A gateway that does implement it follows
ADR-0052: `PREPARE` installs the pinned `hivemind` version into an environment of its own, only from
a source the operator configured on the device and never from the wire; `START` starts the on-device
Warden as a background process under the same node key, giving it the Queen's URL and pin, the
`warden_id` and `cell_id`, the access level, the tier and the device's local manifest; `WITHDRAW`
stops it. Each is answered with `swarm.colonized`.

## 18. A signed test vector

A gateway written in another language proves it can sign and verify by reproducing this frame, which
was produced by the reference codec and checked independently with nothing but a JSON library and an
Ed25519 implementation. The key is a test key, `00 01 02 ... 1f`; it must never be used for anything
else.

- Private key (seed), hex: `000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f`
- Public key, hex: `03a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8`
- Node id: `node_01M4TJ4GG0248H248H248H248H`

The frame as the sender wrote it (a `session.exec` from a Warden to a device), shown pretty-printed.
Whitespace and key order on the wire do not matter to verification, because a receiver rebuilds the
canonical bytes from the parsed object; the wire form keeps `é` as UTF-8. The frame carries
`version` 1.12, the minor of the codec that produced it: a receiver accepts any minor of major 1,
and the signature covers whatever `version` string the sender wrote.

```json
{
  "correlation_id": null,
  "id": "msg_01M4TJ4GG0ANANANANANANANAN",
  "kind": "session.exec",
  "node_id": "node_01M4TJ4GG0248H248H248H248H",
  "payload": {
    "argv": ["echo", "héllo"],
    "cwd": null,
    "env": [],
    "has_stdin": false,
    "lease_id": "lease_01M4TJ4GG08H248H248H248H24",
    "max_output_bytes": 16777216,
    "task_id": null,
    "timeout_s": 30.0,
    "worker_id": null
  },
  "recipient": "device_01M4TJ4GG06CSK6CSK6CSK6CSK",
  "sender": "warden_01M4TJ4GG048H248H248H248H2",
  "sent_at": "2026-10-13T12:00:00Z",
  "signature": "dBpuCGZ8IaE5kWQLoxgspxfCF1pUowMSqDtBrfWbGw7TNS64QeJENOYuzPf0pKH4ohsuMyd8Mlrp/jcIXKSDAQ==",
  "version": "1.12"
}
```

The canonical bytes the signature covers: the same object without the `signature` key, 472 bytes,
with the non-ASCII character escaped. Note `\u00e9` in lower case and `30.0` with its decimal point:

```text
{"correlation_id":null,"id":"msg_01M4TJ4GG0ANANANANANANANAN","kind":"session.exec","node_id":"node_01M4TJ4GG0248H248H248H248H","payload":{"argv":["echo","h\u00e9llo"],"cwd":null,"env":[],"has_stdin":false,"lease_id":"lease_01M4TJ4GG08H248H248H248H24","max_output_bytes":16777216,"task_id":null,"timeout_s":30.0,"worker_id":null},"recipient":"device_01M4TJ4GG06CSK6CSK6CSK6CSK","sender":"warden_01M4TJ4GG048H248H248H248H2","sent_at":"2026-10-13T12:00:00Z","version":"1.12"}
```

To check an implementation: (1) derive the public key from the seed and compare; (2) parse the
frame, remove `signature`, rebuild the canonical bytes by the rules of section 6 and compare them
byte for byte; (3) verify the signature over those bytes with the public key; (4) sign the canonical
bytes with the seed. Ed25519 signing is deterministic (it needs no random nonce), so the signature
you produce is the same 88 characters.
