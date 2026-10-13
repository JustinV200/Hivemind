"""Tests for hivemind.swarm.registry.rows: how an invite and a node become a row and come back.

Fits into the Hive:
    Mirrors src/hivemind/swarm/registry/rows.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). A row is the plain columns a query filters on plus the model as JSON, which is
    the source of truth, so the tests hold the two to each other and each statement to the values
    that fill it.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.registry.rows for the module under test.
"""

from __future__ import annotations

import sqlite3

import pytest
from builders.swarm import make_device_party, make_swarm_invite, make_swarm_node

from hivemind.swarm.models import InviteState, SwarmInvite, SwarmNode
from hivemind.swarm.node_state import NodeStatus
from hivemind.swarm.registry import rows
from waggle.clock import FakeClock

_CLOCK = FakeClock()


def _row(body: str) -> sqlite3.Row:
    """A row with one `body` column, as a SELECT of the stored JSON returns."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    found: sqlite3.Row = connection.execute("SELECT ? AS body", (body,)).fetchone()
    return found


def _placeholders(statement: str) -> int:
    """How many values a statement takes."""
    return statement.count("?")


def test_an_invites_insert_values_fill_its_statement_and_carry_the_columns_and_the_body() -> None:
    invite = make_swarm_invite(clock=_CLOCK, label="desk")

    values = rows.invite_insert_values(invite)

    assert len(values) == _placeholders(rows.INSERT_INVITE_SQL)
    assert values[:5] == (
        invite.code_hash,
        "ISSUED",
        invite.expires_at.isoformat(),
        invite.created_at.isoformat(),
        None,
    )
    assert SwarmInvite.model_validate_json(str(values[5])) == invite


def test_an_invites_update_values_end_with_the_key_the_row_is_found_by() -> None:
    invite = make_swarm_invite(clock=_CLOCK)

    values = rows.invite_update_values(invite)

    assert len(values) == _placeholders(rows.UPDATE_INVITE_SQL)
    assert values[0] == invite.state.value and values[-1] == invite.code_hash


def test_an_invite_comes_back_from_its_row_exactly() -> None:
    invite = make_swarm_invite(clock=_CLOCK, state=InviteState.REVOKED)

    assert rows.invite_from_row(_row(invite.model_dump_json())) == invite


def test_a_nodes_insert_values_fill_its_statement_and_carry_the_columns_and_the_body() -> None:
    node = make_swarm_node(make_device_party(_CLOCK), _CLOCK)

    values = rows.node_insert_values(node)

    assert len(values) == _placeholders(rows.INSERT_NODE_SQL)
    assert values[:8] == (
        node.device_id,
        node.node_id,
        node.cell_id,
        node.warden_id,
        node.public_key_hex,
        "ONLINE",
        "GATEWAY",
        node.enrolled_at.isoformat(),
    )
    assert SwarmNode.model_validate_json(str(values[8])) == node


def test_a_nodes_update_values_follow_the_changed_columns_and_end_with_the_key() -> None:
    node = make_swarm_node(make_device_party(_CLOCK), _CLOCK)

    values = rows.node_update_values(node)

    assert len(values) == _placeholders(rows.UPDATE_NODE_SQL)
    assert values[:2] == ("ONLINE", "GATEWAY") and values[-1] == node.device_id
    assert node.status is NodeStatus.ONLINE


def test_the_five_identities_are_checked_in_the_order_the_statement_names_them() -> None:
    node = make_swarm_node(make_device_party(_CLOCK), _CLOCK)

    values = rows.node_identity_values(node)

    assert len(values) == _placeholders(rows.NODE_IDENTITY_IN_USE_SQL) == 5
    assert values == (
        node.device_id,
        node.node_id,
        node.cell_id,
        node.warden_id,
        node.public_key_hex,
    )


def test_a_node_comes_back_from_its_row_exactly() -> None:
    node = make_swarm_node(make_device_party(_CLOCK), _CLOCK, label="kitchen-pi")

    assert rows.node_from_row(_row(node.model_dump_json())) == node


@pytest.mark.parametrize(
    "statement",
    [
        rows.SELECT_INVITE_SQL,
        rows.SELECT_NODE_SQL,
        rows.SELECT_NODE_BY_NODE_ID_SQL,
        rows.SELECT_NODE_BY_CELL_SQL,
        rows.LIST_INVITES_BY_STATE_SQL,
        rows.LIST_NODES_BY_STATUS_SQL,
        rows.LAPSED_INVITES_SQL,
    ],
)
def test_each_lookup_statement_takes_exactly_one_value_and_reads_only_the_body(
    statement: str,
) -> None:
    assert _placeholders(statement) == 1 and statement.startswith("SELECT body FROM")
