"""Define GuiSurface: the seam the Capping gate applies, verifies and undoes GUI steps through.

A GUI proposal (`ActionKind.GUI`, waggle 1.6) carries typed `GuiStep`s: clicks, keystrokes, page
navigation, element fills, a clip to speak (ADR-0032). The gate applies every other action kind
itself through the Cell's session, but a GUI step needs the attached Exoskeleton, which lives at
Layer 3 and so cannot be imported here. `GuiSurface` is the Layer 2 protocol the Exoskeleton
implements and the Warden injects into `GateDeps.gui`, exactly as it injects the `Snapshotter`:
before applying, the gate asks the surface for an undo point and the evidence the declared
postconditions need (`before`); it applies the steps through it (`apply`); it checks the GUI
postcondition kinds through it, each an eventual assertion polled for a bounded settle time
(`check`); on failure it rolls back through it when no snapshot was taken (`restore`); and it
tells it the terminal outcome so the flight recorder can close the action's entry (`finish`); and
when an applied irreversible action is judged, it hands the judge what was recorded (`evidence`).
This module also names what the allowlist rung requires of each step (`required_capabilities`).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.supervision.capping`.
    Implemented by `hivemind.exoskeleton.surface.ExoskeletonSurface`; called by the gate
    (`gate.gui`), `apply` and the allowlist check. Calls into `hivemind.guard` (Capability),
    `.postconditions`, `.proposal` and waggle only.

Key invariants:
    - The gate never applies a GUI proposal without a surface; it rejects it before any check.
    - Every step names the capabilities it needs; a NAVIGATE also needs `net:<host>` for any
      URL that leaves the Cell (http and https; file and about:blank stay on it). A file URL
      must also stay inside the lease's scratch, which the allowlist rung checks separately.

See Also:
    - docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md.
    - waggle.messages.capping.gui for GuiStep and GuiOp.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from hivemind.guard import Capability
from hivemind.supervision.capping.checks.judge import JudgeEvidence
from hivemind.supervision.capping.postconditions import PostconditionOutcome
from hivemind.supervision.capping.proposal import Proposal
from waggle.messages.capping import GuiOp, GuiStep, RollbackMethod
from waggle.messages.labels import Postcondition, PostconditionKind

# The postcondition kinds only a GUI surface can check: a page's URL and an element's text through
# the browser, a screen region's pixels through the CompoundEye.
GUI_POSTCONDITION_KINDS = frozenset(
    {
        PostconditionKind.URL_MATCHES,
        PostconditionKind.ELEMENT_TEXT,
        PostconditionKind.REGION_CHANGED,
    }
)
# The GUI kinds a task's acceptance may state (ADR-0032, "Acceptance can be structural"): the ones
# that read the state a subtask leaves behind. REGION_CHANGED compares against a before-digest
# only a proposal has, so it is never an acceptance criterion.
ACCEPTANCE_GUI_KINDS = frozenset({PostconditionKind.URL_MATCHES, PostconditionKind.ELEMENT_TEXT})
_DESKTOP_OPS = frozenset(
    {GuiOp.MOVE, GuiOp.CLICK, GuiOp.DOUBLE_CLICK, GuiOp.TYPE, GuiOp.PRESS, GuiOp.SCROLL}
)
# The steps the browser fast path runs; a browser procedure (roadmap step 6.7) is made of these.
BROWSER_OPS = frozenset(
    {GuiOp.NAVIGATE, GuiOp.BROWSER_CLICK, GuiOp.BROWSER_FILL, GuiOp.BROWSER_PRESS}
)
_OFF_CELL_SCHEMES = frozenset({"http", "https"})  # file:// and about:blank never leave the Cell.

__all__ = [
    "ACCEPTANCE_GUI_KINDS",
    "BROWSER_OPS",
    "GUI_POSTCONDITION_KINDS",
    "GuiApplyResult",
    "GuiSurface",
    "required_capabilities",
]


@dataclass(frozen=True, slots=True)
class GuiApplyResult:
    """What applying a GUI proposal's steps did."""

    succeeded: bool  # Every step ran.
    steps_applied: int  # How many ran before the first failure (all of them on success).
    failure_reason: str | None = None  # Why the first failing step failed; never typed text.


class GuiSurface(Protocol):
    """Apply, verify and undo one Cell's GUI steps for the Capping gate, and record them."""

    async def before(self, proposal: Proposal) -> None:
        """Take the undo point and the before-evidence for `proposal`, ahead of applying it.

        Latency: interactive to a second (a checkpoint, a frame, a digest per REGION_CHANGED
        postcondition). Failure: never raises for missing evidence; a postcondition that needed
        it then fails its own check.

        Args:
            proposal: The CAPPED proposal about to be applied.
        """
        ...

    async def apply(self, proposal: Proposal) -> GuiApplyResult:
        """Run `proposal.action.gui` in order, stopping at the first step that fails.

        Latency: per step (a page load for NAVIGATE, the clip's length for SAY).

        Args:
            proposal: The proposal whose steps to run.

        Returns:
            Whether every step ran, and why the first failure failed.
        """
        ...

    async def check(
        self, proposal: Proposal, index: int, postcondition: Postcondition
    ) -> PostconditionOutcome:
        """Check one GUI postcondition, polling until it holds or its settle time runs out.

        Args:
            proposal: The proposal just applied (its before-evidence is looked up by id).
            index: The postcondition's position in the proposal's list.
            postcondition: One of GUI_POSTCONDITION_KINDS.

        Returns:
            Whether it held, and what was observed (a URL, a length, "changed"); never a frame.
        """
        ...

    async def restore(self, proposal: Proposal) -> bool:
        """Put back the undo point `before` took for `proposal`.

        Returns:
            True when browser state was restored (GUI_STATE); False when there was nothing to
            restore (a desktop-only proposal, or no browser attached).
        """
        ...

    async def evidence(self, proposal: Proposal) -> JudgeEvidence | None:
        """Return what the recorder kept for `proposal`, for a judge to review.

        Latency: immediate (what `finish` already recorded). Returns None when nothing was
        recorded for it (no recorder, or an id this surface never finished).

        Args:
            proposal: A proposal this surface has already finished.
        """
        ...

    async def finish(self, proposal: Proposal, rollback: RollbackMethod | None) -> None:
        """Tell the surface `proposal` reached its terminal state, so its record can close.

        Args:
            proposal: The proposal in its terminal state (VERIFIED, REJECTED or ROLLED_BACK).
            rollback: How it was rolled back, when it was.
        """
        ...


def required_capabilities(step: GuiStep) -> tuple[Capability, ...]:
    """Return the capabilities a bee must hold for the gate to apply `step`.

    Args:
        step: One GUI step.

    Returns:
        `exoskeleton:display` for desktop input, `exoskeleton:browser` for a browser step (plus
        `net:<host>` for navigation that leaves the Cell), `exoskeleton:audio` for SAY.
    """
    if step.op in _DESKTOP_OPS:
        return (Capability.parse("exoskeleton:display"),)
    if step.op is GuiOp.SAY:
        return (Capability.parse("exoskeleton:audio"),)
    needs = [Capability.parse("exoskeleton:browser")]
    if step.op is GuiOp.NAVIGATE and step.url is not None:
        parts = urlsplit(step.url)
        if parts.scheme.lower() in _OFF_CELL_SCHEMES and parts.hostname:
            needs.append(Capability.parse(f"net:{parts.hostname}"))
    return tuple(needs)
