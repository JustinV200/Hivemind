# Phase 11 (the Swarm: Pollen Packet gateways, Wardens for devices, and Nucs): design, map, handoff

## Start here (handover, 2026-10-15)

- **Phase 11 is finished and nothing is in flight.** Every phase 11 box in the roadmap is ticked
  with its as-built note, each exit criterion is proven as section 6's table says, ADR-0051 to
  ADR-0057 are accepted, and Waggle is 1.13. Everything is pushed to `ccr-75a88a63-nwtvjo`;
  `origin/main` (`5d02b91`) is an ancestor, so merging the branch into `main` is a fast-forward.
  No pull request is open, and neither leftover worktree holds a commit the branch lacks.
  Sections 1 to 5 are the design as planned; where they differ from section 6 or the ADRs, those
  win.
- **The branch has never been through GitHub CI.** `ci.yml` runs on pushes to `main` and on pull
  requests, `pollen.yml` on tags and on pull requests that touch the packet (a manual dispatch
  needs the file on `main`), `integration.yml` nightly on `main`; this branch has no workflow run
  at all. Phases 8, 9 and 12 reached `main` the same way and took eight pushes (CI runs 26 to 33)
  to go green, mostly for Windows. What the first run will meet:
  - `e2e (ubuntu-latest)` has a 10-minute timeout, and phase 11's container scenarios run in it:
    they skip only when no Docker daemon answers (`tests/e2e/swarm/container.py`,
    `daemon_answers`), and the Ubuntu runners have one. Here a 22-test subset of them took 8 m 16 s
    with warm caches, the diagnosis scenarios take about nine minutes more, and the whole e2e suite
    took 25 minutes. Either give them a marker that `ci.yml` leaves out and `integration.yml`
    (Docker, no 10-minute cap) runs, or give the job the time; the choice is the operator's.
  - `e2e (windows-latest)`: none of them skips on Windows. If the runner's Docker engine, which
    runs Windows containers, answers `docker version`, the fixtures try to build the Linux device
    image there and error instead of skipping.
  - `tests (windows-latest)`, the `arch` container job and `pollen.yml`'s Windows and macOS legs
    run pollen's suite and phase 11's own tests on those hosts for the first time. The one Windows
    failure visible from Linux is fixed (next bullet); anything else needs a Windows run. Section
    6's "ARM64 only in CI" means the matrix is written, not that it has run.
- **Checked at handover, and what it fixed.** In this container:
  - `mypy --platform win32`, what CI's Windows `types` job amounts to, failed with 22 errors in 8
    phase 11 files, and behind them was a real defect: `pollen.platform.posix` read
    `signal.SIGKILL` at import, so on Windows `import pollen.cli` died before the Windows shim
    could be chosen and the packet could not start at all. 8937b14 fixes both; mypy is clean for
    Linux and win32.
  - The `web` job's `pnpm generate:check` failed: be271e3 rewrote `last_patrol_at`'s description
    in `docs/entrance/openapi.json` without regenerating `src/landing_board/types.ts`. f1388f2
    regenerates it; then generate:check, format, lint, typecheck and test pass, and
    `pnpm audit --audit-level=high` finds nothing at that level.
  - CI's `tests` job as written (the unit suite with coverage, then
    `scripts/check_coverage_floors.py`): still running when this was first written. `pip-audit`
    over the exported lock: no known vulnerabilities.
- **`main`'s nightly is red on its own.** `integration.yml` failed its last three runs on `main`
  (2026-10-13 to 10-15). The latest failed the browser contract's
  `test_a_launch_that_never_gets_ready_stops_its_browser_and_raises[real]` (it did not raise) and
  `test_docker_backend.py`'s `test_provision_real_container_reaches_ready_then_destroy_removes_it`
  (a subprocess left running, its transports unclosed), and its `local_llm` job failed too. None
  of that is phase 11's code and none of it was investigated here, but a merge lands on it.
- **Leftovers in this container only**, gone with it:
  `refs/original/refs/heads/ccr-75a88a63-nwtvjo` from an old history rewrite (`git update-ref -d`
  it); the worktrees under `.claude/worktrees/` (`agent-a2f93f65d043b31f2` is locked) with their
  `worktree-agent-*` branches, and `wave-5f-reap`; a local `main` still at `4b66406`; cached
  device images and wheels; `packages/observation-web/node_modules`. `0c48039` does not import on
  its own (`b337d31` completes it), so skip it when bisecting.
- **Next.** The known gaps are section 6's last bullet ("Open, for later phases"). On the roadmap,
  phase 13 (Resilience) needed phases 5, 10 and 11, so it is unblocked; its step 13.2a sends the
  `control.queen_moved` this phase only honours, and must add the TLS pin that message lacks.

> Written 2026-10-13 on branch `ccr-75a88a63-nwtvjo` (the phase 8 + 9 + 12 tree at `a11deb3`,
> ahead of `main` at `4b66406`) before any phase 11 code, as the design every implementer works
> from. Section 1 holds the decisions (the six ADRs in brief), section 2 the module map and the
> interfaces between steps, section 3 the wire and trail additions, section 4 the build order and
> section 5 the exit-criteria plan. Sections 6 onward are filled in as the work lands and become
> the handoff.
>
> **Numbering.** Phase 11 takes ADR-0051 to ADR-0056 and Waggle minor **1.13**.

Read first: `CLAUDE.md`, `.claude/codingrules.md` (sections 3, 4, 5, 6, 7, 8.1-8.3, 8.5, 8.7, 8.8,
8.10, 8.15, 8.16, 11, 12, 14, 15, Appendix C), `.claude/roadmap.md` phase 11 (lines 2016-2433 at
handover), `docs/waggle/spec.md` sections 2-6, 8.5, 8.6, 8.10, 8.11 (QueenMoved), 9, 10.

Baseline at `a11deb3` (2026-10-13, this container): ruff format/check, mypy (2796 files),
lint-imports (12 contracts), the five hygiene scripts all clean; unit and contract suite
14,844 passed, 27 skipped in 13 m 49 s.

## 1. Decisions

### 1.1 ADR-0051 `pollen-packet-is-a-gateway-and-the-trust-model.md`

- **The packet is a gateway.** `pollen` holds no supervision logic, no model client and no trail.
  It executes only what a signed request from a trusted node asks, inside a lease, after a local
  capability check, and reports. It depends on `waggle` (and therefore pydantic, websockets,
  cryptography) and the standard library only.
- **One node per device.** At first run (`pollen enrol`) the packet mints its `device_id`
  (`device_<ULID>`), its `node_id` (`node_<ULID>`) and an Ed25519 key, kept in its state
  directory (`<data_dir>/node.ed25519`, mode 0600, never sent anywhere). A colonized device's
  Warden (Level 1+) signs as the **same node**, reading the key file locally: the spec's address
  binding already lets a node's key speak for the device's `device_id`, its Cell's `warden_id`
  and that Warden's Workers, and nothing else. The Queen therefore knows exactly one key per
  device, pinned at enrolment in the swarm registry.
- **Invites bind the first key.** `hive swarm invite --access LEVEL --shield meadow|propolis
  [--label NAME]` prints one invite string, `pollen1.<base64url(JSON)>`, with `token` (16 random
  bytes as grouped base32, 32 characters, the same shape as the Entrance's invite code; only
  its sha256 is stored), `url` (the swarm listener's advertised URL), `hive_id` (the recipient of the first
  frame), `hive_node_id`,
  `hive_key` (64 hex), `tls_sha256` (the listener certificate's fingerprint, or null on
  loopback), `expires_at` and `label`. The string is a secret: printed once, never logged.
  Invites expire (`[swarm] invite_ttl_minutes`, default 15) and are single use; the operator's
  access cap and tier ride on the invite row, never on the wire.
- **Enrolment is one signed frame.** The first frame on a fresh connection may be only
  `swarm.enrol_request`, signed with the packet's new key; the listener's verifier checks that
  frame against the `public_key_hex` and `node_id` inside its own payload (proof of possession)
  and then the door validates the token. Every later frame is verified against the pinned key.
  The packet pinned the Hive key from the invite: it verifies the accept and every later frame
  against it and refuses an accept whose `hive_key_hex` differs. A denial is `control.error`
  `hive.swarm.enrol_denied` plus `swarm.enrol_refused` on the trail (ids and a reason code only,
  never the token).
- **Sender rules on the packet.** Every frame must be signed by a trusted node (`EnrolAccept.
  trusted_nodes`, later a `QueenMoved`), addressed to the packet's own `device_id`, and sent by
  the Queen (`hive_id`) or by the Cell's current Warden (`warden_id` from the accept or the last
  `swarm.node_update`). Kinds only the Queen sends are accepted only from `hive_id`.
- **Local capability check.** `EnrolAccept.granted_capabilities` is the access level's ceiling in
  the guard grammar, restricted to the families the packet understands: `exec:*`, `fs:read:...`,
  `fs:write:...`, `cell:outside_scratch:...`, `watch:<cell_id>`; the literal `{scratch}` in a scope
  stands for each lease's scratch root. `READ_ONLY` grants `fs:read:**` and `watch:<cell>` only,
  so a read-only device runs no command at all. The packet checks every request before any
  executor runs and refuses with `control.error pollen.capability_denied`.
- **Confidentiality is the link's.** The swarm listener serves `wss://` with a certificate from
  `[swarm] tls_cert`/`tls_key` whenever it binds a non-loopback address; the packet pins the
  certificate's sha256 from the invite instead of trusting a CA (a self-signed certificate is
  fine). Plain `ws://` stays loopback-only, as `waggle.uris` already rules.
- **Replay and freshness.** Both ends drop a duplicate envelope id inside the replay window and a
  frame older than `MAX_MESSAGE_AGE_S` or more than `MAX_CLOCK_SKEW_S` ahead (spec section 3).
- **QueenMoved.** The packet persists the Queen's address and replaces it only on a
  `control.queen_moved` that the current Hive key verifies, with a `sequence` above the highest
  it persisted; it pins `new_node_public_key_hex` for `new_node_id`, honours both addresses until
  `grace_until`, then drops the old key. Sending one is phase 13 (13.2a); honouring it is here.
- **Dead-man switch.** With no Warden on the device (Level 0) and the link lost longer than
  `offline_limit_s`, the packet kills every process its leases started, removes their scratch,
  restores what it recorded, and queues `cell.lease_released` (cause `DEAD_MAN`) in its outbox.
- **Outbox.** `waggle.outbox` under the state directory holds what must reach the Queen after a
  reconnect: `cell.lease_released` and the terminal `session.exit` of an exec that outlived the
  link. `session.output` chunks are never queued (an exec's Warden has already given up on them).

### 1.2 ADR-0052 `runtime-ladder-warden-moves-first.md`

- **Three rungs.** `GATEWAY` (Level 0): only the packet; the device's Warden runs on the Hive
  Stand, in the Queen's process over the memory transport, its sub-bees' brains beside it and
  their hands on the device through `PollenSession`. `COLONIZED` (Level 1): the Warden and its
  sub-bees run on the device (`hivemind-in-cell` in borrowed mode, section 2.9), models from the
  hosting plan's shared sources. `NUC` (Level 2): a model server on the device as well.
- **One rung at a time; the Warden moves first.** `hive swarm promote` climbs one rung, `demote`
  descends one; `GATEWAY -> NUC` is refused. A Warden and its sub-bees are always co-located.
- **The Warden keeps its id across the move.** Migration is checkpoint, Handoff, resume (codingrules
  8.9 "one mechanism, many names"): the Queen pauses the Hive Stand-side Warden's tasks (each
  sub-bee checkpoints and writes a Handoff), shuts that Warden down (its lease is released, the
  device left as found), the packet starts the on-device Warden under the **same `warden_id`**,
  it dials the swarm listener and is attached, and the Queen resumes each paused task on it with
  the Handoff carried inline (`TaskAssign.handoff`, Waggle 1.13), because the device's Warden
  cannot read the Hive Stand's memory store. `warden.migrated` records `from_node`/`to_node`.
- **Colonization (`swarm.colonize`).** `PREPARE`: the packet's `bootstrap` installs the pinned
  `hivemind` runtime version into a venv under its data directory, from the wheelhouse or index
  the operator configured at `pollen install` (never from the wire). `START`: the packet starts
  the on-device Warden as a background process with the device's node key, the swarm listener's
  URL and pin, the Warden id, the Cell id, the access level, the tier and its local manifest.
  `WITHDRAW`: the packet stops it. Each answers `swarm.colonized`.
- **Withdrawal (Level 1 -> 0)** happens only when the on-device Warden is idle (`WATCH`); a busy
  Warden refuses with a reason, since its Handoffs live on the device. Nuc demotion (2 -> 1) is
  local to the device's Warden and needs no migration.
- **The device's own configuration.** An on-device Warden reads a small device manifest
  (`<data_dir>/warden.toml`, written by the operator at `pollen install --warden-manifest`): its
  `[llm.providers]`/`[llm.slots]` for shared sources reachable from the device, and its
  `[llm.servers]` for a Nuc (phase 8 left "a Nuc is where a device-side server is declared").
  Keys come from the device's own environment. The Queen's `NucPromote.server_id` must name one
  of the servers the device's `CapacityReport.model_servers` declared.
- **Decisions are pure.** `swarm/level.py` (`decide_colonize`: device Forage covers a Warden
  plus one sub-bee; and the Hive Stand needs relief (seat or memory pressure past
  `[swarm.ladder] relief_pressure`), a task needs to survive short outages, or the operator asked
  through `desired_level`) and `swarm/nuc.py` (`decide_nuc`: Level 1; a declared server whose
  allowlisted models fit the free VRAM; and Hive Stand seat pressure, autonomy need or the
  operator's `desired_level`). Both are Guard-gated: `NUC_PROMOTION` for every rung change.

### 1.3 ADR-0053 `nucs-warden-migration-and-offline-operation.md`

- **Every remote Warden reconnects.** A Warden that dials the Queen (a Virtual Cell's in-Cell
  Warden, a colonized device's Warden) holds its Queen link through `wardens/offline/link.py`
  (`ReconnectingQueenLink`): on a lost link it enters `OFFLINE`, re-dials with capped backoff
  (0.5 s doubling to 30 s), and on success re-announces (`cell.ready`, `forage.capacity_report`,
  `cell.heartbeat`), replays its outbox, ships its trail segment and records `warden.reconnected`.
- **Offline rules (pure, `wardens/offline/policy.py`).** Queued in the outbox: `task.result`,
  `task.progress` checkpoints, `supervision.alarm_raised`, `forage.request`. Held, not sent:
  human-bound `supervision.question` (re-sent after reconnection; the asking sub-bee stays
  blocked). Refused locally: `cell.request`, a spawn needing shared Forage beyond the frozen grant.
  Shared grants are frozen (not renewed, not revoked locally); the local pool stays usable.
- **What keeps running.** A Nuc keeps its sub-bees running on local bindings. A Level 1 Warden
  with no local model clusters its sub-bees at once (checkpoint, pause, keep warm). Both run full
  Clustering past `[supervision] max_offline_s`. A subtask whose tempo needs a shared source files
  a `ForageRequest` that waits in the outbox; it does not fail.
- **Reconnection is reconciled.** The Queen treats a known `warden_id` re-attaching as a
  reconnection, not a new Warden: she swaps the link's transport, re-sends the current grants,
  ceilings and hosting plan (the known gap in `queen/forage/ceilings.py`), and merges the trail
  segment. A merged segment must carry `TrailSegmentSync.segment_signature` (Waggle 1.13), an
  Ed25519 signature by the node's registered key over the segment's identity and digest; a Swarm
  node's segment without a verifying signature is refused and recorded as `guard.segment_refused`
  (ADR-0007 covers order and dedupe on merge, not authenticity).
- **The Queen's side of a lost device.** On a lost link the Queen marks the node `UNREACHABLE`,
  holds its tasks for `[swarm] unreachable_grace_s` (no fail, no re-placement), asks for the
  Hive Stand-side diagnosis (1.6), then decides: a Nuc's tasks keep waiting up to
  `max_offline_s` (it is still working); a Level 0 or Level 1 device's tasks are re-placed.

### 1.4 ADR-0054 `access-levels-watch-mode-and-the-patrol.md`

- **Watching is read-only by construction.** A Real Cell's Warden with no active bees enters
  `WATCH` and releases its lease (a device keeps no Hive scratch between tasks). Observations come
  from a `WatchProbe` (`wardens/watch/probe.py`) that returns `waggle.messages.swarm.Observation`
  values: `SessionWatchProbe` runs fixed read-only commands through the Warden's own session where
  the Warden is local to its Cell (the Hive Stand, an on-device Warden); `swarm.watch.
  PollenWatchProbe` asks a Level 0 device's packet with `swarm.observe`, which the packet answers
  from fixed collectors without a lease and without running any command a request could shape.
- **What may be observed.** Exactly `guard.watch.WATCH_OBSERVATIONS`: process list, resource use,
  logs in allowed roots, file-change events in allowed roots; plus network counters (resource
  use). Never screen or input. Bounded by `watch:<cell_id>`, which the packet also checks.
- **Observations are Bee Bread, `C2`.** Stored with `[supervision.watch] retention_s` (default 24
  h), sampled every `interval_s` (default 300 s).
- **The Patrol.** Every `[supervision.watch] patrol_interval_s` (default 3600 s) the Warden runs
  one awake episode on `ModelSlot.WARDEN` over the observations since the last Patrol, with the
  pure pre-filter's signals (`wardens/watch/signals.py`: new processes over a CPU share, disk past
  a fill ratio, a restart loop) labelled as facts. It decides one of `ALARM` (`AlarmKind.
  PATROL_ANOMALY`, Waggle 1.13), `NECTAR` (a summary, `C2`), `WAX` (a `cell.wax_proposed` with
  origin `PATROL`) or `NOTHING`; every Patrol records `warden.patrol` with its outcome and counts,
  and "nothing notable" discards the observations it covered. A bee assignment moves the Warden
  back to `ACTIVE`. Applies to the Hive Stand too.

### 1.5 ADR-0055 `waggle-replay-window-and-frame-ceiling.md` (amends ADR-0005)

- **Receivers must deduplicate.** `waggle/transport/replay.py` keeps the envelope ids seen per
  node over `replay_window_s` (default 600) with a cap on ids kept (default 50,000 per
  connection); the server half drops a duplicate without handing it to the receiver and reports
  it through a callback. A duplicate inside the reconnection grace after `warden.reconnected` is
  expected (outbox replay after a lost ack) and silent; any other is `guard.replay_refused` with
  the node id, recorded by the listener wrapper (waggle writes no trail). The packet keeps the
  same window on its side.
- **A frame ceiling per connection.** `waggle/transport/ceiling.py`: a token bucket of
  `max_frames_per_minute` (default 6000) with a burst allowance (default 600). Over it the server
  closes the connection with code `4029` and reason `waggle.transport.frame_ceiling`; the peer
  reconnects with its usual backoff. The listener wrapper records `guard.frame_ceiling` with the
  node id. Below the ceiling nothing changes.
- The gateway contract (11.7a) carries both obligations, so a compiled gateway is held to them.

### 1.6 ADR-0056 `active-reconnection-diagnostic-drones.md`

- **A Drone, not a new role.** Where a model is reachable, reconnection is investigated: a
  short-lived Drone with the objective "diagnose and, if possible, restore the link to <peer>",
  running ordinary Capping-gated scratch work through its `CellSession` (`run_command`, one-off
  scripts in scratch). It never touches the Royal Jelly Lab or the Quarantine Comb.
- **Who spawns it.** A Nuc's Warden spawns it from its own local pool with no request (the pool
  is owned outright, 8.10). On the Hive Stand side the Queen hands W0 (the Hive Stand's own
  Warden) a diagnosis task pinned to the Hive Stand (`RequestOrigin.QUEEN`, Drone role, low
  tempo) for a lost device, whatever its level: a Level 0 device's Warden lives on the Hive Stand
  but its hands are on the lost device, so W0's own Cell is where reachability is checked from.
- **Budget.** `[supervision] reconnect_budget` (`attempts`, default 2; `wall_clock_s`, default
  300) is carved out of `max_offline_s`, never added to it (the manifest validator refuses a
  budget not smaller than `max_offline_s`). Clustering keeps the same deadline as a backstop.
- **Never more access.** The Drone is capped by the Cell's access level; being cut off never
  grants more than being connected would. It reports `FIXED`, `DIAGNOSED` (cannot fix) or
  `NO_CAUSE` to its Warden, deposits its findings as Nectar either way, and `warden.diagnosed`
  records the outcome; 11.9's timeline then resumes untouched.
- A Level 1 Warden with no local model skips the Drone and clusters at once.

## 2. Module map and interfaces between steps

Paths are relative to `packages/hivemind/src/hivemind/` unless they say otherwise. Every module
starts with the codingrules 7.2 header; files under 300 LOC, functions under 50, classes under 200,
at most five parameters, at most ten modules per directory (`scripts/check_fanout.py`). Use the
bee terms literally. Never branch on `cell.kind` or `match <x>.kind:` (`check_no_kind_branches`);
dispatch wire frames by payload type through a dict. No module- or class-level `messages` or
`history` attribute (`check_no_transcripts`). No model id or provider URL in code
(`check_no_model_ids`).

### 2.1 Layering

- `swarm` is Layer 3: it may import `guard`, `cell` (and `cell.local` through its face), `memory`,
  `supervision`, `brood_chamber`, `basket`, `pheromone`, `forage`, `llm`, `manifest`, `common` and
  waggle; never `hive` (a Layer 3 sibling), `workers`, `wardens`, `queen`, `entrance`, `cli`.
- Queen-side orchestration of devices is `queen/swarm_gate/` (Layer 6, beside `queen/cell_gate/`).
- The composition of a device Warden is `cli/compose/swarm/` (Layer 7).
- `pollen` imports waggle and the standard library only (`lint-imports` contract "pollen imports
  nothing from hivemind"). `subprocess` is allowed anywhere in `pollen`; every call carries a
  `# SAFETY:` comment and an argument list.

### 2.2 waggle (wave 1A wire, wave 1B transport)

- Wire, see section 3.1: `messages/session/process.py` (new), `messages/session/files.py`
  (`SessionDeleteFile`), `messages/session/commands.py` (`SessionRequestKind.DELETE_FILE`),
  `messages/session/output.py` (validators), `messages/swarm/ladder.py` (new: `Colonize`,
  `Colonized`, `ColonizeAction`, `ColonizeOutcome`, `NodeUpdate`), `messages/swarm/watch.py`
  (new: `Observe`, `Observation` and its value models), `messages/swarm/colonized.py`
  (`TrailSegmentSync.segment_signature`, `segment_signing_bytes`), `messages/task/handoff.py`
  (new: `HandoffDocument`, `HandoffDecision`), `messages/task/assignment.py` (`TaskAssign.
  handoff`), `messages/cell/leases.py` (`CellRequest.scratch_quota_bytes`),
  `messages/supervision/alarms.py` (`AlarmKind.PATROL_ANOMALY`), `catalogue.py`, `messages/
  __init__.py`, `envelope.py` (`PROTOCOL_MINOR = 13`, `PROTOCOL_VERSION = "1.13"`).
- Transport, section 1.5 and TLS: `transport/replay.py` (`ReplayWindow`, pure),
  `transport/ceiling.py` (`FrameCeiling`, pure token bucket over an injected clock),
  `transport/tls.py` (`server_ssl_context(cert, key)`, `pinned_client_ssl_context()`,
  `certificate_sha256(der) -> str`, `check_pin(connection, pin)`), `transport/websocket_server.py`
  (`WebSocketServer(codec, *, host, port, open_timeout_s, limits: ServerLimits | None = None,
  ssl_context=None, on_refusal: Callable[[ConnectionRefusal], Awaitable[None]] | None = None)`,
  `uri` is `wss://` with TLS), `transport/websocket.py` (receive-side hooks the server uses),
  `transport/websocket_client.py` (`DialOptions.tls_pin_sha256: str | None`; a wss URI with a pin
  verifies the peer certificate's sha256 instead of a CA, closing and raising
  `TlsPinMismatchError` (a `ConnectFailedError`) on mismatch). `ConnectionRefusal` is a frozen
  value: `node_id`, `kind` (`REPLAY` | `FRAME_CEILING`), `envelope_id | None`, `at`.
- Errors in `waggle/errors.py`: `TlsPinMismatchError`, `FrameCeilingError`
  (`waggle.transport.frame_ceiling`), table in spec section 7.

### 2.3 swarm (Layer 3)

```
swarm/
  __init__.py, README.md
  errors.py        SwarmError(HiveMindError) tree: EnrolmentDeniedError (code hive.swarm.enrol_denied),
                   NodeNotFoundError, NodeRevokedError, GatewayUnavailableError,
                   DeviceRequestError (carries the control.error code), ShieldUnverifiedError
                   (a cell.LeaseRefusedError), LadderError
  models.py        SwarmNode, SwarmInvite, InviteState, NodeFacts helpers (as_cell())
  node_state.py    NodeStatus (ONLINE, UNREACHABLE, REVOKED) + TRANSITIONS
  level.py         RuntimeLevel ladder TRANSITIONS (mirror of the wire enum), decide_colonize (pure)
  nuc.py           decide_nuc (pure), NucPromotion
  source.py        SwarmSource (RealCellSource for one device), SwarmLeaseReleaser
  watch.py         PollenWatchProbe (swarm.observe over the device's link)
  registry/        protocol.py (SwarmRegistry), sqlite.py, memory.py (fake), rows.py,
                   migrations/0001_create_swarm.sql, __init__.py
  enrolment/       invite.py (mint/parse invite strings, token hash), accept.py (pure decision),
                   capabilities.py (granted capability strings), door.py (redeem atomically,
                   pin, trail), __init__.py
  gateway/         listener.py (SwarmListener), verifier.py (SwarmVerifier), link.py (DeviceLink),
                   hub.py (GatewayHub), heartbeat.py (apply DeviceHeartbeat, liveness), __init__.py
  session/         session.py (PollenSession), exec.py, files.py, process.py,
                   codes.py (control.error code -> cell error), __init__.py
  shield/          propolis.py (fixed commands + pure judges), shielded.py (ShieldedSource
                   decorator over any RealCellSource), __init__.py
```

Interfaces fixed up front:

- `SwarmNode` (pydantic, frozen): `device_id`, `node_id`, `cell_id` (minted by the Queen at
  enrolment), `warden_id` (minted at enrolment, kept for life, the device's Warden at every rung),
  `public_key_hex`, `hostname`, `label`, `runtime_version`, `platform: PlatformReport`,
  `capabilities: CellCapabilities`, `capacity: ForageCapacity`, `requested_access`,
  `access_level`, `comb_shield` (MEADOW | PROPOLIS; validator refuses NIGHT_VEIL), `status:
  NodeStatus`, `level: RuntimeLevel`, `desired_level: RuntimeLevel | None` (an operator's pending
  `promote`/`demote`), `granted_capabilities: tuple[str, ...]`, `offline_limit_s`,
  `heartbeat_interval_s`, `max_sub_bees: int | None`, `enrolled_at`, `last_seen_at | None`,
  `revoked_at | None`, `reason`. `as_cell() -> Cell` (kind REAL, source `"swarm"`).
- `SwarmInvite`: `code_hash`, `label`, `access_level` (the cap), `comb_shield`, `created_at`,
  `expires_at`, `state` (`ISSUED`, `REDEEMED`, `EXPIRED`, `REVOKED`), `device_id | None`.
- `SwarmRegistry` (Protocol): `put_invite`, `get_invite(code_hash)`, `redeem(code_hash, node,
  at) -> SwarmNode` (atomic: refuses a used, expired or revoked invite; raises
  `EnrolmentDeniedError`), `revoke_invite`, `node(device_id)`, `node_by_node_id(node_id)`,
  `node_by_cell(cell_id)`, `nodes(status=None)`, `set_status(device_id, expected, new)`,
  `set_level(device_id, level)`, `set_desired_level`, `set_access(device_id, level,
  granted)`, `set_shield`, `touch(device_id, at, runtime_version)`, `update_facts(device_id,
  capabilities, capacity)`. Contract suite over SQLite and the fake.
- `GatewayHub`: `link(device_id) -> DeviceLink | None`, `link_for_cell(cell_id)`, `async
  wait_for(device_id, timeout_s)`, `subscribe(listener: HubListener)` where `HubListener` has
  `async on_online(node, link)`, `async on_offline(node, reason)`, `async on_frame(node,
  envelope)` for unsolicited frames (capacity reports, a dead-man `cell.lease_released`).
- `DeviceLink`: one connected packet. `async request(payload, *, timeout_s) -> Envelope` (the
  correlated reply, terminal event or `control.error`, which raises `DeviceRequestError`),
  `stream(payload, *, timeout_s) -> AsyncIterator[Envelope]` (every correlated event up to and
  including the terminal one), `async send(payload, *, correlation_id=None)`, `is_open`,
  `async close()`. One reader task per link; frames route by `correlation_id`.
- `SwarmSource(node, hub, identity, trail, clock)`: `name == "swarm"`, `cells()`, `lease()`
  (`cell.request` with `scratch_quota_bytes` from the Warden's ceilings, awaits
  `cell.lease_opened`, builds a `RealCellLease` whose releaser sends `cell.teardown_request` and
  waits for `cell.lease_released`, the restore confirmation), `open_session(lease)` (sends
  `session.open`, returns `PollenSession`).
- `PollenSession(link, lease, guard)` satisfies `CellSession` in full: `exec` (`session.exec`,
  `session.output` -> `OutputChunk`, `session.exit` -> `ExitStatus`; `TIMED_OUT` raises
  `CommandTimeoutError`; stdin through `session.stdin`), `put_file` (chunked `session.put_file`),
  `get_file`, `delete_file` (`session.delete_file`), `start`/`stop`/`is_running`
  (`session.process`), `close` (`session.close`). Before sending any command it checks
  `EnforcementPoint.DEVICE_COMMAND` (needed `exec:<argv0>`, `fs:read:<path>`,
  `fs:write:<path>`, `cell:outside_scratch:<path>` as applies, context access level = the node's)
  and records `swarm.command_sent` (lease id, request id, program basename, argument count, task
  id, worker id; never arguments or content). Program basenames only: argv is `C2`.
- `ShieldedSource(inner, verifier)`: for a PROPOLIS Cell, `lease()` opens the inner lease, opens a
  session, runs `PropolisChecks` (default route via a tunnel interface, DNS servers reached through
  it, no default route via a non-tunnel interface, a direct-egress probe refused), closes the
  session, and either returns the lease (`swarm.shield_verified` with per-check results) or
  releases it at once and raises `ShieldUnverifiedError` (`swarm.shield_refused` with per-check
  results). It never installs, starts or reroutes anything.

### 2.4 queen/swarm_gate (Layer 6)

```
queen/swarm_gate/
  __init__.py
  gate.py          SwarmGate: owns SwarmListener + GatewayHub; start(queen), run(), stop()
  wardens.py       DeviceWardens: one Level 0 Warden per online GATEWAY node (factory, attach, run, stop)
  attach.py        serve a device Warden's connection (shared with cell_gate's attach path)
  reconcile.py     each tick: registry desired state vs live (revoke, node_update, desired_level)
  colonize.py      Level 0 <-> 1 migration (11.6a)
  nuc.py           Level 1 <-> 2 (11.8)
  unreachable.py   a lost device: hold, grace, W0 diagnosis task, decide (11.9, 11.9a)
```

- `DeviceWardenFactory` (Protocol, filled by `cli/compose/swarm/`): `build(node: SwarmNode) ->
  DeviceWarden` where `DeviceWarden` is a frozen pair of the `Warden` and its `WardenLink`
  (memory transport pair, `cell = node.as_cell()`, `hop` from the Queen to `node.warden_id`).
- The device Warden's link is attached with `queen.attach_warden`, started, and run in a task the
  gate owns; `run_hive` runs `SwarmGate.run()` in its TaskGroup beside the Queen and W0, and stops
  it before the Queen (the gate stops every device Warden, releasing its lease, then closes the
  listener).
- A device Warden at Level 1+ dials the swarm listener; its first frame is `cell.ready` signed by
  the device's node key. `queen/swarm_gate/attach.py` hands that connection to the same attach
  path the `CellListener` uses for a Virtual Cell's Warden (refactor `queen/cell_gate/listener.py`
  so the post-handshake attach — fan-out transport, snapshot relay, trail receiver, refusals,
  detach — is one reusable helper; no second copy).
- Placement (`queen/placement/`): a PROPOLIS task never lands on a MEADOW Real Cell (rule added
  beside the existing exclusions); a NIGHT_VEIL task naming a Real Cell is denied
  (`guard.denied` at PLACEMENT) and redirected to Virtual placement.

### 2.5 wardens

- `wardens/offline/` (11.9, 11.9a): `link.py` (`ReconnectingQueenLink`, a `Transport` that
  re-dials through an injected `QueenDialer` and re-announces through an injected announcer),
  `outbox.py` (what queues; durable `waggle.outbox.Outbox` under the Warden's state directory),
  `policy.py` (pure offline rules, section 1.3), `freeze.py` (enter OFFLINE: freeze grants,
  cluster a Warden with no local model), `reconnect.py` (the reconnection sequence),
  `diagnose.py` (a Nuc Warden's own diagnostic Drone within `reconnect_budget`), `__init__.py`.
- `wardens/watch/` (11.10): `probe.py` (`WatchProbe` Protocol, `SessionWatchProbe`),
  `observer.py` (`WatchObserver`: samples into Bee Bread), `signals.py` (pure pre-filter),
  `patrol.py` (`Patrol`: the awake episode and its four actions), `__init__.py`; prompt
  `llm/prompts/patrol.md` with a snapshot test.
- `wardens/state.py`: add `OFFLINE -> WATCH` ("the link came back with no sub-bee running");
  `settled_state` must not move a Warden out of OFFLINE.
- `WardenDeps` is at its class-size limit: group the new collaborators into one frozen value,
  `RemoteDeps` (offline link, outbox, reconnect budget) and `WatchDeps` (probe, observer and
  Patrol settings), each one field on `WardenDeps`, plus `release_lease_when_idle: bool`.

### 2.6 pollen

```
pollen/
  __init__.py, __main__.py, cli.py (argparse: install, uninstall, enrol, run, status, version),
  errors.py (PollenError tree with pollen.* codes), state.py (PacketState: persisted JSON),
  agent/      packet.py (PollenPacket: TickLoop), link.py (dial, verify, address), dispatch.py
              (payload type -> handler; sender/recipient/capability checks), checks.py (pure
              capability matcher), heartbeat.py, moved.py (QueenMoved), __init__.py
  enrol/      invite.py (parse pollen1. strings), request.py (EnrolRequest from the probe),
              enrol.py (dial, enrol, verify accept, persist), __init__.py
  executors/  base.py (DeviceExecutor Protocol), session.py (exec), files.py, process.py
              (background), info.py (platform, capabilities, capacity), observe.py (watch), __init__.py
  lease/      lease.py (DeviceLease), table.py (LeaseTable, persisted), restore.py, quota.py,
              deadman.py, __init__.py
  platform/   base.py (PlatformShim), linux.py, windows.py, macos.py, select.py, __init__.py
  bootstrap/  runtime.py (venv install of the pinned runtime), warden.py (start/stop the
              on-device Warden), __init__.py
```

- Exec semantics match `CellSession`'s contract: argv lists only; a program that cannot start
  exits 127 (`EXIT_COMMAND_NOT_STARTED`) with a stderr line; `timeout_s` kills and reports
  `TIMED_OUT`; `max_output_bytes` kills and reports `KILLED`; every process in its own process
  group (POSIX) or job object (Windows), recorded with the lease before the first output frame.
- Paths: relative paths resolve under the lease's scratch; `..` and symlinks resolved before the
  check; writes outside scratch need `cell:outside_scratch` (FULL) and are recorded with their
  prior bytes for restore; reads follow `fs:read`.
- `pollen install` writes a systemd user unit (Linux, any distribution through XDG paths), a
  LaunchAgent (macOS) or a Scheduled Task (Windows) that runs `pollen run`; where the service
  manager is absent it writes the unit and prints the command to start it. `uninstall` reverses
  it and removes nothing of the operator's.
- 11.7 packaging: `.github/workflows/pollen.yml` builds single-file `pollen` binaries with
  PyInstaller (a `pollen-build` dependency group) on ubuntu-22.04 (x86-64), ubuntu-24.04-arm
  (ARM64, Raspberry Pi), windows-latest, macos-14 (ARM64) and macos-13 (x86-64), each running
  `pollen version` and the pollen unit suite. The binary bundles its own Python, so Raspberry Pi
  OS (Debian bookworm, Python 3.11) needs nothing installed.

### 2.7 cli

- `cli/swarm/` (11.12): `hive swarm invite|list|revoke|promote|demote|access|shield|run`, each
  taking a node as device id, Cell id or label. Registry writes go straight to the database; the
  running Hive's `queen/swarm_gate/reconcile.py` applies them on its next tick (`revoke`
  disconnects the packet, stops the Warden and refuses the key; `access`/`shield` send
  `swarm.node_update`; `promote`/`demote` set `desired_level`). `run <node> "<goal>"` submits a
  goal whose ceiling holds only `cell:real:<cell_id>` (the node's Cell).
- `cli/readback/cells.py`: `hive cells list` adds Swarm Cells from the registry: kind `REAL`,
  source `swarm`, Warden location (`hive_stand` at Level 0, `on_cell` at 1 and 2), level,
  access level, tier and mode (`ACTIVE`/`WATCH`/`OFFLINE`, from the trail's warden edges).
- `cli/compose/swarm/`: `build.py` (`build_swarm(...) -> SwarmAccess | None`, None when `[swarm]
  enabled = false`), `device_warden.py` (the `DeviceWardenFactory`: `build_warden_deps`
  generalised from `HiveStandSource` to any `RealCellSource`, `lease_capability = cell:real`,
  `release_lease_when_idle = True`, `snapshotter = NoopSnapshotter`, `watch` over
  `PollenWatchProbe`, the source wrapped in `ShieldedSource` for PROPOLIS), `__init__.py`.
- `cli/in_cell/` gains a borrowed mode for a colonized device (`HIVEMIND_CELL_LEASE=borrowed`):
  the Cell id, Warden id, node id and key file come from the environment the packet sets; the
  source is `cell.local`'s lease machinery parameterised with that Cell id, source name `swarm`,
  access level and tier (add `cell_id`, `source_name` and `comb_shield` to `HiveStandConfig`, the
  defaults keeping today's behaviour); memory and trail are SQLite files under the device's data
  directory, so Handoffs survive a Warden restart; the link is `ReconnectingQueenLink`.
- `cli/stores.py`: `open_swarm_registry(db)`.

### 2.8 manifest

- `manifest/schema/swarm.py`: `[swarm]` `enabled` (false), `listen_host` ("127.0.0.1"),
  `listen_port` (8720), `advertise_url` (""), `tls_cert`, `tls_key` (paths; required with a
  non-loopback `listen_host`), `invite_ttl_minutes` (15), `heartbeat_interval_s` (15),
  `heartbeat_miss_limit` (4), `dead_man_s` (120), `unreachable_grace_s` (300), `default_access`
  ("full"), and sub-tables `[swarm.ladder]` (`relief_pressure` 0.75, `min_free_memory_bytes`)
  and `[swarm.nuc]` (`seat_pressure` 0.75). `HiveManifest.swarm`. `docs/manifests/full.toml`
  documents every key; every example manifest still loads.
- `[supervision]` gains `reconnect_budget` (`attempts` 2, `wall_clock_s` 300; validator: below
  `max_offline_s`) and `[supervision.watch]` (`interval_s` 300, `retention_s` 86400,
  `patrol_interval_s` 3600, `cpu_share_alarm` 0.5, `disk_fill_alarm` 0.95).

### 2.9 Where each roadmap step lands

| Step | Where |
|---|---|
| 11.1 | `docs/waggle/enrolment.md`; `swarm/enrolment/`; `pollen/enrol/` |
| 11.2 | `swarm/models.py`, `node_state.py`, `registry/`, `gateway/heartbeat.py`, `source.py` |
| 11.3 | `pollen/agent/`, `pollen/state.py` |
| 11.3a, 11.3b | `waggle/transport/ceiling.py`, `replay.py`, `websocket_server.py`; recorded by `swarm/gateway/listener.py` and `queen/cell_gate/listener.py` |
| 11.4 | `pollen/lease/`, `pollen/executors/` |
| 11.5 | `swarm/session/`; contract suite with an in-process packet over a real WebSocket, and an `integration` harness with the packet in a container |
| 11.6 | `queen/swarm_gate/gate.py`, `wardens.py`; `cli/compose/swarm/` |
| 11.6a | `swarm/level.py`, `queen/swarm_gate/colonize.py`, `pollen/bootstrap/`, `cli/in_cell/` borrowed mode, `TaskAssign.handoff` |
| 11.7 | `pollen/platform/`, `pollen/cli.py`, `.github/workflows/pollen.yml` |
| 11.7a | `docs/waggle/gateway-contract.md`; `tests/contracts/test_gateway_contract.py` |
| 11.8 | `swarm/nuc.py`, `queen/swarm_gate/nuc.py`; the device Warden's `ModelServerHost` |
| 11.8a | `swarm/enrolment/` (tier on the invite), `swarm/shield/`, placement rules |
| 11.9 | `wardens/offline/`, Queen reconnection in `queen/attach.py` and `queen/swarm_gate/unreachable.py`, `TrailSegmentSync.segment_signature` |
| 11.9a | `wardens/offline/diagnose.py`, `queen/swarm_gate/unreachable.py` |
| 11.10 | `wardens/watch/`, `swarm/watch.py`, `pollen/executors/observe.py` |
| 11.11 | `PollenSession` start/stop/is_running; the X11 contract over it |
| 11.12 | `cli/swarm/`, `cli/readback/cells.py` |

## 3. Wire and trail additions

### 3.1 Waggle 1.13 (all additive)

New kinds (catalogue rows in this order inside their family blocks; `EXPECTED_ROWS` 81 -> 89):

- `session.delete_file` (`SessionDeleteFile`, request, Warden -> Pollen Packet): `lease_id`,
  `path` (max `MAX_PATH_CHARS`), `task_id | None`, `worker_id | None`. Ends in one `session.exit`
  with `request_kind DELETE_FILE`, outcome `COMPLETED`, `size_bytes`/`sha256` None; a missing
  file is `control.error pollen.not_found`; a refused path `pollen.path_refused`.
- `session.process` (`SessionProcess`, request, Warden -> Pollen Packet): `lease_id`, `action`
  (`ProcessAction`: `START`, `STOP`, `QUERY`), `argv` (START only: as `SessionExec.argv`; empty
  otherwise, validator), `cwd | None`, `env` (tuple of `EnvVar`), `log_path | None` (START
  only), `pid | None` (STOP and QUERY: required; START: None), `task_id | None`,
  `worker_id | None`.
- `session.process_state` (`SessionProcessState`, reply to `session.process`): `lease_id`,
  `action` (echo), `pid` (> 0), `argv` (as started, START only), `log_path | None` (resolved,
  START only), `is_running`, `was_running | None` (STOP only: whether it was running when asked),
  `reason`. A process that cannot start is `control.error pollen.start_failed`.
- `swarm.node_update` (`NodeUpdate`, event, Queen -> Pollen Packet; Queen-only): `device_id`,
  `cell_id`, `warden_id`, `level` (`RuntimeLevel`), `access_level`, `comb_shield` (never
  NIGHT_VEIL, validator), `granted_capabilities` (as `EnrolAccept`), `offline_limit_s`,
  `heartbeat_interval_s`, `reason`. The packet persists it and applies it from the next request.
- `swarm.colonize` (`Colonize`, request, Queen -> Pollen Packet; Queen-only): `cell_id`,
  `device_id`, `action` (`ColonizeAction`: `PREPARE`, `START`, `WITHDRAW`), `warden_id`,
  `runtime_version | None` (required for PREPARE, max 32), `warden_url | None` (required for
  START: the URL the device's Warden dials, `^wss?://\S+$`, max 512), `deadline_s` (> 0),
  `reason`.
- `swarm.colonized` (`Colonized`, reply to `swarm.colonize`): `cell_id`, `device_id`, `action`,
  `outcome` (`ColonizeOutcome`: `PREPARED`, `STARTED`, `WITHDRAWN`, `FAILED`; validator pairs
  each success with its action), `level` (`RuntimeLevel` after the step), `runtime_version |
  None` (set with PREPARED and STARTED), `warden_pid | None` (set exactly with STARTED), `reason`.
- `swarm.observe` (`Observe`, request, Warden -> Pollen Packet): `device_id`, `kinds`
  (`ObservationKind`: `PROCESS_LIST`, `RESOURCE_USE`, `LOGS`, `FILE_CHANGES`; min 1, unique),
  `roots` (paths for LOGS and FILE_CHANGES, max 16), `since_at | None` (the file-change cursor),
  `max_items` (1..500, default 200), `reason`.
- `swarm.observation` (`Observation`, reply to `swarm.observe`): `device_id`, `observed_at`,
  `processes` (`ProcessSample`: `pid`, `name` max 256, `cpu_share` 0..1, `rss_bytes`,
  `started_at | None`; max 500), `resources` (`ResourceSample | None`: `cpu_load`,
  `memory_bytes`, `memory_free_bytes`, `disk_bytes`, `disk_free_bytes`, `uptime_s`, `network`
  (`NetworkCounter`: `interface` max 64, `rx_bytes`, `tx_bytes`; max 32)), `log_lines`
  (`LogLine`: `path`, `line` max 1000; max 200), `file_changes` (`FileChange`: `path`, `change`
  (`FileChangeKind`: `CREATED`, `MODIFIED`, `DELETED`), `at`, `size_bytes | None`; max 500),
  `is_truncated`, `reason`. Everything in it is `C2` on arrival.

New fields and members:

- `TaskAssign.handoff` (`HandoffDocument | None`, default None): the Handoff itself when the
  receiving Warden may not hold it (a Warden migration, a reassignment across nodes). Set only
  with `resume_from`, and `handoff.clearance == resume_from.clearance` (validators). The receiver
  stores it under `resume_from.event_id` before spawning. `HandoffDocument` mirrors
  `memory.Handoff` field for field with the same bounds (`goal`, `progress`, `decisions`
  (`HandoffDecision`: `what`, `why`), `tried_and_failed`, `constraints`, `open_threads`,
  `next_steps`, `do_not_redo`, `pinned_facts`, `notes`, `clearance`, `written_by`, `task_id`); a
  tainted Handoff never travels. A frame over `MAX_FRAME_BYTES` falls back to a fresh start.
- `TrailSegmentSync.segment_signature` (`str | None`, default None, final chunk only, validator):
  padded base64 Ed25519 signature by the node's key over `segment_signing_bytes(node_id,
  first_event_id, last_event_id, event_count, sha256)` (a helper beside the model: the UTF-8 of
  `"waggle-trail-segment-v1\n" + "\n".join(fields)`). Receiver rule: a Swarm node's segment
  without a verifying signature is refused.
- `CellRequest.scratch_quota_bytes` (`int | None`, default None, >= 0): the lease's scratch
  quota from the holder's `Ceilings.scratch_disk_bytes_per_lease`.
- `SessionRequestKind.DELETE_FILE`; `AlarmKind.PATROL_ANOMALY` (hivemind's mirror and the
  shipped escalation policy gain a row: escalate to the Queen, who posts it to the human).
- Spec: the eight kinds in the table and in sections 8.6 and 8.10, the Queen-only list in
  section 3 gains `swarm.node_update` and `swarm.colonize`, section 4 gains the 1.13 entry,
  section 7's table gains `waggle.transport.frame_ceiling` and `waggle.transport.tls_pin`, and
  the close code `4029` joins section 9. A sender uses none of these toward a peer that has not
  advertised minor 13.

### 3.2 Trail kinds (every one classified in `guard/policy/catalogue.py`)

- `swarm` (`SwarmEvent`; existing `invited`, `enrolled`, `promoted`, `demoted`, `revoked`,
  `command_sent`): add `swarm.enrol_refused` (refusal), `swarm.online` and `swarm.unreachable`
  (lifecycle), `swarm.access_changed` and `swarm.shield_changed` (authorised at
  ENTRANCE_ROUTE, the operator's console), `swarm.shield_verified` (observation),
  `swarm.shield_refused` (refusal), `swarm.node_held` (the Queen holding a lost node's tasks;
  decision). `swarm.promoted`/`swarm.demoted` cover every rung (payload `from_level`,
  `to_level`).
- `warden` (`WardenEvent`; existing `reconnected`, `migrated`, `offline`, `watch`): add
  `warden.patrol` (observation; payload outcome, counts), `warden.diagnosed` (outcome).
- `guard` (`GuardEvent`): add `guard.frame_ceiling` (narrowing), `guard.replay_refused`
  (refusal). `guard.segment_refused` gains the reasons `unsigned` and `bad_signature`.
- `PENDING_POINTS` loses `NUC_PROMOTION` (call site `queen/swarm_gate/nuc.py` and
  `colonize.py`) and `DEVICE_COMMAND` (`swarm/session/`); both join `_CALL_SITES`.
- Night Veil skeleton: none of the above survives a Night Veil teardown (Swarm Cells are never
  Night Veil).

### 3.3 Capabilities

- The `swarm_device` role stays empty (a device is a gateway; it acts for nobody).
- The `warden` role gains `watch:*`, capped per Cell by the Warden's set (`watch:<cell_id>`), so
  a Warden may watch its own Cell and no other; the operator and the Queen keep `watch:*`.
- `device:*` stays with the Warden, capped at FULL, and is what `DEVICE_COMMAND` asks only for
  an Exoskeleton attachment on a device; plain commands ask `exec:`/`fs:` as on any Cell.

### 3.4 Appendix C additions

`NodeStatus` (swarm registry, `swarm_nodes.status`), the runtime ladder (`swarm_nodes.level`),
`InviteState` (`swarm_invites.state`), the device-side `DeviceLease` (`pollen/lease/`, the
packet's JSON state), and the Warden's new `OFFLINE -> WATCH` edge.

## 4. Build order

- **Wave 1** (parallel, disjoint files): 1A wire 1.13 + spec + trail kinds + catalogue + ADRs
  0051-0056 + `docs/waggle/enrolment.md` + `docs/waggle/gateway-contract.md` + codingrules
  (layout tree, Appendix C) + `[swarm]`/`[supervision]` manifest schema; 1B waggle transport
  (replay window, frame ceiling, TLS); 1C swarm registry, models, node state, enrolment door.
- **Wave 2**: 2A pollen packet (11.3, 11.4, 11.7 shims and CLI); 2B swarm gateway, `SwarmSource`,
  `PollenSession`, shield checks, contract suites (11.2, 11.5, 11.8a checks); then 2C Queen side
  (swarm gate, device Wardens, composition, placement rules: 11.6, 11.8a placement).
- **Wave 3**: 3A colonization and the borrowed in-Cell mode (11.6a); 3B offline Wardens and
  reconnection on both sides (11.9); 3C watch mode and the Patrol (11.10); 3D Nuc promotion and
  diagnostic Drones (11.8, 11.9a); 3E CLI, readbacks, Exoskeleton over PollenSession, packaging
  workflow, gateway contract suite (11.7, 11.7a, 11.11, 11.12).
- **Wave 4**: exit criteria (section 5), the gates, roadmap ticks with as-built notes, this file's
  handoff sections, push.

## 5. Exit-criteria plan (what is real here, what stands in)

This container has Docker (started with `dockerd`) and Docker Hub reachable, Python 3.12 images,
no ARM emulation, no Tor or OpenVPN images, and no model hub. The second machine is a container:
its own filesystem, process table and network namespace on the default bridge, the packet
installed from wheels built from this tree, dialling the Hive Stand's swarm listener over `wss://`
with a pinned self-signed certificate at the bridge gateway. Models are
`tests/e2e/scripted_openai.py` servers: one on the Hive Stand for the shared sources, one inside
the container as the Nuc's model server (declared in the device's `warden.toml`). Network cuts are
`docker network disconnect`. Every scenario lives in `tests/e2e/swarm/` (helpers) and
`tests/e2e/test_phase11_*.py`, marked `e2e` and skipped when the daemon or the test image is
absent (`HIVEMIND_TEST_DEVICE_IMAGE`).

| Bullet | Plan |
|---|---|
| Enrol, list, Drone completes, scratch gone, process table matches | Real: container packet, Level 0 Warden on the Hive Stand, scripted Drone; `ps` in the container before and after |
| Level 1 without failure, then Nuc, cut mid-task, outbox, segment, no duplicates, allowlist | Real: colonize in the container (runtime from a wheelhouse baked into the image), promote, `docker network disconnect` mid-task, reconnect, assert trail and results |
| Night Veil only on Virtual; the attestation bullets | Placement denial for Real Cells is real; the Tor/OpenVPN/capture/location bullets stay phase 5 and 10 suites with their fakes; the real run needs the night-veil image and is the operator's |
| Propolis on a Real Cell | Real: a container with `NET_ADMIN`, a `tun0` default route and DNS on it; blocked while unverified, placed once verified; left-as-found holds |
| Dead-man on a non-Nuc | Real: disconnect a Level 0 container; lease released `DEAD_MAN`; the device Warden's `CELL_UNREACHABLE` Alarm |
| Overnight `WATCH` and the Patrol | Real with a compressed interval: no writes (file tree snapshot), Patrol on schedule, planted CPU hog becomes an Alarm, a quiet interval records nothing notable |
| Replayed or tampered session message; Raspberry Pi OS | Replay and tamper over a real socket; the PyInstaller binary built in a bookworm container runs in a clean `debian:bookworm` container (x86-64 stand-in; ARM64 is the CI matrix's) |
| Diagnostic Drones | Real in the container: a wrong `/etc/hosts` entry fixed by the Nuc's Drone; a removed network gives up inside budget; an unplugged colonized device gets W0's Drone and a Nectar finding |

## 6. Where things stand (handoff, written as the work lands)

- **The branch.** `ccr-75a88a63-nwtvjo`, pushed after every wave. Every wave's agents worked in
  isolated worktrees; their commits are cherry-picked onto the branch (`git log --oneline
  a11deb3..HEAD`), each gated before the push. **No PR was opened**; the operator asked for a
  branch.
- **Worktree trap.** The Agent tool creates a worktree from `main` (latterly from `origin/main`),
  not from this branch: every brief starts with `git merge --ff-only ccr-75a88a63-nwtvjo` in the
  worktree, and when that refuses (a base that is not an ancestor) the agent runs `git switch -c
  <name> ccr-75a88a63-nwtvjo` in its clean tree instead. `.claude/worktrees/` is git-ignored, and the
  hygiene scripts skip `.claude`.
- **Waggle** is **1.13** (89 kinds). ADRs **0051 to 0057** (0057, Propolis verified by looking on
  a Real and a Virtual Cell, came out of the exit wave).
- **Wave 1 (landed).** The 1.13 wire and spec, trail kinds, `[swarm]` and `[supervision.watch]`
  manifest sections, the warden's `watch:*`; the replay window, the frame ceiling (close 4029) and
  pinned TLS in `waggle.transport`; the six ADRs, `docs/waggle/enrolment.md` (the invite carries
  `hive_id`, eight keys) and `docs/waggle/gateway-contract.md`.
- **Wave 2 (landed).** `packages/pollen` (the packet: enrolment, link, leases, executors, observe,
  dead-man switch, shims, `pollen install`; 550 tests); `hivemind/swarm/` (registry, enrolment
  door, TLS listener with refusals, hub and device links, `SwarmSource`, `PollenSession`, Propolis
  checks, watch probe); `queen/swarm_gate/` (a Level 0 Warden per online device, reconcile,
  minimal unreachable); `cli/compose/swarm/` (`build_swarm`, the device Warden factory, the
  per-lease command guard); placement rules (a PROPOLIS task never on a MEADOW Real Cell);
  the Virtual Cell listener's refusals; `PollenSession` passes the `CellSession` contract against
  a real packet; the gateway conformance suite (`tests/contracts/test_gateway_contract*.py`, run
  another gateway with `HIVEMIND_GATEWAY_COMMAND`); the container harness (`tests/e2e/swarm/`:
  `device_image()`, `Janitor`, `start_stand`, `enrol_container`); `tests/e2e/test_swarm_level0.py`
  (a real packet, a real Level 0 task).
- **Wave 3 (landed).** 3A, colonization (11.6a) and offline Wardens (11.9): the packet serves
  `swarm.colonize` (PREPARE a venv runtime from its wheelhouse, START the in-Cell Warden, WITHDRAW
  it) and records the rung and the on-device Warden in its state; `hive in-cell` gains a borrowed
  mode (`cli/in_cell/borrowed/`) driven by a device manifest (`manifest/device.py`,
  `docs/manifests/device.toml`) and the variables the packet hands it; `wardens/offline/` (the
  reconnecting Queen link, outbox, freeze, resume, OFFLINE back to WATCH); the final trail-segment
  chunk is signed with the node key; an inline Handoff is filed in the Warden's own store before
  spawning; `RealCellLease.release` survives two callers and a cancel mid-release. 3B, the Queen
  side: one attach path for every remote Warden (`queen/attach/`, re-sends grants, ceilings and
  plan when a Warden comes back), the swarm gate admits a colonized device's Warden, holds a lost
  device's Cell (hold, grace, then decide), colonizes on request with rollback, verifies a trail
  segment's signature before merging, and carries a Handoff inline (`TaskAssign.handoff`) to a
  Warden that cannot read her store; `swarm.online` on every link. 3C, watch mode and the Patrol
  (11.10): `wardens/watch/` (a `SessionWatchProbe` on the Hive Stand that runs two fixed read-only
  scripts through the Warden's own lease, the `PollenWatchProbe` on a device, the observer, the
  Patrol prompt and its awake episode, the tick), observations kept in Bee Bread for a retention
  window, the Cell page's last Patrol. 3E: `hive swarm invite|list|revoke|access|shield|promote|
  demote|run` over an operator desk (`swarm/operator/`), a Swarm Cell's Warden location in the
  Observation Hive and `hive cells list`, the Exoskeleton's X11 backends over `PollenSession`
  (device image variants, `tests/integration/test_exoskeleton_on_device.py`), the single-file
  packet built by `scripts/build_pollen.py` on five platforms (`.github/workflows/pollen.yml`),
  and `docs/runbooks/enrolling-a-device.md`.
- **Fixed while integrating.** A lease killed every pid it ever recorded at release, so once a
  finished command's group was gone and the kernel reused the id, a release could signal an
  unrelated process; sessions now report a pid back once it is reaped with its group empty and the
  lease forgets it (34c2446; pollen's own lease already checks a process's creation time). Since
  the Hive Stand's Warden watches its Cell between tasks, its probe's commands share the lease,
  which broke `test_quarantine_on_hive_stand.py` about half its runs (bisected to 39e779d; the test
  now finds the Drone's command by its command line, 58adc82). Earlier: the packet's backoff
  ignored stop (79c330a), a frame-ceiling close in the fan-out pump, and openbox swallowing the
  first click (585dca3).
- **Wave 3D (landed).** 11.8: `swarm/nuc.py` (`decide_nuc`), the Queen's Nuc flows in
  `queen/swarm_gate/colonize/nuc/` (promote: ceilings, `swarm.nuc_promote`, `swarm.nuc_promoted`,
  `swarm.promoted`, then a hosting plan with the device's `local=` sources first; demote the
  reverse), the device Warden's `ticks/nuc/` (starts and stops its model server through phase 8's
  hosting, reports `forage.source_added`), a device manifest that carries the `[forage.map]` rows
  its servers load; a model outside the allowlist is `guard.denied` at SLOT_BINDING. 11.9a:
  `wardens/offline/diagnose/` (a cut-off Nuc spawns one local Drone, no grant slot, bounded by
  `max_sub_bees` and the reconnect budget; the Warden judges FIXED, DIAGNOSED or NO_CAUSE from the
  Drone's closing `CAUSE:` line, records `warden.diagnosed` and queues C2 Nectar), Nectar queued
  while the link is down, a sub-bee for a Warden-minted task whose Honey traffic stops at the
  Warden, and `queen/swarm_gate/unreachable/hive_stand.py` (a lost device gets an ordinary Queen
  task on the Hive Stand: "check reachability of device <cell> ..."). Running the real Drone role
  found two things: a ceiling naming `tool:run_command` left the Drone no tool (fixed on both
  ends), and `run_command` reports only state, so the objectives say to write output to a file.
  Config a Nuc needs: the Hive manifest lists the server id with `load_on_start=false`, the device
  manifest has `[forage.map]` rows; a device manifest's own `[supervision]` (wave 4C) sets its
  Warden's offline limit and reconnect budget.
- **Wave 4F (landed).** The Queen's own rung decisions (`queen/swarm_gate/rungs/`): two pressures
  defined in `pressure.py` (relief: the Hive Stand's own load from the Level 0 Wardens' sub-bees;
  seats: the shared pool's seats in use against its seats), and `TaskNeeds.survives_link_loss`
  (settable from a plan and the local CLI; not yet from the Entrance goal API or the remote CLI);
  the reconcile pass hands every reachable device with no operator `desired_level` to a
  `RungEvaluator`, which moves it through the same Guard-checked flows with its reason on
  `swarm.promoted`/`swarm.demoted`, lowers it only after the reason has been gone a while and only
  while idle, backs off a failed attempt, runs at most one pressure move at a time, and keeps its
  memory of what it raised in process only. The operator's `desired_level` always wins, and
  `demote` at GATEWAY pins a device. A terms change on a colonized or Nuc device sends
  `swarm.node_update` at once and restarts the device's Warden through the packet (WITHDRAW,
  START) with its tasks checkpointed and resumed (`colonize/renew/`, ADR-0053); a Nuc whose Warden
  comes back without its server is promoted again or lowered cleanly (`colonize/nuc/restore.py`).
  Two latent bugs went with it: `open_tasks` read only the oldest thousand tasks of any status,
  and `resend_plan` raised on a plan naming a source no longer on the map. Then 567bc0a: a hold
  asks the Hive Stand for a diagnosis only when it holds tasks, because a Hive that has just
  started finds every enrolled device that has not dialled in yet unreachable.
- **Wave 4 exit scenarios (landed).** 4A, the Level 0 bullets in real containers
  (`tests/e2e/test_phase11_exit_{level0,deadman,watch,propolis,replay}.py`, about two minutes
  together): enrol, list, a Drone's work left as found; the dead-man release and the
  `CELL_UNREACHABLE` Alarm; an idle device watched with no writes, a planted hog an Alarm, quiet
  Patrols "nothing notable"; a Propolis task held until the device's own tunnel verifies, a Night
  Veil goal denied at placement; a replayed `session.exec` run once and a tampered frame closing
  the link (1008). It fixed a device Warden that dropped a task whose lease the device refused
  (now parked and retried every `DeviceDeps.lease_retry_s`). 4D, "installs on a clean Raspberry Pi
  OS" on the x86-64 stand-in (`test_phase11_exit_binary.py`): the single-file packet built as the
  workflow builds it, run by an unprivileged account on a bare `debian:bookworm`, enrolled, a Level
  0 task, uninstalled, the device equal to its snapshot; it fixed the packet passing PyInstaller's
  `LD_LIBRARY_PATH` to everything it started. 4E, Propolis on a Virtual Cell (ADR-0057, accepted):
  the tunnel judges moved down to `hivemind.cell.tunnel`, a PROPOLIS Virtual Cell is attested
  from its image (the four tunnel checks and `tor_disabled`) before it is ready, the in-Cell
  Warden accepts PROPOLIS, `cell.attested` names its tier; proved against containers of the base
  image (`tests/integration/test_docker_propolis.py`, needs `hivemind/base-ubuntu:dev`).
- **Wave 4B (landed).** The Level 1 and Nuc bullet on a real container
  (`tests/e2e/test_phase11_exit_nuc.py`, twelve assertions over one run of about five minutes):
  a long task at Level 0, `hive swarm promote` colonizes the device and the task resumes once on
  it from its Handoff under the same Warden id, the Nuc promotion's frames in order with no
  request, a cable pulled mid-task (the task finishes on the local pool, a high-bar subtask waits
  with its `ForageRequest` queued and resumes once), the segment merged and `warden.reconnected`,
  results once each and nothing duplicated, an allowlisted load with no request and one outside
  the allowlist `guard.denied` at SLOT_BINDING. The two whole-Hive container rigs are one
  (`e2e/swarm/`). Ten defects fixed on the way, each with a regression test: a device nobody saw
  go got a diagnosis; a device's old lease was released before the dead-man report was heard; a
  bee killed by a Level 0 cut counted as a crash and left its task RUNNING with no bee (now held
  in the outage with its Handoff, the open item from 4A); a Level 0 Warden had no outage to hold
  tasks in; a server-only provider with no default model ended the Queen's tick;
  `cell.heartbeat` and capacity reports woke the Queen's model; a `ForageReply` killed a Warden;
  a held task now files a BINDING `ForageRequest`; a move paused a bee mid-command before it
  could write its Handoff (moves now wait up to `HANDOFF_PATIENCE_S`, 30 s); and the stretch of
  trail before a pulled cable is noticed never reached the Hive (the segment rewinds by
  `UNNOTICED` on reconnection).
- **Wave 4C (landed).** The diagnostic Drones on real containers
  (`tests/e2e/test_phase11_exit_diagnose.py`, eleven tests, about nine minutes): (a) a Nuc whose
  link breaks on a planted wrong `/etc/hosts` line has its Drone mend it well inside the budget,
  `warden.diagnosed` FIXED, the task finishing on its own bee and the Nectar arriving after the
  reconnect, with no operator command and no `guard.*` event (the Hive Stand's own look at the lost
  device, ADR-0056, is the only Queen task); (b) with the network gone the Drone ends DIAGNOSED at
  its budget and the Warden clusters fully at `max_offline_s`, its findings delivered from the
  outbox once the cable is back; (c) an unplugged colonized device gets W0's Drone, whose Nectar is
  stored before the hold's `queen.decided`. A device manifest now carries `[supervision]`
  (`max_offline_s`, `[supervision.reconnect_budget]`) for its Warden. Two defects fixed: an
  unplugged colonized device got no Hive Stand diagnosis (its Warden's link closed before the door
  said why, so the hold read "no live link"), and a quiet cut-off Warden overshot its search
  budget and offline limit by up to a heartbeat (it now wakes at its own deadlines).
- **Wave 4G (landed).** What a dead link swallowed before anyone noticed it (wave 4B's open
  item): `waggle.transport.recent` keeps a bounded ring (256 frames, 4 MiB) of the frames a node
  sent that must arrive (the offline table's QUEUE and HOLD rows, `policy.must_arrive`), and on
  reconnection, before the outbox, sends again those sent less than `UNNOTICED` before the loss and
  less than 540 s before the redial (the Queen's 600 s replay window less a minute), under their own
  ids; the packet's `Courier` does the same for `session.exit` and `cell.lease_released`. The
  Queen's replay window drops what she already had. Two Queen-side defects fixed with it: the
  reconnection grace began at `warden.reconnected`, after the trail merge, so a slow merge turned
  the re-sent stretch into `guard.replay_refused` records (it now begins at attach), and frames a
  replaced link had read but the tick had not drained were dropped (they carry over). Proved on a
  real container (`tests/e2e/test_phase11_exit_unnoticed.py`): a result sent seven seconds after
  the cable was pulled, before the loss was noticed, heard once after it came back; the same
  scenario fails without the ring. Left open: after a Queen restart her window is empty, so an
  Alarm or a question sent again can repeat; an outage longer than about nine minutes re-sends
  nothing from its unnoticed stretch.
- **Main merged in (5ad2814).** `origin/main` gained ten Windows-portability fixes to phase 8, 9 and
  12 code after this branch was cut; they are merged, the one conflict (the face of
  `hivemind.cell.local`) resolved by keeping both sides' names, so main is an ancestor again.
- **The independent tester, and wave 5.** One tester re-read every exit clause and every as-built
  note against the code and ran the whole suite (unit 19,037 passed, e2e 206 passed, integration 17
  passed and one failed). What it found, all fixed and each with a test that fails without it: (5A)
  a Phase 11 regression from be8664e, the Queen resuming a Virtual Cell's task fresh because she
  looked for its Handoff in her own store (she now sends the reference alone when she holds no
  copy); a role ending while its bee was PAUSED killed the runtime and then its Warden; and a task
  taken back from a lost colonized device now resumes from the HANDOFF Nectar its bee deposited
  (`queen/swarm_gate/recall.py`, ADR-0053). (5B) a device lease's scratch quota now comes from
  the Queen's `Ceilings` (else `[swarm] scratch_quota_mb`), and an operator names a device's watch
  directories (`hive swarm watch`, `swarm.watch_changed`, ADR-0054). (5C) a colonized device's
  Warden gets the Exoskeleton wiring (recorder, browser launcher, ears; `device_exoskeleton`), so
  its GUI work is recorded (ADR-0052). (5D) the Night Veil attestation judges what it claims
  (hidden service answers 426, DNS placed on Tor's loopback port or a tunnel, a randomized hostname
  check, the fourteenth, with the Docker backend randomizing it, WebRTC off in the Tor Browser
  profile), a Night Veil Cell's Warden runs over the mending link so a dead Tor cuts it off and it
  clusters past `max_offline_s` with no fallback, the Propolis scenario's Night Veil case is a
  human's request, and the Guard Bee counts `frame_ceiling_repeats` and `replay_refusal_burst` per
  node. Also fixed on the way: the Hive Stand's free memory is MemAvailable (MemFree made a warm
  host look out of memory: the busy-host flake), the pid probe in `cell/local` uses ctypes on
  Windows instead of psutil (`cell/local` needs only the standard library), a Level 0 device's
  Warden records into the Hive's own recording store, and the Night Veil image's output chain
  accepted `iif lo`, which no outgoing packet matches, so nothing on the Cell could reach Tor
  (now `oif lo`). Waves 5E and 5F have entries of their own below.
- **Fixed after wave 4 reported.** Virtual placement took any spec for a PROPOLIS task, so one
  that found no PROPOLIS device got a MEADOW Virtual Cell (cb00160: a spec must serve the task's
  tier; NIGHT_VEIL specs only on Night Veil's path); a resumed Overwintered Cell was not attested
  again (70ce8a5); `test_swarm_level0.py` read a revoked device's link before the close that
  follows the Warden's stop (abb5e6d).
- **Open, found by wave 4.** (The Level 0 cut that left a task RUNNING with a crashed sub-bee is
  fixed, wave 4B.) The packet counts a link as proof of life only once it has been open a ping
  interval (20 s), so a `dead_man_s` shorter than that can trip on a young healthy link. A real
  `hive run` readies no PROPOLIS or NIGHT_VEIL Virtual Cell (no Queen-side session to a Virtual
  Cell, the templates are MEADOW only, the Docker backend grants `NET_ADMIN` only to VPN_TOR, no
  PROPOLIS image ships): fail-closed, ADR-0057 says so. ARM64 is proved only by the CI matrix; a
  container has no systemd user session, so `pollen install` prints its manual command there.
  `e2e/swarm/bridge.py` and `e2e/swarm/binary/stand.py` overlap (two whole-Hive rigs).
  A container device reads the host's load: scenarios give their Drone no cores
  (`ManifestTuning.drone_cpu_cores`) or a busy host defers it.
- **Wave 5A (landed).** Three findings of the independent phase 11 tester. (1) The Queen resumed
  every task of a Virtual Cell or a colonized device from nothing (be8664e): `resume_plan` read the
  Handoff from her own store for any remote Warden and dropped the resume when it was not there,
  though the bees wrote it in the Warden's. She now carries it inline when she holds it and the
  Warden cannot read her store, sends the reference alone otherwise, and resumes fresh only when
  she refuses it; a Worker whose store lacks the reference starts fresh and says so. (2) A role
  that ended while its bee was PAUSED raised PAUSED -> FAILED, an edge the table lacked, which
  killed the runtime and, when the Warden reaped it, the Warden. PAUSED -> FAILED and -> DONE are
  edges (the role's end is the role's own event); `WorkerRuntime.run` ends only as a typed
  `WorkerRuntimeError`, and `stop_sub_bee` reads and logs it (`reap_runtime`), so no bee's stored
  error ends a Warden. (3) A task taken back from a lost colonized device resumed from nothing:
  `queen/swarm_gate/recall.py` recalls the newest HANDOFF Nectar its bee deposited (the one carrier
  of the document, ADR-0053's last amendment) unless it is tainted, written under a standing
  isolation, above the task's clearance or a Night Veil Cell's. Left open: the same blindness in a
  colonized Warden's renewal and in Clustering's resume (`pause_on_warden` waits for a checkpoint
  in the Queen's Bee Bread that a device's Warden never writes there; `pause_for_move` and
  `cluster.resume` should call `recall_handoff`), and the Honey Store has no taint ledger.
- **Wave 5E (landed).** Two findings of the independent phase 11 tester. (1) A colonized device's
  Warden did not watch: `cli/in_cell/borrowed` built it with no `WatchDeps`, so step 11.10 stopped
  applying to a device the moment its Warden moved onto it. It now watches like every Real Cell's:
  `SessionWatchProbe` over a lease-free, read-only `ObserverSession` (`cell/local/observer.py`; the
  Warden gives its lease back when idle, so lending the probe the lease's session would have seen
  none), the Patrol's numbers from the device manifest's new `[supervision.watch]`
  (`DeviceWatchSection` on `WatchTuning`, no `roots`), and the operator's directories carried as the
  other terms are: `SwarmNode.watch_roots` -> `NodeUpdate.watch_roots` (only those the capabilities
  cover) -> the packet's state -> `HIVEMIND_WATCH_ROOTS` at START -> `CellReady.watch_roots` back ->
  the desk's `Reported` -> `terms_drifted` -> the wave 4F renewal (a restart), held to the access
  level at both ends (`readable_under`). Waggle 1.13 gained the two fields (additive). The Warden
  wakes on its fifteen-second heartbeat, so a sample or a Patrol cannot come closer than a beat.
  Proved on a container (`tests/e2e/test_phase11_exit_watch_colonized.py`). Writing the proof meant
  removing `serve` from `tests/e2e/scripted_openai.py`, which had no caller and let the file take
  the Patrol answer inside its 400-line cap. (2) `pause_for_move` (a colonized Warden's renewal) and
  `cluster.resume` read only the Queen's Bee Bread for a Handoff; they now call `recall_handoff` too,
  with the same refusals (4862cee, ADR-0053's amendment). Left: `pause_on_warden` still waits for
  a checkpoint in the Queen's own Bee Bread that a device's Warden never writes there (its patience
  is `HANDOFF_PATIENCE_S`; it returns early when she holds an older one), and the Honey Store has
  no taint ledger. Integrating it took `manifest/env.py` past 300 lines, so it became the package
  `hivemind.manifest.env` (`hive`, `in_cell`, `common`, the same public names); the first of its
  two commits (0c48039) is the bare rename and does not import on its own, b337d31 completes it.
- **Wave 5F (landed).** `test_night_veil_boundary.py` failed about one run in three after 5D
  (bisected to 316c536, the mending link for a Night Veil Cell), on an `unclosed transport`
  ResourceWarning in the test after the one that leaked. A Cell the Queen destroys by cancelling it
  (a failed attestation, an Absconding) never closed its Warden's link: `hivemind.common.tasks.reap`
  re-raised a cancel the task had already received, so `warden.stop()` in `run_in_cell_warden`'s
  `finally` ended at its first waiter and the `transport.close()` after it never ran. A bare link
  left open had been harmless; a mending one read the Queen's hang-up as Tor dropping it and
  redialed the test's fake Tor as it closed. Three fixes, each with tests that fail without it: a
  closed `ReconnectingQueenLink` dials no more (`redial(abandoned=...)`, 9e660cc); an in-Cell
  Warden closes its link in a `finally` on its own bound, whatever became of the stop (e5bcfac);
  and `reap` re-raises only a cancel that arrives while it waits (14b075a), so any cleanup run after
  a cancel, `hive run`'s Ctrl-C path included, now reaps every waiter instead of stopping at the
  first. With that, a destroyed in-process Virtual Cell's Warden stops all the way and ships its
  last trail segment, which put a sandbox Warden's own `tool.quarantined` ahead of the Queen's in
  `test_phase9_exit_criteria.py` (12bf1ee reads the Queen's own records). The module then ran 20 of
  20. Also: `test_watch_and_patrol.py`'s hog scenario took any Alarm in the chat for the Patrol's,
  and on a loaded host the liveness sweep's `CELL_UNREACHABLE` (a 50 ms test heartbeat) got there
  first; it now waits for, and checks, the PATROL_ANOMALY the Queen escalated. Old against new,
  alternating: the old failed 3 of 16 runs on that assertion (2 of 8 under eight CPU burners, 1 of
  8 with a liveness-style ALARM line put in the chat first), the new passed all 16; the boundary
  module passed 6 of 6 more on the final tip.
- **Gate numbers.** Wave 1 head: 15,362 unit passed; wave 2B head: 16,733 passed; both 0 failed.
  Wave 3 head (5a8a927): 18,044 unit passed, 27 skipped; e2e 160 passed, the quarantine scenario
  the one failure (fixed above). **Final** (12bf1ee with the watch test's change of f2df0b6; the
  commits after it are documents): unit 19,550 passed, 27 skipped, 0 failed (16 min); e2e 221
  passed, 6 skipped, 0 failed (25 min); integration 19 passed, 34 skipped, 0 failed (3 min); ruff,
  mypy (3,465 files), lint-imports and the five hygiene scripts clean. Every skip is this sandbox's
  (no Playwright or Whisper extra, no X11 or PulseAudio tools, no QEMU, the desktop image and the
  Observation Hive unbuilt) or a contract case its harness cannot play (a fake provider's DEGRADED,
  a rate limit Whisper does not have).
- **Docker here.** Start `dockerd` (the default socket works in this container); the device image
  is `ubuntu:24.04` (this sandbox blocks the Debian mirror, not Ubuntu's); cold build about 40 s,
  cached under `~/.cache/hivemind-tests`; `HIVEMIND_TEST_DEVICE_IMAGE` overrides.
- **Exit criteria: how each is proven.** Real means a real container (or a real Hive in process)
  asserts it; stand-in means a fake or a stand-in does, as section 5 planned.

  | Bullet | How it is proven |
  |---|---|
  | Enrol, list, Drone, left as found | Real: `test_phase11_exit_level0.py` |
  | Level 1 without failing, Nuc, cut mid-task, outbox, segment and results once, no duplicates, the allowlist | Real: `test_phase11_exit_nuc.py` (the allowlisted load is driven by an order the scenario sends) and `test_phase11_exit_unnoticed.py` (a result sent before the loss is noticed) |
  | Night Veil only on Virtual; denied on Real | Real container for the Real refusal (`test_phase11_exit_propolis.py`, a human's request, refused at placement); the Virtual side's thirteen-plus-one checks are stand-ins: real judges over canned command output (`test_virtual_cells_night_veil.py`, the session probe's unit tests), the Docker backend's random hostname against the daemon |
  | Night Veil C0/C1 read, C2 denied | Unit (the floors, the Honey query) |
  | Tor-only link; host capture; Tor killed clusters | The link and "Tor killed: cut off and clustered, no fallback" with a fake Tor stopped mid-run (`test_night_veil_link.py`, `test_night_veil_tor_loss.py`); the host capture is not provable here (no Tor or OpenVPN image) and is the operator's run |
  | Location-blind attestation | Stand-in: the judges over canned output, WebRTC from the Tor Browser profile, the hostname check |
  | Propolis on a Real Cell | Real container with a bare `tun0` and no VPN server: the checks read the real routing table and resolver (`test_phase11_exit_propolis.py`); left as found is real |
  | Propolis on a Virtual Cell | Real containers of the base image through `docker exec` (`tests/integration/test_docker_propolis.py`), and provider level with a fake probe (`test_virtual_cells_propolis.py`); a real `hive run` readies no PROPOLIS Virtual Cell (fail closed, ADR-0057) |
  | Dead-man and the Alarm | Real: `test_phase11_exit_deadman.py` |
  | Overnight watch | Real, intervals of seconds, a scripted Patrol model: `test_phase11_exit_watch.py`, `test_phase11_exit_watch_roots.py`, `test_phase11_exit_watch_colonized.py` |
  | Replay and tamper; Raspberry Pi OS | Real container (`test_phase11_exit_replay.py`); the install is an x86-64 Debian 12 stand-in (`test_phase11_exit_binary.py`), ARM64 only in CI, and a container has no systemd user session |
  | Diagnostic Drones | Real containers: `test_phase11_exit_diagnose.py` (reversible, unfixable, W0) |

  Steps proved beyond the bullets: `test_phase11_exit_quota.py` (11.4), `test_phase11_exit_exoskeleton.py`
  (11.11), `tests/integration/test_exoskeleton_on_device.py` (11.11 at Level 0).
- **Open, for later phases.** After a Queen restart her replay window is empty, so an Alarm or a
  question sent again from an unnoticed stretch can repeat (results and Nectar are idempotent), and
  an outage longer than about nine minutes re-sends nothing from that stretch; the Honey Store has
  no taint ledger. A real `hive run` readies no PROPOLIS or NIGHT_VEIL Virtual Cell: there is no
  Queen-side session to a Virtual Cell, the templates are MEADOW only, the Docker backend grants
  `NET_ADMIN` only to VPN_TOR, no PROPOLIS image ships, the Night Veil image's changes from wave 5D
  are unbuilt here (no Tor Browser download) and its torrc has no DNSPort. A Virtual Cell that
  clustered fully gets no resume from the Queen when it returns. `TaskNeeds.survives_link_loss` is
  not carried by the Entrance goal API or the remote CLI. `control.queen_moved` carries no TLS pin
  (13.2a). The packet installs `hivemind` without the browser extra, so a device has no browser
  launcher; a Cell Warden's ears go through an unmetered gate (codingrules 8.10). A colonized
  Warden is not woken by a link change (it notices on its next beat). The dispatcher's
  placement-failure record names `night_veil_initiation` for a task that does cite a request.
  `pause_on_warden` still waits on the Queen's own Bee Bread for a checkpoint a device Warden never
  writes there. Tool eligibility and the Nuc decision do not read the platform report yet.
  `ReconnectingQueenLink.close()` does not wake a redial asleep between attempts: its receive task
  ends after that wait (at most 30 s) without dialing again. The liveness sweep's
  `CELL_UNREACHABLE` reaches the human's inbox and chat with no `alarm.raised` or `alarm.escalated`
  row (so since phase 3; `escalate_alarm` would give it one), so the trail-rebuilt inbox
  (`cli/readback/inbox.py`) never shows it. Destroying an in-process Virtual Cell (the test backend
  cancels it) lets its Warden stop all the way and ship its last trail since 14b075a, while the
  Docker backend's forced removal ships nothing: a scenario must not rely on a destroyed Cell's last
  words.

