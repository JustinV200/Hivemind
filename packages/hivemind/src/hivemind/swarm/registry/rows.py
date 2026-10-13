"""Define the SQL and row codecs of the swarm registry: the one place its two tables are spelt.

The registry keeps each `SwarmNode` and `SwarmInvite` as its JSON `body` (the source of truth, so
a new field never needs a column) plus a few plain columns that exist for lookup and filtering:
a node's device, node, Cell, Warden and key (the identities that must stay unique), its status
and rung, and an invite's state and expiry. This module holds every statement the SQLite registry
runs, as constants, and the two functions that turn a model into the values of an INSERT and a
row back into a model. Keeping the SQL here keeps `sqlite.py` about transactions and rules, and
the statement text in one reviewable place.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.registry`.
    Called by `SqliteSwarmRegistry` only. Calls into hivemind.swarm.models and the standard
    library's sqlite3 types.

Key invariants:
    - A row's copies (`status`, `level`, `state`, `expires_at`, `device_id`) are always written
      from the model they describe, in the same statement as its body, so they cannot disagree.
    - The body round-trips: `node_from_row(node_row(node)...)` equals `node`.

See Also:
    - hivemind.swarm.registry.migrations/0001_create_swarm.sql for the schema these name.
    - hivemind.entrance.store.sqlite for the same body-plus-copies layout.
"""

from __future__ import annotations

import sqlite3

from hivemind.swarm.models import SwarmInvite, SwarmNode

# Statements: invites.
INSERT_INVITE_SQL = (
    "INSERT INTO swarm_invites (code_hash, state, expires_at, created_at, device_id, body) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)
UPDATE_INVITE_SQL = (
    "UPDATE swarm_invites SET state = ?, device_id = ?, body = ? WHERE code_hash = ?"
)
SELECT_INVITE_SQL = "SELECT body FROM swarm_invites WHERE code_hash = ?"
LIST_INVITES_SQL = "SELECT body FROM swarm_invites ORDER BY created_at, code_hash"
LIST_INVITES_BY_STATE_SQL = (
    "SELECT body FROM swarm_invites WHERE state = ? ORDER BY created_at, code_hash"
)
LAPSED_INVITES_SQL = "SELECT body FROM swarm_invites WHERE state = 'ISSUED' AND expires_at <= ?"

# Statements: nodes.
INSERT_NODE_SQL = (
    "INSERT INTO swarm_nodes (device_id, node_id, cell_id, warden_id, public_key_hex, status, "
    "level, enrolled_at, body) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
UPDATE_NODE_SQL = "UPDATE swarm_nodes SET status = ?, level = ?, body = ? WHERE device_id = ?"
SELECT_NODE_SQL = "SELECT body FROM swarm_nodes WHERE device_id = ?"
SELECT_NODE_BY_NODE_ID_SQL = "SELECT body FROM swarm_nodes WHERE node_id = ?"
SELECT_NODE_BY_CELL_SQL = "SELECT body FROM swarm_nodes WHERE cell_id = ?"
LIST_NODES_SQL = "SELECT body FROM swarm_nodes ORDER BY enrolled_at, device_id"
LIST_NODES_BY_STATUS_SQL = (
    "SELECT body FROM swarm_nodes WHERE status = ? ORDER BY enrolled_at, device_id"
)
NODE_IDENTITY_IN_USE_SQL = (
    "SELECT 1 FROM swarm_nodes WHERE device_id = ? OR node_id = ? OR cell_id = ? "
    "OR warden_id = ? OR public_key_hex = ?"
)
PHEROMONE_TABLE_CHECK_SQL = (
    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'pheromone_events'"
)

__all__ = [
    "INSERT_INVITE_SQL",
    "INSERT_NODE_SQL",
    "LAPSED_INVITES_SQL",
    "LIST_INVITES_BY_STATE_SQL",
    "LIST_INVITES_SQL",
    "LIST_NODES_BY_STATUS_SQL",
    "LIST_NODES_SQL",
    "NODE_IDENTITY_IN_USE_SQL",
    "PHEROMONE_TABLE_CHECK_SQL",
    "SELECT_INVITE_SQL",
    "SELECT_NODE_BY_CELL_SQL",
    "SELECT_NODE_BY_NODE_ID_SQL",
    "SELECT_NODE_SQL",
    "UPDATE_INVITE_SQL",
    "UPDATE_NODE_SQL",
    "invite_from_row",
    "invite_insert_values",
    "invite_update_values",
    "node_from_row",
    "node_identity_values",
    "node_insert_values",
    "node_update_values",
]


def invite_insert_values(invite: SwarmInvite) -> tuple[object, ...]:
    """Return the values of `INSERT_INVITE_SQL` for `invite`: its columns, then its JSON body."""
    return (
        invite.code_hash,
        invite.state.value,
        invite.expires_at.isoformat(),
        invite.created_at.isoformat(),
        invite.device_id,
        invite.model_dump_json(),
    )


def invite_update_values(invite: SwarmInvite) -> tuple[object, ...]:
    """Return the values of `UPDATE_INVITE_SQL`: the changed columns, the body, then the key."""
    return (invite.state.value, invite.device_id, invite.model_dump_json(), invite.code_hash)


def invite_from_row(row: sqlite3.Row) -> SwarmInvite:
    """Decode one `swarm_invites` row's body into the invite it stores."""
    return SwarmInvite.model_validate_json(row["body"])


def node_insert_values(node: SwarmNode) -> tuple[object, ...]:
    """Return the values of `INSERT_NODE_SQL` for `node`: its columns, then its JSON body."""
    return (
        node.device_id,
        node.node_id,
        node.cell_id,
        node.warden_id,
        node.public_key_hex,
        node.status.value,
        node.level.value,
        node.enrolled_at.isoformat(),
        node.model_dump_json(),
    )


def node_update_values(node: SwarmNode) -> tuple[object, ...]:
    """Return the values of `UPDATE_NODE_SQL`: the changed columns, the body, then the key."""
    return (node.status.value, node.level.value, node.model_dump_json(), node.device_id)


def node_identity_values(node: SwarmNode) -> tuple[object, ...]:
    """Return the values of `NODE_IDENTITY_IN_USE_SQL`: the five identities that must be unique."""
    return (node.device_id, node.node_id, node.cell_id, node.warden_id, node.public_key_hex)


def node_from_row(row: sqlite3.Row) -> SwarmNode:
    """Decode one `swarm_nodes` row's body into the node it stores."""
    return SwarmNode.model_validate_json(row["body"])
