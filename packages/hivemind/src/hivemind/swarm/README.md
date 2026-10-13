# hivemind.swarm

The swarm package turns enrolled devices into Real Cells. A device is an existing machine the Hive
borrows and leaves exactly as found; it carries a Pollen Packet (a thin gateway, no model, no
supervision) that dials out to the Hive Stand's swarm listener. This package is the Hive's side of
that conversation: the registry that remembers devices and invites, the enrolment door that admits
a device, the gateway that listens and judges whether each device is still reachable,
`SwarmSource` that leases a device as a Real Cell, `PollenSession` that is that Cell's terminal
carried over Waggle, the Propolis shield that verifies a device's VPN before work is placed on it,
and the watch probe that observes an idle device read-only. Promoting a colonized device to a Nuc
(a model server of its own) and the decision to colonize are wave 3 (`decide_colonize`, `nuc`);
this package holds the ladder's transitions only (`level`).

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

## Public API (roadmap steps 11.1, 11.2, 11.5, 11.8a)

- **Errors** (`errors`): `SwarmError` and its subclasses (`EnrolmentDeniedError`,
  `InviteFormatError`, `NodeRevokedError`, `LadderError`, `GatewayUnavailableError`,
  `DeviceRequestError`, `ShieldUnverifiedError`, ...), each with a stable `hive.swarm.*` code;
  `EnrolRefusal`, the closed set of reasons a refusal is recorded under.
- **Models and machines** (`models`, `node_state`, `level`): `SwarmNode`, `SwarmInvite`,
  `InviteState` (ISSUED -> REDEEMED | EXPIRED | REVOKED), `NodeStatus` (ONLINE <-> UNREACHABLE ->
  REVOKED), `RuntimeLevel` and the ladder GATEWAY <-> COLONIZED <-> NUC (`can_move`,
  `assert_move`, `is_promotion`). Every machine is one transition table with a comment on each
  edge; a forbidden edge raises.
- **Registry** (`registry`): the `SwarmRegistry` Protocol (invites and nodes), `SqliteSwarmRegistry`
  (migration series `swarm`), `MemorySwarmRegistry`, one contract suite
  (`tests/contracts/test_swarm_registry_contract.py`). `redeem` is atomic and single use; every
  change takes an optional trail event written in the same transaction.
  `hivemind.cli.stores.open_swarm_registry(db)` opens the SQLite one.
- **Enrolment** (`enrolment`): `EnrolmentDoor` (`mint_invite`, `enrol`), `EnrolmentRules`,
  `InviteEndpoint`, `parse_invite_string` / `build_invite_string`, `granted_capabilities` (the
  access level's capability strings, cut to the five families a packet can enforce),
  `decide_enrolment` and `node_update_for` (pure).
- **Gateway** (`gateway`): `SwarmListener(ListenerConfig, ListenerDeps)`, `GatewayHub` and the
  `HubListener` Protocol (`on_online`, `on_offline`, `on_frame`), `DeviceLink` (`request`,
  `stream`, `send`), `HeartbeatMonitor`, `LivenessLoop`, `ConnectionRouter`, and the
  `WardenConnectionHandler` Protocol a device Warden's connection is handed to.
- **Source and session** (`source`, `session`): `SwarmSource(node, hub, recorder, guard)` is a
  `RealCellSource` for one device and a `HubListener`; `PollenSession` is a full `CellSession`
  (`exec`, files, background processes); `CommandGuard` / `EnforcedCommandGuard` check every
  device command at the `DEVICE_COMMAND` point and record `swarm.command_sent`.
- **Shield and watch** (`shield`, `watch`): `ShieldedSource(inner, PropolisChecks)` holds a
  Propolis device's lease back until fixed read-only commands show its tunnel carries the default
  route and DNS; `PollenWatchProbe.observe(kinds, roots, since_at)` asks an idle device what it
  looks like without a lease.

## What is never recorded

An invite token appears only in the string shown once at the Hive Stand; the registry keeps its
SHA-256. No trail event, log line or error carries a token, a key, a command's arguments, a path or
a file's content: events name ids, counts and reason codes, and a command is recorded as the
program's base name plus a count of its arguments. What a device reports (a process list, routes,
log lines) is the most sensitive label on arrival and is neither logged nor recorded here.

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
every clause of the `CellSession` contract rather than registered in that suite, which runs the
local session only.
