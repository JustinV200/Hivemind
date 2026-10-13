"""Tests for hivemind.royal_jelly.registry.sql: the row-to-values mapping and the HIVE cell text.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/registry/sql.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.royal_jelly.registry.sql for the module under test.
"""

from __future__ import annotations

import pytest
from builders.royal_jelly import make_offer, make_tool_spec

from hivemind.common.sqlite import connect
from hivemind.royal_jelly.registry import (
    InMemoryCombRegistry,
    QueenPrincipal,
    ToolKey,
    WardenPrincipal,
    apply_comb_registry_migrations,
)
from hivemind.royal_jelly.registry.sql import (
    INSERT_SQL,
    SELECT_ALL_SQL,
    cell_text,
    key_values,
    row_to_values,
    values_to_row,
)
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_warden_id
from waggle.messages.tool.authoring import ToolScope


def test_hive_scope_maps_to_the_empty_cell_text_and_back() -> None:
    cell = new_cell_id(FakeClock())

    assert cell_text(None) == ""
    assert cell_text(cell) == cell
    assert key_values(ToolKey("echo_tool", ToolScope.HIVE, None)) == ("echo_tool", "HIVE", "")


@pytest.mark.parametrize("scope", [ToolScope.HIVE, ToolScope.CELL])
async def test_a_row_survives_an_insert_and_a_select(scope: ToolScope) -> None:
    clock = FakeClock()
    spec = make_tool_spec(scope=scope)
    report, offer = make_offer(spec)
    principal = (
        QueenPrincipal()
        if scope is ToolScope.HIVE
        else WardenPrincipal(new_warden_id(clock), spec.cell_id)  # type: ignore[arg-type]
    )
    row = (await InMemoryCombRegistry(clock).promote(spec, report, offer, principal, "why")).row
    connection = connect(":memory:")
    apply_comb_registry_migrations(connection, clock)

    connection.execute(INSERT_SQL, row_to_values(row))
    stored = values_to_row(connection.execute(SELECT_ALL_SQL).fetchone())

    assert stored == row
