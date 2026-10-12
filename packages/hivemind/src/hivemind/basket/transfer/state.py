"""Define the transfer phases and the single table of moves between them.

One transfer of one blob is the same small machine on both ends: nothing happens yet (IDLE), an
offer has been made or accepted (OFFERED), chunks are flowing (TRANSFERRING), and then it ends in
COMPLETE (the whole blob verified) or REFUSED (a by-rule refusal, or an integrity failure). The
sender and the receiver each hold one phase and move it only through `advance`, so an impossible
move is a loud `TransferError`, never a silent state. Appendix C of the coding rules lists this
machine (owner: `hivemind.basket.transfer`; it persists nowhere, a transfer restarts from the
receiver's partial file).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.basket.transfer`.
    Called by `sender` and `receiver`. Calls into `hivemind.basket.errors` only.

Key invariants:
    - COMPLETE and REFUSED are terminal: no edge leaves them.
    - Every edge in `TRANSITIONS` is commented with the event that takes it.

See Also:
    - .claude/codingrules.md section 9 and Appendix C for the state-machine rule.
    - hivemind.basket.transfer.sender and receiver, the two holders of a phase.
"""

from __future__ import annotations

from enum import Enum
from types import MappingProxyType

from hivemind.basket.errors import TransferError, TransferFailure

__all__ = ["TRANSITIONS", "TransferPhase", "advance"]


class TransferPhase(Enum):
    """Where one transfer of one blob has got to."""

    IDLE = "IDLE"  # No offer made or received yet.
    OFFERED = "OFFERED"  # An offer stands; no chunk has moved.
    TRANSFERRING = "TRANSFERRING"  # At least one chunk has moved.
    COMPLETE = "COMPLETE"  # The whole blob arrived and verified. Terminal.
    REFUSED = "REFUSED"  # Refused by rule, or failed integrity. Terminal.


# The single transition table. Read-only so no caller can add an edge at run time.
TRANSITIONS = MappingProxyType(
    {
        # An offer is made (sender) or accepted (receiver); or the receiver refuses it outright.
        TransferPhase.IDLE: frozenset({TransferPhase.OFFERED, TransferPhase.REFUSED}),
        # The first chunk moves; or the offer is refused after acceptance (a late rule check).
        TransferPhase.OFFERED: frozenset({TransferPhase.TRANSFERRING, TransferPhase.REFUSED}),
        # The last chunk verifies; or an integrity failure / a mid-transfer refusal ends it.
        TransferPhase.TRANSFERRING: frozenset({TransferPhase.COMPLETE, TransferPhase.REFUSED}),
        # Terminal: a finished transfer starts over only as a new transfer object.
        TransferPhase.COMPLETE: frozenset(),
        TransferPhase.REFUSED: frozenset(),
    }
)


def advance(current: TransferPhase, target: TransferPhase, sha256: str) -> TransferPhase:
    """Return `target` if the table allows the move from `current`, else raise.

    Args:
        current: The phase now.
        target: The phase wanted.
        sha256: The blob's hash, named in the error.

    Returns:
        `target`.

    Raises:
        TransferError: `complete` when `current` is terminal (the transfer already ended),
            `unknown` for any other move the table does not list.
    """
    if target in TRANSITIONS[current]:
        return target
    # A terminal phase has no way out; say so, as that is the common peer mistake.
    reason: TransferFailure = "unknown" if TRANSITIONS[current] else "complete"
    raise TransferError(
        reason, f"transfer of {sha256} cannot move from {current.value} to {target.value}."
    )
