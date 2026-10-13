"""Define CombToolRow, the registry's stored row, and the value types the registry passes around.

The Comb Registry (the catalogue of promoted tools) keeps one row per (name, scope, Cell, version).
`CombToolRow` is that row as Python values: every column the `comb_tools` table holds, plus
`is_current` (whether this version is the one callers get when they name no version). Around it
sit the small values the protocol needs: `ToolKey` (which tool, ignoring version), `PromotionOffer`
(the package facts a promotion claims), `ChangeKind` and `RegistryChange` (what a write did, so the
caller can record the matching event; the registry records nothing on the trail itself).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.registry. Built
    by the SQLite store and the in-memory fake, read by `hivemind.royal_jelly.registry.rules`, the
    promotion flow, the Worker's tool loader and the CLI.

Key invariants:
    - A row is immutable evidence: its spec, hashes and report never change after promotion; only
      `is_current`, `state`, `retired_at`, `retired_reason` and `supersedes_version` do.
    - `is_current` implies `state` is PROMOTED (the table enforces it as well).
    - `ToolKey.cell_id` is set exactly when scope is CELL.

See Also:
    - docs/adr/0046 (tool spec, Comb Registry and scopes) for the columns.
    - hivemind.royal_jelly.registry.sqlite for the table these map.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from hivemind.royal_jelly.spec import QuarantineReport, ToolSpec
from hivemind.royal_jelly.tool_state import ToolState
from waggle.ids import CellId, ToolId
from waggle.messages.tool.authoring import QuarantinePath, ToolScope

__all__ = ["ChangeKind", "CombToolRow", "PromotionOffer", "RegistryChange", "ToolKey"]


@dataclass(frozen=True, slots=True)
class ToolKey:
    """Which tool, ignoring version: a name at a scope (and, for CELL, a Cell)."""

    name: str  # The tool's name (the capability tool:<name>).
    scope: ToolScope  # HIVE or CELL.
    cell_id: CellId | None  # The owning Cell for CELL scope; None for HIVE.

    def __post_init__(self) -> None:
        """Refuse a key whose Cell does not agree with its scope."""
        # Same both-directions rule ToolSpec enforces; a key that breaks it names nothing.
        if (self.scope is ToolScope.CELL) != (self.cell_id is not None):
            raise ValueError(
                f"ToolKey cell_id is required exactly when scope is CELL, got scope "
                f"{self.scope.value} with cell_id {self.cell_id}."
            )


@dataclass(frozen=True, slots=True)
class PromotionOffer:
    """The package facts a promotion claims; the registry checks them against the report."""

    package_sha256: str  # Hash of the package bytes now in the Basket.
    package_size_bytes: int  # Size of those bytes.
    reviewed: bool = False  # True once the Queen's awake review accepted a REAL_CELL-path report.


@dataclass(frozen=True, slots=True)
class CombToolRow:
    """One stored version of one tool: every `comb_tools` column plus `is_current`."""

    tool_id: ToolId
    name: str
    version: str
    scope: ToolScope
    cell_id: CellId | None  # Stored as '' for HIVE; surfaced as None.
    spec_json: str  # The ToolSpec in canonical form.
    package_sha256: str
    package_size: int
    report_json: str  # The passing QuarantineReport, verbatim.
    quarantine_path: QuarantinePath
    reviewed: bool  # The promotion's review flag.
    promoted_by: str  # The principal's label.
    promoted_at: datetime
    state: ToolState  # PROMOTED or RETIRED.
    is_current: bool  # Whether naming no version resolves to this row.
    supersedes_version: str | None  # The version it displaced when it last became current.
    retired_at: datetime | None
    retired_reason: str | None
    reason: str  # Why it was promoted.

    @property
    def key(self) -> ToolKey:
        """Return the tool this row is a version of."""
        return ToolKey(self.name, self.scope, self.cell_id)

    @property
    def spec(self) -> ToolSpec:
        """Parse and return the stored spec."""
        return ToolSpec.from_json(self.spec_json)

    @property
    def report(self) -> QuarantineReport:
        """Parse and return the stored report."""
        return QuarantineReport.from_json(self.report_json)


class ChangeKind(Enum):
    """What a registry write did, so the caller records the matching trail event."""

    PROMOTED = "PROMOTED"  # A new version, now current: record `tool.promoted`.
    ROLLED_BACK = (
        "ROLLED_BACK"  # An older version current again: `tool.promoted` and `tool.rolled_back`.
    )
    RETIRED = "RETIRED"  # A version withdrawn: record `tool.retired`.


@dataclass(frozen=True, slots=True)
class RegistryChange:
    """The description a write returns; the registry itself records nothing on the trail."""

    kind: ChangeKind  # Which write this was.
    row: CombToolRow  # The row as it stands after the write.
    supersedes_version: str | None  # The version the write displaced as current, if any.
    by: str  # The acting principal's label.
    reason: str  # The reason the principal gave.
    at: datetime  # The registry clock's instant for the write.
