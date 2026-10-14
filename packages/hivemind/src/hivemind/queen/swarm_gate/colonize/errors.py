"""Define ColonizeStepError: one step of moving a Warden onto a device (or off it) that failed.

Colonization is a short sequence of steps, each of which can go wrong in a way the Queen answers by
undoing what she did so far: the packet refused an order or never answered it, the link to the
device was lost, the Warden it started never dialled in. Each is raised as this one error, carrying
a single stable word (`packet_refused`, `packet_silent`, `link_lost`, `start_failed`,
`warden_never_attached`, ...) so the flow's rollback and the trail record the same thing and no
address, path or text a device wrote ever reaches either.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.colonize`. Raised by `.steps` and `.flow`, caught by `.flow`, which
    rolls the move back. Calls into `hivemind.swarm` (SwarmError) only.

Key invariants:
    - The word is a stable lowercase identifier, never free text or something a device sent.
    - It is a `SwarmError`, so a caller that handles the Swarm's failures handles this too.

See Also:
    - hivemind.queen.swarm_gate.colonize.flow for where it is caught and rolled back.
"""

from __future__ import annotations

from typing import ClassVar

from hivemind.swarm import SwarmError

__all__ = ["ColonizeStepError", "word_for"]


class ColonizeStepError(SwarmError):
    """Raise when a colonization step fails; `word` says which way, as a stable identifier."""

    code: ClassVar[str] = "hive.swarm.colonize_step_failed"

    def __init__(self, word: str) -> None:
        """Build the error for a step that failed.

        Args:
            word: A short stable lowercase identifier for how it failed.
        """
        super().__init__(f"A colonization step failed: {word}.")
        self.word = word


def word_for(error: Exception) -> str:
    """Return the stable word a failure is recorded as: its own, or `internal_error`.

    Args:
        error: Whatever ended a flow.

    Returns:
        The step's word for a `ColonizeStepError`; for any other error the one word that says the
        Queen's own bookkeeping failed, since its message may carry text a device or a store wrote.
    """
    return error.word if isinstance(error, ColonizeStepError) else "internal_error"
