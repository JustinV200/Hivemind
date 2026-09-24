"""Test hivemind.entrance.streams.registry: every live socket, closable by session or device.

Fits into the Hive:
    Mirrors src/hivemind/entrance/streams/registry.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio

from hivemind.entrance.auth.session import Listener
from hivemind.entrance.streams import CloseReason, SocketRegistry
from waggle.ids import DeviceId

_PHONE = DeviceId("device_01J8ZQ7X9K3M2N4P5Q6R7S8T9V")
_LAPTOP = DeviceId("device_01J8ZQ7X9K3M2N4P5Q6R7S8T9W")


def test_closing_by_session_device_and_listener_reaches_only_those_sockets() -> None:
    registry = SocketRegistry()
    phone_remote = registry.open(Listener.REMOTE, _PHONE, "hash-a")
    phone_local = registry.open(Listener.LOOPBACK, _PHONE, "hash-b")
    laptop = registry.open(Listener.REMOTE, _LAPTOP, "hash-c")

    by_session = registry.close_session("hash-b")
    by_device = registry.close_device(_LAPTOP, CloseReason.SESSION_ENDED)

    assert (by_session, by_device) == (1, 1)
    assert phone_local.reason is CloseReason.SESSION_ENDED
    assert laptop.reason is CloseReason.SESSION_ENDED
    assert phone_remote.reason is None


def test_the_first_reason_given_is_the_one_kept() -> None:
    registry = SocketRegistry()
    socket = registry.open(Listener.REMOTE, _PHONE, "hash-a")

    registry.close_listener(Listener.REMOTE, CloseReason.REDUCED)
    registry.close_all()

    assert socket.reason is CloseReason.REDUCED


async def test_close_remote_waits_for_each_remote_socket_to_go() -> None:
    registry = SocketRegistry()
    remote = registry.open(Listener.REMOTE, _PHONE, "hash-a")
    local = registry.open(Listener.LOOPBACK, _PHONE, "hash-b")

    async def serve(socket_reason: asyncio.Future[CloseReason]) -> None:
        # What a socket's task does: wait to be told, send its close, then leave the registry.
        socket_reason.set_result(await remote.wait_closed())
        registry.discard(remote)

    told: asyncio.Future[CloseReason] = asyncio.get_running_loop().create_future()
    async with asyncio.TaskGroup() as group:
        group.create_task(serve(told))
        await asyncio.sleep(0)
        await registry.close_remote()

    assert told.result() is CloseReason.REDUCED
    assert local.reason is None
    assert registry.count(Listener.REMOTE) == 0
    assert registry.count() == 1
