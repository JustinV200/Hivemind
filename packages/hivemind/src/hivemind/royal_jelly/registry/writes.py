"""Run the Comb Registry's three writes as single SQLite transactions.

`promote`, `retire` and `rollback` are the only writers of `comb_tools`; each is one function here,
run on the store's connection thread, that reads what it needs, applies the pure checks of
`registry.rules`, and writes inside one `BEGIN IMMEDIATE` transaction. The `is_current` flag is
flipped in that same transaction (clear the old current, set the new one), so a reader never sees
two current versions or none mid-change, and a refused write leaves no trace. None of them records
anything on the Pheromone Trail (the audit log): each returns a `RegistryChange` and the caller
records the matching `tool.*` event.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.registry. Called
    by `SqliteCombRegistry` through its `ConnectionThread`; calls into `registry.rules` (policy),
    `registry.sql` (statements), `hivemind.common.sqlite.transaction` and `tool_state`.

Key invariants:
    - Every function checks authority and policy before it opens a transaction, then re-reads
      inside it: a concurrent writer cannot slip a duplicate or a second current version past a
      check made on stale data.
    - These three functions are the only code that issues INSERT or UPDATE against `comb_tools`.

See Also:
    - docs/adr/0046 (tool spec, Comb Registry and scopes) for the write rules.
    - hivemind.royal_jelly.registry.rules for the checks applied here.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace

from hivemind.common.sqlite import transaction
from hivemind.royal_jelly.errors import ToolNotFoundError
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
)
from hivemind.royal_jelly.registry.sql import (
    CLEAR_CURRENT_SQL,
    INSERT_SQL,
    MARK_CURRENT_SQL,
    RETIRE_SQL,
    SELECT_CURRENT_SQL,
    SELECT_ONE_SQL,
    key_values,
    row_to_values,
    values_to_row,
)
from hivemind.royal_jelly.spec import QuarantineReport, ToolSpec
from hivemind.royal_jelly.tool_state import ToolState, transition

__all__ = [
    "fetch_current",
    "fetch_version",
    "promote_transaction",
    "retire_transaction",
    "rollback_transaction",
]


def fetch_version(connection: sqlite3.Connection, key: ToolKey, version: str) -> CombToolRow | None:
    """Return the row at `key` and `version`, or None."""
    record = connection.execute(SELECT_ONE_SQL, (*key_values(key), version)).fetchone()
    return None if record is None else values_to_row(record)


def fetch_current(connection: sqlite3.Connection, key: ToolKey) -> CombToolRow | None:
    """Return `key`'s current row, or None when it has none (never promoted, or retired)."""
    record = connection.execute(SELECT_CURRENT_SQL, key_values(key)).fetchone()
    return None if record is None else values_to_row(record)


def promote_transaction(
    connection: sqlite3.Connection,
    spec: ToolSpec,
    report: QuarantineReport,
    offer: PromotionOffer,
    context: WriteContext,
) -> RegistryChange:
    """Check a promotion and store it as the tool's new current version, atomically."""
    # Pure checks first, so a refusal never takes the write lock.
    check_promotion(spec, report, offer, context.principal)
    key = ToolKey(spec.name, spec.scope, spec.cell_id)
    with transaction(connection):
        # Re-read under the lock: the duplicate check must see every committed row.
        check_duplicate(fetch_version(connection, key, spec.version), spec)
        displaced = fetch_current(connection, key)
        # Clear the old current before the insert, or the one-current unique index would object.
        if displaced is not None:
            connection.execute(CLEAR_CURRENT_SQL, key_values(key))
        row = build_promoted_row(
            spec, report, offer, context, displaced.version if displaced else None
        )
        connection.execute(INSERT_SQL, row_to_values(row))
    return RegistryChange(
        ChangeKind.PROMOTED,
        row,
        row.supersedes_version,
        context.principal.label,
        context.reason,
        context.at,
    )


def retire_transaction(
    connection: sqlite3.Connection, key: ToolKey, version: str, context: WriteContext
) -> RegistryChange:
    """Mark one version RETIRED (never deleted) and clear its current flag, atomically."""
    check_authority(key, context.principal, queen_may_act_on_cell=True)
    with transaction(connection):
        row = fetch_version(connection, key, version)
        if row is None:
            raise ToolNotFoundError(f"Tool {key.name!r} has no version {version} to retire.")
        # PROMOTED -> RETIRED is the one edge; retiring a RETIRED version raises here.
        state = transition(row.state, ToolState.RETIRED)
        connection.execute(
            RETIRE_SQL, (context.at.isoformat(), context.reason, *key_values(key), version)
        )
    retired = replace(
        row, state=state, is_current=False, retired_at=context.at, retired_reason=context.reason
    )
    return RegistryChange(
        ChangeKind.RETIRED, retired, None, context.principal.label, context.reason, context.at
    )


def rollback_transaction(
    connection: sqlite3.Connection, key: ToolKey, to_version: str, context: WriteContext
) -> RegistryChange:
    """Make an older PROMOTED version current again, atomically."""
    check_authority(key, context.principal, queen_may_act_on_cell=True)
    with transaction(connection):
        current = fetch_current(connection, key)
        target = check_rollback(
            key, fetch_version(connection, key, to_version), current, to_version
        )
        displaced = current.version if current is not None else None
        # Same order as promote: clear the old current first, then flag the target.
        if current is not None:
            connection.execute(CLEAR_CURRENT_SQL, key_values(key))
        connection.execute(MARK_CURRENT_SQL, (displaced, *key_values(key), to_version))
    restored = replace(target, is_current=True, supersedes_version=displaced)
    return RegistryChange(
        ChangeKind.ROLLED_BACK,
        restored,
        displaced,
        context.principal.label,
        context.reason,
        context.at,
    )
