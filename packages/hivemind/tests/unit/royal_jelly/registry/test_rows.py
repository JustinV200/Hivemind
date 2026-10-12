"""Tests for hivemind.royal_jelly.registry.rows: ToolKey's scope rule and the row's accessors.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/registry/rows.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.royal_jelly.registry.rows for the module under test.
"""

from __future__ import annotations

import pytest
from builders.royal_jelly import make_offer, make_tool_spec

from hivemind.royal_jelly.registry import InMemoryCombRegistry, QueenPrincipal, ToolKey
from hivemind.royal_jelly.spec import spec_sha256
from waggle.clock import FakeClock
from waggle.ids import new_cell_id
from waggle.messages.tool.authoring import ToolScope


def test_a_hive_key_has_no_cell_and_a_cell_key_must_have_one() -> None:
    assert ToolKey("echo_tool", ToolScope.HIVE, None).cell_id is None
    assert ToolKey("echo_tool", ToolScope.CELL, new_cell_id(FakeClock())).cell_id is not None


def test_a_key_whose_cell_disagrees_with_its_scope_is_refused() -> None:
    with pytest.raises(ValueError, match="cell_id"):
        ToolKey("echo_tool", ToolScope.CELL, None)
    with pytest.raises(ValueError, match="cell_id"):
        ToolKey("echo_tool", ToolScope.HIVE, new_cell_id(FakeClock()))


async def test_a_row_parses_its_stored_spec_and_report_and_names_its_key() -> None:
    spec = make_tool_spec()
    report, offer = make_offer(spec)
    registry = InMemoryCombRegistry(FakeClock())

    row = (await registry.promote(spec, report, offer, QueenPrincipal(), "initial")).row

    assert row.spec == spec
    assert row.report == report
    assert row.key == ToolKey(spec.name, ToolScope.HIVE, None)
    assert row.report.spec_sha256 == spec_sha256(row.spec)
