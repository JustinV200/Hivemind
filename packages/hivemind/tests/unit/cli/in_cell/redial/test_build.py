"""Tests for hivemind.cli.in_cell.redial.build: which Cells mend, the link, and the Warden's deps.

Only a Night Veil Cell's link is one that mends. The link, built over a fake Tor and a listener,
connects and announces before it is returned and leaves its outbox below the Cell's scratch root.
`equip` gives a built Warden what being cut off needs: an `Outage`, the link and the Hive's offline
limit in `DeviceDeps` (without the idle lease release a borrowed device has, and without a search
for why the link went), the wake that a change of the link sends the Warden's tick, and the
Warden's own trail sync bound into the link.

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/redial/build.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests/unit/cli/in_cell/test_main_night_veil_redial.py for the whole entry point.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest
from builders.tor_stand import ONION, TorStand, tor_stand
from builders.wardens import make_warden_deps

from hivemind.cell import CellIdentity
from hivemind.cli.in_cell.config import InCellRuntimeConfig, build_runtime_config
from hivemind.cli.in_cell.redial import OUTBOX_DIR, RedialingLink, connect_redialing, redials
from hivemind.manifest import read_in_cell_env
from hivemind.pheromone import MemoryPheromoneTrail
from hivemind.wardens.spawn import InCellSpawnSource
from waggle.clock import SystemClock
from waggle.messages.cell.status import CellReady

WAIT_S = 10.0  # Bounds every await that could hang; loopback answers in milliseconds.
_CLOCK = SystemClock()


@pytest.fixture
async def stand() -> AsyncIterator[TorStand]:
    """A signed loopback listener, and a fake Tor SOCKS port routing the onion name to it."""
    async with tor_stand() as made:
        yield made


def _config(stand: TorStand, tmp_path: Path, **extra: str) -> InCellRuntimeConfig:
    """The runtime config of a Night Veil Cell dialling `stand` through its Tor."""
    return build_runtime_config(read_in_cell_env(stand.environ(tmp_path, **extra)), _CLOCK)


def _source(config: InCellRuntimeConfig, trail: MemoryPheromoneTrail) -> InCellSpawnSource:
    """The one-Cell source `config` describes."""
    identity = CellIdentity(
        hive_id=config.hive_id, node_id=config.node_id, actor=str(config.warden_id)
    )
    return InCellSpawnSource(config.spawn_config, identity, trail, _CLOCK)


async def _connected(
    stand: TorStand, tmp_path: Path, **extra: str
) -> tuple[RedialingLink, InCellRuntimeConfig]:
    """Connect a Night Veil Cell's mending link to `stand`, with `extra` laid over its variables."""
    config = _config(stand, tmp_path, **extra)
    trail = MemoryPheromoneTrail(_CLOCK)
    link = await asyncio.wait_for(
        connect_redialing(config, _source(config, trail), trail, _CLOCK), timeout=WAIT_S
    )
    return link, config


# ──────────────────────────────────────────────────────────────────────────────
# Which Cells mend.
# ──────────────────────────────────────────────────────────────────────────────


async def test_only_a_night_veil_cells_link_is_one_that_mends(
    stand: TorStand, tmp_path: Path
) -> None:
    night_veil = _config(stand, tmp_path)
    environ = stand.environ(
        tmp_path, HIVEMIND_COMB_SHIELD="MEADOW", HIVEMIND_QUEEN_WAGGLE_URL="ws://localhost:8710"
    )
    del environ["HIVEMIND_SOCKS_PROXY_URL"]
    meadow = build_runtime_config(read_in_cell_env(environ), _CLOCK)

    assert redials(night_veil) is True
    assert redials(meadow) is False


# ──────────────────────────────────────────────────────────────────────────────
# The link.
# ──────────────────────────────────────────────────────────────────────────────


async def test_the_link_is_announced_and_online_when_it_is_returned(
    stand: TorStand, tmp_path: Path
) -> None:
    arrival = asyncio.ensure_future(anext(stand.server.connections()))

    redialing, _config_used = await _connected(stand, tmp_path)
    online = redialing.link.is_online
    accepted = await asyncio.wait_for(arrival, timeout=WAIT_S)
    first = (await asyncio.wait_for(anext(accepted.receive()), timeout=WAIT_S)).payload
    await redialing.link.close()

    assert online  # `connect_redialing` returns only once the Cell has announced itself.
    assert isinstance(first, CellReady)  # The first thing the Queen heard was who the Cell is.
    assert stand.proxy.requests[0][0] == ONION


async def test_the_link_keeps_its_outbox_below_the_scratch_root_and_the_peer_and_limit(
    stand: TorStand, tmp_path: Path
) -> None:
    arrival = asyncio.ensure_future(anext(stand.server.connections()))

    redialing, config = await _connected(stand, tmp_path, HIVEMIND_MAX_OFFLINE_S="42")
    await asyncio.wait_for(arrival, timeout=WAIT_S)
    await redialing.link.close()

    assert (config.spawn_config.scratch_root / OUTBOX_DIR).is_dir()
    assert redialing.peer == ONION
    assert redialing.max_offline_s == 42.0


# ──────────────────────────────────────────────────────────────────────────────
# What being cut off needs.
# ──────────────────────────────────────────────────────────────────────────────


async def test_equip_gives_the_warden_an_outage_the_link_and_the_limit(
    stand: TorStand, tmp_path: Path
) -> None:
    arrival = asyncio.ensure_future(anext(stand.server.connections()))
    redialing, _config_used = await _connected(stand, tmp_path, HIVEMIND_MAX_OFFLINE_S="42")
    await asyncio.wait_for(arrival, timeout=WAIT_S)
    deps, _queen, _warden_id = make_warden_deps()

    equipped = redialing.equip(deps)
    await redialing.link.close()

    device = equipped.device
    assert device is not None and device.outage is not None and device.remote is not None
    assert device.remote.link is redialing.link
    assert device.remote.max_offline_s == 42.0
    assert device.remote.reconnect_budget is None  # No Drone is sent to look into Tor.
    assert device.release_lease_when_idle is False  # A Virtual Cell's lease is not borrowed.


async def test_a_change_of_the_link_wakes_the_wardens_tick_at_once(
    stand: TorStand, tmp_path: Path
) -> None:
    arrival = asyncio.ensure_future(anext(stand.server.connections()))
    redialing, _config_used = await _connected(stand, tmp_path)
    await asyncio.wait_for(arrival, timeout=WAIT_S)
    deps, _queen, _warden_id = make_warden_deps()
    device = redialing.equip(deps).device
    assert device is not None and device.outage is not None
    outage = device.outage
    outage.acknowledge()  # Nothing has changed since the Warden last looked.
    wake = outage.waiter(_CLOCK)

    await redialing.link.close()  # The link goes OFFLINE.

    assert await asyncio.wait_for(wake, timeout=WAIT_S) is True


class _Sync:
    """A TrailSync that counts the segments it ships and the windows it is asked to rewind."""

    def __init__(self) -> None:
        self.shipped = 0
        self.rewound: list[timedelta] = []

    async def sync(self) -> bool:
        """Count one shipment."""
        self.shipped += 1
        return True

    def rewind(self, window: timedelta) -> None:
        """Keep the window."""
        self.rewound.append(window)


async def test_the_wardens_own_trail_sync_ships_what_the_reconnection_ships(
    stand: TorStand, tmp_path: Path
) -> None:
    arrival = asyncio.ensure_future(anext(stand.server.connections()))
    redialing, _config_used = await _connected(stand, tmp_path)
    await asyncio.wait_for(arrival, timeout=WAIT_S)
    deps, _queen, _warden_id = make_warden_deps()
    own = _Sync()
    redialing.equip(dataclasses.replace(deps, trail_sync=own))

    await redialing.trail_sync.sync()  # What the link's reconnection does, through the one object.
    redialing.trail_sync.rewind(timedelta(seconds=5))
    await redialing.link.close()

    assert own.shipped == 1  # One export, by the Warden's own sync, not a second one of the link's.
    assert own.rewound == [timedelta(seconds=5)]
