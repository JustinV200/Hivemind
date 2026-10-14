"""Tests for the parts of ModelServerHost a Nuc promotion uses: a named start and a later load.

A promotion starts a declared server and asks for some of its models first (``only``), bounded by
the Queen's deadline; afterwards the Warden may load another allowlisted model by itself, which is
only ever reported as a new source. A model the ceilings do not allow is refused, and a refusal
someone asked for is on the trail as a Guard denial.

Fits into the Hive:
    Mirrors src/hivemind/wardens/local_pool/hosting/host.py and offering.py (codingrules section
    3), split from test_host.py by feature (codingrules section 5.1).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.local_pool.hosting.host for the module under test.
    - builders.hosting for the rig: a hosting-capable Cell, a scripted session and provider.
"""

from __future__ import annotations

import asyncio

import pytest
from builders.hosting import HostingRig, drive, make_hosting_rig, make_server_config, vram_source

from hivemind.forage import Ceilings
from hivemind.pheromone import TrailQuery
from waggle.messages.forage import SourceRemovalCause

_GIB = 1024**3


def _ceilings(loadable: tuple[str, ...], vram: int = 16 * _GIB) -> Ceilings:
    """Ceilings that allow `loadable` and `vram` bytes of models."""
    return Ceilings(
        max_sub_bees=2,
        model_vram_bytes=vram,
        model_disk_bytes=0,
        loadable_sources=loadable,
        exportable_seats=0,
    )


def _rig(ceilings: Ceilings | None = None) -> HostingRig:
    """A rig whose one server serves three models of 4, 6 and 8 GiB."""
    sources = (
        vram_source("small", 4 * _GIB),
        vram_source("mid", 6 * _GIB),
        vram_source("large", 8 * _GIB),
    )
    return make_hosting_rig((make_server_config(sources=sources),), ceilings=ceilings)


async def _denials(rig: HostingRig) -> list[str]:
    """The rule of every `guard.denied` row on the Warden's trail."""
    events = await rig.deps.trail.query(TrailQuery())
    return [str(e.payload["rule"]) for e in events if e.kind == "guard.denied"]


def _offered(rig: HostingRig) -> set[str]:
    """The ids of the sources on the Warden's live Forage map."""
    return {source.source_id for source in rig.forage_map.sources()}


async def test_a_start_that_names_models_offers_only_those() -> None:
    rig = _rig()

    assert await rig.host.start("box", only=("mid",)) is True

    assert _offered(rig) == {"mid"}
    await rig.queen_end.pump_until(lambda: bool(rig.queen_end.source_added))
    assert [m.source.source_id for m in rig.queen_end.source_added] == ["mid"]


async def test_the_models_named_are_offered_in_the_servers_order_not_the_askers() -> None:
    rig = _rig(_ceilings(("small", "mid", "large"), vram=10 * _GIB))

    await rig.host.start("box", only=("mid", "small"))

    # 4 + 6 fit the 10 GiB; the server's order decides who is first to spend VRAM.
    assert [s.source_id for s in rig.host.running["box"].sources] == ["small", "mid"]


async def test_a_name_outside_the_ceilings_is_refused_and_recorded_as_a_denial() -> None:
    rig = _rig(_ceilings(("small",)))

    started = await rig.host.start("box", only=("small", "large"))

    assert started is True  # The model that is allowed still loads.
    assert _offered(rig) == {"small"}
    assert await _denials(rig) == ["guard.scope.loadable_source"]


async def test_a_name_the_server_does_not_serve_is_refused_and_recorded() -> None:
    rig = _rig()

    await rig.host.start("box", only=("small", "ghost"))

    assert _offered(rig) == {"small"}
    assert await _denials(rig) == ["guard.scope.server_source"]


async def test_nothing_allowed_means_no_server_and_a_denial_for_each_name() -> None:
    rig = _rig(_ceilings(()))

    assert await rig.host.start("box", only=("small", "mid")) is False

    assert rig.session.started == ()  # Starting it would only have wasted VRAM.
    assert await _denials(rig) == ["guard.scope.loadable_source"] * 2


async def test_a_manifest_start_that_leaves_models_out_records_no_denial() -> None:
    rig = _rig(_ceilings(("small",)))

    await rig.host.start("box")  # No `only`: nobody asked for the other two.

    assert _offered(rig) == {"small"}
    assert await _denials(rig) == []


async def test_a_deadline_shorter_than_the_manifests_ends_the_wait_sooner() -> None:
    rig = _rig()
    rig.provider.set_outage(True)  # The server never answers.

    started = await drive(rig.clock, rig.host.start("box", deadline_s=1.0))

    assert started is False
    assert rig.session.running_pids == ()
    assert rig.clock.monotonic() < 2.5  # The manifest alone would have waited 3 s.


async def test_a_deadline_longer_than_the_manifests_does_not_stretch_the_wait() -> None:
    rig = _rig()
    rig.provider.set_outage(True)

    await drive(rig.clock, rig.host.start("box", deadline_s=600.0))

    assert rig.clock.monotonic() < 4.5  # The manifest's own 3 s still bounds it.


async def test_a_start_cancelled_while_it_waits_leaves_no_process_behind() -> None:
    rig = _rig()
    rig.provider.set_outage(True)
    task = asyncio.ensure_future(rig.host.start("box"))
    for _ in range(25):  # Let it start the process and settle into its health wait.
        await asyncio.sleep(0)
    started = len(rig.session.running_pids)
    assert started == 1

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not rig.session.running_pids
    assert rig.door.unregistered == ["local-prov"]  # The provider went back too.
    assert rig.host.running == {}


async def test_a_later_load_of_an_allowed_model_is_a_new_source_and_nothing_else() -> None:
    rig = _rig(_ceilings(("small", "mid")))
    await rig.host.start("box", only=("small",))
    before = await _denials(rig)

    assert await rig.host.load("box", "mid") is True

    assert _offered(rig) == {"small", "mid"}
    assert [s.source_id for s in rig.host.running["box"].sources] == ["small", "mid"]
    assert rig.host.server_of("mid") == "box"
    await rig.queen_end.pump_until(lambda: len(rig.queen_end.source_added) == 2)
    assert await _denials(rig) == before == []  # Autonomous: no request, no denial.
    kinds = [e.kind for e in await rig.deps.trail.query(TrailQuery()) if e.kind != "guard.denied"]
    assert kinds == ["forage.source_added"] * 2


async def test_loading_a_model_already_offered_changes_nothing() -> None:
    rig = _rig()
    await rig.host.start("box", only=("small",))

    assert await rig.host.load("box", "small") is True

    await rig.queen_end.pump_until(lambda: bool(rig.queen_end.source_added))
    assert len(rig.queen_end.source_added) == 1  # Not announced a second time.


async def test_loading_into_a_server_that_is_not_running_does_nothing() -> None:
    rig = _rig()

    assert await rig.host.load("box", "small") is False

    assert _offered(rig) == set()
    assert await _denials(rig) == []  # Not a refusal of the model: there is nowhere to put it.


async def test_a_load_outside_loadable_sources_is_refused_and_recorded() -> None:
    rig = _rig(_ceilings(("small",)))
    await rig.host.start("box", only=("small",))

    assert await rig.host.load("box", "mid") is False

    assert _offered(rig) == {"small"}
    assert await _denials(rig) == ["guard.scope.loadable_source"]


async def test_a_load_past_the_vram_ceiling_is_refused_and_recorded() -> None:
    rig = _rig(_ceilings(("small", "large"), vram=10 * _GIB))
    await rig.host.start("box", only=("small",))

    assert await rig.host.load("box", "large") is False  # 4 + 8 > 10.

    assert _offered(rig) == {"small"}
    assert await _denials(rig) == ["guard.scope.model_vram"]


async def test_a_load_of_a_model_the_server_does_not_serve_is_refused_and_recorded() -> None:
    rig = _rig()
    await rig.host.start("box", only=("small",))

    assert await rig.host.load("box", "ghost") is False

    assert await _denials(rig) == ["guard.scope.server_source"]


async def test_a_loaded_model_leaves_with_its_server_like_any_other() -> None:
    rig = _rig()
    await rig.host.start("box", only=("small",))
    await rig.host.load("box", "mid")

    await rig.host.stop("box", SourceRemovalCause.SERVER_STOPPED)

    assert _offered(rig) == set()
    await rig.queen_end.pump_until(lambda: len(rig.queen_end.source_removed) == 2)
    assert {m.source.source_id for m in rig.queen_end.source_removed} == {"small", "mid"}


async def test_the_vram_a_loaded_model_takes_counts_against_the_next_one() -> None:
    rig = _rig(_ceilings(("small", "mid", "large"), vram=14 * _GIB))
    await rig.host.start("box", only=("small",))

    assert await rig.host.load("box", "large") is True  # 4 + 8 = 12 of 14.
    assert await rig.host.load("box", "mid") is False  # 12 + 6 > 14.
