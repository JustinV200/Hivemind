"""Tests for hivemind.workers.roles.forager.nectar.deposit_nectar.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/forager/nectar.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.forager.nectar for the module under test.
"""

from __future__ import annotations

from builders.llm import make_tool_call
from builders.workers import make_assignment, make_gui_context

from hivemind.cell import HoneyClearance
from hivemind.exoskeleton import Peripherals
from hivemind.exoskeleton.browser.fake import FIXTURE_ORIGIN, FakeBrowser, login_site
from hivemind.llm import ImagePart
from hivemind.memory.bee_bread.entry import BeeBreadEntryKind
from hivemind.workers.roles.forager.nectar import deposit_nectar
from hivemind.workers.tools import ToolOutput
from waggle.clock import FakeClock
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.task import WorkerRole

_SECRET_TEXT = "the page shows password=SuperSecretValue123456 in a debug banner"  # noqa: S105 -- test text


async def test_a_browser_read_result_is_deposited_named_scrubbed_with_clearance_kept() -> None:
    clock = FakeClock()
    browser = FakeBrowser(login_site(), clock)
    await browser.navigate(f"{FIXTURE_ORIGIN}/login")
    ctx = make_gui_context(Peripherals(browser=browser), clock=clock)
    assignment = make_assignment(
        clock=clock, role=WorkerRole.FORAGER, clearance=WireHoneyClearance.C2
    )
    call = make_tool_call(name="browser_read", arguments={})

    await deposit_nectar(ctx, assignment, call, ToolOutput(text=_SECRET_TEXT))

    entries = await ctx.memory.list_bee_bread_by_task(assignment.task_id, HoneyClearance.C2)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.kind is BeeBreadEntryKind.TOOL_RESULT
    assert entry.clearance is HoneyClearance.C2  # Kept, never raised or lowered.
    assert entry.task_id == assignment.task_id
    payload = entry.payload or ""
    assert f"{FIXTURE_ORIGIN}/login" in payload  # The URL is named.
    assert "SuperSecretValue123456" not in payload  # The secret is scrubbed.
    assert "[redacted]" in payload
    assert entry.text is None  # Full-content entries carry payload, never text (module docs).


async def test_a_browser_snapshot_result_is_also_deposited() -> None:
    clock = FakeClock()
    browser = FakeBrowser(login_site(), clock)
    await browser.navigate(f"{FIXTURE_ORIGIN}/login")
    ctx = make_gui_context(Peripherals(browser=browser), clock=clock)
    assignment = make_assignment(clock=clock, role=WorkerRole.FORAGER)
    call = make_tool_call(name="browser_snapshot", arguments={})

    await deposit_nectar(ctx, assignment, call, ToolOutput(text="the page's own tree"))

    entries = await ctx.memory.list_bee_bread_by_task(assignment.task_id, HoneyClearance.C2)
    assert len(entries) == 1
    assert entries[0].kind is BeeBreadEntryKind.TOOL_RESULT


async def test_a_results_media_never_reaches_the_deposited_entry() -> None:
    # browser_screenshot/see never trigger a deposit at all (only the two structural reads do),
    # but a hostile or buggy tool could still hand this hook a ToolOutput with media attached;
    # deposit_nectar must still never read or store it.
    clock = FakeClock()
    browser = FakeBrowser(login_site(), clock)
    await browser.navigate(f"{FIXTURE_ORIGIN}/login")
    ctx = make_gui_context(Peripherals(browser=browser), clock=clock)
    assignment = make_assignment(clock=clock, role=WorkerRole.FORAGER)
    call = make_tool_call(name="browser_read", arguments={})
    frame = ImagePart(media_type="image/png", data_base64="aGVsbG8=")

    await deposit_nectar(ctx, assignment, call, ToolOutput(text="page text", media=(frame,)))

    entries = await ctx.memory.list_bee_bread_by_task(assignment.task_id, HoneyClearance.C2)
    assert len(entries) == 1
    payload = entries[0].payload or ""
    assert "aGVsbG8=" not in payload  # The frame's own bytes never entered the payload.


async def test_any_other_tool_call_deposits_nothing() -> None:
    clock = FakeClock()
    browser = FakeBrowser(login_site(), clock)
    ctx = make_gui_context(Peripherals(browser=browser), clock=clock)
    assignment = make_assignment(clock=clock, role=WorkerRole.FORAGER)
    call = make_tool_call(name="browser_navigate", arguments={"url": f"{FIXTURE_ORIGIN}/login"})

    await deposit_nectar(ctx, assignment, call, ToolOutput(text="ignored"))

    entries = await ctx.memory.list_bee_bread_by_task(assignment.task_id, HoneyClearance.C2)
    assert entries == ()


async def test_deposit_nectar_is_a_noop_without_an_attached_browser() -> None:
    # Defensive: unreachable in practice (browser_read/browser_snapshot could not have run at
    # all without a browser), but this hook must never crash the loop if it somehow is.
    ctx = make_gui_context(Peripherals())
    assignment = make_assignment(clock=ctx.clock, role=WorkerRole.FORAGER)
    call = make_tool_call(name="browser_read", arguments={})

    await deposit_nectar(ctx, assignment, call, ToolOutput(text="unreachable"))

    entries = await ctx.memory.list_bee_bread_by_task(assignment.task_id, HoneyClearance.C2)
    assert entries == ()
