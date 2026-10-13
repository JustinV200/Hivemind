"""Provide the Comb Registry: the catalogue of promoted tools, at hive and Cell scope.

The Comb Registry is the source of truth for executable tool definitions, versions and scope
(codingrules 8.4). A tool is promoted into it only with a passing Quarantine Comb report (the
sandbox every new tool must pass), at HIVE scope (usable on every Cell) by the Queen or at CELL
scope (usable on one Cell) by that Cell's Warden. Versions are immutable; `retire` marks a row
RETIRED and `rollback` makes an earlier PROMOTED version current again. The registry stores package
hashes, never bytes: the bytes are Basket blobs named by the same hash.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside the royal_jelly package.
    Called by the promotion flow, the Queen's tick code, a Warden's tool loader and `hive tools`;
    calls into the spec package, `hivemind.common` and `hivemind.guard` (via the spec).

Key invariants:
    - `promote` is the only writer that creates a PROMOTED row and it has no force flag.
    - The registry records nothing on the trail; each write returns a `RegistryChange` and the
      caller records the event.
    - At most one current version per tool, flipped in the write's own transaction.

See Also:
    - docs/adr/0046 (tool spec, Comb Registry and scopes) for the decisions.
    - hivemind.royal_jelly.spec for the ToolSpec and QuarantineReport it stores.

Public API:
    - CombRegistry (protocol): list_tools, get, visible_tools, promote, retire, rollback.
    - SqliteCombRegistry, apply_comb_registry_migrations (sqlite): the durable implementation.
    - InMemoryCombRegistry (fake): the same behaviour over a dict, for tests and dry runs.
    - CombToolRow, ToolKey, PromotionOffer, RegistryChange, ChangeKind (rows): stored and passed
      values.
    - QueenPrincipal, WardenPrincipal, Principal (principals): who may act.
"""

from hivemind.royal_jelly.registry.fake import InMemoryCombRegistry
from hivemind.royal_jelly.registry.principals import Principal, QueenPrincipal, WardenPrincipal
from hivemind.royal_jelly.registry.protocol import CombRegistry
from hivemind.royal_jelly.registry.rows import (
    ChangeKind,
    CombToolRow,
    PromotionOffer,
    RegistryChange,
    ToolKey,
)
from hivemind.royal_jelly.registry.sqlite import SqliteCombRegistry, apply_comb_registry_migrations

__all__ = [
    "ChangeKind",
    "CombRegistry",
    "CombToolRow",
    "InMemoryCombRegistry",
    "Principal",
    "PromotionOffer",
    "QueenPrincipal",
    "RegistryChange",
    "SqliteCombRegistry",
    "ToolKey",
    "WardenPrincipal",
    "apply_comb_registry_migrations",
]
