"""Test hivemind.entrance.runtime.listeners: a listener failing after start never stops the Queen.

Over real uvicorn listeners and a real, running Queen, with one listener made to fail mid-run the
way a broken socket would (its server's periodic tick raises ``OSError`` once): the loopback
listener is restarted on the same address after a backoff, the human hears of the outage once,
and a goal submitted through the restarted door is planned by the Queen, who never stopped; the
remote listener's failure instead reduces the Entrance and raises an Alarm, while the loopback
door and the Queen run on.

Fits into the Hive:
    Mirrors src/hivemind/entrance/runtime/listeners.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest
import uvicorn
from builders.entrance.serving import ProgramGrant, RigOptions, ServingRig, serving
from builders.human import single_task_plan
from builders.queen import plan_responder

from hivemind.entrance.app import OPENAPI_PATH
from hivemind.entrance.reducer import EntranceMode
from hivemind.llm import FakeLLMProvider
from hivemind.queen import GoalRequestState
from hivemind.queen.chat import ChatEntry, ChatKind, ChatQuery

_WAIT_S = 5.0  # The first restart comes after half a second; everything else is local.
_SUBMITTER = ProgramGrant(capabilities=("observe", "entrance:submit"))

Tick = Callable[[uvicorn.Server, int], Awaitable[bool]]


class _Breakage:
    """Makes the server listening on one port fail once, mid-run, as a broken socket would."""

    def __init__(self, port: int) -> None:
        """Aim at ``port``; nothing fails until ``tick`` replaces uvicorn's own."""
        self.port = port
        self.failed = False
        self._original: Tick = uvicorn.Server.on_tick

    def tick(self) -> Tick:
        """Return uvicorn's tick, failing once for the aimed-at server."""

        async def on_tick(server: uvicorn.Server, counter: int) -> bool:
            if not self.failed and _port_of(server) == self.port:
                self.failed = True
                raise OSError("the listening socket failed under the server")
            return await self._original(server, counter)

        return on_tick


def _port_of(server: uvicorn.Server) -> int | None:
    """The port a started server listens on."""
    for listening in getattr(server, "servers", ()):
        for sock in listening.sockets:
            return int(sock.getsockname()[1])
    return None


async def _alarms(rig: ServingRig) -> list[ChatEntry]:
    """Every Alarm the human was told of, in the Queen's chat."""
    return [line for line in await rig.deps.chat.read(ChatQuery()) if line.kind is ChatKind.ALARM]


async def _eventually(check: Callable[[], Awaitable[bool]]) -> None:
    """Wait until an async ``check`` holds; fail after ``_WAIT_S``."""
    async with asyncio.timeout(_WAIT_S):
        while True:
            if await check():
                return
            await asyncio.sleep(0.01)


async def test_a_failed_loopback_listener_is_restarted_and_the_queen_runs_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeLLMProvider(responder=plan_responder(single_task_plan))
    async with serving(RigOptions(provider=provider)) as rig:
        _, session = await rig.program(_SUBMITTER)
        listeners = rig.entrance.listeners
        breakage = _Breakage(listeners.loopback_port)
        running = asyncio.ensure_future(rig.queen.run())
        try:
            monkeypatch.setattr(uvicorn.Server, "on_tick", breakage.tick())
            await _eventually(lambda: _told(rig))
            down = not listeners.loopback_listening
            await rig.until(lambda: listeners.loopback_listening, _WAIT_S)
            # A new client: the failed server closed every connection it had.
            fresh = rig.client()
            submitted = await fresh.call(session, "POST", "/v1/goals", {"text": "Write a haiku."})
            request_id = submitted.json()["id"]
            await _eventually(lambda: _planned(rig, request_id))
            alarms = await _alarms(rig)
            queen_ran_on = not running.done()
        finally:
            await rig.queen.stop()
            await running

    assert breakage.failed and down
    assert submitted.status_code == 202, submitted.text
    assert queen_ran_on
    assert len(alarms) == 1 and "loopback listener failed" in alarms[0].text


async def test_a_remote_listener_failing_after_start_reduces_and_the_queen_runs_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with serving(RigOptions(remote=True)) as rig:
        listeners = rig.entrance.listeners
        port = listeners.remote_port
        assert port is not None
        breakage = _Breakage(port)
        running = asyncio.ensure_future(rig.queen.run())
        try:
            monkeypatch.setattr(uvicorn.Server, "on_tick", breakage.tick())
            await _eventually(lambda: _reduced(rig))
            await _eventually(lambda: _told(rig))
            loopback = await rig.client().http.get(OPENAPI_PATH)
            alarms = await _alarms(rig)
            queen_ran_on = not running.done()
        finally:
            await rig.queen.stop()
            await running

    assert breakage.failed
    assert not listeners.remote_listening
    assert loopback.status_code == 200
    assert queen_ran_on
    assert len(alarms) == 1 and "remote listener failed" in alarms[0].text


async def _told(rig: ServingRig) -> bool:
    """Whether the human has been told of a failed listener."""
    return bool(await _alarms(rig))


async def _reduced(rig: ServingRig) -> bool:
    """Whether the Entrance is reduced to loopback only."""
    return await rig.store.entrance_mode.get() is EntranceMode.REDUCED


async def _planned(rig: ServingRig, request_id: str) -> bool:
    """Whether the Queen has planned the goal request."""
    return (await rig.deps.goal_requests.get(request_id)).state is GoalRequestState.PLANNED
