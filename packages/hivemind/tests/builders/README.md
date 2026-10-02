# hivemind test-data builders

Small factory functions used across the other test trees to build valid test data without
repeating pydantic model boilerplate in every test: `make_task`, `make_cell_spec`, and similar.
Builders return real, validated models; they never bypass validation to save time.

Import them as `from builders.tasks import make_task` (this directory is on pytest's and mypy's
path; see the root `pyproject.toml`'s `pythonpath`/`mypy_path` comments for roadmap step 2.4).

## `tasks.py` (roadmap phase 2, brood_chamber)

`make_task_spec`, `make_outcome`, `make_task`, `make_question`, `make_answer`,
`make_graph_draft`: build `hivemind.brood_chamber`'s task and question models. Every builder takes
an optional `clock: waggle.clock.Clock` (default a fresh `FakeClock`) so minted ids and timestamps
are deterministic; `make_task(status=...)` fills in whatever placement, outcome or pending-question
fields that status requires, so any `TaskStatus` is valid on its own with no further overrides.

## `recordings.py` (roadmap step 6.6, the flight recorder)

`make_frame`, `make_recording_info`, `make_recorded_action`: a real decodable PNG `Frame`, a
recording header, and a recorded browser sign-in (two typed steps already through `redact_step`,
so the password fill's text is the mask, their descriptions, both sides' evidence, one held
`URL_MATCHES` check) exactly as `hivemind.exoskeleton.recorder` stores them.
Every timestamp counts from `RECORDING_START`, so "older" and "newer" are a `timedelta` away.

## `exoskeleton.py` (roadmap step 6.4, attach)

`ScriptedDesktop`, `desktop_session`: a `FakeSession` that answers attach's commands the way a
working (or deliberately broken) desktop Cell would; `drive`: run a coroutine that waits on a
`FakeClock`, advancing it one poll interval at a time; `StubLauncher`: a `BrowserLauncher` that
starts a fake browser process through the session.

## `hosting.py` (roadmap step 8.3, a model server on a Cell)

`make_hosting_rig`: a `ModelServerHost` over fakes -- a Cell that reports `can_host_model`, a
`FakeSession` (`script_start` decides whether the server "starts"), a `FakeLLMProvider` whose
`set_outage` decides whether its health probe passes, a live `ForageMap` and a `RegistryDoor`
recording every register and unregister; `make_hosting_warden`: a whole not-yet-started `Warden`
over the same; `make_server_config` and `vram_source`: a `ServerConfig` and a source with a VRAM
need; `drive`: run a coroutine that waits on a `FakeClock` (a health wait), advancing it as it
sleeps.

## `gui.py` (roadmap step 6.5, the gate's GUI seam)

`ScriptedSurface`: a `GuiSurface` whose steps apply, postconditions hold, restores succeed and
evidence exist as a test scripts them, recording the order the gate called it in.

## `workers.py` additions (roadmap step 6.5, the Exoskeleton's tools)

`make_gui_context`: a `WorkerContext` with an Exoskeleton attached over a real gate with a real
`ExoskeletonSurface` (settle time 0); `run_tool`: invoke one tool as the registry would;
`proposals_of`: the proposals a context's gate saw; `GUI_GRANTS`: the capabilities GUI tools need.

## `rehearsal.py` (roadmap step 6.7, rehearsal)

`recorded_action`: one `RecordedAction` exactly as the flight recorder keeps it (typed steps
through `redact_step`); `login_recording`: a whole recording of the fixture site's login, one
rolled-back mistake included.

## `honey.py` and `honey_wire.py` (roadmap phase 7, honey_store)

`honey.py` builds the Honey Store's own data and stores: `make_nectar_draft`, `make_honey_draft`,
`make_nectar`, `make_nectar_submission`, `make_nectar_deposit`/`make_deposit_chunks` (a chunked
Waggle deposit), `make_honey_identity`, `make_ripener_deps`, and `open_test_honey_store` /
`open_test_honey_store_with_trail` (a real temp-file SQLite store, the Pheromone Trail migrated
first). `make_nectar_draft`'s default content is fixed, and the store dedupes by content digest:
override `content` when a test needs distinct rows. `honey_wire.py` builds what crosses Waggle for
it: `make_honey_access` (a whole `HoneyAccess` over one store), `make_honey_link` (a `WardenLink`
for a given Cell), `make_honey_query`, `make_honey_hit`, `make_deposit_meta`, and `seed_finding`
(one ripened finding a query can find).

## `audio.py` (roadmap steps 6.5 and 10.5f, transcription)

`silence_pcm`, `tone_pcm`, `make_silence_clip`, `make_tone_clip`, `make_chunks`: generated
16-bit PCM clips (never recordings of anyone, ADR-0033) and their push-to-talk chunks;
`StandInWhisperModel`/`StandInLoader`: the in-process Whisper adapter's model seam, scripted.
`silent_wav` is a whole WAV file as a device uploads it, and `marked_wav` plants a distinctive byte
run in a clip's samples, so a test can search every store, trail payload and log line for the
audio itself (`tests/e2e/test_voice_on_hive_serve.py`).

## `night_veil.py` (codingrules section 12, the Night Veil boundary)

`make_night_veil(durable, clock, identity)`: build the Night Veil boundary over a
`MemoryPheromoneTrail`, wired the way `hivemind.cli.compose.night_veil.build_night_veil` wires the
real one over the Hive's SQLite trail: fresh `EphemeralSegments`, the `VeiledTrail` a lifecycle or
Queen under test records through, and a `NightVeilTeardownPurge` whose recorder writes to the
durable trail past the boundary, with no side channels (none is registered in production today).

## `warden_routing.py` (roadmap step 8.1, the Warden's routing step)

`routing_overrides`: the `make_warden_deps` overrides that wire a Warden for routing (a Forage map
over the given sources, `RoutingRules`, a chain of `[llm.slots]` rows and a `rebind` that resolves
each key through the real `hivemind.llm.slots.resolve_key`), so a spawn test gets a real
`BoundModel` chain in manifest order to compare the routed order against.

## `royal_jelly.py` (roadmap phase 9, spec and Comb Registry)

`make_tool_spec(**overrides)`: a valid HIVE-scope echo `ToolSpec` (`scope=ToolScope.CELL` mints a
Cell); `make_package_files(spec)` / `make_package(files, spec)`: the three-file tool (`tool.py`,
`test_tool.py`, `spec.json`) as a mapping or as deterministic zip bytes;
`make_passing_report(spec, package_sha256, size)` / `make_failing_report(..., failing_gate=)`: a
`QuarantineReport` that passed all six gates, or stopped at one; `make_offer(spec)`: a real package, its
passing report and the matching `PromotionOffer`. Anything a test needs invalid goes
through `model_copy(update=...)`, which skips validation on purpose.

## `observation.py` and `entrance/screens.py` (roadmap step 12.1, the Observation Hive's screens)

`observation.py` builds the inputs of the pure screen builders in `hivemind.observation`: `make_event`
(a trail event of any kind at a chosen instant), `make_llm_call` (a completed `llm.call` with its
usage), `make_cell_view`, `make_warden_view` and `make_sample` (one bee's telemetry), with `T0` and
`after(seconds)` for instants. `entrance/screens.py` builds a small Hive for the Entrance's screen
reads: `scene` (one attached Warden over the Queen's in-memory stores, with the reads the Entrance
would have, plus `submit`, `place`, `finish`, `record` and `write_wax` to populate it through the
real Brood Chamber, trail and memory), `heartbeat`, and the `Census`, `Lifecycle` and `StaticMap`
stand-ins for the Queen's Warden table, the Virtual Cell lifecycle and the Forage map.

## `swarm/` (roadmap phase 11, the Swarm's Hive side)

Everything a Swarm test needs, importable as `from builders.swarm import ...`. `parties.py` builds
the signing parties and registry rows: `make_hive_party`, `make_device_party` (real Ed25519 keys and
ids), `make_swarm_node`, `make_heartbeat`, `make_cell_ready`, `make_capacity_report`.
`enrolment.py` builds the ceremony's values: `make_endpoint`, `make_rules`, `make_swarm_invite`,
`make_enrol_request`, `make_enrol_envelope`, and `enrol_node` (puts a node into a registry the only
way one gets in, by redeeming an invite). `hive.py` is the Hive Stand's side over in-memory storage:
`make_hive_rig` (registry, trail, door, hub, recorder; `mint`, `events`, `kinds`, `listener`),
`HubLog` (a hub listener that keeps what it heard, with `wait_until`) and `WardenHandlerLog`.

Three ways to talk to the Hive's side. `rig.py`: `make_link_rig` starts a `DeviceLink` to a
`ScriptedDevice` over an in-memory transport (`make_source`, `open_rig_session` lease it and open a
`PollenSession`). `raw.py`: `make_raw_link` is the same link with the device end driven by hand,
frame by frame, for the tests of the link, exchange and hub. `dial.py`: `make_router_rig` serves a
`ConnectionRouter` over in-memory connections whose Hive end verifies with the real key ring, and
`dial(party)` returns the device's end. `client.py`: `DeviceClient` dials the real listener over a
real socket (TLS with a pin when asked) with the device's own key.

`device/` is the scripted Pollen Packet: it answers leases, sessions, execs, file transfers,
background processes, `swarm.observe` and node updates the way `docs/waggle/gateway-contract.md`
says, from a `DeviceScript` the test turns (per-program or per-command-line `ExecScript`s, refusals,
silences, release behaviour) and a virtual disk. It is test scripting only; the real Packet is
`packages/pollen`. `routes.py` writes the text of a device's routing table and DNS settings as the
Propolis commands print it (`linux_probes`, `macos_probes`); `tls.py` writes a throwaway
self-signed certificate (`make_tls_material`); `guards.py` has `RecordingGuard`, a `CommandGuard`
that allows everything except what a test denies.

A `DeviceScript` is a mutable dataclass that is read afresh on every request: a test that changes
one builds its own rather than editing a module-level script other tests share.

## `offline.py`, `offline_warden.py` (roadmap 11.9, a Warden cut off from the Queen)

`offline.py` is the link's side. `Wire` (from `make_wire`) wraps each message a Warden sends the
Queen (`result`, `progress`, `alarm`, `forage_request`, `question`, `cell_request`, `heartbeat`,
`cell_heartbeat`, `lease_released`, `trail_segment`) in an envelope addressed the way the Warden
addresses it. `AutoClock` is a FakeClock whose sleeps pass at once and are recorded, so a whole
backoff schedule runs in no time and the test reads it back. `ScriptedQueen` is a `QueenDialer`: it
hands out in-memory connections, keeps every frame the Warden puts on them, fails or holds back the
next dials on cue (`fail_dials`, `block_dials`) and drops or closes the current connection.
`LinkRig` puts a `ReconnectingQueenLink` over it with the reader a Warden's receive task is, and
`until` waits for a phase, a dial or a frame without a timer.

`offline_warden.py` is the Warden's side: `start_offline_scene` starts a real `Warden` on a device's
deps (`DeviceDeps` with an `Outage`, and a `RemoteDeps` over a `SwitchLink` the test flips for a
Warden that dials the Queen) with one task running `pausable`, a role that works until told to hand
off, writes a Handoff and stops, and, started again from it, records what it resumed from.
`OfflineScene.cut`, `restore` and `wait_paused` drive and observe the outage. A `Searching` (a
budget, the Queen's host, a way to make the Warden a Nuc, and optionally the real Drone role in place
of the script) makes it search for why its link went (roadmap 11.9a).

## `diagnose.py` (roadmap 11.9a, a cut-off Nuc's diagnostic Drone)

`start_nuc_scene(script)` starts that scene as a Nuc: ceilings from the Queen and a stand-in model
server serving the one source the Warden's `worker` slot row names, whose provider counts as local.
The Drone is a script: `says(text)` ends at once with `text` as its closing message, `hangs`
works until it is stopped, `raises` crashes, `says_when(event, text)` waits for a signal and
`in_turn(first, second)` gives each Drone of a search its own. `start_thinking_nuc_scene(provider)`
runs the real Drone role on a `FakeLLMProvider` the test scripts, for what only the real role
shows (the tools it holds, the words its model is given). `diagnosed`, `drones` and `nectar_text`
read what a search left on the trail, in the Warden's table and at the Queen's end.

## `borrowed.py` (roadmap 11.6a, a colonized device's Warden)

`make_kit(tmp_path, url, *, access_level, with_server)` writes what the Pollen Packet gives the
Warden it starts on a colonized device (the device's node key as a file of raw bytes, a complete
device manifest, the environment that names them) and returns a `BorrowedKit`: the `environ`, the
state directory, every id, the Queen's signer and the device's. `device_verifier` accepts frames
signed as the device's node and nothing else, so a scripted `WebSocketServer(Codec(signer=
kit.queen_signer, verifier=kit.device_verifier))` stands in for the swarm listener and rejects a
frame signed with another key. `device_manifest_text(with_server=...)` is the manifest alone;
with a server it also holds the `[forage.map]` row of the model that server loads and a
`local_worker` slot the worker slot falls back to, as a Nuc needs.

## `pollen_packet.py` (roadmap 11.6a, the packet's side of a colonized device)

`make_packet(tmp_path, *, url, pin, level)` writes what a Pollen Packet holds after it has enrolled:
the node key (with the packet's own `create_key`), the `PacketState` with the device manifest
recorded as an install setting, and the Queen's and the device's signers; it returns a `Packet`.
`install_runtime` makes the runtime environment a finished PREPARE leaves, out of this workspace's
own interpreter and its `hivemind-in-cell` console script (`warden_script()` finds it), so a Warden
the packet starts from it is the real one. Used by the two `contracts/test_pollen_warden_*` tests.

## `trail_faults.py` (a bee's runtime dying under its Warden)

`FailingTrail(clock, failing_kind, times=1)` is an in-memory Pheromone Trail that raises `OSError`
the first `times` it is asked to record one named kind of event (a full disk, say), and records
everything else as usual; `failures` counts the raises. Passed as a Worker's `trail` or as
`make_warden_deps(trail=...)`, it kills one bee's runtime on `worker.started` and lets the next one
live, which is how `unit/wardens/test_warden_runtime_death.py` and `unit/workers/runtime/
test_loop_paused.py` get a runtime that ends on an error it did not handle.

## `recall.py` (roadmap 11.9, a remote Warden's Handoff in the Queen's Honey Store)

`make_deposits(deps, clock, tmp_path)` opens a real Honey Store and returns a `Deposits`: the Queen's
`deps` with that store in them, and the ways a bee on a colonized device or in a Virtual Cell fills
the record, through the real `split_deposit` and `NectarIntake`. `deposit(site, handoff, age_s)` is
the bee's HANDOFF Nectar (a `Site` is its Warden, Cell and task), `hold` files a checkpoint in the
Queen's own memory, `taint` and `isolate` write what her trail would say, and `decided` reads her
`queen.decided` records. `Refusal` names the four things a recall refuses (tainted, written under a
standing isolation, above the task's clearance, a Night Veil Cell's) and `refused(site, case)`
plants each, so the three paths that resume a task from a Handoff (a device taken back, a Warden
renewed, Clustering's resume) are held to one list, each with nothing in her own memory first.
## `orphan_browsers.py` (phase 6 handoff 4.5 item 7)

`stop_orphaned_test_browsers`: kills the Chromium a killed earlier test run left behind. The
hivemind suite's `conftest.py` calls it once per session, before the first test. It reads Linux's
`/proc` only and finds nothing elsewhere, which matches where the real browser harnesses run. A
process is stopped only when its executable is Chromium-family, an argument names a harness
scratch directory (`/tmp/hm-...`), and no Python process is among its ancestors, so a concurrent
run's browsers (descendants of its live pytest) are never touched. `orphaned_test_browsers` lists
them without signalling anything.
