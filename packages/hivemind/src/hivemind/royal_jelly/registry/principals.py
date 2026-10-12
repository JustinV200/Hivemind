"""Define the principals that may act on the Comb Registry: the Queen and one Cell's Warden.

A principal is the identity a registry write is made as. The Comb Registry (the catalogue of
promoted tools) lets only two kinds of bee change it: the Queen (the Hive's central orchestrator),
who alone may act at HIVE scope (a tool every Cell can use), and a Warden (the always-on supervisor
of one Cell), who may act only at CELL scope and only for its own Cell. The registry never trusts a
claim carried in a message: an in-process Warden builds its principal from its own Cell, and the
Queen builds a remote Warden's from the `WardenLink.cell` of the connection the message arrived on.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.registry. Built
    by the Queen's tick code and by a Warden for itself; read by `hivemind.royal_jelly.registry.
    rules`, which decides what each principal may do. Calls into waggle's ids only.

Key invariants:
    - Plain frozen values: holding one grants nothing by itself; the registry's rules check the
      principal against the scope and Cell of every write.
    - `label` is the stable text stored in the row's `promoted_by` column: "queen" or a Warden id.

See Also:
    - docs/adr/0046 (tool spec, Comb Registry and scopes) for the authority rules.
    - hivemind.royal_jelly.registry.rules for the checks that read these.
"""

from __future__ import annotations

from dataclasses import dataclass

from waggle.ids import CellId, WardenId

QUEEN_LABEL = "queen"  # The label stored for the Queen; a Warden id never collides (prefixed).

__all__ = ["QUEEN_LABEL", "Principal", "QueenPrincipal", "WardenPrincipal"]


@dataclass(frozen=True, slots=True)
class QueenPrincipal:
    """The Queen: may act at HIVE scope, and may retire or roll back at either scope."""

    @property
    def label(self) -> str:
        """Return the text stored as `promoted_by` for the Queen."""
        return QUEEN_LABEL


@dataclass(frozen=True, slots=True)
class WardenPrincipal:
    """A Warden acting for its own Cell: may act only at CELL scope on that Cell's tools."""

    warden_id: WardenId  # The acting Warden.
    cell_id: CellId  # The one Cell it supervises; built from its own Cell or WardenLink.cell.

    @property
    def label(self) -> str:
        """Return the text stored as `promoted_by` for this Warden (its id)."""
        return str(self.warden_id)


# The closed set of principals; a `match` over it is exhaustive for mypy.
Principal = QueenPrincipal | WardenPrincipal
