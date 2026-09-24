"""Decide whether a judge may be asked to lower one Nectar's label, and to what (ADR-0034, pure).

A Nectar is a raw deposit in the Honey Store (the Hive's knowledge base); its label is a
`HoneyClearance` (C0 public, C1 internal, C2 personal or sensitive), carried by the Honey rows
ripened from it too. Anything gathered on a Real Cell (a borrowed device, the Hive Stand included)
is C2 by the provenance floor, whatever it says, so a default C1 goal never reads it. ADR-0034 lets
an independent judge, or the human, lower such a label; `lowering_target` is the one rule that
says which Nectar may be proposed and to which label. It is evaluated when a proposal is filed and
again inside the transaction that applies it, so a merge, a raise or a taint in between leaves the
label where it is.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.honey_store.lowering`.
    Called by the lowering service (`review`, when filing) and the store's apply transaction
    (`hivemind.honey_store.store.sqlite.lowering`). Calls into `hivemind.cell`,
    `hivemind.honey_store.clearance` (raise_label) and `hivemind.honey_store.models` only; no I/O.

Key invariants:
    - A returned target always ranks strictly below the Nectar's current label and never below its
      declared label: a judge never takes a label below what any depositor said.
    - A Nectar written before ADR-0034 (any of its three labelling facts unknown) is never
      eligible, nor is one that is unripened, tainted, from a Night Veil Cell, or of HUMAN or WATCH
      origin.
    - The store's candidate scan (`lowering_candidates`) mirrors these clauses in SQL; the contract
      suite checks the two agree, so no ineligible row ever occupies a pass's bound.

See Also:
    - docs/adr/0034-honey-label-lowering-is-a-judge-reviewed-proposal.md for the rule itself.
    - hivemind.honey_store.clearance for intake_floor and raise_label, the rules it builds on.
    - hivemind.honey_store.models.nectar for the three facts it reads.
"""

from __future__ import annotations

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.honey_store.clearance import raise_label
from hivemind.honey_store.models import Nectar, NectarOrigin, NectarState

__all__ = ["HUMAN_ONLY_ORIGINS", "lowering_target"]

# ADR-0034: their C2 is the content's own nature -- a person's words, or observations of the
# operator's machine -- so only the human ever lowers them.
HUMAN_ONLY_ORIGINS = frozenset({NectarOrigin.HUMAN, NectarOrigin.WATCH})


def lowering_target(nectar: Nectar) -> HoneyClearance | None:
    """Return the label a judge may be asked to lower `nectar` to, or None when it is not eligible.

    Eligible when all of these hold (ADR-0034): it is ripened, not tainted and not from a Night
    Veil Cell; its origin is neither the human nor watch mode; its label is held up by the Real
    Cell floor alone (no depositor declared it, the floor reaches it); and the Ripener's own
    reading of the text ranks below it.

    Args:
        nectar: The stored Nectar, as the store reads it now.

    Returns:
        The higher of its declared label and the Ripener's reading (always strictly below its
        current label), or None when any clause fails, a labelling fact is unknown included.

    Example:
        A C2 Hive Stand deposit declared C1 that the Ripener read at C0 gets C1: the judge is
        asked whether the text may carry C1, never C0, because its depositor said C1.
    """
    # Only settled, trusted Nectar outside the Night Veil boundary: an unripened row has no
    # reading, a tainted one is suspect, and a Night Veil export stays exactly as it crossed.
    if nectar.state is not NectarState.RIPENED or nectar.tainted:
        return None
    if nectar.origin_tier is CombShieldLevel.NIGHT_VEIL or nectar.origin in HUMAN_ONLY_ORIGINS:
        return None
    declared, floor = nectar.declared_clearance, nectar.floor_clearance
    reading = nectar.ripener_clearance
    # A row from before ADR-0034 (or with no model reading) has facts nobody knows: never lower it.
    if declared is None or floor is None or reading is None:
        return None
    current = nectar.clearance
    # Held up by the Real Cell floor alone: the floor reaches the label and no depositor did.
    if floor.rank < current.rank or declared.rank >= current.rank:
        return None
    # The Ripener must have read the text itself as less sensitive than its label.
    if reading.rank >= current.rank:
        return None
    # Both rank below the label, so the higher of them does too: never below what was declared.
    return raise_label(declared, reading)
