# hivemind end-to-end tests

Whole-Hive scenarios that run the Queen, at least one Warden and its Workers together, the way
`hive run "goal"` would from the CLI. These are the slowest and fewest tests in the tree; they
exist to catch problems that only show up when every layer runs at once.

- `test_phase2_exit_criteria.py` (`@pytest.mark.e2e`) -- the phase 2 exit criteria (`.claude/
  roadmap.md`): submits a three-task graph through `hive tasks submit`
  (`typer.testing.CliRunner`), drives it through every task status including BLOCKED and PAUSED
  via a `BroodChamber` built directly in-process, "restarts" by reopening the store on the same
  SQLite file and checking identical state through both the chamber and `hive tasks show`, and
  checks the trail is complete and in order; a second test merges two nodes' trail segments
  through `hive trail export`/`merge` and checks the merged log has no duplicate ids and is
  ordered by `(at, node_id)`. `scripts/brood_demo.py` is the same scenario run as real child
  processes, for a human to watch.
- `test_kernel_on_hive_stand.py` (`@pytest.mark.e2e`) -- roadmap step 3.22's eight whole-Hive
  kernel scenarios, each parametrised over `capabilities in ("full", "none")` and run once
  through a single Hive (no retry loop anywhere in this module: the kernel fix-forward commit
  closed the real-SQLite-timing races the checkpoint suite's own retry loops were guarding
  against):
    - **(a)** the three-haiku goal completes, in the trail order the kernel's own structure
      actually guarantees, both in-process and through `hive run` itself.
    - **(b)** a hand-killed Drone is respawned by Warden autopilot alone, no Queen awake episode.
    - **(c)** a Drone that crashes three times walks the full escalation ladder (a Warden RESPAWN,
      two escalations) to the Queen, who REBINDs it to the manifest's own fallback slot and the
      goal finishes there -- the trail's own `queen.decided(REBIND)` names the fallback binding,
      and the goal is proven to have actually run on it.
    - **(d)** a Drone's question blocks the task until one `hive inbox answer` (CliRunner) resumes
      it. Ran on the manifest's own default heartbeat cadence: the second kernel fix-forward
      commit's own fix 3 (one answer-forwarding rule, called from both the Queen's tick and
      `run_goal`'s poll loop) closed the race a slower `heartbeat_interval_s` used to paper over;
      see the test's own docstring for the exact race and which fix closed it.
    - **(e)** a scripted checkpoint resumes and finishes from its own Handoff.
    - **(f)** a write outside scratch is rejected and never lands, and the goal still finishes.
    - **(g)** a failed `run_command` proposal is rolled back, the goal still finishes, and its
      whole Alarm chain reaches the trail: `alarm.raised` (kind `POSTCONDITION_FAILED`, raised by
      the Worker) and `alarm.handled` (action `RETRY`, the Warden's own policy dispatch one hop
      later). The second kernel fix-forward commit's own fix 1 (the Worker now records
      `alarm.raised`) and fix 2 (a pending Alarm is flushed before every terminal WorkerState
      transition) together close the gap the previous suite's own xfail here documented; see the
      module's own docstring for the exact mechanics.
    - **(h)** the left-as-found snapshot holds -- an unchanged tree, empty scratch, every started
      pid dead.

  Scripted through `tests.e2e.kernel_helpers.HaikuScript` over one `hivemind.llm.fake.
  FakeLLMProvider`, against a real Hive Stand lease and real SQLite (never `pump_until_done`'s own
  `FakeClock`, unlike `tests.unit.cli.test_compose`'s in-process counterpart of scenario (a)).
- `test_zero_grant.py` (`@pytest.mark.e2e`) -- `.claude/phase-4-handoff.md` section 4.2 item 1: a
  manifest whose `[forage.reserve]` claims every seat `[forage.map]` offers (patched onto a
  `builders.cli.fake_manifest` manifest through `tests.e2e.kernel_helpers.
  set_forage_reserve_seats`, since that builder exposes no reserve knob of its own) makes the
  Queen's first grant compute to `max_sub_bees = 0`; asserts `run_goal` returns FAILED with a
  readable reason well inside its own timeout, never a timeout itself, and that `forage.denied`
  and `task.failed` both land on the trail. Then the zero-grant fix, on a FakeClock pump: with the
  host's load faked (`os.getloadavg`) at nearly all of the Hive Stand's pinned cores, a goal waits
  PENDING behind one `forage.denied` with `deferred = true` and finishes once the load drops; one
  whose load never drops fails with the figures once `[forage] zero_grant_patience_s` passes.
- `test_honey_compounds.py` (`@pytest.mark.e2e`) -- roadmap phase 7's first two exit criteria:
  (a) a goal run twice on the Hive Stand at C2 -- the first Drone discovers a fact with a command,
  the Queen deposits the verified outcome, one ripening pass turns it into Honey, and the second
  run's `TaskAssign` carries it (`queen.honey_consulted` on the trail) and the second Drone makes
  fewer model calls; run at both capability levels, the 8,192-token "none" window included; (b)
  the same at C1 attaches nothing (Hive Stand Honey is C2 by provenance); (c) a Handoff written at
  t0 is deposited by the House Bee's sweep a day later, ripened, and found with full provenance.
- `test_honey_wire.py` (`@pytest.mark.e2e`) -- roadmap step 7.8: a real Queen tick handler, a real
  Warden running its own loop and a real `WorkerRuntime` using the real `recall`/`remember` tools,
  over memory transports and a seeded SQLite Honey Store. Proves a query's round trip hop by hop
  (Worker -> Warden -> Queen -> Warden -> Worker, correlation ids intact) and a two-chunk deposit
  reaching intake whole with the Worker's provenance.
- `test_hive_stand_identity.py` (`@pytest.mark.e2e`) -- phase 7 handoff item 4: two separate
  `HiveStandSource`/`HoneyAccess` builds from the same manifest and database file, driven one
  after the other (standing in for two separate `hive run` processes), lease the identical Hive
  Stand Cell id (`hivemind.cell.hive_stand_cell_id`, derived from `[hive] node_id`), and the
  second leaseholder reads back, at that same `cell:<id>` scope, a Honey row the first one
  deposited and ripened before releasing.

Every manifest this suite writes pins `[hive_stand.capacity] cores` (`builders.cli.fake_manifest`,
`tests.e2e.scripted_openai.write_manifest`), so the test host's own load average can never leave a
Drone's grant without a free core.
- `test_injection_on_hive_stand.py` (`@pytest.mark.e2e`) -- roadmap step 10.6b's invariant on a
  real run: a Drone's question is answered through `hive inbox answer` with seed payloads from the
  shipped pattern file, as a compromised device would answer it. The Worker's registry withholds
  the answer from the model, `guard.injection_suspected` lands on the real trail with the keyed
  hash (never the words), the steered outside-scratch write is capped and rejected, the goal still
  finishes, and the scanner's key is minted in the manifest's secrets dir on that first flag.
- `test_quarantine_on_hive_stand.py` (`@pytest.mark.e2e`) -- roadmap step 10.6c on a real run: a
  real Drone writes its first haiku and starts a real minute-long command; while it runs, the Queen
  orders a quarantine through `Queen.intervene` (the lever a Guard request pulls), suspect from the
  command's `capping.proposed`. The Hive Stand's Warden writes the checkpoint first, stops the
  Drone, and the command's process dies with it while the lease is still open; `warden.intervened`
  and `memory.tainted` land, the Handoff loader refuses the checkpoint, the task is PAUSED and a
  SECURITY Alarm reaches the human's inbox. Then the only way out: a resume from the still tainted
  checkpoint is refused at the `quarantine` point and the task held again with nothing spawned;
  once a judge clears it, the same resume lets a fresh Drone out from it and the goal finishes. Its
  manifest heartbeat is 0.5 s: the quarantine runs in one Warden tick (a 0.2 s kill grace
  included), which the builder's own 0.05 s cadence would read as an unreachable Cell.
- `test_night_veil_floors.py` (`@pytest.mark.e2e`) -- roadmap steps 10.3a-d through a running
  Queen: a human's goal request naming NIGHT_VEIL is planned on her own tick, placed on a fresh
  Virtual Cell (the real `CellLifecycle`/`QueenReadinessGate`/`LifecycleVirtualCellProvider` path
  over a fake backend and a fake attestation probe) whose minted bootstrap dials the hidden service
  through the Tor SOCKS proxy, and assigned bound to NIGHT_VEIL with `queen.placed` citing the
  request; a tier the planner set on its own is refused at placement
  (`guard.tier_floor.night_veil_initiation`) before any Cell is provisioned; a Night Veil goal
  whose plan needs the cloud metadata endpoint is refused before anything is persisted
  (`guard.tier_floor.night_veil_location`).
- `test_night_veil_link.py` (`@pytest.mark.e2e`) -- roadmap step 10.3a's exit bullet through a
  whole Hive (`build_hive`/`run_hive`): a human's Night Veil request is provisioned on the phase 5
  suite's container-spawning fake backend, whose real in-Cell Warden reads its tier, the onion
  Queen URL and the Tor proxy from its minted bootstrap and dials the real `CellListener` only
  through a `FakeSocksProxy` playing Tor (asked for the onion name, never resolved locally). The
  Cell announces NIGHT_VEIL, the task is bound to NIGHT_VEIL (a Cell announcing MEADOW, as every
  one did before, fails it) and the Drone's work succeeds. The attestation probe is the all-green
  fake: production's is fail-closed until a Queen-side CellSession exists. The task's
  `task.assigned` on the trail is its skeleton (no Cell, no tier), so the binding is read off the
  Brood Chamber's own `assign`.
- `test_night_veil_boundary.py` (`@pytest.mark.e2e`) -- codingrules section 12's Night Veil
  boundary through the same Hive, on every path a Night Veil Cell ends: a release after its task
  succeeds, a provision whose attestation fails after the Cell existed (the retry's second Cell
  then does the work), an Absconding that finds the Cell still working after its Queen stopped,
  and a restarted Queen whose reconcile finds the Cell gone. While the Cell works, its whole record
  (its Warden's shipped segments and the Queen's own detail about it) is in its ephemeral segment
  and none of it on the durable trail; after each end, every durable event about the Cell, its
  task, its grant or its Warden is a skeleton kind with its skeleton payload (or the purge's own
  `cell.purged`), no task words appear anywhere, no row from the Cell's node exists, the
  ephemeral store is empty, and the in-Cell trail was a `MemoryPheromoneTrail`. Every other store
  is read back too, and none names the work: no memory row (a Queen episode about it is seeded
  first), no word of its task in the Brood Chamber (a task still running when its Cell ends is
  cancelled), no Forage ledger or checkpoint row, and no snapshot image (one is left on a Docker
  daemon beside the Hive's backend: `FakeDockerClient` behind a `DockerCellBackend` with no room).
  The Absconding and restart scenarios hold the Worker after its first round of Capped writes
  (shipping the Cell's trail, as its next heartbeat would) so the Cell is still working when its
  Queen stops, and each purge still summarises that Capping: the restarted Queen's from the counts
  the first one checkpointed. A MEADOW Cell's whole local trail still merges into the durable trail
  exactly as recorded, title and all, with no purge. The Hive, its reads and its store checks live
  in `night_veil_hive/` (`rig`, `reads`, `stores`).
- `test_phase10_remote_laptop.py` (`@pytest.mark.e2e`) -- roadmap phase 10's second exit
  criterion over `hive serve`'s own composition (a real Queen, her Hive Stand Warden and a Drone
  over a scripted `FakeLLMProvider`, the Hive's SQLite file, the Entrance on a real loopback
  listener): the operator sets the password and mints an invite with `hive entrance`, a second
  config directory (the laptop) enrols with `hive remote enrol` and is approved, and a real `hive
  run --remote` process follows a goal to its end while the laptop's other terminal answers the
  Drone's question with `hive inbox --remote answer` (the task SUCCEEDED). Requests without
  credentials, with a stolen token signed by another key and replayed are refused, as is the laptop
  while pending, locked by five wrong passwords (unlocked on loopback) and revoked. The follower is
  a child process because `CliRunner` swaps the process's standard streams while a command runs.
- `test_mutual_tls.py` (`@pytest.mark.e2e`; skipped on a machine with no private IPv4 address) --
  mutual-TLS device certificates on real sockets (roadmap 10.5a/10.5d, ADR-0041): `hive serve`'s
  own composition in `lan` mode, its remote listener bound to this machine's own private address
  behind a throwaway authority's server certificate the laptop pins with `--ca-file` (no trust
  store or hosts file is touched). A laptop enrolled on loopback gets its certificate at
  approval, fetches it, moves to the remote listener and runs a goal with `hive run --remote` (the
  task SUCCEEDED); a device without a certificate is refused at the handshake; after `hive entrance
  revoke` the laptop's next handshake is refused while another device's still succeeds; a laptop
  enrolled `--offline` is registered with `hive entrance register`, imports the certificate
  `approve --certificate-out` wrote, and reads its inbox; and `vpn` with `mutual_tls = true`
  admits a device with its certificate the same way.
- `test_phase10_phone.py` (`@pytest.mark.e2e`) -- roadmap phase 10's third exit criterion: a phone
  that speaks only to the remote listener. `hive serve`'s own Hive (the scripted goal whose Drone
  asks one question) is served by `remote_serve.py`, which builds the Entrance exactly as `hive
  serve` does but under the serving rig's test-only vpn plan (public origin and passkey relying
  party `hive.example.ts.net`, the remote listener on a second loopback port, plain HTTP), since
  `hive serve` rightly refuses a vpn bind without an overlay address and TLS. Every push delivery
  goes to `push_network.py`'s recorder. The phone takes the invite code from the link the QR
  encodes (no QR decoder is locked; the QR shown is proven to be that link by encoding it again),
  redeems it with a `SoftPasskey`, is refused while pending and approved at the Hive Stand, logs in
  with passkey plus password, subscribes to Web Push, and decrypts the question's notice, its
  withdrawal and its goal's completion with its own key after answering; the approve route is a
  404 for it on the remote listener.
- `test_phase10_program_webhook.py` (`@pytest.mark.e2e`) -- roadmap phase 10's fourth exit
  criterion over `hive serve`'s composition: the document-only client (`landing_client/`) enrols
  an Ed25519 program approved with no `observe` capability and a one-dollar daily cap, registers a
  webhook, submits a goal, verifies the question's notice under the Hive key it pinned (for its
  subscription and no other, `X-Hive-Event-Id` its event id), answers it, and hears the withdrawal
  and the completion. The same key is refused `GET /v1/cells` (`capability_denied`, `observe`) and
  a goal above its cap (`step_up_required`, `over_daily_cap`, a pending id the console lists).
- `test_push_withdrawal.py` (`@pytest.mark.e2e`) -- roadmap step 10.5b's named test: a laptop's
  `hive run --remote` asks, a program answers the question it heard by signed webhook, and a
  phone's Web Push copy is withdrawn under the same Topic (decrypted with the phone's own key); the
  program's webhook hears the withdrawal too and the laptop's follow sees the goal end.
- `test_slow_provision.py` (`@pytest.mark.e2e`) -- a Virtual Cell provision slowed far past the
  manifest's liveness window (the container-spawning fake backend's `set_provision_delay`): the
  Hive Stand's Warden, heartbeating the whole time the Queen's tick is stalled, is never reported
  unreachable, and the Drone's model calls inside the Cell go through the Cell's own Fanner, so
  its `llm.call` rows reach the Queen's trail with the Cell's shipped segment.
- `test_cell_heartbeat_cadence.py` (`@pytest.mark.e2e`) -- a Virtual Cell whose in-Cell Warden
  heartbeats every 15 s (its own default, declared on every Heartbeat) outlives its task through
  Overwintering; the Queen goes on judging it for ten of the manifest's 0.3 s windows past its
  first Heartbeat and raises no `CELL_UNREACHABLE` about it: each Warden is judged by the cadence
  it declares, never below the manifest's. About twenty seconds, most of it that first 15 s.
- `test_virtual_cell_capacity.py` (`@pytest.mark.e2e`) -- the whole process reads a busy host
  (`os.getloadavg` faked at 3.9, as a container sharing a four-core Hive Stand's kernel would read
  it): a goal placed on a Virtual Cell still gets its bee and succeeds, because the Cell reports
  the reservation its bootstrap names (`HIVEMIND_RESERVATION`, from its `VirtualCellSpec`, no
  load) -- exactly the capacity the Queen placed it by -- instead of the host's figures.

## Budget

All sixteen parametrised cases (eight scenarios × two capability levels; nine test functions, one
of which -- scenario (a) -- has both an in-process and a `hive run`-CLI form) run in under twenty
seconds on this host (roadmap step 3.22's own exit criterion), typically well under it -- see
`--durations=10` below for the slowest individual cases on a given run. No case retries and none
is xfailed: the two kernel fix-forward commits closed every race this suite once had to work
around instead of assert through.

## What each scenario asserts

No case in `test_kernel_on_hive_stand.py` retries and none is xfailed; every one below runs once
per capability level and asserts real trail events (and, where named, filesystem or process state)
rather than only `report.succeeded`.

| Scenario | Trail events (and other state) asserted |
|---|---|
| (a) | `cell.leased`/`queen.planned`/`queen.assigned` in order; `capping.proposed`/`capped`/`applied`/`verified` in order; `task.succeeded` before `cell.released`; `forage.granted` and `worker.spawned` present (see the table below for what each means); scratch empty after release; the CLI form's own output also names `task.succeeded`. |
| (b) | `worker.spawned` at least twice (the respawn); `queen.awake` never appears. |
| (c) | `worker.failed` exactly 3 times; `worker.spawned` at least 4 times; `alarm.escalated` present; exactly one `queen.decided` with `action="REBIND"` naming `binding="local_worker"`; the goal actually ran on the fallback model id. |
| (d) | the task reaches `BLOCKED` before `hive inbox answer` is called; the goal succeeds once the CLI answers it. |
| (e) | `worker.handing_off` and `worker.resumed` both present; the `memory.checkpoint` event's own id resolves to a real, storable Handoff. |
| (f) | `capping.rejected` present; the file the rejected write targeted never exists on disk. |
| (g) | `capping.rolled_back` present; scratch holds exactly the three real haiku files (nothing the rolled-back command touched lingers); exactly one `alarm.raised` with payload `kind="POSTCONDITION_FAILED"`; exactly one `alarm.handled` with payload `action="RETRY"`. |
| (h) | every pid the lease started is dead; the Hive Stand's own tree outside the SQLite data dir is byte-for-byte unchanged; scratch is empty. |

## What the order assertions check

Scenario (a) asserts that certain trail event kinds appear, in a required relative order, as one
Hive runs a goal to completion. One line each, in the order they appear on a clean run:

| Kind | Meaning |
|---|---|
| `cell.leased` | The Warden acquired its Cell lease from the Hive Stand. |
| `queen.planned` | The Queen decomposed the goal into its task graph. |
| `queen.assigned` | The Queen placed a task with a Warden and a Cell. |
| `forage.granted` | The Warden's own Forage allocation (bindings, `max_sub_bees`) was recorded. |
| `worker.spawned` | A sub-bee (a Drone) was spawned to run the task. |
| `capping.proposed` | A Drone proposed an action through the Capping gate. |
| `capping.capped` | The proposal passed its pre-apply checks (schema, size cap). |
| `capping.applied` | The proposal's action was actually applied. |
| `capping.verified` | The proposal's declared postconditions held after applying. |
| `task.succeeded` | The task's Warden-verified outcome was recorded as SUCCEEDED. |
| `cell.released` | The Warden released its Cell lease, left exactly as found. |

Run just this suite with:

```
PYTHONIOENCODING=utf-8 uv run --frozen pytest -m "e2e" packages/hivemind/tests/e2e -q --durations=10
```
