"""Decide which Exoskeleton tools one Worker is offered: those its peripherals and model can use.

A tool offered with nothing behind it only invites a model to call it and be refused every time,
so (roadmap step 6.5) each Exoskeleton tool is offered only when what it needs is there: the
desktop input tools when the Antennae (pointer and keyboard) are attached; `see` when the display
is and the bound model declares `vision`; the browser tools, and its structural reads, when a
browser is attached, and `browser_screenshot` only for a vision model; `say` when Buzz (audio) is
attached, and `listen` only when the Worker can also hear what it records, through `Ears` (the
transcriber slot) or a model that declares `audio`. A terminal-only task has no Exoskeleton and is
offered none of them. Mirrors how `http_request` is offered only with a `net` capability
(`hivemind.workers.tools.registry.build_registry`, which calls this).

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools.exoskeleton`. Called by
    `hivemind.workers.tools.registry.build_registry` once per Drone attempt. Calls into
    `hivemind.workers.context` and this package's tool modules only.

Key invariants:
    - Branches on what is attached and what the model declares, never on a provider's name or a
      Cell's kind (codingrules 8.6, 8.7).
    - ACTION_TOOL_NAMES and READ_TOOL_NAMES together name every tool offered here, and no tool is
      in both: an action tool always proposes, a read-only one never does.

See Also:
    - hivemind.workers.context for WorkerContext.exoskeleton, .ears and .bound.
    - hivemind.llm.capabilities for ProviderCapabilities.vision and .audio.
"""

from __future__ import annotations

from hivemind.workers.context import WorkerContext
from hivemind.workers.tools.exoskeleton.audio import LISTEN_SPEC, SAY_SPEC
from hivemind.workers.tools.exoskeleton.browser import (
    BROWSER_CLICK_SPEC,
    BROWSER_FILL_SPEC,
    BROWSER_NAVIGATE_SPEC,
    BROWSER_PRESS_SPEC,
)
from hivemind.workers.tools.exoskeleton.desktop import (
    CLICK_SPEC,
    MOVE_SPEC,
    PRESS_SPEC,
    SCROLL_SPEC,
    TYPE_SPEC,
)
from hivemind.workers.tools.exoskeleton.look import (
    BROWSER_READ_SPEC,
    BROWSER_SCREENSHOT_SPEC,
    BROWSER_SNAPSHOT_SPEC,
    SEE_SPEC,
)
from hivemind.workers.tools.registry import ToolSpec

_DESKTOP = (CLICK_SPEC, MOVE_SPEC, TYPE_SPEC, PRESS_SPEC, SCROLL_SPEC)  # Offered with Antennae.
# Offered with a browser, vision or not: the fast path acts and reads by structure.
_BROWSER_ACTIONS = (
    BROWSER_NAVIGATE_SPEC,
    BROWSER_CLICK_SPEC,
    BROWSER_FILL_SPEC,
    BROWSER_PRESS_SPEC,
)
_BROWSER_READS = (BROWSER_SNAPSHOT_SPEC, BROWSER_READ_SPEC)
# Every tool that proposes through the Capping gate, and every one that only reads.
ACTION_TOOL_NAMES = frozenset(
    spec.definition.name for spec in (*_DESKTOP, *_BROWSER_ACTIONS, SAY_SPEC)
)
READ_TOOL_NAMES = frozenset(
    spec.definition.name
    for spec in (SEE_SPEC, BROWSER_SCREENSHOT_SPEC, *_BROWSER_READS, LISTEN_SPEC)
)

__all__ = ["ACTION_TOOL_NAMES", "READ_TOOL_NAMES", "exoskeleton_specs"]


def exoskeleton_specs(ctx: WorkerContext) -> tuple[ToolSpec, ...]:
    """Return the Exoskeleton tools `ctx`'s peripherals and bound model can use, in a fixed order.

    Args:
        ctx: This attempt's WorkerContext: its `exoskeleton` (what is attached), its `ears` and
            its `bound` model's declared capabilities.

    Returns:
        Every offered tool's spec; empty when no Exoskeleton is attached.
    """
    handle = ctx.exoskeleton
    if handle is None:
        return ()  # A terminal-only task: nothing to see, touch or hear.
    attached = handle.peripherals
    # Media tools only when every fallback could take their media too: a provider without vision
    # refuses an image, so a screenshot offered on the primary alone fails at the first fallback.
    sees, hears = ctx.bound.sees, ctx.bound.hears
    # Each row: whether its tools are offered, and which. A row per need, read top to bottom.
    rows: tuple[tuple[bool, tuple[ToolSpec, ...]], ...] = (
        (attached.antennae is not None, _DESKTOP),
        (attached.compound_eye is not None and sees, (SEE_SPEC,)),
        (attached.browser is not None, (*_BROWSER_ACTIONS, *_BROWSER_READS)),
        (attached.browser is not None and sees, (BROWSER_SCREENSHOT_SPEC,)),
        (attached.buzz is not None and (ctx.ears is not None or hears), (LISTEN_SPEC,)),
        (attached.buzz is not None, (SAY_SPEC,)),
    )
    return tuple(spec for offered, specs in rows if offered for spec in specs)
