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

## `audio.py` (roadmap step 6.5a, the transcription subset 10.5f needs)

`silent_wav`, `make_clip`, `make_transcript`: build transcription fixtures on the fly. A clip is a
real 16-bit mono PCM WAV of silence written by the standard library's `wave` module, so no binary
fixture is committed and a test names the exact length it needs; `make_clip` passes it through
`AudioClip.from_upload`, the same door a device's upload takes. `marked_wav` plants a distinctive
byte run in a clip's samples, so a test can search every store, trail payload and log line for
the audio itself (`tests/e2e/test_voice_on_hive_serve.py`).

## `night_veil.py` (codingrules section 12, the Night Veil boundary)

`make_night_veil(durable, clock, identity)`: build the Night Veil boundary over a
`MemoryPheromoneTrail`, wired the way `hivemind.cli.compose.night_veil.build_night_veil` wires the
real one over the Hive's SQLite trail: fresh `EphemeralSegments`, the `VeiledTrail` a lifecycle or
Queen under test records through, and a `NightVeilTeardownPurge` whose recorder writes to the
durable trail past the boundary, with no side channels (none is registered in production today).
