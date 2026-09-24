"""Build flight-recorder test data: frames, recording headers and recorded actions.

The flight recorder (roadmap step 6.6, ADR-0032) keeps one `RecordingInfo` per Exoskeleton attach
and one `RecordedAction` per GUI proposal, frames included. These builders give every recorder,
store, playback and CLI test the same small, realistic shapes with sensible defaults
(codingrules 14.5: builders, not fixtures with twenty fields): real decodable PNG frames from
`solid_png`, a browser login's two typed steps already through `redact_step` (the password's text
is the mask) with the descriptions the recorder derives from them, and a URL whose secret
parameter is already masked, exactly as the recorder stores them.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the recording store
    contract suite and the unit tests of `hivemind.exoskeleton.recorder` and `hive recordings`.

Key invariants:
    - Every timestamp is timezone-aware UTC and counts from `RECORDING_START`, so a test that
      needs "older" and "newer" recordings just adds a timedelta.
    - Nothing built here carries raw secret text: the text fields hold what the recorder would
      already have scrubbed.

See Also:
    - hivemind.exoskeleton.recorder.models for the shapes built here.
    - hivemind.exoskeleton.frames.solid_png for the frames.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hivemind.exoskeleton.frames import Frame, solid_png
from hivemind.exoskeleton.recorder import (
    Evidence,
    RecordedAction,
    RecordedPostcondition,
    RecordingInfo,
    redact_step,
)
from waggle.messages.capping import ElementTarget, GuiOp, GuiStep

RECORDING_START = datetime(2026, 9, 24, 9, 0, tzinfo=UTC)  # Every builder's default "now".
LOGIN_URL = "https://site.test/login?session=%5Bredacted%5D"  # Already masked, as stored.
HOME_URL = "https://site.test/home"  # Where a successful sign-in lands.
# A browser sign-in's two typed steps as the recorder keeps them: through redact_step, so the
# secret fill's text is the mask and the step stays marked secret.
LOGIN_GUI = (
    redact_step(
        GuiStep(
            op=GuiOp.BROWSER_FILL, target=ElementTarget(label="Password"), text="x", secret=True
        )
    ),
    GuiStep(op=GuiOp.BROWSER_CLICK, target=ElementTarget(role="button", name="Sign in")),
)
LOGIN_STEPS = tuple(step.describe() for step in LOGIN_GUI)  # Their one-line descriptions.

__all__ = [
    "HOME_URL",
    "LOGIN_GUI",
    "LOGIN_STEPS",
    "LOGIN_URL",
    "RECORDING_START",
    "make_frame",
    "make_recorded_action",
    "make_recording_info",
]


def make_frame(
    rgb: tuple[int, int, int] = (32, 64, 96),
    *,
    size: tuple[int, int] = (6, 4),
    at: datetime = RECORDING_START,
) -> Frame:
    """Build a real, decodable single-colour PNG frame.

    Args:
        rgb: Its colour; two frames of different colours have different bytes and digests.
        size: (width, height) in pixels.
        at: When it was captured.

    Returns:
        The Frame.
    """
    return Frame.from_png(solid_png(size[0], size[1], rgb), at)


def make_recording_info(
    recording_id: str = "rec_1",
    *,
    cell_id: str = "cell_a",
    started_at: datetime = RECORDING_START,
    task_id: str | None = "task_1",
    clearance: str = "C1",
) -> RecordingInfo:
    """Build a recording header.

    Args:
        recording_id: The recording's id.
        cell_id: The Cell the Exoskeleton was attached to.
        started_at: When the attach happened; list order is newest first by this.
        task_id: The task it was attached for, or None.
        clearance: The HoneyClearance value of what it saw.

    Returns:
        The RecordingInfo.
    """
    return RecordingInfo(
        recording_id=recording_id,
        cell_id=cell_id,
        task_id=task_id,
        clearance=clearance,
        started_at=started_at,
    )


def make_recorded_action(
    proposal_id: str = "prop_1",
    *,
    at: datetime = RECORDING_START,
    with_frames: bool = True,
    state: str = "VERIFIED",
) -> RecordedAction:
    """Build a recorded browser sign-in: two typed steps, both sides' evidence, one URL check.

    Args:
        proposal_id: The Capping proposal it records.
        at: When the before-evidence was taken; the after frame is a second later, the finish
            two seconds later.
        with_frames: False leaves both sides without a frame (a surface with no screen).
        state: The terminal ProposalState value.

    Returns:
        The RecordedAction, as the recorder would have stored it.
    """
    before_frame = make_frame((10, 20, 30), at=at) if with_frames else None
    after_frame = make_frame((200, 210, 220), at=at + timedelta(seconds=1)) if with_frames else None
    check = RecordedPostcondition(
        kind="URL_MATCHES", subject="page", expected=HOME_URL, has_held=True, observed=HOME_URL
    )
    return RecordedAction(
        proposal_id=proposal_id,
        tier="network_egress",
        steps=LOGIN_STEPS,
        gui=LOGIN_GUI,
        before=Evidence(frame=before_frame, url=LOGIN_URL, snapshot='button "Sign in"'),
        after=Evidence(frame=after_frame, url=HOME_URL),
        postconditions=(check,),
        state=state,
        rollback=None,
        started_at=at,
        finished_at=at + timedelta(seconds=2),
    )
