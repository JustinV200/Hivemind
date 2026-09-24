"""Unit tests for hivemind.workers.tools.exoskeleton.act: reach tiers and the GUI proposal path.

Fits into the Hive:
    Mirrors src/hivemind/workers/tools/exoskeleton/act.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.tools.exoskeleton.act for the module under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.workers import make_assignment, make_context, make_gui_context, proposals_of

from hivemind.exoskeleton import DisplaySource, Peripherals, ScreenSize
from hivemind.exoskeleton.antennae import FakeAntennae
from hivemind.exoskeleton.browser.fake import FakeBrowser, login_site
from hivemind.supervision.capping import RiskTier
from hivemind.workers.tools.exoskeleton import (
    GuiAction,
    PeripheralMissingError,
    act,
    desktop_reach,
    page_reach,
)
from hivemind.workers.tools.exoskeleton.act import current_page_reach
from hivemind.workers.tools.registry import ToolInvocation
from waggle.clock import FakeClock
from waggle.messages.capping import GuiOp, GuiStep

_SECRET = "tr0ub4dor&3"  # noqa: S105 -- a probe value, never a real credential.
_SCRATCH = Path("/home/bee/scratch")  # The lease scratch every file URL here is measured against.


@pytest.mark.parametrize(
    ("url", "tier"),
    [
        ("about:blank", RiskTier.SCRATCH_WRITE),
        ("file:///home/bee/scratch/site/login.html", RiskTier.SCRATCH_WRITE),
        ("file://localhost/home/bee/scratch/site/login.html", RiskTier.SCRATCH_WRITE),
        ("http://127.0.0.1:8000/login", RiskTier.SCRATCH_WRITE),
        ("http://127.8.9.10/", RiskTier.SCRATCH_WRITE),
        ("https://localhost:8443/", RiskTier.SCRATCH_WRITE),
        ("http://app.localhost/", RiskTier.SCRATCH_WRITE),
        ("http://[::1]:8000/", RiskTier.SCRATCH_WRITE),
        ("https://fixture.test/login", RiskTier.NETWORK_EGRESS),
        ("http://10.0.0.7/", RiskTier.NETWORK_EGRESS),
        ("file:///home/bee/.ssh/id_rsa", RiskTier.OUTSIDE_SCRATCH_WRITE),
        ("file:///home/bee/scratch/../.ssh/id_rsa", RiskTier.OUTSIDE_SCRATCH_WRITE),
        ("file://fileserver/share/page.html", RiskTier.OUTSIDE_SCRATCH_WRITE),
        ("about:config", RiskTier.NETWORK_EGRESS),
        ("chrome://settings", RiskTier.NETWORK_EGRESS),
    ],
)
def test_page_reach_keeps_only_pages_in_the_lease_at_scratch_write(
    url: str, tier: RiskTier
) -> None:
    assert page_reach(url, _SCRATCH) is tier


@pytest.mark.parametrize(
    ("display", "tier"),
    [
        (DisplaySource.LEASE, RiskTier.SCRATCH_WRITE),
        (DisplaySource.RUNNING, RiskTier.DEVICE_COMMAND),
        (DisplaySource.NONE, RiskTier.DEVICE_COMMAND),
    ],
)
def test_desktop_reach_is_scratch_write_only_on_a_display_the_lease_started(
    display: DisplaySource, tier: RiskTier
) -> None:
    ctx = make_gui_context(Peripherals(), display=display)

    assert ctx.exoskeleton is not None
    assert desktop_reach(ctx.exoskeleton) is tier


async def test_a_page_whose_url_cannot_be_read_is_treated_as_off_the_cell() -> None:
    browser = FakeBrowser(login_site(), FakeClock())
    await browser.close()  # Every later read fails, as a crashed browser's would.

    assert await current_page_reach(browser, _SCRATCH) is RiskTier.NETWORK_EGRESS


async def test_act_proposes_one_gui_action_whose_summary_redacts_a_secret() -> None:
    ctx = make_gui_context(Peripherals(antennae=FakeAntennae(ScreenSize(100, 100))))
    step = GuiStep(op=GuiOp.TYPE, text=_SECRET, secret=True)
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment(ctx.clock))

    result = await act(invocation, GuiAction("type", (step,), RiskTier.SCRATCH_WRITE, {}))

    (proposal,) = await proposals_of(ctx)
    assert proposal.action.gui == (step,)
    assert _SECRET not in proposal.action.summary and "[redacted" in proposal.action.summary
    assert proposal.reason == "Worker tool type: 1 GUI step(s)"
    assert _SECRET not in result.text


async def test_act_refuses_a_task_with_no_exoskeleton_attached() -> None:
    ctx = make_context()
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment(ctx.clock))
    step = GuiStep(op=GuiOp.MOVE, x=1, y=1)

    with pytest.raises(PeripheralMissingError, match="no Exoskeleton is attached"):
        await act(invocation, GuiAction("move", (step,), RiskTier.SCRATCH_WRITE, {}))
