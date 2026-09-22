"""Tests for hivemind.wardens.ticks.control: handle_stop and send_intervention.

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/control.py (codingrules section 3). `forward_control` and
    `handle_ceilings_set`/`handle_plan_written` are already exercised end to end through
    `hivemind.wardens.warden.Warden`'s own tick (test_warden_forwarding.py); this module covers
    `handle_stop` (roadmap step 5.3 / ADR-0027) and `send_intervention`, `Warden.intervene`'s own
    delegate, neither of which had a dedicated test yet.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.control for the module under test.
"""

from __future__ import annotations

import asyncio
import dataclasses

import pytest
from builders.cells import make_cell
from builders.wardens import make_warden_deps

from hivemind.cell import CellKind
from hivemind.supervision import Cancel
from hivemind.wardens.errors import UnknownSubBeeError
from hivemind.wardens.snapshot_relay import RelaySnapshotter
from hivemind.wardens.state import WardenState
from hivemind.wardens.ticks.control import (
    handle_release_lease,
    handle_snapshot_reply,
    handle_stop,
    send_intervention,
)
from hivemind.wardens.warden import Warden
from waggle.clock import FakeClock
from waggle.ids import new_cell_id
from waggle.messages.cell.leases import CellTeardownRequest
from waggle.messages.cell.snapshot import CellSnapshotReply
from waggle.messages.cell.status import ReleaseCause
from waggle.messages.control.protocol import Shutdown
from waggle.messages.labels import Urgency
from waggle.messages.supervision import Intervene, InterventionAction

_CLOCK = FakeClock()


def _release_lease_intervene(reason: str = "operator asked") -> Intervene:
    return Intervene(
        action=InterventionAction.RELEASE_LEASE,
        subject=None,
        task_id=None,
        slot=None,
        binding=None,
        alarm_id=None,
        reason=reason,
    )


@pytest.mark.parametrize(
    "payload",
    [
        Shutdown(urgency=Urgency.IMMEDIATE, deadline_s=0.0, reason="test teardown"),
        CellTeardownRequest(
            cell_id=new_cell_id(_CLOCK),
            lease_id=None,
            urgency=Urgency.IMMEDIATE,
            cause=ReleaseCause.COMPLETED,
            reason="test teardown",
        ),
    ],
)
async def test_handle_stop_stops_the_warden(payload: object) -> None:
    deps, _queen_end, warden_id = make_warden_deps()
    warden = Warden(warden_id, deps)
    await warden.start()
    run_task = asyncio.ensure_future(warden.run())

    await handle_stop(warden, payload)
    await asyncio.wait_for(run_task, timeout=5.0)

    assert warden.state is WardenState.STOPPED


async def test_send_intervention_raises_for_an_unknown_sub_bee() -> None:
    deps, _queen_end, warden_id = make_warden_deps()
    warden = Warden(warden_id, deps)
    await warden.start()

    with pytest.raises(UnknownSubBeeError):
        await send_intervention(warden, "no-such-worker", Cancel(reason="test"))

    await warden.stop()


async def test_handle_release_lease_releases_the_lease_and_reports_it() -> None:
    """Roadmap step 5.13: the Warden releases its own lease and tells the Queen so."""
    deps, queen_end, warden_id = make_warden_deps()
    warden = Warden(warden_id, deps)
    await warden.start()
    lease = warden.lease
    assert lease is not None
    lease_id = lease.id

    await handle_release_lease(warden, _release_lease_intervene("operator asked"))

    assert warden.lease is None
    released = await queen_end.wait_for_lease_released()
    assert released.lease_id == lease_id
    assert released.reason == "operator asked"


async def test_handle_release_lease_is_idempotent_with_no_lease_held() -> None:
    """A Warden already lease-less (WATCH, a refused lease) has nothing to release or report."""
    deps, queen_end, warden_id = make_warden_deps(cells=())
    warden = Warden(warden_id, deps)
    await warden.start()
    assert warden.lease is None
    assert warden.state is WardenState.WATCH

    await handle_release_lease(warden, _release_lease_intervene())  # Must not raise.

    assert warden.lease is None
    assert queen_end.lease_released == []


async def test_handle_snapshot_reply_is_a_noop_for_the_default_noop_snapshotter() -> None:
    deps, _queen_end, warden_id = make_warden_deps()
    warden = Warden(warden_id, deps)
    reply = CellSnapshotReply(cell_id=new_cell_id(_CLOCK), snapshot_id="snap_x", error=None)

    handle_snapshot_reply(warden, reply)  # Must not raise; NoopSnapshotter has no handle_reply.


async def test_handle_snapshot_reply_resolves_a_relay_snapshotters_pending_ask() -> None:
    """The end-to-end round trip: a RelaySnapshotter's own snapshot() resumes once this fires."""
    clock = FakeClock()
    cell_id = new_cell_id(clock)
    deps, _queen_end, warden_id = make_warden_deps(clock=clock)
    relay = RelaySnapshotter(cell_id, deps.queen_link, deps.hop, clock)
    warden = Warden(warden_id, dataclasses.replace(deps, snapshotter=relay))
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock, id=cell_id)
    ask_task = asyncio.ensure_future(relay.snapshot(cell))
    for _ in range(5):  # Let _ask send the request and start awaiting its own pending future.
        await asyncio.sleep(0)

    handle_snapshot_reply(
        warden, CellSnapshotReply(cell_id=cell_id, snapshot_id="snap_new", error=None)
    )

    assert await asyncio.wait_for(ask_task, timeout=5.0) == "snap_new"
