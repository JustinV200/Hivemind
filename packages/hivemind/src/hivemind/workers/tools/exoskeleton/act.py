"""Propose one GUI action at the tier its reach implies: the path every Exoskeleton action takes.

An Exoskeleton action tool (roadmap step 6.5: a click, a keystroke, a page fill, a clip spoken)
never drives a peripheral itself. It builds typed waggle `GuiStep`s and hands them to `act`, which
turns them into one `ProposedAction` of kind GUI with the postconditions the call's `expect`
states, and runs it through the Capping gate (the quality gate every side effect passes), which
checks, applies it through the attached Exoskeleton, verifies it, rolls it back and records it
(ADR-0032). The tool declares the tier from what the action reaches: input on a display the lease
started is `scratch_write` and on the operator's running display `device_command`; a page that
stays in the lease (a file inside its scratch, about:blank, a loopback host) is `scratch_write`, a
file anywhere else on the Cell `outside_scratch_write` (which the gate's allowlist rung refuses; the
navigate tool refuses it before that) and any other web page `network_egress`;
`irreversible: true` makes it `irreversible`, which a judge reviews before the bee's next step.
The gate's checks may raise a tier, never lower one. This module also finds the attached handle
and peripheral each tool needs, for the read-only tools as much as the actions.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools.exoskeleton`. Called by the
    package's desktop, browser, look and audio tools. Calls into `hivemind.common.logging`,
    `hivemind.exoskeleton`, `hivemind.guard` (the file URL rule), `hivemind.supervision.capping`
    (RiskTier), `hivemind.workers.tools.
    proposals` and `.registry`, this package's `arguments`, `errors` and `expect`, and waggle.

Key invariants:
    - Every GUI action is one proposal through `hivemind.workers.tools.proposals.cap`; nothing here
      touches a peripheral to act, and a failed, rolled-back one raises its Alarm there at once.
    - A reach the tool cannot establish is never assumed to be local: an unreadable page URL, a
      display the lease did not start, a file URL outside scratch or naming a remote host all
      take the higher tier.

See Also:
    - docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md for "the
      tool declares the tier from what the action reaches".
    - hivemind.workers.tools.proposals for make_proposal, cap and tool_output.
    - hivemind.supervision.capping.gui for what the allowlist rung requires of each step.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from hivemind.common.logging import get_logger
from hivemind.exoskeleton import Browser, DisplaySource, ExoskeletonHandle, PeripheralError
from hivemind.guard import file_url_escapes, is_file_url
from hivemind.llm import JsonObject
from hivemind.supervision.capping import RiskTier
from hivemind.workers.tools.exoskeleton.arguments import flag
from hivemind.workers.tools.exoskeleton.errors import PeripheralMissingError
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

log = get_logger(__name__)

__all__ = [
    "GuiAction",
    "act",
    "attached",
    "current_page_reach",
    "desktop_reach",
    "need",
    "page_reach",
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
    return tool_output(await cap(invocation.ctx, proposal))


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


def page_reach(url: str, scratch: Path) -> RiskTier:
    """Return the tier of acting on (or loading) the page at `url`.

    Args:
        url: The page's URL.
        scratch: The lease's scratch directory: the only place a file URL stays in the lease.

    Returns:
        `scratch_write` for a page that stays in the lease (a file inside `scratch`, the blank
        page, a loopback host); `outside_scratch_write` for any other file URL, a read of the
        Cell's disk the gate's allowlist rung refuses; `network_egress` for every other page,
        known scheme or not.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if url.lower() == _BLANK_PAGE:
        return RiskTier.SCRATCH_WRITE
    # A file outside scratch, or on a share another host serves, is not the lease's to read.
    if is_file_url(url):
        escapes = file_url_escapes(url, (scratch,))
        return RiskTier.OUTSIDE_SCRATCH_WRITE if escapes else RiskTier.SCRATCH_WRITE
    if scheme in _WEB_SCHEMES and _is_loopback(parts.hostname):
        return RiskTier.SCRATCH_WRITE
    return RiskTier.NETWORK_EGRESS


async def current_page_reach(browser: Browser, scratch: Path) -> RiskTier:
    """Return the tier of acting on the page the browser shows now.

    Args:
        browser: The attached browser.
        scratch: The lease's scratch directory, as for `page_reach`.

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
    return page_reach(url, scratch)


def _summary(steps: tuple[GuiStep, ...]) -> str:
    """Describe the steps in one line, secrets redacted by GuiStep.describe, within the bound."""
    text = "; ".join(step.describe() for step in steps)
    return text if len(text) <= MAX_SUMMARY_CHARS else f"{text[: MAX_SUMMARY_CHARS - 3]}..."


def _is_loopback(host: str | None) -> bool:
    """Return whether `host` is this machine: localhost (RFC 6761) or a loopback address."""
    if host is None:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False  # A name that is not an address and not localhost could be anywhere.
