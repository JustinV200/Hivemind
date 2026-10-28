# hivemind.swarm

The swarm package turns enrolled devices into Real Cells. A device is an existing machine the Hive
borrows and leaves exactly as found; it carries a Pollen Packet (a thin gateway, no model, no
supervision) that dials out to the Hive Stand's swarm listener. This package is the Hive's side of
that conversation: the registry that remembers devices and invites, the enrolment door that admits
a device, the gateway that listens and judges whether each device is still reachable,
`SwarmSource` that leases a device as a Real Cell, `PollenSession` that is that Cell's terminal
carried over Waggle, the Propolis shield that verifies a device's VPN before work is placed on it,
and the watch probe that observes an idle device read-only. This package holds the ladder's
transitions and the two pure decisions on it: whether a device can carry its own Warden (`level`)
and whether a colonized device should start a model server of its own and become a Nuc (`nuc`);
carrying the moves out is the Queen's (`hivemind.queen.swarm_gate.colonize` and
`hivemind.queen.swarm_gate.colonize.nuc`).

Layer 3 (sources of Cells, and capabilities handed down). It imports `guard`, `cell`, `memory`,
`supervision`, `brood_chamber`, `basket`, `pheromone`, `forage`, `llm`, `manifest`, `common` and
`waggle`, and never `hive`, `workers`, `wardens`, `queen`, `entrance` or `cli`. Nothing here
branches on `cell.kind` or runs a subprocess.

## From an invite to a command on the device

```text
 operator                 Hive Stand (this package)                      device (Pollen Packet)
 --------                 -------------------------                      ----------------------
 hive swarm invite  --->  EnrolmentDoor.mint_invite
                            stores sha256(token), cap, tier, expiry
                            returns the "pollen1." string ONCE   ---->   pollen enrol <string>
                                                                          mints device key + ids
                          SwarmListener (wss://, pinned cert, limits)
                            Codec verifies with SwarmVerifier(KeyRing)
                            swarm.enrol_request (self-signed proof) <--  first frame
                          ConnectionRouter -> EnrolmentDoor.enrol
                            registry.redeem: node + spent invite
                            + swarm.enrolled, one transaction
                            swarm.enrol_accept (correlated)       --->   pins the Hive key
                          next connection: first frame is a signed
                            swarm.device_heartbeat                <---   (reconnect)
                          ConnectionRouter -> HeartbeatMonitor.admit
                            DeviceLink attached to GatewayHub
                            swarm.node_update (current terms)     --->
                            HubListener.on_online -> Queen's swarm gate
 Warden for the device:
   SwarmSource.lease(request)
     cell.request -> cell.lease_opened                           <-->   opens a lease, scratch root
   ShieldedSource (PROPOLIS only): PropolisChecks over a session
   SwarmSource.open_session(lease) -> PollenSession
     exec / put_file / get_file / start / stop / is_running
       CommandGuard.authorise (DEVICE_COMMAND) -> request          -->  packet re-checks its own
       swarm.command_sent recorded                                      granted capabilities
   lease.release() -> cell.teardown_request -> cell.lease_released <--  device restores, confirms
```

Silence is the signal that matters: a device is `UNREACHABLE` when its link ends or when
`heartbeat_miss_limit` beats in a row are missing (`LivenessLoop` sweeps every few seconds, also
for a device that enrolled and never connected), and `ONLINE` again on the next verified
heartbeat. A device with no Warden on it releases its own leases after `dead_man_s`; the source
hears that release and records it, so the lease is never left looking open.

## Public API (roadmap steps 11.1, 11.2, 11.5, 11.6a, 11.8a, 11.12)

- **Errors** (`errors`): `SwarmError` and its subclasses (`EnrolmentDeniedError`,
  `InviteFormatError`, `NodeRevokedError`, `LadderError`, `GatewayUnavailableError`,
  `DeviceRequestError`, `ShieldUnverifiedError`, ...), each with a stable `hive.swarm.*` code;
  `EnrolRefusal`, the closed set of reasons a refusal is recorded under.
- **Models and machines** (`models`, `node_state`, `level`): `SwarmNode`, `SwarmInvite`,
  `InviteState` (ISSUED -> REDEEMED | EXPIRED | REVOKED), `NodeStatus` (ONLINE <-> UNREACHABLE ->
  REVOKED), `RuntimeLevel` and the ladder GATEWAY <-> COLONIZED <-> NUC (`can_move`,
  `assert_move`, `is_promotion`). Every machine is one transition table with a comment on each
  edge; a forbidden edge raises. `decide_colonize(ColonizeFacts)` is the pure rule for the first
  rung (Python 3.12 or newer, access level SCRATCH or FULL, Forage for a Warden and a sub-bee, and
  a reason: the operator asking, the Hive Stand under pressure, or a task that must survive
  outages): it returns a `ColonizeReason` or a refusal word, never an exception.
  `decide_nuc(NucFacts)` is the pure rule for the second (`nuc`): the device is COLONIZED, its
  Warden declared a model server in its capacity report, the Hive allows that server models (the
  Forage map rows its manifest lists for the server id) and at least one fits the VRAM the device
  has free (a model with no VRAM figure runs on the CPU and needs none), and something asks for
  it: the operator's `desired_level`, the shared pool's seat pressure past `[swarm.nuc]
  seat_pressure`, or a task that must survive a lost link. It returns the server, every model that
  fits alone (the allowlist the Queen sets in the Warden's `Ceilings`), the ones that fit together
  (loaded first) and a `NucReason`, or one refusal word (`not_asked`, `not_colonized`,
  `no_server`, `no_allowlist`, `vram_too_small`).
- **Registry** (`registry`): the `SwarmRegistry` Protocol (invites and nodes), `SqliteSwarmRegistry`
  (migration series `swarm`), `MemorySwarmRegistry`, one contract suite
  (`tests/contracts/test_swarm_registry_contract.py`). `redeem` is atomic and single use; every
  change takes an optional trail event written in the same transaction.
  `hivemind.cli.stores.open_swarm_registry(db)` opens the SQLite one.
- **Enrolment** (`enrolment`): `EnrolmentDoor` (`mint_invite`, `enrol`), `EnrolmentRules`,
  `InviteEndpoint`, `parse_invite_string` / `build_invite_string`, `granted_capabilities` (the
  access level's capability strings, cut to the five families a packet can enforce),
  `decide_enrolment` and `node_update_for` (pure; the update carries the directories the node's
  capabilities let its Warden read, `readable_under`).
- **Gateway** (`gateway`): `SwarmListener(ListenerConfig, ListenerDeps)`, `GatewayHub` and the
  `HubListener` Protocol (`on_online`, `on_offline`, `on_frame`), `DeviceLink` (`request`,
  `stream`, `send`), `HeartbeatMonitor`, `LivenessLoop`, `ConnectionRouter`, and the
  `WardenConnectionHandler` Protocol a device Warden's connection is handed to.
- **Source and session** (`source`, `session`): `SwarmSource(node, hub, recorder, guard)` is a
  `RealCellSource` for one device and a `HubListener`; `PollenSession` is a full `CellSession`
  (`exec`, files, background processes); `CommandGuard` / `EnforcedCommandGuard` check every
  device command at the `DEVICE_COMMAND` point and record `swarm.command_sent`. A device's lease
  is given `DevicePathRule` (`LeaseFacts.paths`): its paths live on another machine's disk, so the
  lease normalises them lexically by that machine's rules (POSIX or Windows) and never resolves
  them against the Hive Stand's own filesystem; the packet resolves symlinks on the device.
- **Shield and watch** (`shield`, `watch`): `ShieldedSource(inner, PropolisChecks)` holds a
  Propolis device's lease back until fixed read-only commands show its tunnel carries the default
  route and DNS; the commands and the judges themselves are `hivemind.cell.tunnel`'s, shared with
  the Virtual Cell attestation (`hivemind.hive.propolis`), and `PropolisChecks` adds the
  `swarm.shield_*` record; `PollenWatchProbe.observe(kinds, roots, since_at)` asks an idle device
  what it looks like without a lease, and `readable_roots(node)` is the directories the operator
  named for that device (`SwarmNode.watch_roots`) that its granted capabilities let the Warden
  read: the only ones it is asked about.
- **Operator desk** (`operator`): what `hive swarm` does to a device that is already enrolled, with
  the trail event each write leaves. `OperatorDesk(registry, recorder)` revokes (`swarm.revoked`),
  sets access (`swarm.access_changed`, with the capability strings recomputed), sets the tier
  (`swarm.shield_changed`, `NIGHT_VEIL` refused), names the directories its idle Warden reads
  logs and file changes in (`set_watch_roots`, `swarm.watch_changed`; `checked_roots` refuses a
  relative path, too many, or one its capabilities do not let the Hive read), and `move` writes
  the `desired_level` wish, one rung, with no event (the Queen's swarm gate acts on it), and the
  `ceiling`, the highest rung the Queen may take the device to by her own decisions: a `promote`
  lifts it and a `demote` sets it to the rung the device is lowered to, which pins a device down.
  `find_node` resolves a device id, a Cell id or a unique label. Refusals carry their reason:
  `UnknownNodeError`, `AmbiguousNodeError`, `TierRefusedError`, `RungUnavailableError`,
  `WatchRootRefusedError`. It is not re-exported above this sub-package: import it from
  `hivemind.swarm.operator`.
- **Pollen Packet** (`packet`, ADR-0067): `build_packet(PacketDeps, PacketOrder)` writes the folder
  `hive swarm packet` hands an operator: the cached pollen build (`find_pollen_build`), an
  installer and README from `templates/`, and an owner-only `secrets/` holding a single-use
  tailnet key (`TailnetKeys`: `TailscaleApiKeys` over Tailscale's API, `FakeTailnetKeys` in tests)
  and a single-use invite from the enrolment door. The key is minted first; any later failure
  revokes both and removes the folder. The OAuth client the key is minted with comes from a
  `SecretSource` (`parse_secret_source`: `credman:` or `env:`). Records `swarm.packet_built`. Not
  re-exported above this sub-package: import it from `hivemind.swarm.packet`.

## Who runs the Warden

Nothing in this package supervises, and it may not import the Queen. The Queen's swarm gate
(`hivemind.queen.swarm_gate`) subscribes to the hub, and keeps one Level 0 Warden per connected
device (the Warden runs on the Hive Stand; its Cell is the device, reached through `PollenSession`),
until the operator colonizes the device and its Warden moves onto it and dials in through the
listener's `bind_warden_handler`.
The composition root (`hivemind.cli.compose.swarm`) builds that Warden around a `SwarmSource`, and
subscribes the source to the hub for the Warden's life so a dead-man release is heard. The Warden
releases its lease whenever it is idle and leases again for the next task, so a borrowed machine
keeps no Hive scratch between tasks.

## What is never recorded

An invite token appears only in the string shown once at the Hive Stand; the registry keeps its
SHA-256. No trail event, log line or error carries a token, a key, a command's arguments, a path or
a file's content: events name ids, counts and reason codes, and a command is recorded as the
program's base name plus a count of its arguments. What a device reports (a process list, routes,
log lines) is the most sensitive label on arrival and is neither logged nor recorded here. The one
event that lists paths is `swarm.watch_changed`: the directories the operator named for a device's
Warden to read, which are the operator's own grant and what an audit of who may read what needs.
The error that refuses a directory names its position in the list, never the directory.

## How to test

```bash
uv run --frozen pytest -q -p no:cacheprovider packages/hivemind/tests/unit/swarm
uv run --frozen pytest -q -p no:cacheprovider packages/hivemind/tests/contracts   # registry + source
uv run --frozen pytest -q -p no:cacheprovider packages/hivemind/tests/unit/swarm \
    packages/hivemind/tests/contracts --cov=hivemind.swarm --cov-report=term-missing
```

The tests need no network and no device. `packages/hivemind/tests/builders/swarm/` provides the
parties with real signing keys, an in-memory Hive Stand (`make_hive_rig`), a router served over
in-memory connections (`make_router_rig`), a device client for real sockets (`DeviceClient`), and a
scripted Pollen Packet (`ScriptedDevice`) that answers like the gateway contract says; the real
Packet is `packages/pollen`. The listener tests use real loopback sockets, TLS with a throwaway
self-signed certificate and its pin. `PollenSession` is tested against the scripted device for
every clause of the `CellSession` contract, and is also registered in that suite
(`tests/contracts/test_cell_session_contract.py`) against the real Packet, enrolled on a real
listener and run in the test process (`tests/contracts/pollen_harness.py`, over the live Hive Stand
of `builders/swarm/live.py`). The same session is driven across a real boundary by
`tests/integration/test_pollen_in_container.py` (the Packet in a docker container, `tests/e2e/swarm/`),
and the gateway contract itself is checked from the outside by the conformance suite
`tests/contracts/test_gateway_contract*.py`, which runs any gateway as a subprocess against the real
listener (`docs/waggle/gateway-contract.md` section 2).
