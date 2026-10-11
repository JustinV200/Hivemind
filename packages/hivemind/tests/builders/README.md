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
