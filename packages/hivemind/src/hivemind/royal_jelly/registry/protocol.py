"""Define CombRegistry: the persistence and authority protocol of the Comb Registry.

The Comb Registry is the catalogue of promoted tools and the source of truth for which executable
tool definitions exist, at which version and scope (codingrules 8.4). This protocol is its one
seam, mirroring `HoneyStore`: `SqliteCombRegistry` is the durable implementation and
`InMemoryCombRegistry` the fake. `promote` is the only writer that creates a PROMOTED row, and it
refuses anything the Quarantine Comb (the sandbox every new tool must pass) has not proven. The
registry stores package hashes, never bytes: the bytes are Basket blobs.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.registry.
    Implemented by the SQLite store and the in-memory fake; called by the promotion flow, the
    Queen's tick code, a Warden's tool loader and `hive tools`. Calls into the row values, the
    principals and the spec package only.

Key invariants:
    - Versions are immutable: a (name, scope, Cell, version) is promoted at most once, and
      `retire` marks a row RETIRED, never deletes it.
    - `promote`, `retire` and `rollback` record nothing on the Pheromone Trail; each returns a
      `RegistryChange` and the caller records the matching event (`tool.promoted`,
      `tool.rolled_back`, `tool.retired`).
    - At most one current version per tool, flipped in the same transaction as the write.
    - There is no force flag anywhere (codingrules section 15).

See Also:
    - docs/adr/0046 (tool spec, Comb Registry and scopes) for the decisions.
    - hivemind.royal_jelly.registry.rules for the refusals every implementation applies.
    - hivemind.honey_store.store.protocol for the store protocol shape this follows.
"""

from __future__ import annotations

from typing import Protocol

from hivemind.royal_jelly.registry.principals import Principal
from hivemind.royal_jelly.registry.rows import CombToolRow, PromotionOffer, RegistryChange, ToolKey
from hivemind.royal_jelly.spec import QuarantineReport, ToolSpec
from waggle.ids import CellId
from waggle.messages.tool.authoring import ToolScope

__all__ = ["CombRegistry"]


class CombRegistry(Protocol):
    """Read promoted tools and make the three writes that change them."""

    async def list_tools(
        self,
        scope: ToolScope | None = None,
        cell_id: CellId | None = None,
        *,
        current_only: bool = False,
    ) -> tuple[CombToolRow, ...]:
        """List stored versions, optionally narrowed.

        Args:
            scope: Only rows at this scope, or every scope when None.
            cell_id: Only rows of this Cell (so only CELL-scope rows), or every Cell when None.
            current_only: Only each tool's current version.

        Returns:
            Rows ordered by name, scope, Cell and numeric version; retired rows included unless
            `current_only`.
        """
        ...

    async def get(
        self, name: str, scope: ToolScope, cell_id: CellId | None, version: str | None = None
    ) -> CombToolRow:
        """Return one version of a tool.

        Args:
            name: The tool's name.
            scope: HIVE or CELL.
            cell_id: The owning Cell for CELL scope; None for HIVE.
            version: The version wanted; None means the current one.

        Returns:
            The row.

        Raises:
            ToolNotFoundError: No such version, or no current version (never promoted or retired).
            ValueError: `cell_id` does not agree with `scope`.
        """
        ...

    async def visible_tools(self, cell_id: CellId) -> tuple[CombToolRow, ...]:
        """Return the current tools a Cell's bees may be offered.

        Args:
            cell_id: The Cell asking.

        Returns:
            Current HIVE rows plus that Cell's current CELL rows, one per name (a HIVE row
            shadows a CELL row of the same name), ordered by name.
        """
        ...

    async def promote(
        self,
        spec: ToolSpec,
        report: QuarantineReport,
        offer: PromotionOffer,
        principal: Principal,
        reason: str,
    ) -> RegistryChange:
        """Store a quarantined tool as its tool's new current version.

        The only writer that creates a PROMOTED row. Records nothing on the trail: the caller
        turns the returned change into a `tool.promoted` event.

        Args:
            spec: The spec offered; its scope and Cell decide who may promote.
            report: The Quarantine Comb's report, which must have passed and match the spec hash,
                and `offer`'s package hash and size.
            offer: The package hash and size now in the Basket, and whether the Queen reviewed a
                REAL_CELL-path report.
            principal: Who promotes: the Queen at HIVE scope, the owning Warden at CELL scope.
            reason: Why it is promoted (1 to 1000 characters), for the trail.

        Returns:
            The change; `change.row` is the new current row.

        Raises:
            WrongPrincipalError: Wrong kind of principal for the scope.
            WrongCellError: A Warden promoting for another Cell.
            ReportFailedError: The report did not pass.
            ReportMismatchError: The report proves a different spec or package.
            NeedsReviewError: REAL_CELL-path report at HIVE scope without `offer.reviewed`.
            VersionExistsError: That version already exists for that (name, scope, Cell).
        """
        ...

    async def retire(
        self, key: ToolKey, version: str, principal: Principal, reason: str
    ) -> RegistryChange:
        """Mark one version RETIRED; the row stays as history and never becomes current again.

        Retiring the current version leaves the tool with no current version until a promotion
        or a rollback; it does not fall back to an older one by itself.

        Args:
            key: Which tool.
            version: The version to retire.
            principal: The Queen, or the owning Warden for a CELL-scope tool.
            reason: Why (1 to 1000 characters), for the trail.

        Returns:
            The change; `change.row` is the retired row.

        Raises:
            WrongPrincipalError: Not the Queen at HIVE scope.
            WrongCellError: A Warden retiring another Cell's tool.
            ToolNotFoundError: No such version.
            ToolTransitionError: The version is already retired.
        """
        ...

    async def rollback(
        self, key: ToolKey, to_version: str, principal: Principal, reason: str
    ) -> RegistryChange:
        """Make an older PROMOTED version current again; its own passing report is its evidence.

        Args:
            key: Which tool.
            to_version: The older version to restore.
            principal: The Queen, or the owning Warden for a CELL-scope tool.
            reason: Why (1 to 1000 characters), for the trail.

        Returns:
            The change; `change.row` is the restored row and `change.supersedes_version` the
            version it displaced.

        Raises:
            WrongPrincipalError: Not the Queen at HIVE scope.
            WrongCellError: A Warden rolling back another Cell's tool.
            ToolNotFoundError: No such version.
            RollbackRefusedError: Retired, already current, not older, or its report fails.
        """
        ...
