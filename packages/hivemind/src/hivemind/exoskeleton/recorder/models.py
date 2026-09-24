"""Define what the flight recorder keeps: Evidence, RecordedAction and the recording's header.

The flight recorder (roadmap step 6.6, ADR-0032) keeps evidence, not a story: for every GUI
proposal made while an Exoskeleton is attached, its steps with any secret redacted, the screen
before and after, the page's URL and accessibility snapshot before and after when a browser is
attached, each declared postcondition with what was observed, the proposal's terminal state and
how it was rolled back. `Evidence` is one side of that (before or after); `RecordedAction` is one
proposal; `RecordingInfo` is the header of one recording, which spans one attach. Frames stay PNG
bytes inside `Evidence`, whose repr never shows them (a `Frame`'s own repr hides them), so a
recording can be logged by id without leaking a pixel.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.recorder`. Built by `recorder.FlightRecorder`, stored by a
    `recorder.store.RecordingStore`, read by the judge review, the playback export and (phase 12)
    the Observation Hive. Calls into `hivemind.exoskeleton.frames` and pydantic only.

Key invariants:
    - Every text field here has already been through `recorder.redact`: step descriptions carry
      secrets as a length, URLs have credential-looking parameters masked, snapshots are scrubbed.
    - `repr(evidence)` never contains image bytes.

See Also:
    - docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md.
    - hivemind.exoskeleton.recorder.recorder for how these are built.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from hivemind.exoskeleton.frames import Frame
from waggle.messages.base import UtcDatetime

MAX_SNAPSHOT_CHARS = 20_000  # An accessibility snapshot past this is cut: evidence, not a mirror.

__all__ = [
    "MAX_SNAPSHOT_CHARS",
    "Evidence",
    "RecordedAction",
    "RecordedPostcondition",
    "RecordingInfo",
]

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class Evidence(BaseModel):
    """What the Cell showed on one side of an action: the screen, and the page when there is one."""

    model_config = _FROZEN

    frame: Frame | None = Field(
        default=None, description="The display (or the browser's viewport, without a display)."
    )
    url: str | None = Field(default=None, description="The page URL, credentials masked.")
    snapshot: str | None = Field(
        default=None,
        max_length=MAX_SNAPSHOT_CHARS,
        description="The page's accessibility tree, scrubbed and bounded.",
    )


class RecordedPostcondition(BaseModel):
    """One declared postcondition and what the gate's GUI surface observed for it."""

    model_config = _FROZEN

    kind: str = Field(description="The PostconditionKind value.")
    subject: str = Field(description="What it was about: a region, an element, the page.")
    expected: str | None = Field(default=None, description="What was expected, scrubbed.")
    has_held: bool | None = Field(
        default=None, description="Whether it held; None when the surface did not check it."
    )
    observed: str = Field(default="", description="What was observed; never a frame.")


class RecordedAction(BaseModel):
    """One GUI proposal as the flight recorder kept it."""

    model_config = _FROZEN

    proposal_id: str = Field(description="The Capping proposal this records.")
    tier: str = Field(description="The RiskTier value the proposal was capped at.")
    steps: tuple[str, ...] = Field(description="Each step's one-line description, redacted.")
    before: Evidence = Field(description="The Cell just before the steps ran.")
    after: Evidence | None = Field(default=None, description="The Cell once the gate was done.")
    postconditions: tuple[RecordedPostcondition, ...] = Field(default=())
    state: str = Field(description="The terminal ProposalState value.")
    rollback: str | None = Field(default=None, description="The RollbackMethod value, if any.")
    started_at: UtcDatetime = Field(description="When the before-evidence was taken.")
    finished_at: UtcDatetime = Field(description="When the proposal reached its terminal state.")


class RecordingInfo(BaseModel):
    """The header of one recording: which Cell, which task, when; its actions are stored apart."""

    model_config = _FROZEN

    recording_id: str = Field(description="This recording's own id.")
    cell_id: str = Field(description="The Cell the Exoskeleton was attached to.")
    task_id: str | None = Field(default=None, description="The task it was attached for.")
    clearance: str = Field(description="The HoneyClearance value of what it saw.")
    started_at: UtcDatetime = Field(description="When the Exoskeleton was attached.")
