"""Tests for hivemind.swarm.registry.sqlite: what only the durable registry promises.

Fits into the Hive:
    Mirrors src/hivemind/swarm/registry/sqlite.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). Everything both registries promise is in the contract suite
    (tests/contracts/test_swarm_registry_contract.py); this file holds what is true of the file
    on disk: its migration series, its constraints, that it survives a restart and, above all,
    that no secret is ever written to it.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.registry.sqlite for the module under test.
    - docs/waggle/enrolment.md section 3.2 for what the invite row holds.
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from builders.cells import make_identity
from builders.swarm import (
    make_device_party,
    make_endpoint,
    make_enrol_envelope,
    make_enrol_request,
    make_hive_party,
    make_rules,
    make_swarm_invite,
    make_swarm_node,
)

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.common.errors import MigrationError
from hivemind.common.sqlite import connect
from hivemind.pheromone import DuplicateEventError, SqlitePheromoneTrail
from hivemind.swarm.enrolment import EnrolmentDoor, parse_invite_string, token_hash
from hivemind.swarm.events import SwarmRecorder
from hivemind.swarm.models import InviteState
from hivemind.swarm.node_state import NodeStatus
from hivemind.swarm.registry import SqliteSwarmRegistry, apply_swarm_migrations, rows
from waggle.clock import FakeClock
from waggle.messages.swarm import RuntimeLevel


@dataclass
class _Disk:
    """A database file with the trail and the registry opened on their own connections."""

    path: Path
    clock: FakeClock
    trail: SqlitePheromoneTrail
    registry: SqliteSwarmRegistry

    def raw(self) -> sqlite3.Connection:
        """A fresh connection for reading the file the way an operator's tools would."""
        return connect(self.path)


@pytest.fixture
async def disk(tmp_path: Path) -> AsyncIterator[_Disk]:
    """A database file with the trail's migrations and the registry's applied."""
    path, clock = tmp_path / "hive.sqlite3", FakeClock()
    trail = await SqlitePheromoneTrail.create(connect(path), clock)
    registry = await SqliteSwarmRegistry.create(connect(path), clock)
    yield _Disk(path, clock, trail, registry)


async def test_the_registrys_migrations_apply_once_and_are_then_current(tmp_path: Path) -> None:
    clock, connection = FakeClock(), connect(tmp_path / "hive.sqlite3")
    await SqlitePheromoneTrail.create(connection, clock)

    first = apply_swarm_migrations(connection, clock)
    second = apply_swarm_migrations(connection, clock)

    tables = {
        row["name"]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert first == (1,) and second == ()
    assert {"swarm_invites", "swarm_nodes"} <= tables


async def test_creating_the_registry_without_the_trails_table_is_refused(tmp_path: Path) -> None:
    with pytest.raises(MigrationError, match="pheromone_events"):
        await SqliteSwarmRegistry.create(connect(tmp_path / "bare.sqlite3"), FakeClock())


async def test_creating_the_registry_twice_on_one_file_is_harmless(disk: _Disk) -> None:
    again = await SqliteSwarmRegistry.create(disk.raw(), disk.clock)

    assert await again.invites() == () and await again.nodes() == ()


async def test_enrolled_nodes_and_spent_invites_survive_a_restart(disk: _Disk) -> None:
    invite = make_swarm_invite(clock=disk.clock)
    await disk.registry.put_invite(invite)
    node = make_swarm_node(make_device_party(disk.clock), disk.clock)
    await disk.registry.redeem(invite.code_hash, node, disk.clock.now())

    reopened = await SqliteSwarmRegistry.create(disk.raw(), disk.clock)

    spent = await reopened.get_invite(invite.code_hash)
    assert await reopened.node(node.device_id) == node
    assert spent is not None and spent.state is InviteState.REDEEMED


async def test_the_plain_columns_follow_the_body_through_every_change(disk: _Disk) -> None:
    invite = make_swarm_invite(clock=disk.clock)
    await disk.registry.put_invite(invite)
    node = make_swarm_node(make_device_party(disk.clock), disk.clock)
    await disk.registry.redeem(invite.code_hash, node, disk.clock.now())
    await disk.registry.set_level(node.device_id, RuntimeLevel.COLONIZED)
    await disk.registry.set_status(
        node.device_id, NodeStatus.ONLINE, NodeStatus.UNREACHABLE, disk.clock.now()
    )

    row = disk.raw().execute("SELECT status, level FROM swarm_nodes").fetchone()
    spent = disk.raw().execute("SELECT state, device_id FROM swarm_invites").fetchone()

    assert (row["status"], row["level"]) == ("UNREACHABLE", "COLONIZED")
    assert (spent["state"], spent["device_id"]) == ("REDEEMED", node.device_id)


_UNIQUE_COLUMNS = {"node_id": 1, "cell_id": 2, "warden_id": 3, "public_key_hex": 4}


@pytest.mark.parametrize("column", list(_UNIQUE_COLUMNS))
async def test_the_file_itself_refuses_two_nodes_sharing_an_identity(
    disk: _Disk, column: str
) -> None:
    invite = make_swarm_invite(clock=disk.clock)
    await disk.registry.put_invite(invite)
    first = make_swarm_node(make_device_party(disk.clock), disk.clock)
    await disk.registry.redeem(invite.code_hash, first, disk.clock.now())
    second = make_swarm_node(make_device_party(disk.clock), disk.clock)
    values = list(rows.node_insert_values(second))
    values[_UNIQUE_COLUMNS[column]] = rows.node_insert_values(first)[_UNIQUE_COLUMNS[column]]

    with pytest.raises(sqlite3.IntegrityError):
        disk.raw().execute(rows.INSERT_NODE_SQL, values)


async def test_a_redemption_whose_event_cannot_be_recorded_leaves_no_trace(disk: _Disk) -> None:
    invite = make_swarm_invite(clock=disk.clock)
    await disk.registry.put_invite(invite)
    recorder = SwarmRecorder(disk.trail, disk.clock, make_identity(disk.clock))
    node = make_swarm_node(make_device_party(disk.clock), disk.clock)
    event = recorder.swarm_event("swarm.enrolled", node.device_id, {"reason": "test"})
    await disk.trail.record(event)

    with pytest.raises(DuplicateEventError):
        await disk.registry.redeem(invite.code_hash, node, disk.clock.now(), event)

    unspent = await disk.registry.get_invite(invite.code_hash)
    assert unspent is not None and unspent.state is InviteState.ISSUED
    assert await disk.registry.nodes() == ()


async def test_neither_the_token_nor_the_invite_string_nor_a_key_is_ever_written_to_the_file(
    disk: _Disk,
) -> None:
    hive = make_hive_party(disk.clock)
    recorder = SwarmRecorder(
        disk.trail, disk.clock, make_identity(disk.clock, hive_id=hive.hive_id)
    )
    door = EnrolmentDoor(disk.registry, recorder, make_rules(make_endpoint(hive)))
    minted = await door.mint_invite(AccessLevel.FULL, CombShieldLevel.MEADOW, "kitchen-pi")
    token = parse_invite_string(minted.invite_string).token
    party = make_device_party(disk.clock)
    request = make_enrol_request(party, token)
    await door.enrol(make_enrol_envelope(party, hive, request, disk.clock))

    connection = disk.raw()
    everything = "\n".join(
        "|".join(str(value) for value in row)
        for table in ("swarm_invites", "swarm_nodes", "pheromone_events")
        for row in connection.execute(f"SELECT * FROM {table}")  # noqa: S608
    )

    assert token not in everything and minted.invite_string not in everything
    assert token_hash(token) in everything  # The hash is the one trace the Hive keeps.
