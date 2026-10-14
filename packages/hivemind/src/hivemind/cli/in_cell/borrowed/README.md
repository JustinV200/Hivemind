# hivemind.cli.in_cell.borrowed

Borrowed mode: the in-Cell Warden entry point (`hivemind-in-cell`) running on a Swarm device the
Hive borrows, instead of in a Virtual Cell the Hive owns (roadmap step 11.6a, Level 1 of the runtime
ladder, ADR-0052). The Pollen Packet starts it with `HIVEMIND_CELL_LEASE=borrowed`; without that
variable, or with `owned`, nothing in this package runs and a Virtual Cell's Warden is exactly what
it was.

The Warden is the one the Hive Stand ran for the device at Level 0, moved onto the device: the same
Warden id (the Queen's grants, ceilings and trail stay attached to one bee), signing as the
device's node with the key the packet holds. It leases the machine through `hivemind.cell.local`'s
machinery (`HiveStandSource`, named for a device: Cell id the Queen minted, source `swarm`), so a
sub-bee's commands run on the device with nothing between, and it gives the lease back whenever it
settles with nothing to supervise. Its memory store, Pheromone Trail segment and Leavings are SQLite
files under the state directory, so a Handoff written before a restart is there after it. Its link
to the Queen is a `ReconnectingQueenLink` (`hivemind.wardens.offline`): a lost link makes it OFFLINE,
and a reconnection announces the Cell again.

## Public API

- **Config** (`config`): `build_borrowed_config(env, clock) -> BorrowedConfig`, `is_borrowed(env)`,
  `BORROWED`, `OWNED`. Validates the packet's environment (below) and the device manifest; refuses a
  tier other than MEADOW, a `wss://` listener without a pin, and a `ws://` one with.
- **Models** (`llm`): `device_llm(section, forage_map) -> DeviceLlm`, the device manifest's
  `[llm]` (and the `[forage.map]` rows its servers load) as the provider, seat, rate-limit, slot
  and server tables, the same answer `hivemind.cli.stores` gives for a Hive Manifest's. A server
  is both reported (id only, nothing loaded) and kept as a `ServerConfig` the Warden can start
  when the Queen promotes the device; `load_on_start` is always false here.
- **Stores** (`stores`): `open_device_stores(state_dir, clock) -> DeviceStores`, three SQLite stores
  in `warden.sqlite`.
- **Link** (`dial`): `DeviceDialer`, `DeviceAnnouncer`, `DeviceIdentity`, `LateTrailSync`.
- **Composition** (`deps`): `build_device_link`, `build_borrowed_warden_deps`, `BorrowedParts`. The
  deps are `hivemind.cli.in_cell.deps.build_core_deps` plus: the SQLite memory store, a lease
  capability `cell:real:<cell id>`, and the `DeviceDeps` group (idle release, an `Outage`, a
  `RemoteDeps` with the offline limit, the budget for a diagnostic Drone when the link goes (both
  from the device manifest's `[supervision]`, the Hive's shipped defaults when it has none) and the
  Queen's host the Drone is told to reach). Of a Virtual Cell's extras, not the snapshotter or the
  resident Basket, which need a Cell the Hive owns; the Exoskeleton wiring
  (`hivemind.cli.compose.exoskeleton.device_exoskeleton`, roadmap step 11.11) is applied, so the
  Warden attaches a display, input, audio or a browser to its own lease through its own session
  (see "The Exoskeleton on a device" below); the declared model servers are known to the Warden but
  never started until a promotion.
- **Run** (`run`): `run_borrowed_warden(env, clock, *, on_deps_built, stop)` and `stop_on_signals`.

## The environment the packet gives the Warden

| Variable | Meaning |
|---|---|
| `HIVEMIND_CELL_LEASE` | `borrowed` |
| `HIVEMIND_QUEEN_WAGGLE_URL` | The swarm listener (`wss://` anywhere, `ws://` on loopback) |
| `HIVEMIND_TLS_PIN` | SHA-256 of the listener's certificate, for `wss://` only |
| `HIVEMIND_CELL_ID` | The Cell the Queen minted for the device |
| `HIVEMIND_WARDEN_ID` | The Warden's id, kept across the move from the Hive Stand |
| `HIVEMIND_NODE_ID` | The device's node id; its key signs |
| `HIVEMIND_CELL_SIGNING_KEY_FILE` | The device's node key: the packet's key file, a raw 32-byte seed |
| `HIVEMIND_HIVE_ID`, `HIVEMIND_QUEEN_NODE_ID`, `HIVEMIND_QUEEN_VERIFY_KEY` | The Queen's address, node and public key |
| `HIVEMIND_ACCESS_LEVEL`, `HIVEMIND_COMB_SHIELD` | What the Queen granted the device |
| `HIVEMIND_STATE_DIR` | Where the Warden's SQLite files, outbox and scratch live |
| `HIVEMIND_DEVICE_MANIFEST` | The device manifest (`[llm]`, `[forage.map]`, `[supervision]`; `docs/manifests/device.toml`) |

Provider API keys are read from the device's own environment, in the variable each provider's
`api_key_env` names, and never from the wire. `tests/contracts/test_pollen_warden_env.py` checks
that the environment `pollen.bootstrap` builds is the one this package reads.

## The Exoskeleton on a device

A task that needs a display, input, audio or a browser (`TaskAssign.exoskeleton`) is equipped by the
Warden that supervises it, and on a colonized device or a Nuc that is the device's own Warden, so
Xvfb, the window manager, `xdotool` and `import` start through its local session, in the lease's
scratch, and are stopped again when the sub-bee ends. Which of them it may start is decided where
it is for every Cell: by the device's probed capabilities (`can_start_display`, `has_browser`, from
the programs on its `PATH`) and by the sub-bee's own capability set, which the device's access
level has already narrowed, so `SCRATCH` grants the browser fast path and only `FULL` a display and
sound (ADR-0054). The wiring is a Virtual Cell's with two differences, both because of what the
Warden runs on. Chromium keeps its own sandbox unless the Warden runs as root (a borrowed machine is
no sandbox); and the flight recorder's store forgets a recording a day after its last action, when
the next one opens (`DEVICE_RECORDING_KEEP`), because this Warden runs for months. As a Virtual
Cell's does, the store lives in the Warden's memory, so no frame of its display is written to the
device's disk and the recordings are gone when the Warden stops; nothing on the device can play one
back, and shipping them to the Queen is a later step. The browser launcher is there only where the
runtime was installed with the `browser` extra, and the Warden never borrows the operator's own
display: `real_display_allowed` is not something the packet tells it.

## What the Queen sees

On every connection, a fixed order, every frame sent by the Warden and signed as the device's node:

1. `cell.ready` (with the level and tier the Queen granted), `forage.capacity_report` (trigger
   `RECONNECTED`: the Cell's live figures and the model servers the manifest declares, none
   started), `cell.heartbeat`;
2. after a link that was lost, the frames of its last stretch that must arrive (a result, a
   checkpoint, an Alarm, a forage request, a release, a chunk of Nectar, a question), again, oldest
   first and under the ids they had: the link was dead before the Warden knew it, and the Queen's
   replay window drops whatever she already held;
3. whatever the durable outbox holds from before, oldest first: a result, a checkpoint, an Alarm
   or a forage request queued while the link was down, or by a run that died (a request is
   replayed in a fresh envelope, since the Queen's replay window would drop the old id);
4. `swarm.trail_segment_sync`, the Warden's trail, whose final chunk carries the node's signature;
5. the questions held back while offline;

and from then on the Warden's own traffic (`task.progress`, `task.result`, `supervision.heartbeat`,
...). The first connection of a process is the same sequence: its outbox may hold what an earlier
run left, and the Warden records `warden.reconnected` on its trail either way.

## How it stops

`SIGTERM` (the packet's WITHDRAW), `SIGINT` or Windows' `SIGBREAK` set the stop event; a Queen-sent
`Shutdown` stops the Warden from inside its tick. Either way the Warden stops its sub-bees, releases
its lease (killing what it started, putting back what it changed, removing its scratch), ships its
last trail segment and closes the link, within `BORROWED_SHUTDOWN_GRACE_S`.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/cli/in_cell packages/hivemind/tests/unit/manifest packages/hivemind/tests/contracts/test_pollen_warden_env.py
```

`builders/borrowed.py` writes a device's key, manifest and environment under a temporary directory;
`tests/unit/cli/in_cell/borrowed/test_run.py` runs the real Warden against a real `WebSocketServer`
that signs as the Queen and verifies as the Queen's listener would. The Exoskeleton is proved on a
container whose image carries an X server by `tests/e2e/test_phase11_exit_exoskeleton.py`.
