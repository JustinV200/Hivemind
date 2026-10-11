"""Tests for hivemind.wardens.local_pool.hosting.host: ModelServerHost, over fakes.

Fits into the Hive:
    Mirrors src/hivemind/wardens/local_pool/hosting/host.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.local_pool.hosting.host for the module under test.
    - builders.hosting for the rig: a hosting-capable Cell, a scripted session and provider.
"""

from __future__ import annotations

import pytest
from builders.hosting import (
    PROGRAM,
    HostingRig,
    drive,
    make_hosting_rig,
    make_server_config,
    vram_source,
)

from hivemind.cell import FakeStart
from hivemind.common.errors import HiveMindError
from hivemind.forage import Ceilings
from hivemind.llm import LLMProvider, ProviderConfig
from hivemind.pheromone import TrailQuery
from hivemind.wardens.errors import UnknownServerError
from waggle.messages.forage import SourceRemovalCause

_GIB = 1024**3


async def _forage_kinds(rig: HostingRig) -> list[str]:
    events = await rig.deps.trail.query(TrailQuery())
    return [e.kind for e in events if e.kind.startswith("forage.")]


async def test_start_runs_the_server_and_offers_every_source_on_this_cell() -> None:
    rig = make_hosting_rig()

    assert await rig.host.start("box") is True

    # The process: argv substituted, started through the session, logging into scratch.
    (spec,) = rig.session.started
    assert spec.argv[0] == PROGRAM and "18080" in spec.argv
    assert "/fake-scratch/host" in spec.argv
    # The provider was registered, and both sources are on the map hosted on this Cell.
    assert rig.door.registered == ["local-prov"]
    assert {s.source_id: s.spec.host_cell_id for s in rig.forage_map.sources()} == {
        "small": rig.cell.id,
        "large": rig.cell.id,
    }
    # The Queen heard of each, and the trail recorded it.
    await rig.queen_end.pump_until(lambda: len(rig.queen_end.source_added) == 2)
    assert {m.source.source_id for m in rig.queen_end.source_added} == {"small", "large"}
    assert await _forage_kinds(rig) == ["forage.source_added"] * 2
    assert set(rig.host.running) == {"box"}


async def test_start_is_idempotent_for_a_server_already_running() -> None:
    rig = make_hosting_rig()
    await rig.host.start("box")

    assert await rig.host.start("box") is True
    assert len(rig.session.started) == 1


async def test_start_names_an_undeclared_server_as_an_error() -> None:
    rig = make_hosting_rig()

    with pytest.raises(UnknownServerError):
        await rig.host.start("ghost")


async def test_start_all_starts_only_servers_that_load_on_start() -> None:
    eager = make_server_config("eager")
    lazy = make_server_config("lazy", load_on_start=False)
    rig = make_hosting_rig((eager, lazy))

    await rig.host.start_all()

    assert set(rig.host.running) == {"eager"}


async def test_a_cell_that_cannot_host_a_model_starts_nothing_and_says_why_once() -> None:
    rig = make_hosting_rig((make_server_config("a"), make_server_config("b")), can_host_model=False)

    await rig.host.start_all()

    assert rig.session.started == ()
    assert rig.door.registered == []
    assert rig.host.declined == "this Cell does not report can_host_model"
    assert rig.host.running == {}


async def test_a_warden_without_a_provider_door_declines_too() -> None:
    rig = make_hosting_rig(has_door=False)

    assert await rig.host.start("box") is False
    assert "provider door" in (rig.host.declined or "")


async def test_ceilings_keep_a_source_out_and_the_rest_load() -> None:
    ceilings = Ceilings(
        max_sub_bees=2,
        model_vram_bytes=8 * _GIB,
        model_disk_bytes=0,
        loadable_sources=("small",),
        exportable_seats=0,
    )
    rig = make_hosting_rig(ceilings=ceilings)

    await rig.host.start("box")

    assert [s.source_id for s in rig.forage_map.sources()] == ["small"]
    await rig.queen_end.pump_until(lambda: bool(rig.queen_end.source_added))
    assert len(rig.queen_end.source_added) == 1


async def test_vram_is_counted_across_servers() -> None:
    first = make_server_config("first", sources=(vram_source("one", 6 * _GIB),))
    second = make_server_config("second", sources=(vram_source("two", 6 * _GIB),), port=18_081)
    ceilings = Ceilings(
        max_sub_bees=2,
        model_vram_bytes=8 * _GIB,
        model_disk_bytes=0,
        loadable_sources=("one", "two"),
        exportable_seats=0,
    )
    rig = make_hosting_rig((first, second), ceilings=ceilings)

    await rig.host.start_all()

    # The second would push past the ceiling, so its server never starts at all.
    assert set(rig.host.running) == {"first"}
    assert len(rig.session.started) == 1


async def test_a_server_that_never_becomes_healthy_is_stopped_and_recorded_dead() -> None:
    rig = make_hosting_rig()
    rig.provider.set_outage(True)

    started = await drive(rig.clock, rig.host.start("box"))

    assert started is False
    assert rig.session.running_pids == ()  # The process was stopped.
    assert rig.forage_map.sources() == ()
    events = [e for e in await rig.deps.trail.query(TrailQuery()) if e.kind.startswith("forage.")]
    assert {e.kind for e in events} == {"forage.source_removed"}
    assert {e.payload["cause"] for e in events} == {"SERVER_DIED"}
    # Nothing was announced: the Queen never heard of a source that never loaded.
    assert rig.queen_end.source_added == []


async def test_a_process_that_exits_at_once_is_recorded_dead_without_waiting() -> None:
    rig = make_hosting_rig()
    rig.session.script_start(PROGRAM, FakeStart(running=False))

    assert await rig.host.start("box") is False
    assert "forage.source_removed" in await _forage_kinds(rig)


async def test_a_program_that_cannot_start_is_recorded_dead() -> None:
    rig = make_hosting_rig()
    rig.session.script_start(PROGRAM, FakeStart(fails="no such program"))

    assert await rig.host.start("box") is False
    assert rig.forage_map.sources() == ()
    assert "forage.source_removed" in await _forage_kinds(rig)


async def test_a_registry_that_refuses_the_provider_fails_the_start_not_the_warden() -> None:
    def refuse(name: str, config: ProviderConfig) -> LLMProvider:
        raise HiveMindError("offline: not loopback")

    rig = make_hosting_rig(register=refuse)

    assert await rig.host.start("box") is False
    assert rig.session.started == ()  # Never reached the process.
    assert "forage.source_removed" in await _forage_kinds(rig)


async def test_stop_withdraws_the_sources_before_it_stops_the_process() -> None:
    rig = make_hosting_rig()
    await rig.host.start("box")

    assert await rig.host.stop("box", SourceRemovalCause.SERVER_STOPPED) is True

    assert rig.forage_map.sources() == ()
    assert rig.session.running_pids == ()
    assert rig.host.running == {}
    assert rig.door.unregistered == ["local-prov"]
    await rig.queen_end.pump_until(lambda: len(rig.queen_end.source_removed) == 2)
    assert {m.cause for m in rig.queen_end.source_removed} == {SourceRemovalCause.SERVER_STOPPED}
    assert "forage.source_removed" in await _forage_kinds(rig)


async def test_stop_is_idempotent() -> None:
    rig = make_hosting_rig()
    await rig.host.start("box")
    await rig.host.stop("box", SourceRemovalCause.SERVER_STOPPED)

    assert await rig.host.stop("box", SourceRemovalCause.SERVER_STOPPED) is False


async def test_stop_closes_a_provider_the_registry_gave_up() -> None:
    rig = make_hosting_rig()
    rig.door.removes_on_unregister = True
    await rig.host.start("box")

    await rig.host.stop("box", SourceRemovalCause.SERVER_STOPPED)

    assert rig.provider.is_closed is True


async def test_stop_all_leaves_no_process_behind() -> None:
    rig = make_hosting_rig((make_server_config("a"), make_server_config("b", port=18_081)))
    await rig.host.start_all()
    started = rig.session.running_pids
    assert len(started) == 2

    await rig.host.stop_all(SourceRemovalCause.LEASE_RELEASED)

    assert not rig.session.running_pids
    assert rig.forage_map.sources() == ()


async def test_unloading_one_source_keeps_the_server_and_the_other_source() -> None:
    rig = make_hosting_rig()
    await rig.host.start("box")

    assert await rig.host.unload("small", SourceRemovalCause.UNLOADED) is True

    assert [s.source_id for s in rig.forage_map.sources()] == ["large"]
    assert len(rig.session.running_pids) == 1
    assert rig.host.server_of("small") is None
    assert rig.host.server_of("large") == "box"


async def test_unloading_the_last_source_stops_the_server() -> None:
    rig = make_hosting_rig((make_server_config(sources=(vram_source("only", 1),)),))
    await rig.host.start("box")

    assert await rig.host.unload("only", SourceRemovalCause.EVICTED) is True

    assert rig.session.running_pids == ()
    assert rig.host.running == {}


async def test_unloading_a_source_nobody_offers_does_nothing() -> None:
    rig = make_hosting_rig()
    await rig.host.start("box")

    assert await rig.host.unload("ghost", SourceRemovalCause.EVICTED) is False
    assert len(rig.forage_map.sources()) == 2


async def test_sweep_stops_a_server_whose_process_died_on_its_own() -> None:
    rig = make_hosting_rig()
    await rig.host.start("box")
    rig.session.exit_process(rig.host.running["box"].process)

    await rig.host.sweep()

    assert rig.host.running == {}
    assert rig.forage_map.sources() == ()
    await rig.queen_end.pump_until(lambda: len(rig.queen_end.source_removed) == 2)
    assert {m.cause for m in rig.queen_end.source_removed} == {SourceRemovalCause.SERVER_DIED}


async def test_sweep_leaves_a_live_server_alone() -> None:
    rig = make_hosting_rig()
    await rig.host.start("box")

    await rig.host.sweep()

    assert set(rig.host.running) == {"box"}
