"""Unit tests for hivemind.exoskeleton.recorder: FlightRecorder and InMemoryRecordingStore."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from builders.capping import make_action, make_proposal

from hivemind.exoskeleton.errors import RecordingNotFoundError
from hivemind.exoskeleton.frames import Frame, solid_png
from hivemind.exoskeleton.recorder import (
    MASK,
    FlightRecorder,
    InMemoryRecordingStore,
    RecordedPostcondition,
    RecordingInfo,
    scrubbed_evidence,
)
from hivemind.supervision.capping import ProposalState, RiskTier
from waggle.clock import FakeClock
from waggle.messages.capping import ActionKind, ElementTarget, GuiOp, GuiStep, RollbackMethod

_FILL = GuiStep(
    op=GuiOp.BROWSER_FILL, target=ElementTarget(label="Password"), text="hunter2", secret=True
)
_AT = datetime(2026, 9, 24, tzinfo=UTC)


def _info(cell: str = "cell_1", when: datetime = _AT) -> RecordingInfo:
    return RecordingInfo(
        recording_id=f"rec-{cell}-{when.minute}", cell_id=cell, clearance="C1", started_at=when
    )


async def test_an_action_is_recorded_with_its_secret_as_a_length_only() -> None:
    store, clock = InMemoryRecordingStore(), FakeClock()
    recorder = FlightRecorder(store, _info(), clock)
    await recorder.open()
    proposal = make_proposal(
        RiskTier.SCRATCH_WRITE, action=make_action(ActionKind.GUI, gui=(_FILL,), steps=())
    )
    frame = Frame.from_png(solid_png(2, 2, (0, 0, 0)), clock.now())

    recorder.begin(
        proposal, scrubbed_evidence(frame, "file:///x?token=abc", "textbox: hunter2", ("hunter2",))
    )
    terminal = proposal.model_copy(update={"state": ProposalState.ROLLED_BACK})
    held = (
        RecordedPostcondition(
            kind="URL_MATCHES", subject="page", expected="file:///y", has_held=False
        ),
    )
    await recorder.end(terminal, None, held, RollbackMethod.GUI_STATE)

    (action,) = await store.actions(recorder.recording_id)
    assert action.steps == ("BROWSER_FILL label='Password' with [redacted: 7 chars]",)
    # The typed step keeps its shape for a procedure export; its secret is the mask.
    assert action.gui == (_FILL.model_copy(update={"text": MASK}),)
    # Everything but the frame's pixels (never JSON: a dump can't leak a screenshot as text).
    assert "hunter2" not in str(action.model_dump(exclude={"before": {"frame"}}))
    assert action.before.url is not None and "abc" not in action.before.url
    assert action.before.snapshot == f"textbox: {MASK}"
    assert (action.state, action.rollback) == ("ROLLED_BACK", "GUI_STATE")


async def test_a_proposal_rejected_before_it_ran_is_still_recorded_without_evidence() -> None:
    store = InMemoryRecordingStore()
    recorder = FlightRecorder(store, _info(), FakeClock())
    await recorder.open()
    rejected = make_proposal(state=ProposalState.REJECTED)

    action = await recorder.end(rejected, None, (), None)

    assert action.before.frame is None and action.after is None
    assert action.state == "REJECTED"


async def test_the_store_lists_newest_first_filters_by_cell_and_purges_a_cell() -> None:
    store = InMemoryRecordingStore()
    older, newer = _info("a", _AT), _info("a", _AT.replace(minute=5))
    await store.open(older)
    await store.open(newer)
    await store.open(_info("b"))

    assert [i.recording_id for i in await store.recordings(cell_id="a")] == [
        newer.recording_id,
        older.recording_id,
    ]
    assert await store.purge_cell("a") == 2
    with pytest.raises(RecordingNotFoundError):
        await store.actions(older.recording_id)
    assert len(await store.recordings()) == 1
