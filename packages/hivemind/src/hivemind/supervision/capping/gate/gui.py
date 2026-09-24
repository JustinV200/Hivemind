"""Route a proposal's GUI work through the gate's injected GuiSurface: refuse, prepare, check, undo.

The gate core (`gate.core`) walks every proposal through the same states; a proposal that drives
the Exoskeleton (an `ActionKind.GUI` action, or any postcondition only a GUI surface can check)
needs five extra moves along the way, and they live here so the core stays within its size limit
(codingrules section 5.1): refuse it before any check when no surface is attached (ADR-0032: "With
no surface a GUI proposal is rejected before any check runs"); ask the surface for its undo point
and before-evidence once it is capped; send its GUI postconditions to the surface rather than the
session; roll it back through the surface (`RollbackMethod.GUI_STATE`) when no snapshot was taken;
and tell the surface the terminal outcome so the flight recorder can close the entry.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside
    `hivemind.supervision.capping.gate`. Called by `gate.core`. Calls into `.gui`,
    `.postconditions`, `.proposal`, `gate.model` and waggle only.

Key invariants:
    - A proposal that uses no GUI never touches the surface, attached or not.
    - A GUI postcondition with no surface to check it is reported as not held, never skipped.

See Also:
    - hivemind.supervision.capping.gui for the GuiSurface protocol.
    - docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md.
"""

from __future__ import annotations

from hivemind.supervision.capping.gate.model import GateDeps
from hivemind.supervision.capping.gui import GUI_POSTCONDITION_KINDS
from hivemind.supervision.capping.postconditions import PostconditionOutcome, check_postcondition
from hivemind.supervision.capping.proposal import Proposal
from waggle.messages.capping import ActionKind, RollbackMethod
from waggle.messages.labels import Postcondition

NO_SURFACE = "no Exoskeleton is attached to apply GUI steps"  # The pre-check rejection reason.

__all__ = [
    "NO_SURFACE",
    "check_one",
    "gui_before",
    "gui_finish",
    "gui_refusal",
    "gui_restore",
    "uses_gui",
]


def uses_gui(proposal: Proposal) -> bool:
    """Return whether `proposal` acts through, or is verified through, the GUI surface."""
    if proposal.action.kind is ActionKind.GUI:
        return True
    return any(pc.kind in GUI_POSTCONDITION_KINDS for pc in proposal.postconditions)


def gui_refusal(deps: GateDeps, proposal: Proposal) -> str | None:
    """Return why `proposal` cannot be capped here at all, or None when it can.

    A GUI action with no surface to apply it through is refused before any check runs, so it is
    never CAPPED only to fail at apply time.
    """
    if proposal.action.kind is ActionKind.GUI and deps.gui is None:
        return NO_SURFACE
    return None


async def gui_before(deps: GateDeps, proposal: Proposal) -> None:
    """Have the surface take its undo point and before-evidence, when the proposal uses it."""
    if deps.gui is not None and uses_gui(proposal):
        await deps.gui.before(proposal)


async def check_one(
    deps: GateDeps, proposal: Proposal, index: int, pc: Postcondition
) -> PostconditionOutcome:
    """Check one postcondition: GUI kinds through the surface, every other kind on the session."""
    if pc.kind not in GUI_POSTCONDITION_KINDS:
        return await check_postcondition(deps.session, index, pc)
    if deps.gui is None:
        return PostconditionOutcome(index=index, kind=pc.kind, has_held=False, observed=NO_SURFACE)
    return await deps.gui.check(proposal, index, pc)


async def gui_restore(deps: GateDeps, proposal: Proposal) -> RollbackMethod | None:
    """Restore the surface's undo point for `proposal`; None when it had nothing to restore."""
    if deps.gui is None or not uses_gui(proposal):
        return None
    restored = await deps.gui.restore(proposal)
    return RollbackMethod.GUI_STATE if restored else None


async def gui_finish(
    deps: GateDeps, proposal: Proposal, rollback: RollbackMethod | None = None
) -> None:
    """Tell the surface `proposal` is terminal, so the recorder can close its entry."""
    if deps.gui is not None and uses_gui(proposal):
        await deps.gui.finish(proposal, rollback)
