"""Unit tests for hivemind.queen.attach: detach_warden.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors src/hivemind/queen/attach.py
    (codingrules section 3). Covers detach_warden directly; hivemind.queen.cell_gate's own
    listener tests already exercise it end to end through a real connection.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.attach for detach_warden, the function under test.
    - hivemind.queen.queen for Queen.attach_warden, the edge this mirrors.
"""

from __future__ import annotations

import asyncio

from builders.queen import make_queen_deps

from hivemind.queen.attach import detach_warden
from hivemind.queen.queen import Queen
from waggle.ids import WardenId


async def test_detach_warden_removes_an_attached_link() -> None:
    deps, link, _end = make_queen_deps()
    queen = Queen(deps)
    queen.attach_warden(link)
    assert link.warden_id in {w.warden_id for w in queen.wardens}

    await detach_warden(queen, link.warden_id)

    assert link.warden_id not in {w.warden_id for w in queen.wardens}


async def test_detach_warden_reaps_the_in_flight_receive_task() -> None:
    deps, link, _end = make_queen_deps()
    queen = Queen(deps)
    queen.attach_warden(link)
    # Force a receive task to exist, the same way a real tick would (queen._receive_tasks is
    # populated lazily by _run_tick; this test creates one directly so detach has something real
    # to reap, matching this function's own key invariant).
    queen._receive_tasks[link.warden_id] = asyncio.ensure_future(anext(link.transport.receive()))

    await detach_warden(queen, link.warden_id)

    assert link.warden_id not in queen._receive_tasks


async def test_detach_warden_is_a_no_op_for_an_unattached_id() -> None:
    deps, _link, _end = make_queen_deps()
    queen = Queen(deps)

    await detach_warden(queen, WardenId("warden_never_attached"))  # Must not raise.
