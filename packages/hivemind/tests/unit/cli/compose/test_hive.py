"""Tests for hivemind.cli.compose.hive and .deps: the Honey Store wired into a whole Hive.

Fits into the Hive:
    Mirrors src/hivemind/cli/compose/hive/ (build, run) and the Honey half of
    src/hivemind/cli/compose/deps.py (codingrules section 3; split by feature, 14.2, from
    tests/unit/cli/test_compose.py,
    which covers the rest of build_hive/run_hive/run_goal). Every Hive here is built from a real
    `builders.cli.fake_manifest` over the real Hive Stand; the Honey Store is the Hive's own
    SQLite file.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.compose.hive for build_hive and run_hive.
    - hivemind.cli.compose.deps for HiveStores and open_default_stores.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from builders.cli import fake_manifest
from builders.honey import make_nectar_submission

from hivemind.brood_chamber import BroodChamber, ChamberIdentity, MemoryTaskStore
from hivemind.cell.leavings import InMemoryLeavingsStore
from hivemind.cli.compose import Hive, HiveStores, build_hive, run_hive
from hivemind.cli.compose.deps import open_default_stores
from hivemind.honey_store import SqliteHoneyStore
from hivemind.manifest import load_manifest
from hivemind.memory import InMemoryMemoryStore
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.queen.chat import InMemoryChatLog
from hivemind.queen.intake import InMemoryGoalRequestStore
from hivemind.workers.roles.house_bee import HouseBeeRipening
from waggle.clock import FakeClock, SystemClock

_SETTLE_POLLS = 500  # Real-time polls (10 ms each) for the loop's first pass to land: 5 s at most.
_POLL_S = 0.01


def _default_hive(tmp_path: Path) -> Hive:
    """A Hive over the manifest's own SQLite file, built outside any event loop."""
    manifest = load_manifest(fake_manifest(tmp_path), {})
    return build_hive(manifest, environ={}, clock=SystemClock())


def test_open_default_stores_opens_the_honey_store_on_the_hives_own_file(tmp_path: Path) -> None:
    manifest = load_manifest(fake_manifest(tmp_path), {})

    stores = open_default_stores(manifest)

    assert isinstance(stores.honey, SqliteHoneyStore)


def test_build_hive_hands_one_honey_store_to_the_queen_and_the_house_bee(tmp_path: Path) -> None:
    hive = _default_hive(tmp_path)

    assert hive.honey is not None
    assert hive.honey.store is hive.stores.honey
    # QueenDeps is private to the Queen; reaching into it is the only way to prove the one build
    # is shared rather than built twice (the same reason tests/unit/cli/test_compose.py does).
    assert hive.queen._deps.honey is hive.honey
    assert isinstance(hive.house_bee, HouseBeeRipening)


def test_build_hive_over_hand_built_stores_has_no_honey_store(tmp_path: Path) -> None:
    clock = FakeClock()
    manifest = load_manifest(fake_manifest(tmp_path, clock=clock), {})
    trail = MemoryPheromoneTrail(clock)
    identity = ChamberIdentity(
        hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor="system"
    )
    stores = HiveStores(
        trail=trail,
        chamber=BroodChamber(MemoryTaskStore(trail), clock, identity),
        memory=InMemoryMemoryStore(trail),
        leavings=InMemoryLeavingsStore(trail),
        goal_requests=InMemoryGoalRequestStore(trail),
        chat=InMemoryChatLog(trail),
    )

    hive = build_hive(manifest, environ={}, clock=clock, stores=stores)

    assert (hive.honey, hive.house_bee) == (None, None)
    assert hive.queen._deps.honey is None


def test_run_hive_ripens_beside_the_queen_and_stops_the_loop_on_exit(tmp_path: Path) -> None:
    hive = _default_hive(tmp_path)
    assert hive.honey is not None
    access = hive.honey

    async def scenario() -> tuple[int, set[asyncio.Task[object]]]:
        before = asyncio.all_tasks() - {asyncio.current_task()}
        await access.intake.submit(make_nectar_submission(content=b"Ripened by the House Bee."))
        async with run_hive(hive):
            # The loop's first pass runs as soon as it starts, beside the Queen's own ticks.
            for _ in range(_SETTLE_POLLS):
                if not await access.store.pending_nectar(10):
                    break
                await asyncio.sleep(_POLL_S)
            pending = len(await access.store.pending_nectar(10))
        leftover = asyncio.all_tasks() - {asyncio.current_task()} - before
        return pending, leftover

    pending, leftover = asyncio.run(scenario())

    assert pending == 0  # Ripened by the loop, not by anything this test called.
    assert leftover == set()  # The loop, the Queen and the Warden all stopped and were reaped.


_SERVER_TABLES = """
[llm.providers.local]
kind = "openai_compat"
base_url = "http://127.0.0.1:18080/v1"

[llm.servers.box]
provider = "local"
argv = ["scripted-server", "--port", "{port}"]
port = 18080
sources = ["local_small"]

[forage.map.local_small]
provider = "local"
model = "small-model"
grade = 2
context_window = 4096
"""


def test_a_manifest_that_declares_a_model_server_gives_the_hive_stand_warden_the_means_to_host_it(
    tmp_path: Path,
) -> None:
    # Roadmap step 8.3: the servers, the provider door and the capability all follow [llm.servers].
    path = fake_manifest(tmp_path)
    path.write_text(path.read_text(encoding="utf-8") + _SERVER_TABLES, encoding="utf-8")
    hive = build_hive(load_manifest(path, {}), environ={}, clock=SystemClock())

    deps = hive.warden._deps
    assert set(deps.servers) == {"box"}
    assert deps.servers["box"].sources[0].source_id == "local_small"
    assert deps.register_provider is not None and deps.unregister_provider is not None
    assert hive.warden_link.cell.capabilities.can_host_model is True


def test_a_manifest_with_no_model_server_leaves_the_hive_stand_unable_to_host_one(
    tmp_path: Path,
) -> None:
    hive = _default_hive(tmp_path)

    assert hive.warden._deps.servers == {}
    assert hive.warden_link.cell.capabilities.can_host_model is False
