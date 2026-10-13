"""Provide InMemoryCombRegistry, a CombRegistry fake that holds its rows in a dict.

Later waves (the promotion flow, the Queen's tick code, a Warden's tool loader) need a registry in
their own tests without a SQLite file. This fake implements the whole `CombRegistry` protocol over
a dict keyed by (name, scope, Cell, version) and shares `registry.rules` with the SQLite store, so
it refuses exactly what the real one refuses; the contract suite runs both. It is shipped code
(codingrules 3: fakes live beside their Protocol), held to the same standard as the real store.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.registry. Used by
    tests, `hive doctor` style dry runs and demo paths. Calls into `registry.rules` and the row
    values only.

Key invariants:
    - Same observable behaviour as `SqliteCombRegistry`, including every refusal and the
      one-current-version-per-tool rule; the contract suite proves it.
    - Not shared across event loops or threads: it keeps no lock because it never awaits between
      a check and its write.

See Also:
    - hivemind.royal_jelly.registry.sqlite for the durable implementation.
    - tests/contracts/test_comb_registry_contract.py for the suite both pass.
"""

from __future__ import annotations

from dataclasses import replace

from hivemind.royal_jelly.errors import ToolNotFoundError
from hivemind.royal_jelly.registry.principals import Principal
from hivemind.royal_jelly.registry.rows import (
    ChangeKind,
    CombToolRow,
    PromotionOffer,
    RegistryChange,
    ToolKey,
)
from hivemind.royal_jelly.registry.rules import (
    WriteContext,
    build_promoted_row,
    check_authority,
    check_duplicate,
    check_promotion,
    check_rollback,
    pick_visible,
    version_key,
)
from hivemind.royal_jelly.spec import QuarantineReport, ToolSpec
from hivemind.royal_jelly.tool_state import ToolState, transition
from waggle.clock import Clock
from waggle.ids import CellId
from waggle.messages.tool.authoring import ToolScope

__all__ = ["InMemoryCombRegistry"]

_Identity = tuple[str, ToolScope, CellId | None, str]  # name, scope, cell, version.


class InMemoryCombRegistry:
    """A CombRegistry over a dict; the fake every later wave's tests use."""

    def __init__(self, clock: Clock) -> None:
        """Start empty.

        Args:
            clock: Injected clock; every write's instant comes from it.
        """
        self._clock = clock
        # The rows, keyed by version identity; replaced (never mutated) on each write.
        self._rows: dict[_Identity, CombToolRow] = {}

    async def list_tools(
        self,
        scope: ToolScope | None = None,
        cell_id: CellId | None = None,
        *,
        current_only: bool = False,
    ) -> tuple[CombToolRow, ...]:
        """List stored versions; see CombRegistry.list_tools."""
        wanted = [
            row
            for row in self._rows.values()
            if (scope is None or row.scope is scope)
            and (cell_id is None or row.cell_id == cell_id)
            and (row.is_current or not current_only)
        ]
        wanted.sort(
            key=lambda r: (r.name, r.scope.value, str(r.cell_id or ""), version_key(r.version))
        )
        return tuple(wanted)

    async def get(
        self, name: str, scope: ToolScope, cell_id: CellId | None, version: str | None = None
    ) -> CombToolRow:
        """Return one version of a tool; see CombRegistry.get."""
        key = ToolKey(name, scope, cell_id)  # Validates that cell_id agrees with scope.
        row = (
            self._current(key) if version is None else self._rows.get(self._identity(key, version))
        )
        if row is None:
            wanted = "a current version" if version is None else f"version {version}"
            raise ToolNotFoundError(f"Tool {name!r} at {scope.value} scope has no {wanted}.")
        return row

    async def visible_tools(self, cell_id: CellId) -> tuple[CombToolRow, ...]:
        """Return the current tools a Cell's bees may be offered; see CombRegistry."""
        return pick_visible(
            row
            for row in self._rows.values()
            if row.is_current and (row.scope is ToolScope.HIVE or row.cell_id == cell_id)
        )

    async def promote(
        self,
        spec: ToolSpec,
        report: QuarantineReport,
        offer: PromotionOffer,
        principal: Principal,
        reason: str,
    ) -> RegistryChange:
        """Store a quarantined tool as its tool's new current version; see CombRegistry."""
        context = WriteContext(principal, reason, self._clock.now())
        check_promotion(spec, report, offer, principal)
        key = ToolKey(spec.name, spec.scope, spec.cell_id)
        check_duplicate(self._rows.get(self._identity(key, spec.version)), spec)
        displaced = self._current(key)
        if displaced is not None:
            self._store(replace(displaced, is_current=False))
        row = build_promoted_row(
            spec, report, offer, context, displaced.version if displaced else None
        )
        self._store(row)
        return RegistryChange(
            ChangeKind.PROMOTED, row, row.supersedes_version, principal.label, reason, context.at
        )

    async def retire(
        self, key: ToolKey, version: str, principal: Principal, reason: str
    ) -> RegistryChange:
        """Mark one version RETIRED; see CombRegistry.retire."""
        context = WriteContext(principal, reason, self._clock.now())
        check_authority(key, principal, queen_may_act_on_cell=True)
        row = self._rows.get(self._identity(key, version))
        if row is None:
            raise ToolNotFoundError(f"Tool {key.name!r} has no version {version} to retire.")
        retired = replace(
            row,
            state=transition(row.state, ToolState.RETIRED),
            is_current=False,
            retired_at=context.at,
            retired_reason=reason,
        )
        self._store(retired)
        return RegistryChange(
            ChangeKind.RETIRED, retired, None, principal.label, reason, context.at
        )

    async def rollback(
        self, key: ToolKey, to_version: str, principal: Principal, reason: str
    ) -> RegistryChange:
        """Make an older PROMOTED version current again; see CombRegistry.rollback."""
        context = WriteContext(principal, reason, self._clock.now())
        check_authority(key, principal, queen_may_act_on_cell=True)
        current = self._current(key)
        target = check_rollback(
            key, self._rows.get(self._identity(key, to_version)), current, to_version
        )
        displaced = current.version if current is not None else None
        if current is not None:
            self._store(replace(current, is_current=False))
        restored = replace(target, is_current=True, supersedes_version=displaced)
        self._store(restored)
        return RegistryChange(
            ChangeKind.ROLLED_BACK, restored, displaced, principal.label, reason, context.at
        )

    def _identity(self, key: ToolKey, version: str) -> _Identity:
        """Return the dict key of one version of `key`."""
        return (key.name, key.scope, key.cell_id, version)

    def _store(self, row: CombToolRow) -> None:
        """Replace (or add) a row under its identity."""
        self._rows[self._identity(row.key, row.version)] = row

    def _current(self, key: ToolKey) -> CombToolRow | None:
        """Return `key`'s current row, if any."""
        return next(
            (r for r in self._rows.values() if r.key == key and r.is_current),
            None,
        )
