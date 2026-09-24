"""Hold the Worker's Exoskeleton tools: see, touch, browse, listen and speak on its Cell.

The Exoskeleton (the optional display, pointer and keyboard, audio and browser a Cell is given for
one task) is driven by these tools (roadmap step 6.5, with the fast path's `browser_*` tools from
6.11). The read-only ones (`see`, `browser_screenshot`, `browser_snapshot`, `browser_read`,
`listen`) look and report, proposing nothing; a screenshot or a recording reaches the model as
media beside the text, never inside it. Every action (`click`, `move`, `type`, `press`, `scroll`,
`browser_navigate`, `browser_click`, `browser_fill`, `browser_press`, `say`) becomes one typed GUI
proposal the Capping gate (the quality gate every side effect passes) checks, applies through the
attached Exoskeleton, verifies against the call's `expect`, rolls back and records (ADR-0032), at
the tier its reach implies; a rolled-back one raises an Alarm at once. `offer` decides which tools
a Worker gets from what is attached and what its model can take.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools`. Offered through
    `hivemind.workers.tools.registry.build_registry`; run by `ToolRegistry.execute` for the Drone.
    Calls into `hivemind.exoskeleton`, `hivemind.llm`, `hivemind.supervision.capping`,
    `hivemind.workers.context`, `hivemind.workers.tools.proposals` and `.registry`, and waggle.

Key invariants:
    - No tool here drives a peripheral to act: actions go through the gate, reads only read.
    - Typed text, frames and audio never reach a result's text, an error or a log.
    - A tool is offered only when its peripheral is attached and the bound model can take what it
      returns (`see`/`browser_screenshot` need `vision`; `listen` needs Ears or `audio`).

See Also:
    - docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md.
    - hivemind.workers.tools.exoskeleton.README for the tool-by-tool map.
    - hivemind.exoskeleton for the peripherals, and hivemind.exoskeleton.surface for the gate's
      side of every action.

Public API (roadmap step 6.5):
    - exoskeleton_specs, ACTION_TOOL_NAMES, READ_TOOL_NAMES (offer): what a Worker is offered.
    - CLICK_SPEC, MOVE_SPEC, TYPE_SPEC, PRESS_SPEC, SCROLL_SPEC (desktop): desktop input.
    - BROWSER_NAVIGATE_SPEC, BROWSER_CLICK_SPEC, BROWSER_FILL_SPEC, BROWSER_PRESS_SPEC (browser).
    - SEE_SPEC, BROWSER_SCREENSHOT_SPEC, BROWSER_SNAPSHOT_SPEC, BROWSER_READ_SPEC,
      MAX_PAGE_READ_CHARS (look): the read-only sight tools.
    - LISTEN_SPEC, SAY_SPEC (audio): hearing and speaking.
    - GuiAction, act, page_reach, desktop_reach (act): one GUI proposal at its reach's tier.
    - expectations, PAGE_SUBJECT (expect): `expect` as the proposal's postconditions.
    - GuiArgumentError, PeripheralMissingError, PeripheralReadError (errors).
"""

from hivemind.workers.tools.exoskeleton.act import GuiAction, act, desktop_reach, page_reach
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
from hivemind.workers.tools.exoskeleton.errors import (
    GuiArgumentError,
    PeripheralMissingError,
    PeripheralReadError,
)
from hivemind.workers.tools.exoskeleton.expect import PAGE_SUBJECT, expectations
from hivemind.workers.tools.exoskeleton.look import (
    BROWSER_READ_SPEC,
    BROWSER_SCREENSHOT_SPEC,
    BROWSER_SNAPSHOT_SPEC,
    MAX_PAGE_READ_CHARS,
    SEE_SPEC,
)
from hivemind.workers.tools.exoskeleton.offer import (
    ACTION_TOOL_NAMES,
    READ_TOOL_NAMES,
    exoskeleton_specs,
)

__all__ = [
    "ACTION_TOOL_NAMES",
    "BROWSER_CLICK_SPEC",
    "BROWSER_FILL_SPEC",
    "BROWSER_NAVIGATE_SPEC",
    "BROWSER_PRESS_SPEC",
    "BROWSER_READ_SPEC",
    "BROWSER_SCREENSHOT_SPEC",
    "BROWSER_SNAPSHOT_SPEC",
    "CLICK_SPEC",
    "LISTEN_SPEC",
    "MAX_PAGE_READ_CHARS",
    "MOVE_SPEC",
    "PAGE_SUBJECT",
    "PRESS_SPEC",
    "READ_TOOL_NAMES",
    "SAY_SPEC",
    "SCROLL_SPEC",
    "SEE_SPEC",
    "TYPE_SPEC",
    "GuiAction",
    "GuiArgumentError",
    "PeripheralMissingError",
    "PeripheralReadError",
    "act",
    "desktop_reach",
    "exoskeleton_specs",
    "expectations",
    "page_reach",
]
