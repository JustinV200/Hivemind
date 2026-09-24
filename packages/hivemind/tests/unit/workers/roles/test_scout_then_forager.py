"""A Scout's report reaches a Forager's brief, and the Forager acts on it (roadmap 6.9, 6.10).

Fits into the Hive:
    Exercises hivemind.workers.roles.scout.Scout, hivemind.workers.roles.forager.Forager and
    hivemind.workers.roles.bounded_loop.prompt.brief_for together, at the Worker level: no Warden
    or Queen involved (their own dispatch of role and recon is out of this dispatch's scope).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.scout.role, .forager.role and .bounded_loop.prompt.
"""

from __future__ import annotations

from builders.llm import make_bound, make_tool_call
from builders.workers import make_assignment, make_gui_context

from hivemind.exoskeleton import Peripherals
from hivemind.exoskeleton.browser.fake import (
    FIXTURE_ORIGIN,
    LOGIN_HEADING,
    LOGIN_PASSWORD,
    LOGIN_USERNAME,
    FakeBrowser,
    login_site,
)
from hivemind.llm import FakeLLMProvider, TextPart, text_response, tool_call_response
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.roles.forager import Forager
from hivemind.workers.roles.scout import Scout
from waggle.clock import FakeClock
from waggle.messages.task import ScoutReport, WorkerRole

_LOGIN_URL = f"{FIXTURE_ORIGIN}/login"
_WELCOME_URL = f"{FIXTURE_ORIGIN}/welcome"


async def test_a_scouts_report_shows_in_the_foragers_brief_and_it_logs_in() -> None:
    clock = FakeClock()

    report = await _scout_the_login_page(clock)
    assert report.feasible is True

    outcome, sent_to_model, browser = await _log_in_with_recon(clock, report)

    assert outcome.claimed is True
    assert outcome.handoff is None
    # The brief the Forager's model actually saw carried the Scout's own findings.
    assert "<<<scout_findings>>>" in sent_to_model
    assert "username field" in sent_to_model
    assert _LOGIN_URL in sent_to_model
    # And it actually logged in on the fake site.
    assert await browser.url() == _WELCOME_URL


async def _scout_the_login_page(clock: FakeClock) -> ScoutReport:
    """Run a Scout over the fixture login page and return its filed report."""
    provider = FakeLLMProvider()
    ctx = make_gui_context(
        Peripherals(browser=FakeBrowser(login_site(), clock)),
        clock=clock,
        bound=make_bound(provider=provider),
    )
    assignment = make_assignment(
        clock=clock, role=WorkerRole.SCOUT, objective=f"Look at {_LOGIN_URL} and describe its form."
    )
    provider.script(
        tool_call_response(
            make_tool_call(
                name="report_findings",
                arguments={
                    "feasible": True,
                    "summary": "The login page has a username field and a password field.",
                    "targets": [_LOGIN_URL],
                    "suggested_steps": [
                        "Fill the Username field",
                        "Fill the Password field",
                        f"Click the {LOGIN_HEADING} button",
                    ],
                },
            )
        )
    )

    outcome = await Scout().run(ctx, assignment, resume_from=None)
    assert outcome.scout_report is not None
    return outcome.scout_report


def _script_login(provider: FakeLLMProvider) -> None:
    """Script the four browser steps that log in on the fixture site, then a closing reply."""
    provider.script(
        tool_call_response(
            make_tool_call(id="c1", name="browser_navigate", arguments={"url": _LOGIN_URL})
        ),
        tool_call_response(
            make_tool_call(
                id="c2",
                name="browser_fill",
                arguments={
                    "target": {"role": "textbox", "name": "Username"},
                    "text": LOGIN_USERNAME,
                },
            )
        ),
        tool_call_response(
            make_tool_call(
                id="c3",
                name="browser_fill",
                arguments={
                    "target": {"role": "textbox", "name": "Password"},
                    "text": LOGIN_PASSWORD,
                    "secret": True,
                },
            )
        ),
        tool_call_response(
            make_tool_call(
                id="c4",
                name="browser_click",
                arguments={
                    "target": {"role": "button", "name": LOGIN_HEADING},
                    "expect": {"url": _WELCOME_URL},
                },
            )
        ),
        text_response("Logged in."),
    )


async def _log_in_with_recon(
    clock: FakeClock, report: ScoutReport
) -> tuple[WorkerOutcome, str, FakeBrowser]:
    """Run a Forager whose TaskAssign.recon carries `report`, driving it to log in.

    Returns:
        The Forager's outcome, the full text of the first request its model actually received
        (to check the recon rendering), and the browser it drove (to check where it ended up).
    """
    provider = FakeLLMProvider()
    browser = FakeBrowser(login_site(), clock)
    ctx = make_gui_context(
        Peripherals(browser=browser), clock=clock, bound=make_bound(provider=provider)
    )
    assignment = make_assignment(
        clock=clock,
        role=WorkerRole.FORAGER,
        objective="Log in to the fixture site with the given credentials.",
        recon=(report,),
    )
    _script_login(provider)

    outcome = await Forager().run(ctx, assignment, resume_from=None)
    first_request = provider.calls[0]
    sent = "\n".join(
        part.text
        for message in first_request.messages
        for part in message.parts
        if isinstance(part, TextPart)
    )
    return outcome, sent, browser
