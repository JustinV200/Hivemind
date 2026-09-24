"""Unit tests for hivemind.exoskeleton.recorder.playback: the playback page and its summary.

The page must carry every frame, and only as an inline data: URI; load and run nothing; show
exactly the text the recorder kept (which it already redacted), escaped; and list every action in
order with its outcome. The summary must hold everything but pixels and survive a JSON round trip.
"""

from __future__ import annotations

import base64
import html
import re

import pytest
from builders.capping import make_action, make_proposal
from builders.recordings import (
    HOME_URL,
    LOGIN_URL,
    make_frame,
    make_recorded_action,
    make_recording_info,
)
from pydantic import ValidationError

from hivemind.exoskeleton.frames import Frame
from hivemind.exoskeleton.recorder import (
    Evidence,
    FlightRecorder,
    InMemoryRecordingStore,
    RecordedAction,
    RecordedPostcondition,
    RecordingSummary,
    render_html,
    scrubbed_evidence,
    summarize,
)
from hivemind.supervision.capping import ProposalState, RiskTier
from waggle.clock import FakeClock
from waggle.messages.capping import ActionKind, ElementTarget, GuiOp, GuiStep, RollbackMethod

_TYPED = "hunter2"  # What the bee typed into the password field; never on the page.
_FILL = GuiStep(
    op=GuiOp.BROWSER_FILL, target=ElementTarget(label="Password"), text=_TYPED, secret=True
)
_DATA_URI = re.compile(r'src="data:image/png;base64,([A-Za-z0-9+/=]+)"')


def _frames(actions: tuple[RecordedAction, ...]) -> list[Frame]:
    """Every frame the actions hold, in page order: each action's before, then its after."""
    sides = [side for a in actions for side in (a.before, a.after) if side is not None]
    return [side.frame for side in sides if side.frame is not None]


def test_the_page_carries_every_frame_once_and_only_as_a_data_uri() -> None:
    actions = (make_recorded_action("p_1"), make_recorded_action("p_2", with_frames=False))

    page = render_html(make_recording_info(), actions)

    expected = [base64.b64encode(frame.png).decode("ascii") for frame in _frames(actions)]
    assert _DATA_URI.findall(page) == expected
    assert all(page.count(data) == 1 for data in expected)
    assert page.count("<img ") == len(expected) == 2


def test_the_page_loads_nothing_and_runs_nothing() -> None:
    page = render_html(make_recording_info(), (make_recorded_action(),))

    assert "<script" not in page.lower()
    assert "<link" not in page.lower()
    # Every src or href is inline: nothing is fetched from anywhere when the page opens.
    assert re.search(r'(src|href)="(?!data:)', page) is None
    assert "default-src 'none'" in page


async def test_the_page_shows_exactly_the_redacted_text_the_recorder_kept() -> None:
    # Arrange: a real FlightRecorder records a password fill the way the gate's surface drives it,
    # with a page that echoes the secret into its URL and its accessibility tree.
    clock, store, info = FakeClock(), InMemoryRecordingStore(), make_recording_info()
    recorder = FlightRecorder(store, info, clock)
    await recorder.open()
    proposal = make_proposal(
        RiskTier.NETWORK_EGRESS, action=make_action(ActionKind.GUI, gui=(_FILL,), steps=())
    )
    leaky_url, leaky_tree = f"{LOGIN_URL}&token={_TYPED}", f'textbox "Password": {_TYPED}'
    recorder.begin(proposal, scrubbed_evidence(make_frame(), leaky_url, leaky_tree, (_TYPED,)))
    check = RecordedPostcondition(kind="URL_MATCHES", subject="page", expected=HOME_URL)
    terminal = proposal.model_copy(update={"state": ProposalState.ROLLED_BACK})
    after = scrubbed_evidence(make_frame((1, 1, 1)), HOME_URL, None, (_TYPED,))
    await recorder.end(terminal, after, (check,), RollbackMethod.GUI_STATE)
    (action,) = await store.actions(info.recording_id)

    page = render_html(info, (action,))

    assert _TYPED not in page
    assert action.before.url is not None and action.before.snapshot is not None
    held_text = (*action.steps, action.before.url, action.before.snapshot, HOME_URL, action.state)
    assert all(html.escape(text) in page for text in held_text)
    assert action.rollback == "GUI_STATE" and "GUI_STATE" in page


def test_recorded_markup_is_escaped_never_rendered() -> None:
    hostile = make_recorded_action().model_copy(
        update={"before": Evidence(url="https://x.test/?q=<b>", snapshot="<script>1</script>")}
    )

    page = render_html(make_recording_info(), (hostile,))

    assert "<script" not in page
    assert "&lt;script&gt;1&lt;/script&gt;" in page
    assert "https://x.test/?q=&lt;b&gt;" in page


def test_actions_appear_in_order_with_their_outcome_and_both_urls() -> None:
    first = make_recorded_action("p_first")
    second = make_recorded_action("p_second", state="ROLLED_BACK").model_copy(
        update={"rollback": "GUI_STATE"}
    )

    page = render_html(make_recording_info(), (first, second))

    assert page.index('id="action-1"') < page.index("p_first") < page.index('id="action-2"')
    assert page.index('id="action-2"') < page.index("p_second")
    assert "ROLLED_BACK" in page and "GUI_STATE" in page
    assert html.escape(LOGIN_URL) in page and html.escape(HOME_URL) in page


def test_a_failed_postcondition_is_marked_and_says_what_was_observed() -> None:
    failed = RecordedPostcondition(
        kind="ELEMENT_TEXT", subject="heading", expected="Welcome", has_held=False, observed="Oops"
    )
    action = make_recorded_action().model_copy(update={"postconditions": (failed,)})

    page = render_html(make_recording_info(), (action,))

    assert '<td class="failed">failed</td>' in page
    assert "Welcome" in page and "Oops" in page


def test_a_proposal_that_never_ran_and_an_empty_recording_say_so() -> None:
    never_ran = make_recorded_action(state="REJECTED").model_copy(
        update={"before": Evidence(), "after": None, "postconditions": ()}
    )

    rejected_page = render_html(make_recording_info(), (never_ran,))
    empty_page = render_html(make_recording_info(), ())

    assert "Never applied." in rejected_page and "No frame." in rejected_page
    assert "None declared." in rejected_page
    assert "No actions were recorded." in empty_page


def test_the_summary_names_frames_by_digest_and_holds_no_pixels() -> None:
    action = make_recorded_action()

    summary = summarize(make_recording_info(), (action,))

    (entry,) = summary.actions
    frame = action.before.frame
    assert frame is not None and entry.before.frame is not None
    assert base64.b64encode(frame.png).decode("ascii") not in summary.model_dump_json()
    assert (entry.number, entry.before.frame.sha256) == (1, frame.sha256)
    assert (entry.before.frame.width, entry.before.frame.height) == (frame.width, frame.height)
    assert entry.before.snapshot_chars == len('button "Sign in"')
    assert entry.after is not None and entry.after.url == HOME_URL
    assert (entry.steps, entry.postconditions) == (action.steps, action.postconditions)


def test_the_summary_round_trips_through_json() -> None:
    actions = (make_recorded_action(), make_recorded_action("p_2", with_frames=False))
    summary = summarize(make_recording_info(), actions)

    assert RecordingSummary.model_validate_json(summary.model_dump_json()) == summary


def test_the_summary_rejects_a_version_it_does_not_know() -> None:
    data = summarize(make_recording_info(), ()).model_dump(mode="json")
    data["version"] = 2

    with pytest.raises(ValidationError):
        RecordingSummary.model_validate(data)
