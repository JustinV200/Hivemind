"""Unit tests for hivemind.workers.tools.exoskeleton.browser: navigate, click, fill, press.

Every test logs in to (or tries to) the fixture login site served by the fake browser
(`hivemind.exoskeleton.browser.fake`), through `ToolRegistry.execute` and a real CappingGate whose
GUI surface is a real ExoskeletonSurface (builders.workers.make_gui_context): the steps the tools
propose are the ones the gate applies to the page, verifies and rolls back.

Fits into the Hive:
    Mirrors src/hivemind/workers/tools/exoskeleton/browser.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.tools.exoskeleton.browser for the module under test.
"""

from __future__ import annotations

from builders.workers import make_gui_context, proposals_of, run_tool

from hivemind.exoskeleton import Peripherals
from hivemind.exoskeleton.browser.fake import (
    FIXTURE_ORIGIN,
    LOGIN_PASSWORD,
    LOGIN_USERNAME,
    WELCOME_HEADING,
    FakeBrowser,
    login_site,
)
from hivemind.guard import CapabilitySet
from hivemind.llm import JsonObject
from hivemind.supervision.capping import Proposal, ProposalState, RiskTier
from hivemind.workers.context import WorkerContext
from hivemind.workers.tools.registry import ToolOutput
from waggle.clock import FakeClock
from waggle.messages.capping import ElementTarget, GuiOp, GuiStep
from waggle.messages.labels import PostconditionKind
from waggle.messages.supervision import AlarmKind

_LOOPBACK = "http://127.0.0.1:8000"  # The fixture site served on this machine: stays on the Cell.
_USERNAME: JsonObject = {"label": "Username"}
_PASSWORD: JsonObject = {"label": "Password"}
_LOG_IN: JsonObject = {"role": "button", "name": "Log in"}


class _Session:
    """The fake browser on the fixture login site, and a context driving it."""

    def __init__(self, origin: str = _LOOPBACK, grants: CapabilitySet | None = None) -> None:
        clock = FakeClock()
        self.origin = origin
        self.browser = FakeBrowser(login_site(origin), clock)
        peripherals = Peripherals(browser=self.browser)
        self.ctx: WorkerContext = (
            make_gui_context(peripherals, clock)
            if grants is None
            else make_gui_context(peripherals, clock, capabilities=grants)
        )

    async def call(self, name: str, arguments: JsonObject) -> ToolOutput:
        """Run one tool call as a Drone would."""
        return await run_tool(self.ctx, name, arguments)

    async def last_proposal(self) -> Proposal:
        """The newest proposal the gate has seen."""
        return (await proposals_of(self.ctx))[-1]

    async def fill_credentials(self, password: str = LOGIN_PASSWORD) -> None:
        """Open the login page and fill both fields, the password as a secret."""
        await self.call("browser_navigate", {"url": f"{self.origin}/login"})
        await self.call("browser_fill", {"target": _USERNAME, "text": LOGIN_USERNAME})
        await self.call("browser_fill", {"target": _PASSWORD, "text": password, "secret": True})


async def test_navigate_to_a_loopback_page_is_a_scratch_write_navigate_step() -> None:
    session = _Session()

    result = await session.call("browser_navigate", {"url": f"{_LOOPBACK}/login"})

    assert result.text.startswith("state=VERIFIED")
    proposal = await session.last_proposal()
    assert proposal.action.gui == (GuiStep(op=GuiOp.NAVIGATE, url=f"{_LOOPBACK}/login"),)
    assert proposal.risk_tier is RiskTier.SCRATCH_WRITE
    assert await session.browser.url() == f"{_LOOPBACK}/login"


async def test_navigate_to_a_remote_page_is_network_egress_and_needs_its_net_grant() -> None:
    granted = _Session(FIXTURE_ORIGIN)
    ungranted = _Session(FIXTURE_ORIGIN, CapabilitySet.parse("exoskeleton:browser"))

    allowed = await granted.call("browser_navigate", {"url": f"{FIXTURE_ORIGIN}/login"})
    refused = await ungranted.call("browser_navigate", {"url": f"{FIXTURE_ORIGIN}/login"})

    assert (await granted.last_proposal()).risk_tier is RiskTier.NETWORK_EGRESS
    assert allowed.text.startswith("state=VERIFIED")
    assert refused.text.startswith("state=REJECTED")
    assert "net:fixture.test" in refused.text


async def test_logging_in_verifies_the_expected_url_and_never_echoes_the_password() -> None:
    session = _Session()
    await session.fill_credentials()

    result = await session.call(
        "browser_click", {"target": _LOG_IN, "expect": {"url": f"{_LOOPBACK}/welcome*"}}
    )

    assert result.text.startswith("state=VERIFIED")
    proposals = await proposals_of(session.ctx)
    fill = proposals[2].action.gui[0]
    assert (fill.op, fill.secret) == (GuiOp.BROWSER_FILL, True)
    assert all(LOGIN_PASSWORD not in proposal.action.summary for proposal in proposals)
    assert LOGIN_PASSWORD not in result.text
    click = proposals[-1]
    assert click.action.gui == (GuiStep(op=GuiOp.BROWSER_CLICK, target=ElementTarget(**_LOG_IN)),)
    assert click.postconditions[0].kind is PostconditionKind.URL_MATCHES


async def test_an_element_expectation_is_element_text_on_the_targets_subject() -> None:
    session = _Session()
    await session.fill_credentials()
    expect: JsonObject = {"element": {"role": "heading"}, "text": "Welcome"}

    result = await session.call("browser_click", {"target": _LOG_IN, "expect": expect})

    (postcondition,) = (await session.last_proposal()).postconditions
    assert postcondition.kind is PostconditionKind.ELEMENT_TEXT
    assert (postcondition.subject, postcondition.expected) == ("role=heading", "Welcome")
    assert result.text.startswith("state=VERIFIED")


async def test_a_failed_expectation_rolls_the_page_back_and_alarms_at_once() -> None:
    # Arrange: a correct login, but an expectation the page will never meet.
    session = _Session()
    await session.fill_credentials()
    expect: JsonObject = {"url": f"{_LOOPBACK}/somewhere-else"}

    result = await session.call("browser_click", {"target": _LOG_IN, "expect": expect})

    assert result.is_error and result.text.startswith("state=ROLLED_BACK")
    assert (await session.last_proposal()).state is ProposalState.ROLLED_BACK
    assert await session.browser.url() == f"{_LOOPBACK}/login"  # The login was undone.
    (alarm,) = session.ctx.telemetry.take_pending_alarms()
    assert alarm.kind is AlarmKind.POSTCONDITION_FAILED
    assert LOGIN_PASSWORD not in alarm.detail


async def test_a_click_on_a_missing_element_fails_its_step_and_alarms_at_once() -> None:
    session = _Session()
    await session.call("browser_navigate", {"url": f"{_LOOPBACK}/login"})

    result = await session.call("browser_click", {"target": {"role": "button", "name": "Nope"}})

    assert result.text.startswith("state=ROLLED_BACK")
    assert "no element matches" in result.text
    assert len(session.ctx.telemetry.take_pending_alarms()) == 1


async def test_actions_on_a_remote_page_take_the_network_egress_tier() -> None:
    session = _Session(FIXTURE_ORIGIN)
    await session.call("browser_navigate", {"url": f"{FIXTURE_ORIGIN}/login"})

    await session.call("browser_fill", {"target": _USERNAME, "text": LOGIN_USERNAME})

    assert (await session.last_proposal()).risk_tier is RiskTier.NETWORK_EGRESS


async def test_press_carries_its_chord_and_target() -> None:
    session = _Session()
    await session.fill_credentials()

    result = await session.call("browser_press", {"keys": "Enter", "target": _PASSWORD})

    step = (await session.last_proposal()).action.gui[0]
    assert (step.op, step.keys, step.target) == (
        GuiOp.BROWSER_PRESS,
        "Enter",
        ElementTarget(**_PASSWORD),
    )
    assert result.text.startswith("state=VERIFIED")
    assert await session.browser.element_text(ElementTarget(role="heading")) == WELCOME_HEADING


async def test_a_target_naming_two_ways_is_refused_before_anything_is_proposed() -> None:
    session = _Session()

    result = await session.call("browser_click", {"target": {"label": "a", "text": "b"}})

    assert result.text.startswith("invalid argument: target")
    assert "exactly one way" in result.text
    assert await proposals_of(session.ctx) == []


async def test_a_javascript_url_is_refused_before_anything_is_proposed() -> None:
    session = _Session()

    result = await session.call("browser_navigate", {"url": "javascript:alert(1)"})

    assert result.text.startswith("invalid argument: url")
    assert await proposals_of(session.ctx) == []
