"""Tests for hivemind.cli.compose.deps.queen: the uplink keys a Queen verifies Basket uploads with.

A Virtual Cell's Warden signs the manifests it ships up with the key the Cell gate registered;
a colonized device's Warden signs with the node key the swarm registry pinned. The cases chain
the two lookups, fold the registry into a QueenDeps and build a whole Hive with the Swarm on to
prove the composition root wires the registry's keys into the Queen's deps.

Fits into the Hive:
    Mirrors src/hivemind/cli/compose/deps/queen.py (codingrules section 3). The registry is the
    real in-memory one, or the SQLite one a built Hive opens; the links are what the swarm gate's
    door builds for a device.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.compose.deps.queen for the module under test.
    - tests.unit.queen.ticks.tools.test_receive for the receiver these keys feed.
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path

from builders.cli import fake_manifest
from builders.queen import make_queen_deps
from builders.swarm import (
    DeviceParty,
    enrol_node,
    make_device_party,
    make_swarm_node,
    swarm_manifest,
)

from hivemind.cli.compose import Hive, build_hive
from hivemind.cli.compose.deps.queen import chain_uplink_keys, with_swarm_uplink_keys
from hivemind.manifest import load_manifest
from hivemind.queen.deps import UplinkKeys, WardenLink
from hivemind.swarm import MemorySwarmRegistry, NodeStatus, SwarmRegistry
from waggle.clock import Clock, FakeClock, SystemClock
from waggle.ids import new_node_id

_GATE_KEY = b"g" * 32
_REGISTRY_KEY = b"r" * 32


def _holder(key: bytes | None, asked: list[str], name: str) -> UplinkKeys:
    """An `UplinkKeys` that answers `key` and notes it was asked."""

    async def lookup(_link: WardenLink) -> bytes | None:
        asked.append(name)
        return key

    return lookup


async def _enrolled(registry: SwarmRegistry, clock: Clock) -> DeviceParty:
    """Enrol a fresh device in `registry` and return who it is."""
    party = make_device_party(clock)
    await enrol_node(registry, make_swarm_node(party, clock), clock)
    return party


async def test_the_chain_answers_with_the_first_holder_that_has_a_key() -> None:
    clock = FakeClock()
    _deps, link, _end = make_queen_deps(clock)
    asked: list[str] = []
    chained = chain_uplink_keys(
        _holder(None, asked, "gate"), _holder(_REGISTRY_KEY, asked, "registry")
    )

    assert await chained(link) == _REGISTRY_KEY
    assert asked == ["gate", "registry"]


async def test_the_chain_stops_asking_once_a_holder_has_answered() -> None:
    clock = FakeClock()
    _deps, link, _end = make_queen_deps(clock)
    asked: list[str] = []
    chained = chain_uplink_keys(
        _holder(_GATE_KEY, asked, "gate"), _holder(_REGISTRY_KEY, asked, "registry")
    )

    assert await chained(link) == _GATE_KEY
    assert asked == ["gate"]


async def test_the_chain_answers_none_when_no_holder_has_a_key() -> None:
    clock = FakeClock()
    _deps, link, _end = make_queen_deps(clock)
    asked: list[str] = []

    assert await chain_uplink_keys(_holder(None, asked, "gate"))(link) is None
    assert await chain_uplink_keys()(link) is None


async def test_the_registry_is_chained_after_the_keys_the_deps_already_hold() -> None:
    clock = FakeClock()
    deps, link, _end = make_queen_deps(clock)
    asked: list[str] = []
    deps = dataclasses.replace(deps, uplink_keys=_holder(_GATE_KEY, asked, "gate"))
    registry = MemorySwarmRegistry()
    party = await _enrolled(registry, clock)

    folded = with_swarm_uplink_keys(deps, registry)

    assert folded.uplink_keys is not None
    # The gate's key answers for a link it holds one for, and the registry is never asked.
    assert await folded.uplink_keys(link) == _GATE_KEY and asked == ["gate"]
    deps = dataclasses.replace(deps, uplink_keys=_holder(None, asked, "gate"))
    folded = with_swarm_uplink_keys(deps, registry)
    assert folded.uplink_keys is not None
    device_link = dataclasses.replace(link, node_id=party.node_id)
    assert await folded.uplink_keys(device_link) == party.signer.public_key_bytes


async def test_the_registry_answers_nothing_for_a_stranger_a_revoked_node_or_no_node() -> None:
    clock = FakeClock()
    deps, link, _end = make_queen_deps(clock)
    registry = MemorySwarmRegistry()
    party = await _enrolled(registry, clock)
    folded = with_swarm_uplink_keys(deps, registry)  # No Virtual side: the registry alone.
    assert folded.uplink_keys is not None

    assert await folded.uplink_keys(link) is None  # The Hive Stand's own link proves no node.
    stranger = dataclasses.replace(link, node_id=new_node_id(clock))
    assert await folded.uplink_keys(stranger) is None
    node = await registry.node_by_node_id(party.node_id)
    assert node is not None
    await registry.set_status(node.device_id, node.status, NodeStatus.REVOKED, clock.now())
    revoked = dataclasses.replace(link, node_id=party.node_id)
    assert await folded.uplink_keys(revoked) is None


def _hive(manifest_path: Path) -> Hive:
    """Build a Hive from a written manifest, outside any event loop."""
    return build_hive(load_manifest(manifest_path, {}), environ={}, clock=SystemClock())


def test_a_hive_with_the_swarm_on_verifies_uploads_against_the_registrys_pinned_keys(
    tmp_path: Path,
) -> None:
    hive = _hive(swarm_manifest(tmp_path))
    assert hive.swarm is not None
    keys = hive.queen_deps.uplink_keys
    assert keys is not None

    async def enrolled_key() -> tuple[bytes | None, bytes]:
        party = await _enrolled(hive.swarm.registry, hive.clock)  # type: ignore[union-attr]
        link = dataclasses.replace(hive.warden_link, node_id=party.node_id)
        return await keys(link), party.signer.public_key_bytes

    found, pinned = asyncio.run(enrolled_key())
    assert found == pinned


def test_a_hive_with_the_swarm_off_holds_no_uplink_keys_as_before(tmp_path: Path) -> None:
    hive = _hive(fake_manifest(tmp_path))

    assert hive.swarm is None and hive.queen_deps.uplink_keys is None
