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
- `test_model_server_on_hive_stand.py` (`@pytest.mark.e2e`) -- roadmap step 8.3 (ADR-0044) over
  real sockets and a real process: the manifest declares `[llm.providers.local]` (`openai_compat`,
  loopback, a free port) and `[llm.servers.local]`, whose argv is `sys.executable` plus
  `tests/e2e/scripted_openai.py --port {port}` (composed in the test; no server program name is in
  `src`), with every slot bound to `local`. `build_hive` composes the Hive Stand's Warden with the
  server table (the Hive Stand then reports `can_host_model`); `run_hive` starts it. Asserts: the
  Warden started the server after it held its lease and its pid is alive; the provider's own
  health probe is HEALTHY; the live Forage map holds the source with the Hive Stand's Cell as
  `host_cell_id`; `forage.source_added` is on the trail from both the Warden's side and the
  Queen's (payload `reporter`); the haiku goal succeeds and every `llm.call` names provider
  `local` (a Drone's among them); and once `run_hive` exits the pid is dead, the source is off the
  map and `forage.source_removed` (cause `LEASE_RELEASED`) is recorded.
- `offline/` (the offline proof, roadmap step 8.6 and 8.7, ADR-0045) -- phase 8's exit criteria run
  against `docs/manifests/offline.toml` itself: every case derives its manifest from that file
  (`offline/manifest.py`; only what a machine with no model server and no Whisper weights forces is
  changed, and `[llm] offline = true`, the Hive ids, the slot wiring and the `[forage.map]` grades
  stay), serves a scripted OpenAI-compatible model on a free loopback port, and runs under a
  no-egress guard (`offline/guard.py`: `socket.socket.connect`, `connect_ex` and
  `socket.create_connection` raise `AssertionError("egress to <addr>")` for any address off
  loopback and record every connect; each case ends by asserting nothing was refused and that at
  least one loopback connect happened, so the guard was live). Each case also asserts that every
  `llm.call` on the trail names a provider the registry's `runs_locally` accepts. The guard sees the
  test process's sockets; a child process (the Chromium of case 7) is outside it. The rig package:
  `offline/__init__.py` (the face), `conftest.py` (the `egress` fixture), `guard.py`, `manifest.py`
  (derivation and a small TOML writer), `live.py` (the same derivation for a real server, from the
  `HIVEMIND_LOCAL_*` environment, for `tests/evals/offline/`), `reads.py` (trail reads and the
  every-call-local judgement), `rig.py` (run a goal in process or through `hive run`), `cells.py`
  (the container-spawning fake backend with the in-Cell Warden's own provider registry kept) and
  `source.py` (the `src/` checksum and `git status --porcelain`). The cases:
    - `offline/test_offline_haiku.py` -- the three-haiku goal on the Hive Stand, in process through
      `run_goal` and through `hive run` (`CliRunner`, the trail read from the database afterwards).
    - `offline/test_offline_virtual_cell.py` -- the same goal placed in a Virtual Cell
      (`[virtual_cells] backend = "fake"`, `prefer = "virtual"`): the Drone's `worker.spawned` and
      `llm.call` events come from the Cell's own node, still on the loopback provider.
    - `offline/test_offline_ripening.py` -- one finding deposited and one ripening pass with RIPENER
      and EMBEDDER on the scripted server (`/v1/chat/completions` for the summary,
      `/v1/embeddings` for deterministic hashed bag-of-words vectors): the SUMMARY row names the
      scripted ripener model, every row is embedded, a paraphrased question finds it with vectors
      in the ranking.
    - `offline/test_offline_spoken_goal.py` -- a WAV clip with a planted byte run is transcribed
      through the Fanner's transcription gate by an `openai_compat` TRANSCRIBER on the scripted
      server's `/v1/audio/transcriptions`; the words are run as the goal; one `llm.call` on
      TRANSCRIBER carries the clip's audio seconds and the audio is on no trail payload.
    - `offline/test_offline_clustering.py` -- a real outage (the server answers 503 while switched
      off) mid-goal: the Drone's second reply is parked in flight, `hive cluster` is ordered, the
      Drone checkpoints at its next call, the task pauses (`memory.checkpoint` before
      `task.paused`), the server returns and `hive wake` resumes it from the Handoff, each file
      written once.
    - `offline/test_offline_src_unchanged.py` -- "a checksum of `src/` is identical between the two
      configurations", read as: the sha256 over every `packages/*/src/**/*.py` is the same before
      and after the haiku goal runs under the offline manifest and under `builders.cli.
      fake_manifest`'s fake provider, and `git status --porcelain` over those paths reads the same
      before and after (empty on a clean checkout, which is how CI runs it).
    - `offline/test_offline_vision.py` -- roadmap 8.7: the phase 6 browser-only login on the Hive
      Stand with a real Chromium and the scripted server as WORKER, twice, differing only in the
      provider's `vision` capability. Without it `browser_snapshot` (the accessibility tree) is
      offered and used, `browser_screenshot`/`see` are never offered or called, and no request
      carried an image part; with it a screenshot is offered, called and comes back as an image
      part. The server records only the shape of each request.
    - `offline/test_offline_guard.py` and `offline/test_offline_manifest.py` -- the proof's own
      tests: the guard refuses a remote address through all three entry points and lets loopback
      through; the derived manifest keeps what `offline.toml` promises.
  The real-model counterparts of these cases are `local_llm`-gated, in `tests/evals/offline/`.
  `scripted_openai.py` grew for this suite (an embeddings route, a ripener script, `Scenario.steps`
  and `plan_json`, and `Switches`: an outage, one parked call, and a recorder of request shapes
  only); its manifest template is `scripted_manifest.toml.tmpl`. Roadmap 11.9a added
  `Scenario.closing`, the message a Drone ends with (a diagnostic Drone's first line is
  `CAUSE: <cause>`), and `steps` as JSON objects in a `--scenario` file; a `Step.when` names the
  words a Drone's conversation must hold for the step to be its own, which is how one model server
  answers a Nuc's task Drone and the diagnostic Drone its Warden sends beside it.
- `test_hive_stand_diagnosis.py` (`@pytest.mark.e2e`) -- roadmap step 11.9a (ADR-0056), the Hive
  Stand's end of an active reconnection: `HiveStandDiagnosis.begin` creates its one task, a real
  Queen dispatches it to a real Warden on a Cell the Queen knows as the Hive Stand, that Warden
  spawns the real `Drone` role under the task's goal ceiling, a model scripted at the provider
  runs a command that writes its output to a file, writes `findings.md` and closes, the Warden
  accepts the file and the Queen completes the task with what the Drone said. Asserts the task
  SUCCEEDED with the closing text as its outcome and that the Guard denied nothing along the way.
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
- `test_honey_compounds.py` (`@pytest.mark.e2e`) -- roadmap phase 7's first two exit criteria: (a) a
  goal run twice on the Hive Stand at C2 -- the first Drone discovers a fact with a command, the
  Queen deposits the verified outcome, one ripening pass turns it into Honey, and the second run's
  `TaskAssign` carries it (`queen.honey_consulted` on the trail) and the second Drone makes fewer
  model calls; run at both capability levels, the 8,192-token "none" window included; (b) the same
  at C1 attaches nothing (Hive Stand Honey is C2 by provenance, and a Ripener that records no
  reading proposes no lowering); (c) a Handoff written at t0 is deposited by the House Bee's sweep a
  day later, ripened, and found with full provenance; (d) at the default C1 and on default ripening
  settings (the short outcome is read because only the Real Cell floor holds its label up), the
  Ripener reads the first outcome as C1 and the clearance judge approves the lowering proposal
  (ADR-0038), so the second run's `TaskAssign` carries it, with `honey.lowering_proposed` and
  `honey.label_lowered` (approver JUDGE) on the trail; (e) the same with the judge rejecting
  attaches nothing, and `hive honey review` shows the proposal REJECTED. `honey_runs.py` is not a
  test module: it holds the scripted two-run Hive (the plan, the Drone, the RIPENER's and both
  judges' replies) the cases share.
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
- `test_virtual_cells_propolis.py` (`@pytest.mark.e2e`) -- phase 11's exit criterion that on a
  Virtual Cell the Propolis checks run against the image, beside
  `test_virtual_cells_night_veil.py`: at the real `LifecycleVirtualCellProvider` a PROPOLIS Cell is
  ready only after a passed attestation (provisioned, attested, ready, in that order on the
  trail), is torn down and never ready after a red one (a running Tor, here), is refused when the
  Hive has no PROPOLIS probe (the composition root's own default), and the environment a backend
  renders for it is what the Cell's own configuration accepts. Through a running Queen over the
  same provider a PROPOLIS task is assigned to the very Cell that was attested, and a Cell that
  failed is torn down and never given a task. The rig is `builders.attested_cells`; the real
  container version of the checks is `tests/integration/test_docker_propolis.py`.
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
- `test_phase9_exit_criteria.py` (`@pytest.mark.e2e`) -- roadmap phase 9's first three exit
  criteria, each a whole Hive from `build_hive` with the real Quarantine Comb and a scripted
  `FakeLLMProvider` answering the planner, the Worker and the SCAFFOLDER. (1) A Forager (over the
  in-memory login site, `royal_jelly_forager.py`) asks for a line counter with `request_tool`; the
  Queen scaffolds, proves it in the dev subprocess sandbox, promotes and announces it
  (`tool.requested ... tool.announced` in order), and the same Forager calls it and finishes with
  its output. (2) The Hive Stand's Warden promotes a cell-scope tool, proved in a sandbox Virtual
  Cell whose own in-process Warden runs the Comb; the registry row is CELL scope for that Cell and a
  later Worker on another Cell is never offered it. (3) Three malicious scaffolds (`socket`,
  `subprocess`, an `open` outside the package) fail the static gate naming the construct, are
  rejected after the one retry, and leave no registry row.
- `test_phase9_exit_criteria_basket.py` (`@pytest.mark.e2e`) -- phase 9's fifth exit criterion: a
  tool promoted at hive scope reaches a Virtual Cell attached afterwards as a Basket blob (the
  Queen's `basket.offered`, the Cell's `basket.received`, resident bytes hashing to the registry's
  `package_sha256`); a blob deposited from the Hive Stand (a Real Cell, so C2) is refused by a
  Night Veil Cell (`CLEARANCE`) while a C0 blob is kept; a dataset never promoted gives a Worker no
  tool, a tool named after it is refused, and a `tool.promoted` whose package is not resident is
  refused as `tool.load_refused`. `royal_jelly_runs.py` holds the scripts, the manifest and `run_lab`
  the two files share. (The fourth criterion, the `local_llm` scaffold run, is
  `tests/evals/scaffold`.)
- `test_phase11_exit_*.py` (`e2e`, `integration`; each skips with no Docker daemon) -- phase 11's
  exit criteria that need only a Level 0 device, each a whole Hive (`swarm/bridge.py`: the real
  Queen, Wardens and trail, a scripted model, the swarm listener on the container network over
  `wss://`) with a container enrolled on it (see "The swarm's second machine"). `level0`: the
  device listed by `hive cells list` as a Real Cell, a Drone's work on it, the device left as
  found. `deadman`: the cable pulled mid-command, so the packet lets go on its own, the device's
  Warden raises the Alarm and the release arrives on return (about eighty seconds, nearly all of it
  the websocket keepalive). `watch`: an idle device watched, a planted CPU hog surfacing as the
  next Patrol's Alarm, no write on the device. `propolis`: a task held until the device's own
  tunnel exists (and a Night Veil goal never placed on it). `replay`: a captured `session.exec`
  sent again runs once, a frame with a broken signature closes the link. `swarm/drone.py` scripts
  the work a device is given, `swarm/snapshots.py` takes the process, file and writable-layer
  snapshots that show a device was left as found, `swarm/tunnel.py` plays the operator's VPN. A
  container reports the load of the machine under it, so `bridge_hive` gives the Drone a footprint
  of no cores and the Hive a slow beat; neither is the subject of these scenarios.
- `test_phase11_exit_nuc.py` (`e2e`, `integration`; skips with no Docker daemon) -- phase 11's
  second exit criterion, the one that needs a device whose Warden moves onto it: one run of about
  six minutes on one container, asserted twelve ways. A long task runs at Level 0; the operator's
  `hive swarm promote` moves the Warden onto the device and the task goes on from its Handoff; a
  second `promote` makes it a Nuc (capacity report with the declared server, ceilings, the order,
  a new source per model, the rung, the plan); a goal of two tasks is put to it, one whose Drone
  thinks on the device's own model and one whose bar only a shared source clears; `docker network
  disconnect` pulls the cable mid-task. The ordinary task ends on the device with its result in the
  outbox, the high-bar one waits with a `forage.request` queued, and after `connect` the segment is
  merged, the results arrive once each and nothing is recorded twice. Last, the Warden is ordered
  to load the Hive's second model (a new source and no request) and the device's own third one (a
  `guard.denied` at `slot_binding`). See "A device that is moved up the ladder".
- `test_phase11_exit_diagnose.py` (`e2e`, `integration`; skips with no Docker daemon) -- phase 11's
  last exit criterion, a Drone that looks into every lost link, in three cases that each have a
  container and a docker network of their own (`e2e.swarm.diagnose`, about two to four minutes each,
  the climb to a Nuc and the task that must outlive the cut being most of it). *Reversible:* a Nuc's
  `/etc/hosts` is given a wrong address for the only name it dials the Hive Stand by (`hive.test`),
  its packet's link and its Warden's are reset from the Hive Stand's end, and the story only reads:
  the Warden's own Drone looks the name up, finds the line and writes the right one back inside the
  device's `[supervision.reconnect_budget]`, `warden.diagnosed` says FIXED, the Warden reconnects
  and the task ends on its own bee with no operator command, no Guard act and no Queen task but the
  Hive Stand's own look. *Unfixable:* the cable is pulled (`docker network disconnect`); the Drone
  finds a machine with no network and says so inside the budget (DIAGNOSED, judged when the budget
  runs out), the Warden goes on to cluster fully when `max_offline_s` does (both to within a second:
  the Warden wakes at its own deadlines and does not wait for a beat), with its outbox holding the
  findings, and the Nectar arrives once the cable is back. *Hive Stand:* a colonized device running
  a task is unplugged; the Queen's hold makes the Hive Stand's look (a Drone pinned to W0's Cell),
  its outcome is deposited as Nectar, and that precedes her decision about the device when the grace
  ends. A device manifest carries its own `[supervision]` (`max_offline_s` and the budget), which is
  what lets the cases run in seconds instead of the shipped ten minutes.
- `test_phase11_exit_unnoticed.py` (`e2e`, `integration`; skips with no Docker daemon) -- the same
  exit criterion for the result the cut hides: a result a Nuc sends in the seconds after the cable
  is pulled, while its connection still looks alive (the websocket keepalive takes twenty to forty
  seconds to give up, and a frame sent in that time returns as sent and is lost). One run of about
  a minute and a half on one container climbed to a Nuc: a first task ends and is heard; a second
  is put, and as its Drone's one ten-second command begins the cable is pulled; the command ends and
  the Warden sends the result into the dead connection; when the keepalive gives up the Warden
  notices and the cable is put back. Asserted: the result was sent after the pull and before the
  loss was noticed and was never in the outbox; the Queen's door heard it once, after the cable
  returned; the task completed once and no bee ran it twice; the first task's result, sent again
  with the rest of that last stretch because the Warden could not know it had arrived, was dropped
  by the Queen's replay window inside the reconnection grace (heard at the callback, nothing on the
  trail), as were the releases the device's packet had sent in that stretch, when it had, and the
  result she did not hold was not; no frame id was heard twice and no event was recorded twice.
  Against the tree without the memory of what was sent the run fails at "the second task to end":
  the result is gone. See "A device that is moved up the ladder".
- `test_phase11_exit_exoskeleton.py` (`e2e`, `integration`; skips with no Docker daemon) -- roadmap
  step 11.11's second half, the X11 backends under a colonized device's own Warden: one run of about
  a minute on one container whose image carries an X server (`device_image(X11_PACKAGES)`, built on
  first use and shared with `tests/integration/test_exoskeleton_on_device.py`, which proves the
  first half, the Hive Stand driving the display through the packet). The operator's `hive swarm
  promote` moves the Warden onto the device; a goal whose ceiling names that Cell and a display, and
  whose one task needs a display, is put to the Queen; the Warden on the device starts Xvfb and
  openbox through its own session, the Drone clicks at a point and presses a key, and its model is
  parked before the report so that the test can look into the container with the display up (the
  desktop in the process table, the display's pointer where the Drone clicked). Asserted: the task
  was placed on the device and succeeded once; the attach and the detach are in the device Warden's
  own trail and the Hive Stand sent the packet no command after the move; the Warden opened one
  flight recording and no frame is on the Hive's trail; and afterwards no display process is left,
  the lease's scratch is gone and the operator's directories are as they were. Against the tree
  that wired the device's Warden with nothing the display still worked, and the run fails at the
  recording. See "A device that is moved up the ladder".
- `test_phase11_exit_watch_colonized.py` (`e2e`, `integration`; skips with no Docker daemon) --
  roadmap step 11.10 on a device whose Warden has moved onto it (Level 1), the same night as
  `test_phase11_exit_watch.py` told of a Level 0 device: one run of about four minutes on one
  container. The operator's `hive swarm promote` moves the Warden onto the device; it watches its
  own machine through a session that holds no lease and keeps what it sees in its Bee Bread on the
  device, on its fifteen-second beat (the manifest asks for a sample and a Patrol at every beat).
  A CPU hog planted on the device makes the next Patrol an ALARM while the operator has named no
  directory (the observations it covers hold no file); `hive swarm watch NODE /srv/app` then makes
  the Queen restart the Warden under the new list (the terms renewal), and a second hog's Patrol
  covers the log lines of that directory and not those of `/srv/elsewhere`. Asserted: the operator
  sees the Warden `on_cell`, COLONIZED and in WATCH; the idle device's Patrols are quiet and one a
  beat; each hog becomes exactly one `PATROL_ANOMALY` Alarm the Queen holds; the Warden started
  twice and leased once for each start (watching takes no lease); the model calls of the Patrols
  are in the Warden's own trail; the machine's words are nowhere on the Hive's trail; the Hive sent
  the packet no command, the lease's scratch is gone, the operator's directories are as they were
  and the container's layer gained nothing outside the packet's directory. See "A device that is
  moved up the ladder".

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

## The Observation Hive in a browser

`test_phase12_observation_hive.py` is phase 12's exit run: `hive serve`'s composition serves the built
React app (`packages/observation-web/dist`, linked into the manifest-relative path `hive serve` looks
in) and a headless Chromium, driven by `packages/observation-web/e2e/observation_run.mjs` through
`observation_browser.py`, joins by invite with a virtual passkey, is approved at the Hive Stand, logs
in, and checks the strip, fleet, Cell page, chat question and answer, thoughts, Attendant, Honey and
a 360 px pass, plus that the page never reloaded and wrote nowhere but the chat and the Entrance's own
routes. It needs Node, Playwright for Node and Chromium, and a `pnpm build`; it skips with the reason
when any is missing. Keep its screenshots with `HIVE_E2E_OUT=<dir>`; see
`packages/observation-web/e2e/README.md`.

## The swarm's second machine

`swarm/` is the rig for scenarios that need a device which is not the Hive Stand: a container with a
filesystem, a process table and a network namespace of its own, running the Pollen Packet installed
from wheels built from this tree, dialling a Hive Stand over `wss://` with a pinned self-signed
certificate (phase 11, `.claude/phase-11-handoff.md` section 5). It needs a Docker daemon (23 or
later) and `uv`; it is used like this:

```python
from e2e.swarm import Janitor, device_image, enrol_container, start_stand

image = device_image()  # builds on first use, then reuses the tag
janitor = Janitor()  # removes every container and network at close()
hive = await start_stand(tmp_path)  # the swarm listener, on the bridge, over wss
device = await enrol_container(hive, janitor, image)
device.container.processes()  # {pid: command}, the snapshot to compare
device.container.file_tree("/var/lib/pollen")  # {path: (kind, mode, size)}
network = janitor.network()  # cut a device off: network.disconnect(container)
```

`device_image()` builds a wheelhouse (`uv build` of the three packages, and the third-party wheels of
the lockfile for CPython 3.12 on x86-64 Linux, downloaded once into `~/.cache/hivemind-tests` and
reused until the lockfile changes), then the image from `swarm/device/Dockerfile`, tagged by a hash
of both: a cold run takes under a minute (nine seconds of downloads, half a minute of image), a run
after a change to a package under ten seconds (the package layer of the image is cached), and a run
with nothing changed only builds the wheels and finds the tag (about two seconds).
`HIVEMIND_TEST_DEVICE_IMAGE` names an image to use as it is. Images of older trees stay until they
are removed (each carries the label `hivemind.test=e2e-swarm`, so
`docker image prune --all --filter label=hivemind.test=e2e-swarm` clears the unused ones). Pair a
lease on the device with `builders.swarm.lease_device`. The proof that it all works is
`tests/integration/test_pollen_in_container.py`.

A scenario on this rig needs Docker, so it is marked `integration` as well as `e2e` (codingrules
14.2): `ci.yml`'s ten-minute e2e job selects `e2e and not integration` and leaves it out, and
`integration.yml` runs it nightly or by hand on a runner with a daemon and no such cap. Only a daemon
that runs Linux containers counts (`daemon_answers`), so on a Windows engine these scenarios skip.

### The single-file packet on a clean Debian 12

`test_phase11_exit_binary.py` is phase 11's "`pollen` installs on a clean Raspberry Pi OS" on the
x86-64 stand-in (the workflow's ARM64 leg is the other half). `swarm/binary/` builds the packet the
way `.github/workflows/pollen.yml` does (the workflow's three commands in `python:3.12-bookworm`,
the checkout copied in) and keeps the file under `~/.cache/hivemind-tests/pollen-binary/<key>/`,
the key being a hash of the sources of `waggle` and `pollen`, the lock, the build script and the
recipe: a change to `hivemind` does not rebuild it, a change to the packet does, and a run after
that finds the file (about a minute cold, a few seconds warm; `HIVEMIND_TEST_CACHE_DIR` moves the
cache, and old keys are safe to delete). Behind a proxy that only the host can reach the build runs
on the host network with the proxy and its certificate, as the device image does.

```python
from e2e.swarm import BRIDGE, Janitor, bridge_hive, build_binary, start_clean

binary = build_binary()  # the cached file, built first if its inputs are new
device = start_clean(Janitor(), {"hive.test": BRIDGE.gateway})  # debian:bookworm, no Python
device.install(binary)  # copy, then `install -m 0755` onto /usr/local/bin/pollen, as root
device.exec(["pollen", "version"])  # as `pi`, the account an operator works as; never root
```

The test enrols that device against a whole Hive on the bridge (`bridge_hive`: the real Queen, gate
and Level 0 Warden, `wss://` with a pinned certificate, a scripted Drone), shows it ONLINE and
reporting itself as Debian 12, places a goal on its Cell (a command, a file and a read back
through the `PollenSession`, the command running the packet itself once more), then stops the
packet, runs `pollen uninstall`, does the runbook's last two steps and compares every file on the
device with a snapshot taken before the binary arrived. `CleanDevice` calls block, so a scenario
that shares an event loop with the Stand calls them through `asyncio.to_thread`.

A container reads the load of the whole machine, and a device's free cores are its cores less that
load, so on a busy host the default half-core Drone found no room and its task waited (a deferred
`forage.denied`) until the load fell. `bridge_manifest` gives the Drone a footprint of no cores,
which leaves nothing for the load to decide; any scenario that puts a task on a device in a
container wants the same, unless it is about scheduling.

Every scenario that puts a whole Hive in front of a container builds it in one place, `swarm/
bridge.py` (`bridge_hive` for the Hive, `bridge_manifest` for its manifest, `BridgeTerms` for what
a scenario adds to it: `[swarm]` keys, a watch table, whole tables such as a Nuc's model server),
and scripts its model with `swarm/drone.py`. When a device is lost the Queen asks the Hive Stand's
own Warden to send a Drone to look into it (roadmap 11.9a), a task of the Hive's and not of the
scenario's: `Script` answers that Drone with a script of its own, so none of the scenario's
attempts or commands are spent on it. A device nobody saw go (enrolled a moment ago, its packet
not dialled yet) is not looked into.

### A device that is moved up the ladder

`swarm/colony/` is the rig for a device whose Warden moves onto it (Level 1) and then serves a model
of its own (Level 2, a Nuc), on top of the whole-Hive rig above:

- `models.py` is the text the two sides agree on. `hive_tables()` is what the Hive's manifest gains:
  the model server (`load_on_start = false`, so the Hive Stand never starts a copy), the two models
  it allows (`SMALL`, loaded at promotion, `EXTRA`) and a strong shared model that clears an exacting
  task's bar. `device_manifest()` is the device's own `warden.toml`: the shared provider (a scripted
  server on the Hive Stand's side of the cable), its own, every slot bound, the `worker` and `judge`
  slots falling back to the local model (a Capping review of a Drone's command asks the judge, and
  without a local one a sampled review failed a bee that was getting on with its own model), the
  server and a Forage map row for each model it could load, one more than the Hive allows (`ROGUE`).
  `shared_scenario` and `local_scenario` are the rounds a Drone makes on either side.
- `device.py` makes the container colonizable: `colonizable()` writes the manifest and the server's
  rounds, and returns the `DeviceOptions` that copy them and the scripted server's source into the
  container before it starts and run `pollen install --runtime-wheelhouse` between enrolling and
  running (the device image carries the wheelhouse). `shared_model()` serves the scripted model on
  the network's host address. `device_outbox()` and `device_events()` read what the Warden on the
  device has queued and recorded, with the runtime's own interpreter, which is the only Python there.
- `orders.py` taps the Queen's door (`tap`: every frame the device's Warden says and when it was
  heard, and every `swarm.nuc_promote` she sends with its answer) and sends the Warden an order of
  the scenario's own (`order`). `trail.py` answers the questions the criteria ask of the Hive's trail
  (what ran first, how many at once, what was recorded twice), and `waits.py` the bounded, named
  waits. `run.py` tells the story in order and returns the `NucRun` the test asserts over.
  `window.py` listens at the callback every refusal of the Queen's replay window passes through
  (`window_drops`), because a duplicate inside the reconnection grace leaves nothing on the trail;
  `unnoticed.py` tells the second story, a cable pulled under a task that ends before the loss is
  noticed, and returns the `UnnoticedRun` that `test_phase11_exit_unnoticed.py` asserts over.

`swarm/colony/desktop.py` is the Exoskeleton story on the same footing, with the device image's X11
variant: `run_desktop_scenario` colonizes the device, puts the desktop goal to the Queen
(`desktop_ceiling`), looks into the container with the display up (`_display_of`: the Xvfb command
line gives the cookie, the server's socket the display number, `xdotool getmouselocation` the
pointer) while the scripted model holds the Drone's third call (`Switches.hold_at`, released well
inside the provider's fifteen seconds), measures the device at rest before and after (`Rest`), and
returns the `DesktopRun` that `test_phase11_exit_exoskeleton.py` asserts over.
`tests/unit/swarm/test_desktop_rig.py` checks the parts that need no daemon.

`swarm/colony/watching.py` is the watch story on the same footing: `run_watch_scenario` enrols and
colonizes a container whose device manifest asks for a sample and a Patrol at every beat (the shared
scripted model alarms only for the process `Scenario.alarm_on` names, and only when a Patrol's facts
carry the Warden's own signal line for it), plants and kills the CPU hog twice around the operator's
`hive swarm watch`, reads the Patrols from the device Warden's own trail (`device_patrols`) and the
observations from its Bee Bread (`device_observations`: right after the ALARM Patrol that covered
them, because a quiet Patrol discards what it covered), and returns the `WatchRun` that
`test_phase11_exit_watch_colonized.py` asserts over. `tests/unit/swarm/test_watch_rig.py` checks
the parts that need no daemon, among them that the scripted model's restated wording is the
Warden's own and that the reader understands the store the Warden really writes.

`swarm/diagnose/` is the rig for the last exit criterion, on top of the two above: `scene` builds the
whole Hive and the enrolled device from a `Setup` (the two scripted models' rounds, the device
manifest's `[supervision]`, the Queen's grace) and hands a `Scene` to a story (`run_case`); `scripts`
has the Drones' rounds (a Nuc's server answers the task's Drone and the diagnostic one by the words
of their objectives, `Step.when`; the Hive Stand's look is a Drone of its own); `plant` puts a wrong
line in the container's `/etc/hosts`, resets the packet's link and the Warden's from the Hive Stand's
end and reads the name as the Drone does; `reads` joins the Nectar the Hive stored with the trail
event that recorded it; `reversible`, `unfixable` and `unplugged` tell the three cases and return
the record each test asserts over. `tests/unit/swarm/test_diagnose_rig.py` checks the parts that need
no daemon, the shell scripts included (run for real on a copy of the hosts file).

The real command, `hive swarm promote`, is how the operator asks for each rung; the Hive moves the
Warden on its next beat, and nothing in the rig does it by hand. The cable is `docker network
disconnect`: the container is not told, and its Warden finds out when the websocket keepalive gives up
(about forty-five seconds). The device's trail reaches the Hive's as it is recorded while the link is
up and as a signed segment after a cut, so the scenario reads the device's own file for what it must
know before the cable is back, and the Hive's trail for everything after.
