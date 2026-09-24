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
    `hivemind.honey_store.clearance` (held_by_floor_alone, raise_label) and
    `hivemind.honey_store.models` only; no I/O.

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
    - hivemind.honey_store.clearance for intake_floor, held_by_floor_alone and raise_label, the
      rules it builds on.
    - hivemind.honey_store.models.nectar for the three facts it reads.
"""

from __future__ import annotations

from hivemind.cell import HoneyClearance
from hivemind.honey_store.clearance import held_by_floor_alone, raise_label
from hivemind.honey_store.models import Nectar, NectarState

__all__ = ["lowering_target"]


def lowering_target(nectar: Nectar) -> HoneyClearance | None:
    """Return the label a judge may be asked to lower `nectar` to, or None when it is not eligible.

    Eligible when all of these hold (ADR-0034): it is ripened; its label is held up by the Real
    Cell floor alone (`held_by_floor_alone`: untainted, not from a Night Veil Cell, not of HUMAN
    or WATCH origin, the floor reaching the label and no depositor declaring it); and the
    Ripener's own reading of the text ranks below it.

    Args:
        nectar: The stored Nectar, as the store reads it now.

    Returns:
        The higher of its declared label and the Ripener's reading (always strictly below its
        current label), or None when any clause fails, a labelling fact is unknown included.

    Example:
        A C2 Hive Stand deposit declared C1 that the Ripener read at C0 gets C1: the judge is
        asked whether the text may carry C1, never C0, because its depositor said C1.
    """
    # Only a settled row the floor alone holds up: an unripened one has no reading yet.
    if nectar.state is not NectarState.RIPENED or not held_by_floor_alone(nectar):
        return None
    declared, reading = nectar.declared_clearance, nectar.ripener_clearance
    # The Ripener must have read the text itself as less sensitive than its label; `declared` is
    # known already (held_by_floor_alone refuses a row without it), the check only narrows it.
    if declared is None or reading is None or reading.rank >= nectar.clearance.rank:
        return None
    # Both rank below the label, so the higher of them does too: never below what was declared.
    return raise_label(declared, reading)
