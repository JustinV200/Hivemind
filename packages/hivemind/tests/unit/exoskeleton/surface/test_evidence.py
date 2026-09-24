"""Unit tests for hivemind.exoskeleton.surface.evidence: a recorded GUI action as judge evidence."""

from __future__ import annotations

from hivemind.exoskeleton.frames import Frame, solid_png
from hivemind.exoskeleton.recorder import Evidence, RecordedAction, RecordedPostcondition
from hivemind.exoskeleton.surface.evidence import SNAPSHOT_EXCERPT_CHARS, judge_evidence
from hivemind.supervision.capping import MAX_EVIDENCE_CHARS
from waggle.clock import FakeClock

_NOW = FakeClock().now()
_BEFORE_PNG = solid_png(2, 2, (255, 255, 255))
_AFTER_PNG = solid_png(2, 2, (0, 200, 0))


def _action(**fields: object) -> RecordedAction:
    values: dict[str, object] = {
        "proposal_id": "p1",
        "tier": "irreversible",
        "steps": ("browser_type role=textbox name='Card' text=<secret: 16 chars>",),
        "before": Evidence(
            frame=Frame.from_png(_BEFORE_PNG, _NOW), url="file:///pay", snapshot="- button 'Pay'"
        ),
        "after": Evidence(frame=Frame.from_png(_AFTER_PNG, _NOW), url="file:///paid"),
        "postconditions": (
            RecordedPostcondition(
                kind="URL_MATCHES", subject="page", expected="file:///paid*", has_held=True
            ),
            RecordedPostcondition(kind="ELEMENT_TEXT", subject="role=heading", expected="Paid"),
        ),
        "state": "VERIFIED",
        "started_at": _NOW,
        "finished_at": _NOW,
    }
    values.update(fields)
    return RecordedAction.model_validate(values)


def test_the_text_carries_steps_pages_and_every_postcondition_outcome() -> None:
    evidence = judge_evidence(_action())

    assert "Tier: irreversible; outcome: VERIFIED" in evidence.text
    assert "<secret: 16 chars>" in evidence.text  # The recorder's redaction, carried as is.
    assert "Before: url=file:///pay" in evidence.text and "- button 'Pay'" in evidence.text
    assert "After: url=file:///paid" in evidence.text
    assert "URL_MATCHES on 'page' expecting 'file:///paid*': held" in evidence.text
    assert "ELEMENT_TEXT on 'role=heading' expecting 'Paid': not checked" in evidence.text


def test_the_frames_are_before_then_after_and_named_in_the_text() -> None:
    evidence = judge_evidence(_action())

    assert evidence.frames == (_BEFORE_PNG, _AFTER_PNG)
    assert "Screens: before, after" in evidence.text


def test_a_proposal_never_applied_has_no_after_side() -> None:
    evidence = judge_evidence(_action(after=None, state="REJECTED"))

    assert evidence.frames == (_BEFORE_PNG,)
    assert "After: not applied" in evidence.text
    assert "Screens: before" in evidence.text


def test_a_long_page_is_excerpted_and_the_whole_text_bounded() -> None:
    page = "x" * 20_000
    evidence = judge_evidence(
        _action(before=Evidence(snapshot=page), after=Evidence(snapshot=page))
    )

    assert len(evidence.text) <= MAX_EVIDENCE_CHARS
    assert "x" * (SNAPSHOT_EXCERPT_CHARS + 1) not in evidence.text
    assert evidence.frames == ()
    assert "Screens: none captured" in evidence.text
