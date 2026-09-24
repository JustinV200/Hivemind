"""Unit tests for hivemind.exoskeleton.rehearsal.rehearse: replaying a procedure (roadmap 6.7)."""

from __future__ import annotations

import pytest
from builders.rehearsal import login_recording

from hivemind.exoskeleton.attach import Peripherals
from hivemind.exoskeleton.browser.fake import LOGIN_PASSWORD, FakeBrowser, login_site
from hivemind.exoskeleton.errors import ProcedureError
from hivemind.exoskeleton.rehearsal import BrowserProcedure, export_procedure, rebase, rehearse
from waggle.clock import FakeClock
from waggle.messages.capping import GuiOp, GuiStep

_FIXTURE_COPY = "http://127.0.0.1:8000"  # Where a rehearsal runs: a local copy of the site.


def _procedure() -> BrowserProcedure:
    """The bee's login, exported from its recording and moved onto the fixture copy."""
    return rebase(export_procedure("fixture-login", "rec_1", login_recording()), _FIXTURE_COPY)


def _browser(clock: FakeClock) -> Peripherals:
    return Peripherals(browser=FakeBrowser(login_site(_FIXTURE_COPY), clock))


async def test_the_login_procedure_passes_on_the_fixture_copy() -> None:
    clock = FakeClock()

    report = await rehearse(_procedure(), _browser(clock), clock, {"password": LOGIN_PASSWORD}, 0.0)

    assert report.passed is True
    assert report.site == _FIXTURE_COPY
    assert [action.steps_applied for action in report.actions] == [1, 3]
    assert LOGIN_PASSWORD not in report.model_dump_json()  # Handed in, never reported.


async def test_a_wrong_secret_fails_the_action_that_should_have_logged_in() -> None:
    clock = FakeClock()

    report = await rehearse(_procedure(), _browser(clock), clock, {"password": "wrong"}, 0.0)

    assert report.passed is False
    failed = report.actions[-1]
    assert failed.failure is None  # Every step ran; the page simply never arrived.
    assert [pc.has_held for pc in failed.postconditions] == [False, False]


async def test_a_navigation_off_the_site_fails_instead_of_leaving() -> None:
    clock = FakeClock()
    procedure = _procedure()
    elsewhere = GuiStep(op=GuiOp.NAVIGATE, url="https://elsewhere.test/")
    first = procedure.actions[0].model_copy(update={"steps": (elsewhere,)})
    procedure = procedure.model_copy(update={"actions": (first, *procedure.actions[1:])})

    report = await rehearse(procedure, _browser(clock), clock, {"password": LOGIN_PASSWORD}, 0.0)

    assert report.passed is False
    assert len(report.actions) == 1  # It stopped there: the next action needs the login page.
    assert "navigates off the rehearsal site" in (report.actions[0].failure or "")


async def test_a_rehearsal_without_every_secret_does_not_start() -> None:
    clock = FakeClock()

    with pytest.raises(ProcedureError, match="password"):
        await rehearse(_procedure(), _browser(clock), clock, {}, 0.0)
