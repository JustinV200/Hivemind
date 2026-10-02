"""Propose one GUI action at the tier its reach implies: the path every Exoskeleton action takes.

An Exoskeleton action tool (roadmap step 6.5: a click, a keystroke, a page fill, a clip spoken)
never drives a peripheral itself. It builds typed waggle `GuiStep`s and hands them to `act`, which
turns them into one `ProposedAction` of kind GUI with the postconditions the call's `expect`
states, and runs it through the Capping gate (the quality gate every side effect passes), which
checks, applies it through the attached Exoskeleton, verifies it, rolls it back and records it
(ADR-0032). The tool declares the tier from what the action reaches: input on a display the lease
started is `scratch_write` and on the operator's running display `device_command`, and input on a
display that shows the lease's browser is tiered at least as the page it shows (ADR-0059); a page
that stays in the lease (a file inside its scratch, about:blank, a loopback host where the Cell's
loopback is private) is `scratch_write`, a file anywhere else on the Cell `outside_scratch_write`
(which the gate's allowlist rung refuses; the navigate tool refuses it before that) and any other
web page `network_egress`, a loopback one included where loopback is the operator's own (its
allowlist rung then needs `net:loopback`); `irreversible: true` makes it `irreversible`, which a
judge reviews before the bee's next step.
The gate's checks may raise a tier, never lower one. This module also finds the attached handle
and peripheral each tool needs, for the read-only tools as much as the actions, and refuses the
key chords that open the browser's own DevTools and file dialogs for the tools that press keys.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools.exoskeleton`. Called by the
    package's desktop, browser, look and audio tools. Calls into `hivemind.common.logging`,
    `hivemind.exoskeleton`, `hivemind.guard` (the file URL rule), `hivemind.supervision.capping`
    (RiskTier, the loopback rule), `hivemind.workers.tools.
    proposals` and `.registry`, this package's `arguments`, `errors` and `expect`, and waggle.

Key invariants:
    - Every GUI action is one proposal through `hivemind.workers.tools.proposals.cap`; nothing here
      touches a peripheral to act, and a failed, rolled-back one raises its Alarm there at once.
    - A reach the tool cannot establish is never assumed to be local: an unreadable page URL, a
      display the lease did not start, a file URL outside scratch or naming a remote host, and a
      loopback host on a Cell whose loopback is not private all take the higher tier.

See Also:
    - docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md for "the
      tool declares the tier from what the action reaches".
    - docs/adr/0059-a-lease-browser-stays-inside-its-granted-hosts-and-its-own-chrome.md for
      loopback and input over the browser.
    - hivemind.workers.tools.proposals for make_proposal, cap and tool_output.
    - hivemind.supervision.capping.gui for what the allowlist rung requires of each step.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from hivemind.common.logging import get_logger
from hivemind.exoskeleton import (
    Browser,
    DisplaySource,
    ExoskeletonHandle,
    PeripheralError,
    chrome_chord,
)
from hivemind.guard import file_url_escapes, is_file_url
from hivemind.llm import JsonObject
from hivemind.supervision.capping import RiskTier, is_loopback_host
from hivemind.workers.tools.exoskeleton.arguments import flag
from hivemind.workers.tools.exoskeleton.errors import GuiArgumentError, PeripheralMissingError
from hivemind.workers.tools.exoskeleton.expect import expectations
from hivemind.workers.tools.proposals import ProposalRequest, cap, make_proposal, tool_output
from hivemind.workers.tools.registry import ToolInvocation, ToolOutput
from waggle.messages.capping import ActionKind, GuiStep, ProposedAction
from waggle.messages.capping.action import MAX_SUMMARY_CHARS

# Desktop input's tier by where its display came from (ADR-0032). A display attach found running
# is the operator's own; anything not named here (no display at all) takes that higher tier too.
_DISPLAY_TIERS: dict[DisplaySource, RiskTier] = {
    DisplaySource.LEASE: RiskTier.SCRATCH_WRITE,
    DisplaySource.RUNNING: RiskTier.DEVICE_COMMAND,
}
_WEB_SCHEMES = frozenset({"http", "https"})  # A page served from a host, near or far.
_BLANK_PAGE = "about:blank"  # The one about: page a GUI step may load; it holds nothing.
_TIER_ORDER = tuple(RiskTier)  # Declared lowest to highest; a reach only ever takes the higher.

log = get_logger(__name__)

__all__ = [
    "GuiAction",
    "act",
    "attached",
    "current_page_reach",
    "desktop_input_reach",
    "desktop_reach",
    "need",
    "page_reach",
    "refuse_chrome_chord",
]


@dataclass(frozen=True, slots=True)
class GuiAction:
    """One action tool's request: its steps, the tier their reach implies, and its arguments."""

    tool: str  # The tool's name, for the proposal's reason.
    steps: tuple[GuiStep, ...]  # Already validated (arguments.build_step), applied in order.
    reach: RiskTier  # The tier what the steps touch implies, before `irreversible` raises it.
    arguments: JsonObject  # The call's own arguments; `expect` and `irreversible` are read here.


async def act(invocation: ToolInvocation, action: GuiAction) -> ToolOutput:
    """Propose `action` as one GUI proposal, run it through the Capping gate, and report back.

    Args:
        invocation: This attempt's context and assignment.
        action: The steps, their reach, and the call's arguments.

    Returns:
        `hivemind.workers.tools.proposals.tool_output`'s rendering of the gate's outcome: what
        happened, never typed text or a frame.

    Raises:
        GuiArgumentError: The call's `expect` or `irreversible` is malformed.
        PeripheralMissingError: No Exoskeleton is attached for this task.
    """
    handle = attached(invocation)
    postconditions = expectations(action.arguments.get("expect"), handle.peripherals)
    # A step the bee says cannot be undone is irreversible whatever it reaches (ADR-0032); the
    # flag only ever raises the tier the reach implies.
    tier = RiskTier.IRREVERSIBLE if flag(action.arguments, "irreversible") else action.reach
    proposed = ProposedAction(
        kind=ActionKind.GUI,
        summary=_summary(action.steps),
        diff=None,
        diff_sha256=None,
        command=(),
        cwd=None,
        paths=(),
        steps=(),
        gui=action.steps,
    )
    request = ProposalRequest(
        tier=tier,
        action=proposed,
        postconditions=postconditions,
        reason=f"Worker tool {action.tool}: {len(action.steps)} GUI step(s)",
    )
    proposal = make_proposal(invocation.ctx, invocation.assignment, request)
    return tool_output(await cap(invocation, proposal))


def attached(invocation: ToolInvocation) -> ExoskeletonHandle:
    """Return this task's attached Exoskeleton.

    Args:
        invocation: This attempt's context and assignment.

    Returns:
        The handle attach returned for this task.

    Raises:
        PeripheralMissingError: The task has none (a terminal-only task never offers these tools,
            so only a direct call reaches this).
    """
    handle = invocation.ctx.exoskeleton
    if handle is None:
        raise PeripheralMissingError("Exoskeleton")
    return handle


def need[PeripheralT](peripheral: PeripheralT | None, name: str) -> PeripheralT:
    """Return `peripheral`, or refuse the call naming what this task does not have attached.

    Args:
        peripheral: One of the handle's peripherals, None when the plan did not attach it.
        name: What to call it in the refusal ("display", "browser", "audio").

    Returns:
        The peripheral.

    Raises:
        PeripheralMissingError: `peripheral` is None.
    """
    if peripheral is None:
        raise PeripheralMissingError(name)
    return peripheral


def desktop_reach(handle: ExoskeletonHandle) -> RiskTier:
    """Return desktop input's tier: `scratch_write` on a lease's display, else `device_command`.

    Args:
        handle: The attached Exoskeleton, whose plan says where its display came from.

    Returns:
        The tier every desktop action on it starts from.
    """
    return _DISPLAY_TIERS.get(handle.plan.display, RiskTier.DEVICE_COMMAND)


def page_reach(url: str, scratch: Path, *, private_loopback: bool = False) -> RiskTier:
    """Return the tier of acting on (or loading) the page at `url`.

    Args:
        url: The page's URL.
        scratch: The lease's scratch directory: the only place a file URL stays in the lease.
        private_loopback: Whether the Cell's loopback is its own
            (`ExoskeletonHandle.private_loopback`); False treats loopback as the operator's own.

    Returns:
        `scratch_write` for a page that stays in the lease (a file inside `scratch`, the blank
        page, a loopback host on a private loopback); `outside_scratch_write` for any other file
        URL, a read of the Cell's disk the gate's allowlist rung refuses; `network_egress` for
        every other page, known scheme or not, a loopback one on a shared loopback included.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if url.lower() == _BLANK_PAGE:
        return RiskTier.SCRATCH_WRITE
    # A file outside scratch, or on a share another host serves, is not the lease's to read.
    if is_file_url(url):
        escapes = file_url_escapes(url, (scratch,))
        return RiskTier.OUTSIDE_SCRATCH_WRITE if escapes else RiskTier.SCRATCH_WRITE
    # Loopback stays in the lease only where nothing but the Cell listens there (ADR-0059).
    host = parts.hostname
    if private_loopback and scheme in _WEB_SCHEMES and host and is_loopback_host(host):
        return RiskTier.SCRATCH_WRITE
    return RiskTier.NETWORK_EGRESS


async def current_page_reach(
    browser: Browser, scratch: Path, *, private_loopback: bool = False
) -> RiskTier:
    """Return the tier of acting on the page the browser shows now.

    Args:
        browser: The attached browser.
        scratch: The lease's scratch directory, as for `page_reach`.
        private_loopback: As for `page_reach`.

    Returns:
        `page_reach` of its current URL; `network_egress` when the URL cannot be read.
    """
    try:
        # External await: one round trip to the browser, bounded by its own timeout.
        url = await browser.url()
    except PeripheralError as error:
        # A page the tool cannot read is treated as off the Cell (module docstring).
        log.debug("exoskeleton_tools.page_url_unreadable", reason=error.reason)
        return RiskTier.NETWORK_EGRESS
    return page_reach(url, scratch, private_loopback=private_loopback)


async def desktop_input_reach(handle: ExoskeletonHandle, scratch: Path) -> RiskTier:
    """Return desktop input's tier, raised to the page's when the lease's browser is on screen.

    Input on a display lands wherever the pointer or focus is, the lease's browser window
    included, so it reaches whatever that page reaches, as the browser's own tools are tiered
    (ADR-0059). A headless browser (no display) is never under the pointer.

    Args:
        handle: The attached Exoskeleton.
        scratch: The lease's scratch directory, as for `page_reach`.

    Returns:
        The higher of `desktop_reach` and, when the browser is shown on the display, the reach
        of the page it shows.
    """
    tier = desktop_reach(handle)
    browser = handle.peripherals.browser
    if browser is None or handle.plan.display is DisplaySource.NONE:
        return tier
    shown = await current_page_reach(browser, scratch, private_loopback=handle.private_loopback)
    return max(tier, shown, key=_TIER_ORDER.index)


def refuse_chrome_chord(keys: str) -> None:
    """Refuse a chord that opens the browser's own DevTools, Save As, Open File or Print.

    A lease never uses those windows: DevTools reads and writes local folders and runs code with
    the browser's own privileges, and a file dialog lists and writes the Cell's directories
    (ADR-0059). The browser shuts them again itself; refusing here keeps the bee's step honest.

    Args:
        keys: The chord the call names.

    Raises:
        GuiArgumentError: The chord opens one of those windows.
    """
    window = chrome_chord(keys)
    if window is not None:
        raise GuiArgumentError(
            f"keys: that chord opens the browser's own {window}, which a lease never uses."
        )


def _summary(steps: tuple[GuiStep, ...]) -> str:
    """Describe the steps in one line, secrets redacted by GuiStep.describe, within the bound."""
    text = "; ".join(step.describe() for step in steps)
    return text if len(text) <= MAX_SUMMARY_CHARS else f"{text[: MAX_SUMMARY_CHARS - 3]}..."
