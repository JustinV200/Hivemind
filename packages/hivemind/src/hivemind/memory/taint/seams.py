"""Declare the taint's phase 7 seam: the House Bee duty that re-ripens tainted Nectar.

Roadmap step 10.6d: "A House Bee duty re-ripens tainted Nectar with the flagged span stripped into
a new Honey item that starts tainted and goes to the judge; the old item is retired, never
edited." Nectar (raw material waiting to be ripened into Honey, the cold knowledge tier) and the
Honey Store arrive in phase 7, so this module only declares the duty's shape, as a Protocol the
House Bee's sweep will implement then; nothing implements or calls it yet, on purpose (a fake here
would be a second, untested Honey Store). The contract it fixes now, so phase 7 builds against it:
the old deposit is retired whole and never edited (forensics keep the evidence, ADR-0043); the new
item inherits the old one's `TaintSource` through `hivemind.memory.taint.set.taint_memory` (a
label carried over, which the only-setter test will allow for the sweep's module alone); and it
reaches prompts only after `hivemind.memory.taint.clear.clear_taint` returns CLEARED for it.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.memory.taint`. To be
    implemented by the House Bee sweep (`hivemind.workers.roles.house_bee`) in phase 7, over the
    Honey Store's own `TaintLedger`. Calls into this package's `marker` and
    `hivemind.memory.context` only.

Key invariants:
    - Declared, not implemented: no module in this repository satisfies or calls
      `TaintedNectarRipener` until phase 7 lands the Honey Store.

See Also:
    - .claude/roadmap.md steps 7.4 (Nectar intake), 7.6 (the House Bee) and 10.6d.
    - hivemind.memory.taint.ledger for TaintLedger, the store seam the same phase fills.
"""

from __future__ import annotations

from typing import Protocol

from hivemind.memory.context import MemoryContext
from hivemind.memory.taint.marker import TaintTarget

__all__ = ["TaintedNectarRipener"]


class TaintedNectarRipener(Protocol):
    """Re-ripen one tainted Nectar deposit into a new, still-tainted Honey item (phase 7)."""

    async def reripen(self, nectar: TaintTarget, ctx: MemoryContext) -> TaintTarget:
        """Strip the flagged span from `nectar` into a new Honey item, and retire the old one.

        Args:
            nectar: A TAINTED Nectar deposit (`TaintedKind.NECTAR`).
            ctx: The memory tables, identity and clock the new item's label is written with.

        Returns:
            The new Honey item, labelled TAINTED with the old item's source, waiting for the
            judge; the old deposit is retired whole, never edited.
        """
        ...
