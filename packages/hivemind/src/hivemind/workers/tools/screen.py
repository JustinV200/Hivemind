"""Screen a tool's result before the model reads it: scan it, record a flag, apply the verdict.

A tool's result is outside text: a web page from `http_request`, a command's output or a file's
bytes from the Cell's own session (`run_command`, `read_file`), a human's answer through `ask`.
Roadmap step 10.6b runs the untrusted-content scanner (`hivemind.guard.scanner`) on every one of
them, in `ToolRegistry.execute`, before the text becomes the model's next tool-result part.
`screen_tool_result` builds the scan site from the Worker's own context (its id as the consuming
bee, its Cell's Comb Shield tier, its own capability set as the task's targets, its own trail), and
then applies the verdict: a PASS result is returned exactly as the tool produced it (the tool-result
part is already its own channel, so fencing every clean result would only change every prompt),
a LABEL result is fenced under a harder label with a warning naming what fired, and a DROP result
is replaced by a withheld notice carrying the keyed hash, so the injected text never reaches the
model, the attempt's call records, or a Handoff built from them. A flag never stops the bee: it
reads the verdict's consequence and carries on, and at most asks.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools`. Called by
    `hivemind.workers.tools.registry.ToolRegistry.execute` for every tool that ran. Calls into
    `hivemind.cell` (CellIdentity), `hivemind.guard.scanner`, `hivemind.memory` (render_untrusted,
    UntrustedText), `hivemind.workers.tools.registry` (ToolInvocation, under TYPE_CHECKING) and
    waggle only.

Key invariants:
    - Every result a tool returns is scanned before any model can read it; only the registry's own
      refusal and validation messages (the Hive's words, not outside text) skip the scanner.
    - A DROP verdict's text is never returned.

See Also:
    - hivemind.guard.scanner.scanner for ContentScanner.scan.
    - hivemind.memory.hot_state.untrusted for render_untrusted, the verdict's rendering.
    - docs/guard/untrusted-content.md for the families and thresholds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.cell import CellIdentity
from hivemind.guard.scanner import ScanAction, ScanRecorder, ScanSite, ScanSource
from hivemind.memory import UntrustedText, render_untrusted

if TYPE_CHECKING:
    # Type-checking only: registry imports this module for real, so a runtime import back would
    # cycle; the annotation is a string at runtime (from __future__ import annotations).
    from hivemind.workers.tools.registry import ToolInvocation

__all__ = ["screen_tool_result"]


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
        `text` unchanged on PASS; fenced under a harder label on LABEL; a withheld notice with the
        keyed hash on DROP.

    Raises:
        hivemind.common.errors.SecretStoreError: The scanner's key could not be read or minted.
    """
    ctx = invocation.ctx
    site = ScanSite(
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
        task_id=invocation.assignment.task_id,
        ref=tool,
    )
    # A local, CPU-bound scan (bounded input) plus, on a flag, one trail write and at most one
    # secret-store read the first time; a failure there propagates rather than skip the scan.
    verdict = await ctx.scanner.scan(text, site)
    if verdict.action is ScanAction.PASS:
        return text
    label = f"{source.value} untrusted"
    return render_untrusted(UntrustedText(label=label, text=text, verdict=verdict))
