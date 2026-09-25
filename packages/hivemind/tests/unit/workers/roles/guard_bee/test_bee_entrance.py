"""Tests for the Guard Bee over a real Hive Entrance: every door rule's order reduces it.

Each of the four shipped rules that recommend `reduce_entrance` is fired here by what a real
client does to a real Entrance (uvicorn on loopback, both listeners): a forged signature and a
replayed nonce (`request_forgery`), a burst of failed remote logins (`login_failure_burst`),
lockouts on two devices (`lockouts_across_devices`) and an unknown client knocking on the invite
route (`invite_abuse`). The Entrance records each refusal on the Queen's trail; the Guard Bee, built
from the Queen's own parts as the composition root builds it, reads them on its next round and
records `guard.reduce_ordered`; the running Entrance follows the trail and reduces itself within a
second of the round, its remote listener stopped and `guard.reduced` naming the Guard's order.
The Entrance never reduces itself on any of these, so the reduction is the rule's.

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
import contextlib
import time
from collections.abc import Awaitable, Callable

import httpx
from builders.entrance.auth import WRONG_PASSWORD
from builders.entrance.landing import DeviceKey, LandingClient
from builders.entrance.serving import RIG_SECTION, ProgramGrant, ServingRig, serving
from builders.entrance.serving import RigOptions as ServingOptions
from builders.guard_bee import RecordingDoor, guard_bee_for_queen

from hivemind.entrance.enrol import new_invite_code
from hivemind.entrance.reducer import EntranceMode
from hivemind.pheromone import TrailQuery
from hivemind.workers.roles.guard_bee import Disposition

_MODE = "/v1/entrance/mode"  # A read any observing device may make, on either listener.
_OBSERVER = ProgramGrant(capabilities=("observe",), remote=True)  # A remote program device.
Knock = Callable[[ServingRig], Awaitable[None]]


async def _reduced_by_rule(knock: Knock) -> tuple[list[str], float, bool, list[object]]:
    """Knock on a real Entrance, run one Guard Bee round, and wait for the door to narrow.

    Returns:
        The rules the round reduced the Entrance for, the seconds from the round to REDUCED,
        whether the remote listener still listened, and the `guard.reduced` reasons.
    """
    async with serving(ServingOptions(remote=True)) as rig:
        door = RecordingDoor()
        guard_bee = guard_bee_for_queen(rig.deps, door)
        await knock(rig)
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
        reduced = await rig.deps.trail.query(TrailQuery(kind="guard.reduced"))
        await guard_bee.aclose()
    assert door.filed == []  # Narrowing the door is the Guard Bee's own: nothing asks the Queen.
    ordered = [r.report.rule for r in responses if r.disposition is Disposition.REDUCE_ORDERED]
    return ordered, elapsed, listening, [event.payload["reason"] for event in reduced]


async def _forge(rig: ServingRig) -> None:
    """Present a live token with a signature over another request, then replay a nonce."""
    remote, session = await rig.program(_OBSERVER)
    forged = remote.signed_headers(session, "GET", _MODE, b"")
    other = remote.signed_headers(session, "GET", f"{_MODE}?x=1", b"")
    forged["X-Hive-Signature"] = other["X-Hive-Signature"]
    signed = remote.signed_headers(session, "GET", _MODE, b"")
    statuses = [
        (await remote.http.get(_MODE, headers=forged)).status_code,
        (await remote.http.get(_MODE, headers=signed)).status_code,
        (await remote.http.get(_MODE, headers=signed)).status_code,  # The replay.
    ]
    assert statuses == [401, 200, 401]


async def _fail_logins(client: LandingClient, key: DeviceKey, times: int) -> None:
    """Try `times` remote logins with the wrong password; every one is refused."""
    for _ in range(times):
        with contextlib.suppress(httpx.HTTPStatusError):
            await client.login(key, WRONG_PASSWORD)


async def _login_burst(rig: ServingRig) -> None:
    """Twenty refused remote logins: five wrong passwords lock the device, the rest are refused."""
    remote, session = await rig.program(_OBSERVER)
    await _fail_logins(remote, session.key, 20)


async def _lock_two_devices(rig: ServingRig) -> None:
    """Lock two remote devices with wrong passwords: ten refusals, two lockouts."""
    for _ in range(2):
        remote, session = await rig.program(_OBSERVER)
        # `[entrance] lockout_attempts` wrong passwords in a row lock the device.
        await _fail_logins(remote, session.key, RIG_SECTION.lockout_attempts)


async def _knock_on_invites(rig: ServingRig) -> None:
    """Five redemptions of codes that were never invites, from one address."""
    client = rig.client(remote=True)
    for _ in range(5):
        # Well formed as a real code is, and never minted by this Entrance: an unknown code.
        with contextlib.suppress(httpx.HTTPStatusError):
            await client.enrol(new_invite_code())


async def test_a_forgery_burst_reduces_a_real_entrance_within_a_second() -> None:
    ordered, elapsed, listening, reasons = await _reduced_by_rule(_forge)

    assert ordered == ["request_forgery"]
    assert elapsed < 1.0 and not listening and reasons == ["guard_order"]


async def test_a_burst_of_failed_remote_logins_reduces_a_real_entrance() -> None:
    ordered, elapsed, listening, reasons = await _reduced_by_rule(_login_burst)

    assert "login_failure_burst" in ordered
    assert elapsed < 1.0 and not listening and reasons == ["guard_order"]


async def test_lockouts_on_two_devices_reduce_a_real_entrance() -> None:
    ordered, elapsed, listening, reasons = await _reduced_by_rule(_lock_two_devices)

    assert ordered == ["lockouts_across_devices"]  # Ten refusals: short of a login burst.
    assert elapsed < 1.0 and not listening and reasons == ["guard_order"]


async def test_an_unknown_client_knocking_on_the_invite_route_reduces_a_real_entrance() -> None:
    ordered, elapsed, listening, reasons = await _reduced_by_rule(_knock_on_invites)

    assert ordered == ["invite_abuse"]
    assert elapsed < 1.0 and not listening and reasons == ["guard_order"]
