"""Tests for hivemind.memory.context: MemoryIdentity and MemoryContext are plain, frozen bundles.

Fits into the Hive:
    Mirrors src/hivemind/memory/context.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.context for the module under test.
"""

from __future__ import annotations

import dataclasses

import pytest

from hivemind.memory.context import MemoryContext, MemoryIdentity
from hivemind.memory.store.memory import InMemoryMemoryStore
from hivemind.pheromone import MemoryPheromoneTrail
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id


def test_memory_identity_holds_its_three_fields() -> None:
    clock = FakeClock()
    identity = MemoryIdentity(
        hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system"
    )

    assert identity.actor == "system"


def test_memory_identity_is_frozen() -> None:
    clock = FakeClock()
    identity = MemoryIdentity(
        hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system"
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        identity.actor = "human"  # type: ignore[misc]


def test_memory_context_bundles_store_identity_and_clock() -> None:
    clock = FakeClock()
    identity = MemoryIdentity(
        hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system"
    )
    store = InMemoryMemoryStore(MemoryPheromoneTrail(clock))

    ctx = MemoryContext(store=store, identity=identity, clock=clock)

    assert ctx.store is store
    assert ctx.identity is identity
    assert ctx.clock is clock


def test_memory_context_is_frozen() -> None:
    clock = FakeClock()
    identity = MemoryIdentity(
        hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system"
    )
    ctx = MemoryContext(
        store=InMemoryMemoryStore(MemoryPheromoneTrail(clock)), identity=identity, clock=clock
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.clock = FakeClock()  # type: ignore[misc]
