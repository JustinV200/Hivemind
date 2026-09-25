"""Screen outside text before a Worker's model reads it: a tool's result, or a Honey hit.

A tool's result is outside text: a web page from `http_request`, a command's output or a file's
bytes from the Cell's own session (`run_command`, `read_file`), a human's answer through `ask`.
Roadmap step 10.6b runs the untrusted-content scanner (`hivemind.guard.scanner`) on every one of
them, in `ToolRegistry.execute`, before the text becomes the model's next tool-result part.
`screen_tool_result` builds the scan site from the Worker's own context (its id as the consuming
bee, its Cell's Comb Shield tier, its own capability set as the task's targets, its own trail), and
then applies the verdict: a PASS result is returned exactly as the tool produced it, up to the
scanner's bound (the tool-result part is already its own channel, so fencing every clean result
would only change every prompt),
a LABEL result is fenced under a harder label with a warning naming what fired, and a DROP result
is replaced by a withheld notice carrying the keyed hash, so the injected text never reaches the
model, the attempt's call records, or a Handoff built from them. A flag never stops the bee: it
reads the verdict's consequence and carries on, and at most asks. `screen_honey_hits` does the
same for the Honey hits a task's assignment carries (roadmap steps 7.9 and 10.6b, the
`ScanSource.HONEY_HIT` seam): each excerpt is scanned before assembly and wrapped with its verdict
as a `RetrievedItem`, which `hivemind.memory.assemble` renders under that verdict.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools`. Called by
    `hivemind.workers.tools.registry.ToolRegistry.execute` for every tool that ran, and by
    `hivemind.workers.roles.bounded_loop.prompt` for a task's Honey hits. Calls into
    `hivemind.cell` (CellIdentity), `hivemind.guard.scanner`, `hivemind.memory` (render_untrusted,
    UntrustedText, RetrievedItem), `hivemind.workers.context` and
    `hivemind.workers.tools.registry` (WorkerContext, ToolInvocation, under TYPE_CHECKING) and
    waggle only.

Key invariants:
    - Every result a tool returns is scanned before any model can read it; only the registry's own
      refusal and validation messages (the Hive's words, not outside text) skip the scanner.
    - Every Honey hit a Worker is handed is scanned before assembly; none crosses unscanned.
    - A DROP verdict's text is never returned, and no verdict's text past the scanner's bound is.

See Also:
    - hivemind.guard.scanner.scanner for ContentScanner.scan.
    - hivemind.memory.hot_state.untrusted for render_untrusted, the verdict's rendering.
    - docs/guard/untrusted-content.md for the families and thresholds.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from hivemind.cell import CellIdentity
from hivemind.guard.scanner import ScanAction, ScanRecorder, ScanSite, ScanSource
from hivemind.memory import RetrievedItem, UntrustedText, render_untrusted, within_scan
from waggle.ids import TaskId
from waggle.messages.honey import HoneyHit

if TYPE_CHECKING:
    # Type-checking only: registry imports this module for real, so a runtime import back would
    # cycle; the annotation is a string at runtime (from __future__ import annotations).
    from hivemind.workers.context import WorkerContext
    from hivemind.workers.tools.registry import ToolInvocation

__all__ = ["screen_honey_hits", "screen_tool_result"]


async def screen_tool_result(
    invocation: ToolInvocation, tool: str, source: ScanSource, text: str
) -> str:
    """Scan one tool result for its Worker and return the text the model may read.

    Args:
        invocation: The attempt's context (the Worker, its Cell, its set, its trail) and
            assignment (its task).
        tool: The tool's name, recorded on a flag as the text's locator.
        source: Where the text came from: SESSION_OUTPUT for the Cell's own session, TOOL_RESULT
            for everything else.
        text: The result exactly as the tool returned it.

    Returns:
        `text` unchanged on PASS (cut at the scanner's bound, with a notice, if it was longer);
        fenced under a harder label on LABEL; a withheld notice with the keyed hash on DROP.

    Raises:
        hivemind.common.errors.SecretStoreError: The scanner's key could not be read or minted.
    """
    ctx = invocation.ctx
    site = _site(ctx, source, invocation.assignment.task_id, tool)
    # A local, CPU-bound scan (bounded input) plus, on a flag, one trail write and at most one
    # secret-store read the first time; a failure there propagates rather than skip the scan.
    verdict = await ctx.scanner.scan(text, site)
    if verdict.action is ScanAction.PASS:
        # Unfenced, as the tool returned it, but never past the scanner's bound.
        return within_scan(text, verdict)
    label = f"{source.value} untrusted"
    return render_untrusted(UntrustedText(label=label, text=text, verdict=verdict))


async def screen_honey_hits(
    ctx: WorkerContext, task_id: TaskId, hits: Sequence[HoneyHit]
) -> tuple[RetrievedItem, ...]:
    """Scan every Honey hit a task hands its Worker, before any of them reaches assembly.

    Args:
        ctx: The attempt's context: the Worker as the consuming bee, its Cell's tier, its set and
            its trail.
        task_id: The task the hits arrived for (its `TaskAssign.task_id`).
        hits: The hits as the assignment carries them (`TaskAssign.honey`, the Queen's pre-check).

    Returns:
        One scanned `RetrievedItem` per hit, in order, each carrying the scanner's verdict on its
        excerpt; `hivemind.memory.assemble` renders every one under that verdict.

    Raises:
        hivemind.common.errors.SecretStoreError: The scanner's key could not be read or minted.
    """
    items: list[RetrievedItem] = []
    for hit in hits:
        site = _site(ctx, ScanSource.HONEY_HIT, task_id, hit.honey_ref)
        # One local scan per hit; a flag adds one trail write, as for a tool result.
        verdict = await ctx.scanner.scan(hit.excerpt, site)
        items.append(RetrievedItem.from_hit(hit, verdict))
    return tuple(items)


def _site(ctx: WorkerContext, source: ScanSource, task_id: TaskId, ref: str) -> ScanSite:
    """Build the scan site for text a Worker is about to read: it consumes, at its Cell's tier."""
    return ScanSite(
        source=source,
        consumer=ctx.worker_id,
        recorder=ScanRecorder(
            trail=ctx.trail,
            identity=CellIdentity(
                hive_id=ctx.identity.hive_id, node_id=ctx.identity.node_id, actor=ctx.identity.actor
            ),
            clock=ctx.clock,
        ),
        tier=ctx.cell.comb_shield,
        targets=ctx.capabilities,
        task_id=task_id,
        ref=ref,
    )
