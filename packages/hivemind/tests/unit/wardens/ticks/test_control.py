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

import pytest
from builders.wardens import make_warden_deps

from hivemind.supervision import Cancel
from hivemind.wardens.errors import UnknownSubBeeError
from hivemind.wardens.state import WardenState
from hivemind.wardens.ticks.control import handle_stop, send_intervention
from hivemind.wardens.warden import Warden
from waggle.clock import FakeClock
from waggle.ids import new_cell_id
from waggle.messages.cell.leases import CellTeardownRequest
from waggle.messages.cell.status import ReleaseCause
from waggle.messages.control.protocol import Shutdown
from waggle.messages.labels import Urgency

_CLOCK = FakeClock()


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
