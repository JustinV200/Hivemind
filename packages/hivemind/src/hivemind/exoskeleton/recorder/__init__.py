"""Keep evidence of every GUI action: the flight recorder package.

While an Exoskeleton is attached, every GUI proposal the Capping gate handles is recorded (roadmap
step 6.6, ADR-0032): its steps with secrets redacted, the screen before and after, the page URL and
accessibility snapshot where a browser is attached, each postcondition and what was observed, the
terminal state and the rollback. `models` defines what is kept, `redact` scrubs it, `store` keeps
it in memory and `sqlite` in two tables of the Hive's database (`migrations` holds their schema),
`recorder` builds it as the gate goes, and `playback` shows it back as a self-contained HTML page
and a pixel-free JSON summary.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside the exoskeleton package.
    Driven by `hivemind.exoskeleton.surface`; read by judge review, `hive recordings` and the
    Observation Hive's read side. Calls into `hivemind.common` (sqlite, migrations),
    `hivemind.exoskeleton.frames`, `.errors`, `hivemind.supervision.capping` (Proposal) and waggle.

Key invariants:
    - No frame, recording or typed secret ever reaches a log or the trail from here.

See Also:
    - docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md.
    - hivemind.exoskeleton.recorder's README.md for the layout and how to test it.

Public API:
    - Evidence, RecordedAction, RecordedPostcondition, RecordingInfo, MAX_SNAPSHOT_CHARS (models).
    - MASK, redact_step, scrub_text, scrub_url (redact).
    - RecordingStore, InMemoryRecordingStore, DEFAULT_LIST_LIMIT (store).
    - SqliteRecordingStore, apply_recording_migrations (sqlite): the durable store.
    - FlightRecorder, scrubbed_evidence, MAX_EXPECTED_CHARS (recorder).
    - render_html, summarize, RecordingSummary, ActionSummary, SideSummary, FrameRef,
      SUMMARY_VERSION, DIGEST_CHARS (playback): a recording played back.
"""

from hivemind.exoskeleton.recorder.models import (
    MAX_SNAPSHOT_CHARS,
    Evidence,
    RecordedAction,
    RecordedPostcondition,
    RecordingInfo,
)
from hivemind.exoskeleton.recorder.playback import (
    DIGEST_CHARS,
    SUMMARY_VERSION,
    ActionSummary,
    FrameRef,
    RecordingSummary,
    SideSummary,
    render_html,
    summarize,
)
from hivemind.exoskeleton.recorder.recorder import (
    MAX_EXPECTED_CHARS,
    FlightRecorder,
    scrubbed_evidence,
)
from hivemind.exoskeleton.recorder.redact import MASK, redact_step, scrub_text, scrub_url
from hivemind.exoskeleton.recorder.sqlite import SqliteRecordingStore, apply_recording_migrations
from hivemind.exoskeleton.recorder.store import (
    DEFAULT_LIST_LIMIT,
    InMemoryRecordingStore,
    RecordingStore,
)

__all__ = [
    "DEFAULT_LIST_LIMIT",
    "DIGEST_CHARS",
    "MASK",
    "MAX_EXPECTED_CHARS",
    "MAX_SNAPSHOT_CHARS",
    "SUMMARY_VERSION",
    "ActionSummary",
    "Evidence",
    "FlightRecorder",
    "FrameRef",
    "InMemoryRecordingStore",
    "RecordedAction",
    "RecordedPostcondition",
    "RecordingInfo",
    "RecordingStore",
    "RecordingSummary",
    "SideSummary",
    "SqliteRecordingStore",
    "apply_recording_migrations",
    "redact_step",
    "render_html",
    "scrub_text",
    "scrub_url",
    "scrubbed_evidence",
    "summarize",
]
