"""Tests for hivemind.cli.in_cell.borrowed.deps: the link and WardenDeps of a device's Warden.

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/borrowed/deps.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one). Builds the real deps over real stores in a temporary directory, without
    starting anything; test_run.py runs the Warden they make.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.in_cell.borrowed.deps for the module under test.
    - tests/unit/cli/in_cell/test_deps.py for the Virtual Cell's counterpart.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from builders.borrowed import SERVER_ID, BorrowedKit, make_kit

from hivemind.cell import NoopSnapshotter
from hivemind.cell.local import HiveStandSource
from hivemind.cell.source import CellIdentity
from hivemind.cli.in_cell.borrowed import (
    BorrowedConfig,
    BorrowedParts,
    LateTrailSync,
    build_borrowed_config,
    build_borrowed_warden_deps,
    build_device_link,
    open_device_stores,
)
from hivemind.cli.in_cell.borrowed.config import DEVICE_MAX_OFFLINE_S
from hivemind.cli.in_cell.deps import VIRTUAL_CELL_LEASE
from hivemind.guard import Capability, CapabilityFamily
from hivemind.manifest import read_in_cell_env
from hivemind.memory import SqliteMemoryStore
from hivemind.pheromone import SqlitePheromoneTrail
from hivemind.wardens.deps import WardenDeps
from hivemind.wardens.offline import LinkPhase, Outage, ReconnectingQueenLink
from waggle.clock import SystemClock

_CLOCK = SystemClock()


@pytest.fixture
async def built(tmp_path: Path) -> AsyncIterator[tuple[BorrowedKit, BorrowedConfig, WardenDeps]]:
    """A device Warden's real deps over real stores, nothing started or connected."""
    kit = make_kit(tmp_path, "ws://127.0.0.1:8720", with_server=True)
    config = build_borrowed_config(read_in_cell_env(kit.environ), _CLOCK)
    stores = await open_device_stores(config.state_dir, _CLOCK)
    runtime = config.runtime
    source = HiveStandSource(
        config.cell,
        CellIdentity(
            hive_id=runtime.hive_id, node_id=runtime.node_id, actor=str(runtime.warden_id)
        ),
        stores.trail,
        _CLOCK,
        stores.leavings,
    )
    link = build_device_link(config, source, stores, LateTrailSync(), _CLOCK)
    deps = build_borrowed_warden_deps(config, BorrowedParts(source, link, stores), _CLOCK)
    yield kit, config, deps


async def test_the_lease_needs_this_one_cell_and_not_a_virtual_one(
    built: tuple[BorrowedKit, BorrowedConfig, WardenDeps],
) -> None:
    kit, _config, deps = built

    assert deps.lease_capability == Capability(family=CapabilityFamily.CELL_REAL, scope=kit.cell_id)
    assert deps.lease_capability != VIRTUAL_CELL_LEASE


async def test_memory_and_trail_are_the_durable_ones(
    built: tuple[BorrowedKit, BorrowedConfig, WardenDeps],
) -> None:
    _kit, _config, deps = built

    assert isinstance(deps.memory, SqliteMemoryStore)  # A Handoff survives a restart.
    assert isinstance(deps.trail, SqlitePheromoneTrail)
    assert deps.trail_sync is not None  # The segment is shipped to the Queen, signed.
    assert deps.node_signer is not None and deps.node_signer_id is not None


async def test_it_is_a_device_wardens_that_dials_the_queen(
    built: tuple[BorrowedKit, BorrowedConfig, WardenDeps],
) -> None:
    _kit, _config, deps = built

    device = deps.device
    assert device is not None
    assert device.release_lease_when_idle is True  # The machine keeps no scratch between tasks.
    assert isinstance(device.outage, Outage)
    assert device.remote is not None
    link = deps.queen_link
    assert isinstance(link, ReconnectingQueenLink)  # The Warden's link redials and replays.
    assert device.remote.link is link
    assert device.remote.max_offline_s == DEVICE_MAX_OFFLINE_S
    assert link.phase is LinkPhase.OFFLINE  # Built unconnected: the run connects it.


async def test_the_warden_keeps_its_ids_and_signs_as_the_devices_node(
    built: tuple[BorrowedKit, BorrowedConfig, WardenDeps],
) -> None:
    kit, _config, deps = built

    assert deps.identity.node_id == kit.node_id
    assert deps.identity.hive_id == kit.hive_id
    assert deps.identity.actor == kit.warden_id
    assert deps.hop.sender == kit.warden_id and deps.hop.node_id == kit.node_id
    assert deps.hop.recipient == kit.hive_id


async def test_none_of_a_virtual_cells_extras_are_built_for_a_borrowed_machine(
    built: tuple[BorrowedKit, BorrowedConfig, WardenDeps],
) -> None:
    _kit, _config, deps = built

    assert isinstance(deps.snapshotter, NoopSnapshotter)  # A device cannot be snapshotted.
    assert deps.resident_basket is None and deps.basket_store is None
    assert deps.browser_launcher is None and deps.recording_store is None  # No Exoskeleton wiring.
    assert deps.ears is None
    # A model server is a promotion, never a Level 1 default: it is known so a promotion can start
    # it, and it is never started at the Warden's own start whatever the manifest says.
    assert set(deps.servers) == {SERVER_ID}
    assert deps.servers[SERVER_ID].load_on_start is False


async def test_the_models_are_the_device_manifests_and_the_judge_is_bound(
    built: tuple[BorrowedKit, BorrowedConfig, WardenDeps],
) -> None:
    _kit, config, deps = built

    assert deps.bindings == config.runtime.slots
    assert deps.judge_reviewer is not None  # The manifest binds every slot, the judge's included.
    assert deps.routing_rules is not None and deps.routing_rules.offline is False
