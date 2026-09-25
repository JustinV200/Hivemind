"""Open the Hive Entrance's live views as a device does, and feed them trail events.

``open_view`` connects to a view on the loopback listener, sends the session's signed first frame
and waits until the view has subscribed to its feed (the stream hub's, or the telemetry board's),
so whatever a test writes next is certain to reach it; ``refusal`` opens one expecting the
Entrance to refuse it. ``next_frame`` reads one frame and ``close_code`` reads until the Entrance
closes the socket. ``trail_event`` builds one event of any kind on the Queen's node, and ``burst``
records several back to back, so the hub's next poll hands them all to a view at once: the way a
test makes a view fall further behind than its backlog.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tests of
    hivemind.entrance.streams.views.

Key invariants:
    - Every wait is bounded by ``VIEW_WAIT_S``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from builders.entrance.landing import LandingClient, LandingSession
from builders.entrance.serving import ServingRig
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from hivemind.pheromone import PheromoneEvent, event_class_for
from waggle.ids import new_event_id

VIEW_WAIT_S = 3.0  # Generous: every frame and close here follows a local write.

__all__ = [
    "VIEW_WAIT_S",
    "burst",
    "close_code",
    "next_frame",
    "open_view",
    "refusal",
    "trail_event",
]


async def open_view(
    rig: ServingRig,
    client: LandingClient,
    session: LandingSession,
    target: str,
    feed: Callable[[], int] | None = None,
) -> ClientConnection:
    """Open ``target`` on loopback, send its signed first frame, and wait until it subscribed.

    Args:
        rig: The running rig.
        client: The device's client (it signs the first frame).
        session: The device's session.
        target: The view's path and query, exactly as requested.
        feed: How many subscribers the view's feed has; the stream hub's when omitted.

    Returns:
        The open socket; the caller closes it.
    """
    hub = rig.entrance.services.streams.hub
    subscribers = feed if feed is not None else (lambda: hub.subscribers)
    before = subscribers()
    socket = await connect(f"ws://127.0.0.1:{rig.entrance.listeners.loopback_port}{target}")
    await socket.send(client.hello(session, target))
    await rig.until(lambda: subscribers() > before, VIEW_WAIT_S)
    return socket


async def refusal(
    rig: ServingRig, client: LandingClient, session: LandingSession, target: str
) -> int | None:
    """Open ``target`` with a signed first frame the Entrance is expected to refuse.

    Args:
        rig: The running rig.
        client: The device's client.
        session: The device's session.
        target: The view's path and query.

    Returns:
        The close code the Entrance answered with.
    """
    url = f"ws://127.0.0.1:{rig.entrance.listeners.loopback_port}{target}"
    async with connect(url) as socket:
        await socket.send(client.hello(session, target))
        return await close_code(socket)


async def next_frame(socket: ClientConnection) -> Any:  # noqa: ANN401 -- one parsed JSON frame.
    """Read the next frame, parsed."""
    async with asyncio.timeout(VIEW_WAIT_S):
        return json.loads(await socket.recv())


async def close_code(socket: ClientConnection) -> int | None:
    """Read until the Entrance closes the socket; return its close code."""
    try:
        async with asyncio.timeout(VIEW_WAIT_S):
            while True:
                await socket.recv()
    except ConnectionClosed as closed:
        return closed.rcvd.code if closed.rcvd is not None else None


def trail_event(
    rig: ServingRig, kind: str, subject_id: str, payload: dict[str, Any] | None = None
) -> PheromoneEvent:
    """Build one event of ``kind`` about ``subject_id``, as the Queen's node records it now.

    Args:
        rig: The running rig (its Queen's identity and clock).
        kind: Any kind of any family.
        subject_id: What it is about.
        payload: Its payload; empty when omitted.

    Returns:
        The event, of its family's class.
    """
    identity = rig.deps.identity
    return event_class_for(kind)(
        id=new_event_id(rig.clock),
        hive_id=identity.hive_id,
        node_id=identity.node_id,
        at=rig.clock.now(),
        actor="system",
        kind=kind,
        subject_id=subject_id,
        payload=payload if payload is not None else {},
    )


async def burst(rig: ServingRig, kind: str, subject_id: str, count: int) -> None:
    """Record ``count`` events of ``kind`` back to back, all before the hub's next poll.

    Args:
        rig: The running rig.
        kind: The events' kind.
        subject_id: What they are about.
        count: How many.
    """
    # Nothing here waits: the hub cannot poll in between, so one poll finds every event.
    for _ in range(count):
        await rig.deps.trail.record(trail_event(rig, kind, subject_id))
