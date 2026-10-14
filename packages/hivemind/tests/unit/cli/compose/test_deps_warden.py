"""Tests for hivemind.cli.compose.deps.warden and .links: the Warden bag any Real Cell shares.

Fits into the Hive:
    Mirrors src/hivemind/cli/compose/deps/warden.py and links.py (codingrules section 3). The parts
    are the ones a real `build_hive` over a real `builders.cli.fake_manifest` builds, so the two
    builders are compared on exactly what the Hive Stand's own Warden is made from.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.compose.deps.warden for build_warden_deps and build_cell_warden_deps.
    - hivemind.cli.compose.links for build_hive_links.
"""

from __future__ import annotations

from pathlib import Path

from builders.cells import make_cell
from builders.cli import fake_manifest

from hivemind.cell import CellKind
from hivemind.cell.fake import FakeCellSource
from hivemind.cell.source import CellIdentity
from hivemind.cli.compose import Hive, build_hive
from hivemind.cli.compose.deps import (
    HIVE_STAND_LEASE,
    HiveParts,
    build_cell_warden_deps,
    build_warden_deps,
)
from hivemind.cli.compose.links import build_hive_links
from hivemind.guard import Capability, CapabilityFamily
from hivemind.manifest import load_manifest
from waggle.clock import FakeClock, SystemClock
from waggle.ids import new_hive_id, new_node_id, new_warden_id


def _hive(tmp_path: Path) -> Hive:
    """A Hive over the manifest's own SQLite file, built outside any event loop."""
    return build_hive(load_manifest(fake_manifest(tmp_path), {}), environ={}, clock=SystemClock())


def _parts(hive: Hive) -> HiveParts:
    """The shared parts `hive` was built from, with the Royal Jelly handles it holds."""
    return HiveParts(
        manifest=hive.manifest,
        registry=hive.registry,
        fanner=hive.fanner,
        stores=hive.stores,
        clock=hive.clock,
        enforcer=hive.enforcer,
        royal_jelly=hive.royal_jelly,
    )


def test_the_hive_stands_bag_is_the_shared_bag_plus_what_only_its_cell_has(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    parts = _parts(hive)
    cell = make_cell(kind=CellKind.REAL, clock=hive.clock)
    links = build_hive_links(hive.manifest.hive.id, hive.manifest.hive.node_id, cell, hive.clock)
    identity = CellIdentity(
        hive_id=hive.manifest.hive.id, node_id=hive.manifest.hive.node_id, actor="system"
    )
    source = FakeCellSource((cell,), identity, hive.stores.trail, hive.clock)
    lease = Capability(family=CapabilityFamily.CELL_REAL, scope=cell.id)

    shared = build_cell_warden_deps(parts, source, links, lease)
    stand = build_warden_deps(parts, hive.source, links)

    # What any Real Cell's Warden gets: the Hive's own policy, models, stores and Guard.
    assert shared.source is source and shared.lease_capability == lease
    assert shared.enforcer is stand.enforcer is hive.enforcer
    assert shared.trail is stand.trail and shared.memory is stand.memory
    assert shared.map is stand.map is hive.fanner.deps.map  # Routing and metering read one map.
    assert shared.routing_rules == stand.routing_rules
    assert shared.bindings == stand.bindings
    # What only the Hive Stand's Warden has, because only its Cell is this machine's own.
    assert stand.lease_capability == HIVE_STAND_LEASE
    assert stand.royal_jelly is hive.royal_jelly and shared.royal_jelly is None
    assert stand.resident_basket is not None and shared.resident_basket is None
    assert stand.recording_store is not None and shared.recording_store is None
    assert shared.keep_root is None and shared.servers == {}
    assert shared.register_provider is None and shared.unregister_provider is None


def test_the_hive_stands_own_warden_is_built_exactly_as_before(tmp_path: Path) -> None:
    hive = _hive(tmp_path)

    deps = hive.warden._deps

    assert deps.lease_capability == HIVE_STAND_LEASE
    assert deps.royal_jelly is hive.royal_jelly
    assert deps.resident_basket is hive.royal_jelly.resident  # type: ignore[union-attr]
    assert deps.recording_store is not None
    assert deps.device is None  # Only a Swarm device's Warden carries `DeviceDeps`.


def test_hive_links_use_the_warden_id_they_are_given_and_mint_one_otherwise() -> None:
    clock = FakeClock()
    cell = make_cell(kind=CellKind.REAL, clock=clock)
    hive_id, node_id, given = new_hive_id(clock), new_node_id(clock), new_warden_id(clock)

    kept = build_hive_links(hive_id, node_id, cell, clock, warden_id=given)
    minted = build_hive_links(hive_id, node_id, cell, clock)

    assert kept.warden_id == kept.queen_link.warden_id == given
    assert kept.queen_link.hop.recipient == kept.warden_hop.sender == given
    assert minted.warden_id != given
    assert minted.queen_link.warden_id == minted.warden_id
