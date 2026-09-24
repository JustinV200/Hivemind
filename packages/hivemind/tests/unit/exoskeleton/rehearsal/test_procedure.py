"""Unit tests for hivemind.exoskeleton.rehearsal.procedure: export and rebase (roadmap 6.7)."""

from __future__ import annotations

import pytest
from builders.rehearsal import login_recording, recorded_action

from hivemind.exoskeleton.browser.fake import FIXTURE_ORIGIN
from hivemind.exoskeleton.errors import ProcedureError
from hivemind.exoskeleton.recorder import RecordedAction, RecordedPostcondition
from hivemind.exoskeleton.rehearsal import export_procedure, rebase
from waggle.messages.capping import ElementTarget, GuiOp, GuiStep
from waggle.messages.labels import PostconditionKind

_SITE = FIXTURE_ORIGIN
_OPEN = GuiStep(op=GuiOp.NAVIGATE, url=f"{_SITE}/login")
_SUBMIT = GuiStep(op=GuiOp.BROWSER_CLICK, target=ElementTarget(role="button", name="Log in"))
LOGIN_RECORDING = login_recording()


def test_only_verified_actions_are_exported_with_their_checks() -> None:
    procedure = export_procedure("fixture-login", "rec_1", LOGIN_RECORDING)

    assert procedure.origin == _SITE
    assert [len(action.steps) for action in procedure.actions] == [1, 3]
    assert [pc.kind for pc in procedure.actions[1].postconditions] == [
        PostconditionKind.URL_MATCHES,
        PostconditionKind.ELEMENT_TEXT,
    ]


def test_a_secret_becomes_a_named_slot_and_never_part_of_the_procedure() -> None:
    procedure = export_procedure("fixture-login", "rec_1", LOGIN_RECORDING)

    (slot,) = procedure.secrets
    assert (slot.name, slot.action, slot.step, slot.field) == ("password", 1, 1, "label='Password'")
    assert "honeycomb" not in procedure.model_dump_json()


@pytest.mark.parametrize(
    ("actions", "reason"),
    [
        ((recorded_action((_OPEN,), state="ROLLED_BACK"),), "no verified action"),
        ((recorded_action((_OPEN, GuiStep(op=GuiOp.CLICK, x=1, y=1))),), "a CLICK step"),
        (
            (
                recorded_action(
                    (_OPEN,), (RecordedPostcondition(kind="REGION_CHANGED", subject="0,0,9,9"),)
                ),
            ),
            "checked by REGION_CHANGED",
        ),
        ((recorded_action((_SUBMIT,)),), "first step must navigate"),
    ],
)
def test_what_cannot_be_replayed_is_refused(
    actions: tuple[RecordedAction, ...], reason: str
) -> None:
    with pytest.raises(ProcedureError, match=reason):
        export_procedure("fixture-login", "rec_1", actions)


def test_rebase_moves_the_procedure_onto_a_fixture_origin() -> None:
    procedure = export_procedure("fixture-login", "rec_1", LOGIN_RECORDING)

    moved = rebase(procedure, "http://127.0.0.1:8000/")

    assert moved.origin == "http://127.0.0.1:8000"
    assert moved.actions[0].steps[0].url == "http://127.0.0.1:8000/login"
    assert moved.actions[1].postconditions[0].expected == "http://127.0.0.1:8000/welcome*"
    assert moved.actions[1].postconditions[1].expected == "Welcome, alice"  # Not a URL.


@pytest.mark.parametrize("origin", ["file://", "http://127.0.0.1:8000/app"])
def test_rebase_refuses_what_is_not_a_bare_web_origin(origin: str) -> None:
    procedure = export_procedure("fixture-login", "rec_1", LOGIN_RECORDING)

    with pytest.raises(ProcedureError, match="http"):
        rebase(procedure, origin)
