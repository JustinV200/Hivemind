"""Hold the Comb Registry's SQL statements and the mapping between rows and values.

The `comb_tools` table (migration 0001) is read and written through a handful of fixed statements.
They live here, apart from the store that runs them and the transactions that sequence them, so
the on-disk shape can be reviewed on its own and no statement is ever assembled from strings (every
value is a bound parameter). This module also owns the one awkward mapping: a HIVE-scope tool has
no Cell, but its `cell_id` column is stored as the empty text (never NULL) so the unique index on
(name, scope, cell_id, version) works; `cell_text` and `text_cell` are the only two places that
convert.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.registry. Called
    by `registry.writes` and `registry.sqlite`; calls into the row values and waggle's ids only.

Key invariants:
    - Every statement takes bound parameters only.
    - `row_to_values(values_to_row(r))` round-trips; HIVE scope always maps to the empty text.

See Also:
    - hivemind.royal_jelly.registry.migrations for the table these statements address.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from hivemind.royal_jelly.registry.rows import CombToolRow, ToolKey
from hivemind.royal_jelly.tool_state import ToolState
from waggle.ids import CellId, ToolId
from waggle.messages.tool.authoring import QuarantinePath, ToolScope

INSERT_SQL = """
INSERT INTO comb_tools
    (tool_id, name, version, scope, cell_id, spec_json, package_sha256, package_size,
     report_json, quarantine_path, reviewed, promoted_by, promoted_at, state, is_current,
     supersedes_version, retired_at, retired_reason, reason)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
SELECT_ONE_SQL = (
    "SELECT * FROM comb_tools WHERE name = ? AND scope = ? AND cell_id = ? AND version = ?"
)
SELECT_CURRENT_SQL = (
    "SELECT * FROM comb_tools WHERE name = ? AND scope = ? AND cell_id = ? AND is_current = 1"
)
SELECT_ALL_SQL = "SELECT * FROM comb_tools"
SELECT_VISIBLE_SQL = (
    "SELECT * FROM comb_tools WHERE is_current = 1 AND (scope = 'HIVE' OR cell_id = ?)"
)
CLEAR_CURRENT_SQL = (
    "UPDATE comb_tools SET is_current = 0 WHERE name = ? AND scope = ? AND cell_id = ? "
    "AND is_current = 1"
)
MARK_CURRENT_SQL = (
    "UPDATE comb_tools SET is_current = 1, supersedes_version = ? "
    "WHERE name = ? AND scope = ? AND cell_id = ? AND version = ?"
)
RETIRE_SQL = (
    "UPDATE comb_tools SET state = 'RETIRED', is_current = 0, retired_at = ?, retired_reason = ? "
    "WHERE name = ? AND scope = ? AND cell_id = ? AND version = ?"
)

__all__ = [
    "CLEAR_CURRENT_SQL",
    "INSERT_SQL",
    "MARK_CURRENT_SQL",
    "RETIRE_SQL",
    "SELECT_ALL_SQL",
    "SELECT_CURRENT_SQL",
    "SELECT_ONE_SQL",
    "SELECT_VISIBLE_SQL",
    "cell_text",
    "key_values",
    "row_to_values",
    "values_to_row",
]


def cell_text(cell_id: CellId | None) -> str:
    """Return the `cell_id` column text: the empty text for HIVE scope (never NULL)."""
    return "" if cell_id is None else str(cell_id)


def key_values(key: ToolKey) -> tuple[str, str, str]:
    """Return the (name, scope, cell_id) parameters that address a tool's versions."""
    return (key.name, key.scope.value, cell_text(key.cell_id))


def row_to_values(row: CombToolRow) -> tuple[object, ...]:
    """Return a row's INSERT parameters, in `INSERT_SQL`'s column order."""
    return (
        str(row.tool_id),
        row.name,
        row.version,
        row.scope.value,
        cell_text(row.cell_id),
        row.spec_json,
        row.package_sha256,
        row.package_size,
        row.report_json,
        row.quarantine_path.value,
        int(row.reviewed),
        row.promoted_by,
        row.promoted_at.isoformat(),
        row.state.value,
        int(row.is_current),
        row.supersedes_version,
        row.retired_at.isoformat() if row.retired_at else None,
        row.retired_reason,
        row.reason,
    )


def values_to_row(record: sqlite3.Row) -> CombToolRow:
    """Decode one `comb_tools` record into a CombToolRow."""
    return CombToolRow(
        tool_id=ToolId(record["tool_id"]),
        name=record["name"],
        version=record["version"],
        scope=ToolScope(record["scope"]),
        # The empty text is HIVE scope's "no Cell"; convert back to the value callers expect.
        cell_id=CellId(record["cell_id"]) if record["cell_id"] else None,
        spec_json=record["spec_json"],
        package_sha256=record["package_sha256"],
        package_size=record["package_size"],
        report_json=record["report_json"],
        quarantine_path=QuarantinePath(record["quarantine_path"]),
        reviewed=bool(record["reviewed"]),
        promoted_by=record["promoted_by"],
        promoted_at=datetime.fromisoformat(record["promoted_at"]),
        state=ToolState(record["state"]),
        is_current=bool(record["is_current"]),
        supersedes_version=record["supersedes_version"],
        retired_at=datetime.fromisoformat(record["retired_at"]) if record["retired_at"] else None,
        retired_reason=record["retired_reason"],
        reason=record["reason"],
    )
