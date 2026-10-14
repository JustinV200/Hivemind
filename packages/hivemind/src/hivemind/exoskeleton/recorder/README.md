# hivemind.exoskeleton.recorder

The flight recorder (roadmap step 6.6, ADR-0032) keeps evidence, not a story: while an Exoskeleton
(a Cell's optional display, input, audio and browser) is attached, every GUI proposal the Capping
gate handles becomes one `RecordedAction` in the attach's recording. It holds the typed steps and
their one-line descriptions with secrets redacted, the screen before and after, the page URL and
accessibility snapshot on each side when a browser is attached, each declared postcondition with
whether it held and what was observed, the terminal state and the rollback method. Redaction
happens where the evidence is made, so everything downstream (the stores, playback, the judge) only
ever sees scrubbed text. Frames are PNG bytes; they never reach a log, the trail or a JSON body.

## Layout

| Module | What it holds |
|---|---|
| `models.py` | `Evidence`, `RecordedAction`, `RecordedPostcondition`, `RecordingInfo`: what is kept. |
| `redact.py` | `scrub_text`, `scrub_url`, `redact_step`, `MASK`: scrubbing at the source. |
| `recorder.py` | `FlightRecorder` (`open`, `begin`, `end`) and `scrubbed_evidence`, driven by the gate's GUI surface. |
| `store.py` | The `RecordingStore` protocol and `InMemoryRecordingStore` (tests, fakes, the Warden of a Virtual Cell or of a borrowed device), which may be given a `Retention` to forget stale recordings as a new one opens. |
| `sqlite.py` | `SqliteRecordingStore`: two tables of the Hive's database, frames as BLOB columns, plus the `prune_before` retention sweep. |
| `migrations/` | The numbered SQL series that creates those two tables. |
| `playback.py` | `render_html` (a self-contained page: frames inline as `data:` URIs, no script, a CSP that forbids loading anything) and `summarize` (`RecordingSummary`, the pixel-free JSON the Observation Hive reuses, roadmap step 12.4). |

## Where recordings live

- **Hive Stand Warden:** `SqliteRecordingStore` on the `[hive] db` file, opened by
  `hivemind.cli.compose.exoskeleton.open_hive_recordings`, which also runs the retention sweep
  (`[exoskeleton] recording_retention_days`, default 30) as the Hive starts. A recording is swept
  once nothing in it happened within the window, so a live attach is never pruned mid-recording.
- **In-Cell Warden (a Virtual Cell):** `InMemoryRecordingStore`. The Cell has no database file of
  its own, so its recordings live with the Cell and go when it is torn down; shipping them to the
  Queen's store is a later step.
- **Warden of a colonized device or a Nuc:** `InMemoryRecordingStore` too
  (`hivemind.cli.compose.exoskeleton.device_exoskeleton`, roadmap step 11.11), by choice and not for
  want of a file: it has the state directory's SQLite files, but they hold what must survive a
  restart, nothing on the device can play a recording back, and frames on the disk of a machine the
  Hive only borrows would be one more thing left there. A recording lives as long as the Warden's
  process, or until a day after its last action (`DEVICE_RECORDING_KEEP`): the store is given a
  `Retention` and forgets the stale ones as the next recording opens, so a Warden that runs for
  months holds a day's frames and not every one it ever made. A Bee Bread `RECORDING` entry, in
  the Warden's own memory store, outlives its recording and then names one that is gone.
- **Night Veil:** the recording store is registered as a Night Veil side channel
  (`SideChannels.flight_recordings`, attached by `hivemind.cli.compose.night_veil.
  attach_side_channels`), so a Night Veil Cell's recordings are purged with the Cell
  (codingrules section 12).
- A Bee Bread `RECORDING` entry references each recording by id; that is how an episode record
  reaches it until phase 7's Nectar intake exists.

`hive recordings list [--cell ID] [--json]`, `hive recordings show ID [--json]` and
`hive recordings export ID --out DIR` read the Hive's store. `show` prints frames only as their
size and short digest; `export` writes `<id>.html` and `<id>.json`.

## How to test this

- `tests/contracts/test_recording_store_contract.py` runs the same clauses over both stores: open is
  idempotent, actions keep their order, frames and typed steps round-trip byte for byte, unknown
  ids raise `RecordingNotFoundError`, listing is newest first with a Cell filter and a limit,
  `purge_cell` removes a Cell's recordings and actions, and the retention sweep (on every store that
  has one).
- `tests/unit/exoskeleton/recorder/` covers the recorder and redaction, the SQLite store's own
  storage promises against the raw tables (`test_sqlite.py`), and playback (`test_playback.py`).
- `tests/unit/cli/test_recordings.py` drives `hive recordings` against a real database file;
  `tests/unit/cli/compose/test_exoskeleton.py` covers the wiring, the retention sweep and the
  Night Veil purge.
