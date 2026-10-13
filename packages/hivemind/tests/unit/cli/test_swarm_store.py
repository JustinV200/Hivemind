"""Tests for hivemind.cli.stores.open_swarm_registry: how the Swarm's table of devices is opened.

Fits into the Hive:
    The Swarm's registry is opened the same way every other store is (a function that takes the
    database file and returns the store, ready to use), by the composition root and by the
    `hive swarm` commands, each on its own connection to the one file. The properties that matter
    are the ones that connect them: a fresh file gets everything it needs, a file other stores
    already use is not disturbed, and an invite one command mints is there for the next.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.stores for the function under test and its siblings.
    - packages/hivemind/tests/unit/cli/test_stores.py for the same checks on the other stores.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from builders.swarm import make_device_party, make_swarm_invite, make_swarm_node

from hivemind.cli.stores import open_comb_registry, open_swarm_registry, open_trail
from hivemind.common.migrations import applied_versions
from hivemind.common.sqlite import connect
from hivemind.swarm.models import InviteState
from hivemind.swarm.registry import SUBSYSTEM, SqliteSwarmRegistry
from waggle.clock import FakeClock


def test_it_returns_a_ready_registry_on_a_fresh_file(tmp_path: Path) -> None:
    registry = open_swarm_registry(tmp_path / "hive.sqlite3")

    assert isinstance(registry, SqliteSwarmRegistry)
    assert asyncio.run(registry.invites()) == () and asyncio.run(registry.nodes()) == ()


def test_it_applies_the_swarm_migration_and_the_trails_on_a_fresh_file(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"

    open_swarm_registry(db)

    assert applied_versions(connect(db), SUBSYSTEM) == (1,)
    assert applied_versions(connect(db), "pheromone") != ()


def test_it_works_on_a_file_other_stores_already_opened(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"
    open_trail(db)
    open_comb_registry(db)

    registry = open_swarm_registry(db)

    assert asyncio.run(registry.invites()) == ()


def test_opening_it_again_changes_nothing_and_keeps_what_was_stored(tmp_path: Path) -> None:
    db, clock = tmp_path / "hive.sqlite3", FakeClock()
    first = open_swarm_registry(db)
    invite = make_swarm_invite(clock=clock)
    asyncio.run(first.put_invite(invite))

    second = open_swarm_registry(db)  # What a running Hive does while a command has just minted.

    assert applied_versions(connect(db), SUBSYSTEM) == (1,)
    assert asyncio.run(second.get_invite(invite.code_hash)) == invite


def test_what_one_opened_registry_stores_the_next_one_sees(tmp_path: Path) -> None:
    db, clock = tmp_path / "hive.sqlite3", FakeClock()
    first = open_swarm_registry(db)
    invite = make_swarm_invite(clock=clock)
    node = make_swarm_node(make_device_party(clock), clock)

    async def enrol() -> None:
        await first.put_invite(invite)
        await first.redeem(invite.code_hash, node, clock.now())

    asyncio.run(enrol())
    second = open_swarm_registry(db)

    spent = asyncio.run(second.get_invite(invite.code_hash))
    assert asyncio.run(second.node(node.device_id)) == node
    assert spent is not None and spent.state is InviteState.REDEEMED
