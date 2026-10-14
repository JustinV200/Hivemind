"""Tests for hivemind.wardens.ticks.nuc.run: a promotion starts the server, a demotion stops it.

A device's Warden starts the model server its own manifest declares when the Queen orders it, and
offers the models she named within the ceilings she set; it answers with the models now on offer
and the rung it is on, or with a stable word for why it could not. These tests run the job
directly over a started Warden whose server `box` serves `small` (4 GiB), `mid` (6 GiB) and
`large` (8 GiB), and read the answer off the Queen's end of the wire.

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/nuc/run.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.nuc.run for the module under test.
    - builders.nuc for the rig, the ceilings helper and the orders.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from builders.hosting import drive
from builders.nuc import (
    GIB,
    SERVER_ID,
    NucRig,
    allow,
    demote_order,
    make_nuc_rig,
    promote_order,
    reply_of,
)

from hivemind.pheromone import TrailQuery
from hivemind.wardens.state import WardenState
from hivemind.wardens.ticks.lease import release_lease
from hivemind.wardens.ticks.nuc import live_host, run_order
from waggle.ids import new_cell_id, new_message_id
from waggle.messages.forage import SourceRemovalCause
from waggle.messages.swarm import NucOutcome, RuntimeLevel


@pytest.fixture
async def rig() -> AsyncIterator[NucRig]:
    """A started Warden whose server `box` is declared and not running."""
    made = await make_nuc_rig()
    yield made
    await made.warden.stop()


async def _denials(rig: NucRig) -> list[str]:
    """The rule of every `guard.denied` row on the Warden's trail."""
    events = await rig.deps.trail.query(TrailQuery())
    return [str(e.payload["rule"]) for e in events if e.kind == "guard.denied"]


def _holds_a_lease(rig: NucRig) -> bool:
    """Whether the Warden holds a lease now, read through a call so no narrowing is carried over."""
    return rig.warden.lease is not None


def _offered(rig: NucRig) -> set[str]:
    return {source.source_id for source in rig.forage_map.sources()}


async def test_a_promotion_starts_the_server_and_offers_the_models_named(rig: NucRig) -> None:
    allow(rig.warden, "small", "mid")
    request_id = new_message_id(rig.deps.clock)

    await run_order(rig.warden, request_id, promote_order(rig.warden, "small"))

    envelope, reply = await reply_of(rig)
    assert envelope.correlation_id == request_id  # The Queen matches it to the order she sent.
    assert reply.outcome is NucOutcome.PROMOTED and reply.level is RuntimeLevel.NUC
    assert reply.server_id == SERVER_ID and reply.loaded_models == ("small",)
    assert _offered(rig) == {"small"}  # Only the model asked for; `mid` is merely allowed.
    assert set(rig.warden.hosted_servers) == {SERVER_ID}
    assert [m.source.source_id for m in rig.queen.source_added] == ["small"]  # Before the reply.


async def test_a_promotion_fails_closed_when_the_queen_has_set_no_ceilings(rig: NucRig) -> None:
    await run_order(rig.warden, new_message_id(rig.deps.clock), promote_order(rig.warden, "small"))

    _envelope, reply = await reply_of(rig)
    assert reply.outcome is NucOutcome.FAILED and reply.level is RuntimeLevel.COLONIZED
    assert "no_ceilings" in reply.reason
    assert rig.warden.hosted_servers == {} and rig.forage_map.sources() == ()


async def test_a_promotion_names_only_a_server_the_device_declares(rig: NucRig) -> None:
    allow(rig.warden, "small")

    order = promote_order(rig.warden, "small", server_id="elsewhere")
    await run_order(rig.warden, new_message_id(rig.deps.clock), order)

    _envelope, reply = await reply_of(rig)
    assert reply.outcome is NucOutcome.FAILED and "server_unknown" in reply.reason
    assert rig.warden.hosted_servers == {}


async def test_a_promotion_for_another_cell_starts_nothing(rig: NucRig) -> None:
    allow(rig.warden, "small")
    order = promote_order(rig.warden, "small").model_copy(
        update={"cell_id": new_cell_id(rig.deps.clock)}
    )

    await run_order(rig.warden, new_message_id(rig.deps.clock), order)

    _envelope, reply = await reply_of(rig)
    assert reply.outcome is NucOutcome.FAILED and "wrong_cell" in reply.reason
    assert rig.warden.hosted_servers == {}


async def test_a_server_that_never_answers_is_a_failed_promotion_and_nothing_is_left(
    rig: NucRig,
) -> None:
    allow(rig.warden, "small")
    rig.door.provider.set_outage(True)  # The process starts and the health probe never passes.

    order = promote_order(rig.warden, "small", deadline_s=1.0)
    await drive(rig.clock, run_order(rig.warden, new_message_id(rig.clock), order))

    _envelope, reply = await reply_of(rig)
    assert reply.outcome is NucOutcome.FAILED and "server_not_started" in reply.reason
    assert reply.level is RuntimeLevel.COLONIZED
    assert rig.warden.hosted_servers == {} and rig.forage_map.sources() == ()
    session = rig.warden._session
    assert session is not None and not session.running_pids  # type: ignore[attr-defined]


async def test_a_model_outside_the_ceilings_is_left_out_and_the_denial_is_recorded(
    rig: NucRig,
) -> None:
    allow(rig.warden, "mid")

    order = promote_order(rig.warden, "small", "mid")
    await run_order(rig.warden, new_message_id(rig.deps.clock), order)

    _envelope, reply = await reply_of(rig)
    assert reply.outcome is NucOutcome.PROMOTED and reply.loaded_models == ("mid",)
    assert await _denials(rig) == ["guard.scope.loadable_source"]


async def test_nothing_allowed_is_a_failed_promotion_with_a_denial_for_each_model(
    rig: NucRig,
) -> None:
    allow(rig.warden)

    order = promote_order(rig.warden, "small", "mid")
    await run_order(rig.warden, new_message_id(rig.deps.clock), order)

    _envelope, reply = await reply_of(rig)
    assert reply.outcome is NucOutcome.FAILED and "server_not_started" in reply.reason
    assert await _denials(rig) == ["guard.scope.loadable_source"] * 2
    assert rig.warden.hosted_servers == {}


async def test_the_models_that_fit_together_load_and_the_one_that_does_not_is_denied(
    rig: NucRig,
) -> None:
    allow(rig.warden, "small", "mid", "large", vram=11 * GIB)

    order = promote_order(rig.warden, "small", "mid", "large")
    await run_order(rig.warden, new_message_id(rig.deps.clock), order)

    _envelope, reply = await reply_of(rig)
    assert reply.loaded_models == ("small", "mid")  # 4 + 6 fit the 11 GiB; the 8 does not.
    assert await _denials(rig) == ["guard.scope.model_vram"]


async def test_a_repeated_promotion_offers_what_the_first_left_out(rig: NucRig) -> None:
    allow(rig.warden, "small", "mid")
    await run_order(rig.warden, new_message_id(rig.deps.clock), promote_order(rig.warden, "small"))
    await reply_of(rig)

    again = promote_order(rig.warden, "small", "mid")
    await run_order(rig.warden, new_message_id(rig.deps.clock), again)

    _envelope, reply = await reply_of(rig, 2)
    assert reply.loaded_models == ("small", "mid")
    session = rig.warden._session
    assert session is not None and len(session.running_pids) == 1  # type: ignore[attr-defined]


async def test_a_demotion_stops_the_server_and_the_sources_leave_the_hive(rig: NucRig) -> None:
    allow(rig.warden, "small", "mid")
    await run_order(rig.warden, new_message_id(rig.deps.clock), promote_order(rig.warden, "small"))
    await reply_of(rig)

    await run_order(rig.warden, new_message_id(rig.deps.clock), demote_order(rig.warden))

    _envelope, reply = await reply_of(rig, 2)
    assert reply.outcome is NucOutcome.DEMOTED
    assert reply.level is RuntimeLevel.COLONIZED and reply.loaded_models == ()
    assert rig.warden.hosted_servers == {} and rig.forage_map.sources() == ()
    assert [m.cause for m in rig.queen.source_removed] == [SourceRemovalCause.SERVER_STOPPED]
    session = rig.warden._session
    assert session is not None and not session.running_pids  # type: ignore[attr-defined]


async def test_a_demotion_with_nothing_running_is_still_answered(rig: NucRig) -> None:
    await run_order(rig.warden, new_message_id(rig.deps.clock), demote_order(rig.warden))

    _envelope, reply = await reply_of(rig)
    assert reply.outcome is NucOutcome.DEMOTED and reply.level is RuntimeLevel.COLONIZED


async def test_a_demotion_for_another_cell_leaves_the_server_running(rig: NucRig) -> None:
    allow(rig.warden, "small")
    await run_order(rig.warden, new_message_id(rig.deps.clock), promote_order(rig.warden, "small"))
    await reply_of(rig)
    other = demote_order(rig.warden).model_copy(update={"cell_id": new_cell_id(rig.deps.clock)})

    await run_order(rig.warden, new_message_id(rig.deps.clock), other)

    _envelope, reply = await reply_of(rig, 2)
    assert reply.outcome is NucOutcome.FAILED and reply.level is RuntimeLevel.NUC
    assert set(rig.warden.hosted_servers) == {SERVER_ID}


async def test_a_promotion_leases_the_cell_again_when_the_idle_release_gave_it_back(
    rig: NucRig,
) -> None:
    allow(rig.warden, "small")
    lease = rig.warden.lease
    assert lease is not None
    # What a device Warden does when it has settled to WATCH with nothing to do.
    rig.warden._state = WardenState.WATCH
    await release_lease(rig.warden, lease)
    assert not _holds_a_lease(rig)

    await run_order(rig.warden, new_message_id(rig.deps.clock), promote_order(rig.warden, "small"))

    _envelope, reply = await reply_of(rig)
    assert reply.outcome is NucOutcome.PROMOTED
    assert _holds_a_lease(rig)  # The server runs under a lease again.
    assert set(rig.warden.hosted_servers) == {SERVER_ID}


async def test_the_host_is_rebuilt_over_a_new_lease_only_when_nothing_runs(rig: NucRig) -> None:
    first = await live_host(rig.warden)
    assert first is not None

    again = await live_host(rig.warden)

    assert again is not first  # Nothing ran, so the next lease's session gets a host of its own.
    allow(rig.warden, "small")
    await run_order(rig.warden, new_message_id(rig.deps.clock), promote_order(rig.warden, "small"))
    running = await live_host(rig.warden)
    assert running is rig.warden._models and running is not again
    assert await live_host(rig.warden) is running  # With a server up, the host is kept.
