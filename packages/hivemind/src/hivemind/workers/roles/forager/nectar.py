"""Deposit every successful browser_read/browser_snapshot result as Nectar, in Bee Bread.

Roadmap step 6.9: until phase 7's Honey Store exists, Nectar (raw material a Forager brings back,
named for what a real forager bee carries to the hive) is a Bee Bread entry -- the same stance
ADR-0032 takes for flight recordings. `deposit_nectar` is the Forager's `hivemind.workers.roles.
bounded_loop.profile.RoleProfile.on_tool_result` hook: it runs after every tool call that did not
fail, and does something only for `browser_read` and `browser_snapshot`, the two Exoskeleton reads
that hand back page content. The entry uses `BeeBreadEntryKind.TOOL_RESULT`
(`hivemind.memory.bee_bread.deposit.deposit_tool_result`, until now uncalled): a page read is
exactly what that kind already means ("an oversized tool result... in full"), so no new kind is
needed. The page's current URL, read fresh off the attached browser, is prepended so the entry
names where the text came from; the whole thing is then scrubbed for credential shapes
(`hivemind.exoskeleton.recorder.redact.scrub_text`) and capped, the same bound `browser_read` and
`browser_snapshot` already cut their own text to, before it is deposited. Only `output.text` is
ever read here: `output.media` (a screenshot's frame) is never touched, so a frame can never reach
Bee Bread through this hook.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.forager`. Set as `RoleProfile.
    on_tool_result` by `hivemind.workers.roles.forager.role.Forager.__init__`; run by
    `hivemind.workers.roles.bounded_loop.executor.LoopExecutor.execute` after a successful
    `browser_read`/`browser_snapshot` call. Calls into `hivemind.cell` (HoneyClearance),
    `hivemind.exoskeleton` (PeripheralError), `hivemind.exoskeleton.recorder.redact` (scrub_text),
    `hivemind.memory` (MemoryContext, deposit_tool_result), `hivemind.workers.context`,
    `hivemind.workers.tools.exoskeleton` (MAX_PAGE_READ_CHARS) and waggle only. Workers at Layer 4
    importing `hivemind.exoskeleton.recorder`'s public API, a Layer 3 module, is downward and
    allowed (codingrules section 4).

Key invariants:
    - Runs only for `browser_read` and `browser_snapshot`; every other tool call is a no-op here.
    - Never reads `ToolOutput.media`: a frame cannot reach Bee Bread through this hook.
    - The deposited entry's clearance is always this task's own (`assignment.clearance`), never
      raised or lowered.

See Also:
    - .claude/roadmap.md step 6.9 for "every successful browser_read or browser_snapshot result is
      deposited as Nectar."
    - docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md for the same
      "Bee Bread until phase 7" stance this hook follows for recordings.
    - hivemind.memory.bee_bread.deposit for deposit_tool_result, this module's one write path.
    - hivemind.exoskeleton.recorder.redact for scrub_text, this module's one scrubbing path.
"""

from __future__ import annotations

from hivemind.cell import HoneyClearance
from hivemind.exoskeleton import Browser, PeripheralError
from hivemind.exoskeleton.recorder.redact import scrub_text
from hivemind.llm import ToolCall
from hivemind.memory import MemoryContext, deposit_tool_result
from hivemind.workers.context import WorkerContext
from hivemind.workers.tools import ToolOutput
from hivemind.workers.tools.exoskeleton import MAX_PAGE_READ_CHARS
from waggle.messages.task import TaskAssign

# The two Exoskeleton reads that hand back page content; every other tool call is a no-op here.
_PAGE_READ_TOOLS = frozenset({"browser_read", "browser_snapshot"})
_UNKNOWN_URL = "unknown URL"  # When the current page's URL cannot itself be read.

__all__ = ["deposit_nectar"]


async def deposit_nectar(
    ctx: WorkerContext, assignment: TaskAssign, call: ToolCall, output: ToolOutput
) -> None:
    """Deposit `output`'s text as Nectar (a Bee Bread TOOL_RESULT entry) for a page read.

    Args:
        ctx: This attempt's WorkerContext; supplies the attached browser, the memory store to
            deposit into, and the identity and clock every deposit stamps.
        assignment: The task this attempt is working; supplies the deposited entry's task id and
            clearance.
        call: The tool call that just ran; only `browser_read` and `browser_snapshot` deposit
            anything.
        output: That call's own result; only `.text` is ever read (module docstring).
    """
    if call.name not in _PAGE_READ_TOOLS:
        return
    handle = ctx.exoskeleton
    if handle is None or handle.peripherals.browser is None:
        return  # Defensive: unreachable, since neither tool could have succeeded without one.
    url = await _current_url(handle.peripherals.browser)
    text = scrub_text(f"{url}\n\n{output.text}", MAX_PAGE_READ_CHARS)
    mem_ctx = MemoryContext(store=ctx.memory, identity=ctx.identity, clock=ctx.clock)
    await deposit_tool_result(
        text, assignment.task_id, HoneyClearance.from_wire(assignment.clearance), mem_ctx
    )


async def _current_url(browser: Browser) -> str:
    """Read the attached browser's current URL, or a placeholder when that read itself fails."""
    try:
        # External await: one round trip to the browser, bounded by its own timeout; the read
        # this hook follows already succeeded, so this is expected to be fast.
        return await browser.url()
    except PeripheralError:
        return _UNKNOWN_URL
