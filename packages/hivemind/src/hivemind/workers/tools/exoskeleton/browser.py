"""Implement the Exoskeleton's browser action tools: navigate, click, fill and press on a page.

The browser fast path (roadmap steps 6.5 and 6.11, ADR-0031) drives the browser attached for this
task through its accessibility tree rather than pixels, so a model with no vision can still do
browser work: it reads the page with `browser_snapshot` (read-only, `look`) and acts on an element
named by role and name, label, visible text or CSS selector. Each tool here turns one call into
one typed waggle `GuiStep` and proposes it through `act` (ADR-0032), where the Capping gate applies
it, verifies the call's `expect`, and rolls the page back (URL, cookies, local storage) and raises
an Alarm when that fails. The tier comes from the page: one that stays in the lease (a file inside
its scratch, about:blank, a loopback host) is `scratch_write`, any other `network_egress`; a
navigation takes its destination's, every other action the page shown now. A file URL anywhere
else on the Cell is refused before it is proposed: a file URL reads the Cell's disk without the
path rules `read_file` applies, so the browser may open only the bee's own pages in scratch, and
the gate and the browser itself refuse the rest again. Filled text travels only in its step.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools.exoskeleton`. Offered by
    `offer.exoskeleton_specs` whenever a browser is attached. Calls into `hivemind.exoskeleton`
    (Browser), `hivemind.guard` (the file URL rule), `hivemind.llm`, this package's `act`,
    `arguments` and `errors`,
    `hivemind.workers.tools.registry` and waggle's GUI step models only.

Key invariants:
    - Every call proposes exactly one GUI step; nothing here drives the browser directly (it is
      only read, for the URL that decides the tier).
    - No navigation to a file URL outside the lease's scratch is ever proposed.
    - A result never contains filled text: it is the gate's outcome, rendered by
      `hivemind.workers.tools.proposals.tool_output`.

See Also:
    - hivemind.workers.tools.exoskeleton.act for the proposal and the reach rules.
    - hivemind.workers.tools.exoskeleton.look for the read-only page tools.
    - hivemind.exoskeleton.browser for the Browser the gate drives.
"""

from __future__ import annotations

from hivemind.exoskeleton import Browser
from hivemind.guard import file_url_escapes
from hivemind.llm import JsonObject
from hivemind.workers.tools.exoskeleton.act import (
    GuiAction,
    act,
    attached,
    current_page_reach,
    need,
    page_reach,
)
from hivemind.workers.tools.exoskeleton.arguments import (
    TARGET_SCHEMA,
    action_definition,
    build_step,
    flag,
    required_text,
    target_from,
)
from hivemind.workers.tools.exoskeleton.errors import GuiArgumentError
from hivemind.workers.tools.registry import ToolInvocation, ToolOutput, ToolSpec
from waggle.messages.capping import GuiOp

# The sentence every browser action's description ends with: who applies it, and what follows.
_GATED = "Proposed through the Capping gate, which applies it and then checks expect."
# The refusal a file URL outside scratch gets: the rule, and where to go instead.
_OUTSIDE_SCRATCH = (
    "url: a file URL may only open a page inside this task's scratch directory; "
    "read any other file with read_file"
)

BROWSER_NAVIGATE_DEFINITION = action_definition(
    "browser_navigate",
    "Load a URL (http, https, about:blank, or a file inside this task's scratch directory) in "
    f"this task's browser. {_GATED}",
    {"url": {"type": "string"}},
    ("url",),
)
BROWSER_CLICK_DEFINITION = action_definition(
    "browser_click",
    f"Click one element of the page, named by target. {_GATED}",
    {"target": TARGET_SCHEMA},
    ("target",),
)
BROWSER_FILL_DEFINITION = action_definition(
    "browser_fill",
    f"Replace the value of one input on the page with text. {_GATED}",
    {
        "target": TARGET_SCHEMA,
        "text": {"type": "string"},
        "secret": {"type": "boolean", "description": "True for a password or token: redacted."},
    },
    ("target", "text"),
)
BROWSER_PRESS_DEFINITION = action_definition(
    "browser_press",
    f"Press one key chord (Enter, ctrl+a) in the page, on target when given. {_GATED}",
    {"keys": {"type": "string", "description": "Key names joined by +."}, "target": TARGET_SCHEMA},
    ("keys",),
)

__all__ = [
    "BROWSER_CLICK_DEFINITION",
    "BROWSER_CLICK_SPEC",
    "BROWSER_FILL_DEFINITION",
    "BROWSER_FILL_SPEC",
    "BROWSER_NAVIGATE_DEFINITION",
    "BROWSER_NAVIGATE_SPEC",
    "BROWSER_PRESS_DEFINITION",
    "BROWSER_PRESS_SPEC",
    "browser_click",
    "browser_fill",
    "browser_navigate",
    "browser_press",
]


async def browser_navigate(invocation: ToolInvocation, arguments: JsonObject) -> ToolOutput:
    """Propose loading `url`; its tier is the destination's reach.

    Args:
        invocation: This attempt's context and assignment.
        arguments: `url`, optional `expect` and `irreversible`.

    Returns:
        The gate's outcome, as tool output. A `network_egress` navigation also needs a
        `net:<host>` capability, which the gate's allowlist rung checks.

    Raises:
        GuiArgumentError: A malformed argument, a scheme a GUI step may not load, or a file URL
            outside the lease's scratch (or naming another host).
        PeripheralMissingError: No browser is attached.
    """
    _browser(invocation)
    url = required_text(arguments, "url")
    scratch = invocation.ctx.session.scratch_dir
    # Refused before any proposal exists: the browser would read a file the lease never lent.
    if file_url_escapes(url, (scratch,)):
        raise GuiArgumentError(_OUTSIDE_SCRATCH)
    step = build_step({"op": GuiOp.NAVIGATE, "url": url})
    reach = page_reach(url, scratch)
    return await act(invocation, GuiAction("browser_navigate", (step,), reach, arguments))


async def browser_click(invocation: ToolInvocation, arguments: JsonObject) -> ToolOutput:
    """Propose clicking the element `target` names; see `browser_navigate` for the rest."""
    browser = _browser(invocation)
    step = build_step({"op": GuiOp.BROWSER_CLICK, "target": target_from(arguments.get("target"))})
    reach = await current_page_reach(browser, invocation.ctx.session.scratch_dir)
    return await act(invocation, GuiAction("browser_click", (step,), reach, arguments))


async def browser_fill(invocation: ToolInvocation, arguments: JsonObject) -> ToolOutput:
    """Propose filling the input `target` names with `text`, redacted when `secret`."""
    browser = _browser(invocation)
    fields = {
        "op": GuiOp.BROWSER_FILL,
        "target": target_from(arguments.get("target")),
        "text": required_text(arguments, "text"),
        "secret": flag(arguments, "secret"),
    }
    step = build_step(fields)
    reach = await current_page_reach(browser, invocation.ctx.session.scratch_dir)
    return await act(invocation, GuiAction("browser_fill", (step,), reach, arguments))


async def browser_press(invocation: ToolInvocation, arguments: JsonObject) -> ToolOutput:
    """Propose pressing one key chord in the page, on `target` when the call names one."""
    browser = _browser(invocation)
    raw_target = arguments.get("target")
    target = None if raw_target is None else target_from(raw_target)
    keys = required_text(arguments, "keys")
    step = build_step({"op": GuiOp.BROWSER_PRESS, "keys": keys, "target": target})
    reach = await current_page_reach(browser, invocation.ctx.session.scratch_dir)
    return await act(invocation, GuiAction("browser_press", (step,), reach, arguments))


BROWSER_NAVIGATE_SPEC = ToolSpec(definition=BROWSER_NAVIGATE_DEFINITION, run=browser_navigate)
BROWSER_CLICK_SPEC = ToolSpec(definition=BROWSER_CLICK_DEFINITION, run=browser_click)
BROWSER_FILL_SPEC = ToolSpec(definition=BROWSER_FILL_DEFINITION, run=browser_fill)
BROWSER_PRESS_SPEC = ToolSpec(definition=BROWSER_PRESS_DEFINITION, run=browser_press)


def _browser(invocation: ToolInvocation) -> Browser:
    """Return this task's attached browser, or refuse the call when it has none."""
    return need(attached(invocation).peripherals.browser, "browser")
