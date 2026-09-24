"""Test hivemind.entrance.runtime.entrance: the running Entrance narrows its door on time.

ADR-0033 over real uvicorn listeners: a reduction closes every live remote socket and stops the
remote listener within a second, the loopback listener serving on; a remote listener that fails
(here: one that cannot bind) reduces the Entrance and puts an Alarm in front of the human, while
the Queen runs on; reopening from loopback, after step-up, brings the remote listener back.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio
import time

from builders.entrance.serving import ProgramGrant, RigOptions, serving
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from hivemind.entrance.app import OPENAPI_PATH
from hivemind.entrance.reducer import EntranceMode
from hivemind.entrance.streams import CloseReason
from hivemind.queen.chat import ChatKind, ChatQuery

_STREAM = "/v1/entrance/stream"  # The security view: any observing device may open it.
_UNASSIGNED = "192.0.2.1"  # TEST-NET-1: never an address of this host, so it cannot be bound.


async def test_a_reduction_closes_a_live_remote_socket_and_the_listener_within_a_second() -> None:
    async with serving(RigOptions(remote=True)) as rig:
        remote, session = await rig.program(ProgramGrant(capabilities=("observe",), remote=True))
        console, console_session = await rig.console_session()
        url = f"ws://127.0.0.1:{rig.entrance.listeners.remote_port}{_STREAM}"
        async with connect(url) as socket:
            await socket.send(remote.hello(session, _STREAM))
            started = time.monotonic()
            reduced = await console.call(console_session, "POST", "/v1/entrance/reduce")
            try:
                async with asyncio.timeout(1.0):
                    while True:
                        await socket.recv()
            except ConnectionClosed as closed:
                code = closed.rcvd.code if closed.rcvd is not None else None
            elapsed = time.monotonic() - started
        mode = await console.call(console_session, "GET", "/v1/entrance/mode")

    assert reduced.status_code == 200, reduced.text
    assert code == CloseReason.REDUCED.code
    assert elapsed < 1.0
    assert mode.json() == {"mode": "REDUCED", "exposed": True, "remote_listening": False}


async def test_reopening_from_loopback_after_step_up_serves_the_remote_listener_again() -> None:
    async with serving(RigOptions(remote=True)) as rig:
        console, session = await rig.console_session()
        await console.call(session, "POST", "/v1/entrance/reduce")

        await console.step_up(session)
        reopened = await console.call(session, "POST", "/v1/entrance/open")
        served = await rig.client(remote=True).http.get(OPENAPI_PATH)

    assert reopened.json() == {"changed": True, "mode": "OPEN"}
    assert served.status_code == 200


async def test_a_remote_listener_that_cannot_start_reduces_and_raises_an_alarm() -> None:
    async with serving(RigOptions(remote=True, remote_host=_UNASSIGNED)) as rig:
        mode = await rig.store.entrance_mode.get()
        alarms = [
            line for line in await rig.deps.chat.read(ChatQuery()) if line.kind is ChatKind.ALARM
        ]
        loopback = await rig.client().http.get(OPENAPI_PATH)

    assert mode is EntranceMode.REDUCED
    assert not rig.entrance.listeners.remote_listening
    assert len(alarms) == 1 and "remote listener failed" in alarms[0].text
    assert loopback.status_code == 200
