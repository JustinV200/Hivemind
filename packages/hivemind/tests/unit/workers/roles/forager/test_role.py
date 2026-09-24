"""Unit tests for hivemind.workers.roles.forager.Forager: the bounded see/act role.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/forager/role.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.forager.role for the module under test.
"""

from __future__ import annotations

import pytest
from builders.llm import make_bound, make_tool_call
from builders.workers import make_assignment, make_context, make_gui_context

from hivemind.cell import HoneyClearance
from hivemind.exoskeleton import Peripherals
from hivemind.exoskeleton.browser.fake import FIXTURE_ORIGIN, FakeBrowser, login_site
from hivemind.llm import FakeLLMProvider, text_response, tool_call_response
from hivemind.workers.roles.forager import Forager, ForagerRequiresExoskeletonError
from waggle.clock import FakeClock
from waggle.messages.task import WorkerRole


async def test_forager_happy_path_reads_the_login_page_and_deposits_it_as_nectar() -> None:
    clock = FakeClock()
    provider = FakeLLMProvider()
    browser = FakeBrowser(login_site(), clock)
    ctx = make_gui_context(
        Peripherals(browser=browser), clock=clock, bound=make_bound(provider=provider)
    )
    assignment = make_assignment(
        clock=clock,
        role=WorkerRole.FORAGER,
        objective="Look at the login page and report what fields it has.",
    )
    navigate = make_tool_call(
        id="call_1", name="browser_navigate", arguments={"url": f"{FIXTURE_ORIGIN}/login"}
    )
    read = make_tool_call(id="call_2", name="browser_read", arguments={})
    provider.script(
        tool_call_response(navigate),
        tool_call_response(read),
        text_response("The login page has a username field and a password field."),
    )

    outcome = await Forager().run(ctx, assignment, resume_from=None)

    assert outcome.claimed is True
    assert outcome.handoff is None
    # The browser_read landed as Nectar (hivemind.workers.roles.forager.nectar.deposit_nectar).
    entries = await ctx.memory.list_bee_bread_by_task(assignment.task_id, HoneyClearance.C2)
    assert len(entries) == 1


async def test_forager_refuses_without_an_attached_exoskeleton_and_never_calls_the_model() -> None:
    # make_context() leaves ctx.exoskeleton unset and gives the model nothing scripted: if the
    # refusal did not come first, the very next line would raise a *different* error (nothing
    # scripted on the FakeLLMProvider), so this also proves the model is never called.
    ctx = make_context()
    assignment = make_assignment(clock=ctx.clock, role=WorkerRole.FORAGER)

    with pytest.raises(ForagerRequiresExoskeletonError):
        await Forager().run(ctx, assignment, resume_from=None)


def test_forager_role_is_always_forager() -> None:
    assert Forager().role is WorkerRole.FORAGER
