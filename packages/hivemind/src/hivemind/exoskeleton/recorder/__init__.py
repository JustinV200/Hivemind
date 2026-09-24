"""Keep evidence of every GUI action: the flight recorder package.

While an Exoskeleton is attached, every GUI proposal the Capping gate handles is recorded (roadmap
step 6.6, ADR-0032): its steps with secrets redacted, the screen before and after, the page URL and
accessibility snapshot where a browser is attached, each postcondition and what was observed, the
terminal state and the rollback. `models` defines what is kept, `redact` scrubs it, `store` keeps
it (in memory here; `sqlite` in the Hive's database), and `recorder` builds it as the gate goes.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside the exoskeleton package.
    Driven by `hivemind.exoskeleton.surface`; read by judge review, playback export and the
    Observation Hive's read side. Calls into `hivemind.exoskeleton.frames`, `.errors`,
    `hivemind.supervision.capping` (Proposal) and waggle.

Key invariants:
    - No frame, recording or typed secret ever reaches a log or the trail from here.

See Also:
    - docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md.

Public API:
    - Evidence, RecordedAction, RecordedPostcondition, RecordingInfo, MAX_SNAPSHOT_CHARS (models).
    - MASK, scrub_text, scrub_url (redact).
    - RecordingStore, InMemoryRecordingStore, DEFAULT_LIST_LIMIT (store).
    - FlightRecorder, scrubbed_evidence, MAX_EXPECTED_CHARS (recorder).
"""

from hivemind.exoskeleton.recorder.models import (
    MAX_SNAPSHOT_CHARS,
    Evidence,
    RecordedAction,
    RecordedPostcondition,
    RecordingInfo,
)
from hivemind.exoskeleton.recorder.recorder import (
    MAX_EXPECTED_CHARS,
    FlightRecorder,
    scrubbed_evidence,
)
from hivemind.exoskeleton.recorder.redact import MASK, scrub_text, scrub_url
from hivemind.exoskeleton.recorder.store import (
    DEFAULT_LIST_LIMIT,
    InMemoryRecordingStore,
    RecordingStore,
)

__all__ = [
    "DEFAULT_LIST_LIMIT",
    "MASK",
    "MAX_EXPECTED_CHARS",
    "MAX_SNAPSHOT_CHARS",
    "Evidence",
    "FlightRecorder",
    "InMemoryRecordingStore",
    "RecordedAction",
    "RecordedPostcondition",
    "RecordingInfo",
    "RecordingStore",
    "scrub_text",
    "scrub_url",
    "scrubbed_evidence",
]
