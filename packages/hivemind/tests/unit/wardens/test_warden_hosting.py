"""Tests for hivemind.wardens.warden with model servers: started after the lease, gone before it.

Fits into the Hive:
    Exercises hivemind.wardens.warden's `_host_models` and `stop()` through a whole Warden, over
    fakes (codingrules section 3: a behaviour across several modules gets its own module here).
    Roadmap step 8.3, ADR-0044.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.local_pool.hosting for the host the Warden builds.
    - tests/unit/wardens/local_pool/hosting for the host's own behaviour.
"""

from __future__ import annotations

import asyncio

from builders.hosting import RegistryDoor, make_hosting_warden, make_server_config
from builders.wardens import make_warden_deps

from hivemind.llm import FakeLLMProvider
from hivemind.pheromone import TrailQuery
from hivemind.wardens import Warden
from hivemind.wardens.state import WardenState
from waggle.clock import FakeClock
from waggle.messages.forage import SourceRemovalCause


async def _settle(cycles: int = 20) -> None:
    """Give the background run() task several event-loop turns to react to a clock advance."""
    for _ in range(cycles):
        await asyncio.sleep(0)


async def test_start_hosts_the_declared_server_once_the_lease_is_held() -> None:
    warden, _deps, queen_end, forage_map, _door = make_hosting_warden()

    await warden.start()

    assert warden.lease is not None
    assert warden._models is not None and set(warden._models.running) == {"box"}
    assert {s.spec.host_cell_id for s in forage_map.sources()} == {warden._cell.id}  # type: ignore[union-attr]
    await queen_end.pump_until(lambda: len(queen_end.source_added) == 2)
    await warden.stop()


async def test_stop_withdraws_every_source_and_kills_the_process_before_the_lease_is_released() -> (
    None
):
    warden, deps, queen_end, forage_map, _door = make_hosting_warden()
    await warden.start()
    session = warden._session
    assert session is not None and len(session.running_pids) == 1  # type: ignore[attr-defined]

    await warden.stop()

    assert session.running_pids == ()  # type: ignore[attr-defined]
    assert forage_map.sources() == ()
    await queen_end.pump_until(lambda: len(queen_end.source_removed) == 2)
    assert {m.cause for m in queen_end.source_removed} == {SourceRemovalCause.LEASE_RELEASED}
    kinds = [e.kind for e in await deps.trail.query(TrailQuery())]
    assert kinds.index("forage.source_removed") < kinds.index("cell.released")
    assert warden.state is WardenState.STOPPED


async def test_a_warden_with_no_declared_server_builds_no_host() -> None:
    warden, _deps, _queen_end, _map, _door = make_hosting_warden(with_servers=False)

    await warden.start()

    assert warden._models is None
    await warden.stop()


async def test_a_warden_without_a_lease_hosts_nothing() -> None:
    clock = FakeClock()
    config = make_server_config()
    deps, _queen_end, warden_id = make_warden_deps(
        clock,
        cells=(),
        servers={"box": config},
        register_provider=RegistryDoor(FakeLLMProvider()).register,
    )
    warden = Warden(warden_id, deps)

    await warden.start()

    assert warden.state is WardenState.WATCH
    assert warden._models is None


async def test_a_cell_that_cannot_host_a_model_starts_without_failing() -> None:
    warden, _deps, _queen_end, forage_map, door = make_hosting_warden(can_host_model=False)

    await warden.start()

    assert warden.state is WardenState.ACTIVE
    assert warden._models is not None and warden._models.running == {}
    assert door.registered == []
    assert forage_map.sources() == ()
    await warden.stop()


async def test_the_heartbeat_sweep_withdraws_a_server_that_died_on_its_own() -> None:
    warden, deps, queen_end, forage_map, _door = make_hosting_warden()
    await warden.start()
    run_task = asyncio.ensure_future(warden.run())
    await _settle()  # Let the tick loop reach its heartbeat sleep before time moves.
    assert warden._models is not None and warden._session is not None
    warden._session.exit_process(warden._models.running["box"].process)  # type: ignore[attr-defined]

    deps.clock.advance(deps.heartbeat_interval_s + 0.1)  # type: ignore[attr-defined]
    await _settle()
    await asyncio.wait_for(
        queen_end.pump_until(lambda: len(queen_end.source_removed) == 2), timeout=5.0
    )

    assert {m.cause for m in queen_end.source_removed} == {SourceRemovalCause.SERVER_DIED}
    assert forage_map.sources() == ()
    await warden.stop()
    await asyncio.wait_for(run_task, timeout=5.0)
