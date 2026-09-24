"""Tests for the Guard Bee over a real Hive Entrance: a forgery burst reduces it within a second.

A program enrolled and logged in on the remote listener has its token used by a forger: one
request with a signature that does not verify, one replaying a nonce already spent. The Entrance
records both refusals on the Queen's trail; the Guard Bee, built from the Queen's own parts as a
composition root would build it, reads them on its next round, orders the Entrance reduced, and
the running Entrance (uvicorn on loopback) stops its remote listener within a second of the round.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/guard_bee/respond.py's reduce order (codingrules section 3),
    split by feature from test_respond.py because it runs a real Entrance.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.streams.orders for the follower that obeys the order.
    - tests.unit.entrance.runtime.test_entrance for a reduction's own timing.
"""

from __future__ import annotations

import asyncio
import time

from builders.entrance.serving import ProgramGrant, serving
from builders.entrance.serving import RigOptions as ServingOptions
from builders.guard_bee import RecordingDoor, guard_bee_for_queen

from hivemind.entrance.reducer import EntranceMode
from hivemind.workers.roles.guard_bee import Disposition

_MODE = "/v1/entrance/mode"  # A read any observing device may make, on either listener.


async def test_a_forgery_burst_reduces_a_real_entrance_within_a_second() -> None:
    async with serving(ServingOptions(remote=True)) as rig:
        remote, session = await rig.program(ProgramGrant(capabilities=("observe",), remote=True))
        door = RecordingDoor()
        guard_bee = guard_bee_for_queen(rig.deps, door)
        # A forged signature: the headers of one request, the signature of another.
        forged = remote.signed_headers(session, "GET", _MODE, b"")
        forged["X-Hive-Signature"] = remote.signed_headers(session, "GET", f"{_MODE}?x=1", b"")[
            "X-Hive-Signature"
        ]
        refused = await remote.http.get(_MODE, headers=forged)
        # A replay: one signed request sent twice.
        signed = remote.signed_headers(session, "GET", _MODE, b"")
        served = await remote.http.get(_MODE, headers=signed)
        replayed = await remote.http.get(_MODE, headers=signed)

        started = time.monotonic()
        responses = await guard_bee.tick()
        async with asyncio.timeout(1.0):
            # The Entrance follows the trail; the order is obeyed within a poll or two.
            while True:
                if await rig.store.entrance_mode.get() is EntranceMode.REDUCED:
                    break
                await asyncio.sleep(0.01)
        elapsed = time.monotonic() - started
        listening = rig.entrance.listeners.remote_listening
        await guard_bee.aclose()

    assert (refused.status_code, served.status_code, replayed.status_code) == (401, 200, 401)
    assert [(r.report.rule, r.disposition) for r in responses] == [
        ("request_forgery", Disposition.REDUCE_ORDERED)
    ]
    assert elapsed < 1.0 and not listening
    assert door.filed == []  # Narrowing the door is its own to do: nothing is asked of the Queen.
