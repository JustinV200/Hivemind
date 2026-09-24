"""Plan what attach starts for one task: a pure decision from the need, the Cell and the grant.

Attach (roadmap step 6.4, ADR-0031) equips a Cell with exactly the Exoskeleton a task asked for and
nothing more. What that means on a given Cell is decided here, before anything starts, from three
inputs only: the task's `ExoskeletonNeed` (a desktop or the browser alone, and whether audio), the
Cell's own `CellCapabilities` report (can it start a display, is its running display one the
operator lets the Hive drive, does it have audio tools and a browser) and the `CapabilitySet` the
bee holds (`exoskeleton:display`, `:real_display`, `:audio`, `:browser`). It never reads
`cell.kind` (codingrules section 8.7: branch on capabilities), so a Virtual Cell, a Linux Real Cell
and the Windows Hive Stand all go through the same rules. A need that cannot be met raises
`AttachError` naming the missing capability, before a single process is started.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton.attach`.
    Called by `hivemind.exoskeleton.attach.core.attach`. Calls into `hivemind.cell`
    (CellCapabilities), `hivemind.guard` (Capability, CapabilitySet), `hivemind.exoskeleton.errors`
    and waggle's ExoskeletonNeed only.

Key invariants:
    - The operator's own display is used only when the Cell reports it allowed AND the bee holds
      `exoskeleton:real_display`; nothing else ever selects DisplaySource.RUNNING.
    - A browser-only need never starts a display or a sound server.
    - `plan_attach` is pure: the same inputs always give the same plan or the same error.

See Also:
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md, "Attach is a pure plan".
    - hivemind.guard.access for which access level's ceiling holds which exoskeleton scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from hivemind.cell import CellCapabilities
from hivemind.exoskeleton.errors import AttachError
from hivemind.guard import Capability, CapabilitySet
from waggle.messages.task import ExoskeletonNeed

# The four exoskeleton scopes a bee may hold (hivemind.guard.capabilities.EXOSKELETON_SCOPES).
DISPLAY = Capability.parse("exoskeleton:display")
REAL_DISPLAY = Capability.parse("exoskeleton:real_display")
AUDIO = Capability.parse("exoskeleton:audio")
BROWSER = Capability.parse("exoskeleton:browser")

__all__ = [
    "AUDIO",
    "BROWSER",
    "DISPLAY",
    "REAL_DISPLAY",
    "AttachPlan",
    "DisplaySource",
    "plan_attach",
]


class DisplaySource(Enum):
    """Where an attached desktop's display comes from."""

    NONE = "NONE"  # No desktop: a browser-only task, so any browser runs headless.
    RUNNING = "RUNNING"  # The display already running on the Cell, which its operator allowed.
    LEASE = "LEASE"  # An Xvfb and window manager attach starts for this lease alone.


@dataclass(frozen=True, slots=True)
class AttachPlan:
    """What attach will provide: a display from where, a sound server, a browser."""

    display: DisplaySource
    audio: bool  # Start the lease's own sound server.
    browser: bool  # Start the lease's own browser.

    def peripherals(self) -> tuple[str, ...]:
        """Return the peripherals this plan attaches, by name, for the trail and the tools.

        Returns:
            A subset of ("compound_eye", "antennae", "buzz", "browser"), in that order.
        """
        names: list[str] = []
        if self.display is not DisplaySource.NONE:
            names += ["compound_eye", "antennae"]  # A display is seen and driven together.
        if self.audio:
            names.append("buzz")
        if self.browser:
            names.append("browser")
        return tuple(names)


def plan_attach(
    need: ExoskeletonNeed, capabilities: CellCapabilities, granted: CapabilitySet
) -> AttachPlan:
    """Decide what to attach for `need`, on a Cell reporting `capabilities`, for a bee's `granted`.

    Args:
        need: What the task asked for.
        capabilities: The Cell's own capability report.
        granted: The capabilities the task's bee holds.

    Returns:
        The plan: always a display for a desktop need, a sound server when audio was asked for,
        and a browser for a browser-only need or wherever the Cell has one the bee may drive.

    Raises:
        AttachError: The need cannot be met on this Cell with this grant; the reason names what
            is missing.
    """
    if need.browser_only:
        _require_browser(capabilities, granted)
        return AttachPlan(display=DisplaySource.NONE, audio=False, browser=True)
    display = _plan_display(capabilities, granted)
    if need.audio:
        _require_audio(capabilities, granted)
    # A desktop task gets the browser too when there is one to drive: most GUI work is in one,
    # and the fast path lets a model without vision still read the page.
    browser = capabilities.has_browser and granted.allows(BROWSER)
    return AttachPlan(display=display, audio=need.audio, browser=browser)


def _plan_display(capabilities: CellCapabilities, granted: CapabilitySet) -> DisplaySource:
    """Pick the operator's own display when allowed and granted, else a lease display, else fail."""
    if (
        capabilities.has_display
        and capabilities.real_display_allowed
        and granted.allows(REAL_DISPLAY)
    ):
        return DisplaySource.RUNNING
    if capabilities.can_start_display and granted.allows(DISPLAY):
        return DisplaySource.LEASE
    if capabilities.can_start_display:
        raise AttachError("a desktop needs the exoskeleton:display capability, which is not held")
    raise AttachError(
        "this Cell can neither start a display nor lend one its operator allowed the Hive to drive"
    )


def _require_audio(capabilities: CellCapabilities, granted: CapabilitySet) -> None:
    """Refuse an audio need the Cell's tools or the bee's grant cannot meet."""
    if not capabilities.has_audio:
        raise AttachError("this Cell has no sound server tools for the audio the task needs")
    if not granted.allows(AUDIO):
        raise AttachError("audio needs the exoskeleton:audio capability, which is not held")


def _require_browser(capabilities: CellCapabilities, granted: CapabilitySet) -> None:
    """Refuse a browser-only need the Cell or the bee's grant cannot meet."""
    if not capabilities.has_browser:
        raise AttachError("this Cell has no browser for the browser-only task")
    if not granted.allows(BROWSER):
        raise AttachError("a browser needs the exoskeleton:browser capability, which is not held")
