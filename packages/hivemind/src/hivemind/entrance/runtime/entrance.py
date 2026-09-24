"""Provide HiveEntrance: the running Hive Entrance, from start-up checks to a clean stop.

``HiveEntrance.run`` is the Entrance's whole life inside ``hive serve`` (ADR-0032), in one task
group beside the Queen's. Before anything listens it settles what a restart may have left: every
session is re-judged against its device's standing, every push subscription of a device no longer
APPROVED is deleted before the first delivery, lapsed devices and held requests are expired, a
goal a person confirmed whose request a crash kept from being committed is committed now
(``recovery``), and a Guard Bee's reduce order recorded while no Entrance followed the trail is
obeyed. Then it serves the loopback listener, starts the remote one only when the persisted mode is
OPEN and the plan exposes one, and runs its background work: the stream hub, the push outbox, the
reduce-order follower and a sweep that expires devices and held requests on time. A listener that
fails after start is logged and raises an Alarm to the human: the remote one reduces the Entrance,
the loopback one is restarted with bounded backoff (``listeners``); the Queen never stops for
either. ``stop`` closes every socket with a reason, stops both listeners and the background
work, and lets ``run`` return.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.runtime``. Built by
    ``hivemind.entrance.runtime.build``; run by the ``hive serve`` composition root. Calls into
    every Entrance package and the Queen's door (for the Alarm).

Key invariants:
    - Every task the Entrance starts belongs to ``run``'s task group; none outlives it.
    - Nothing listens before the start-up checks have run.

See Also:
    - docs/adr/0032-hive-entrance-http-websocket-api-and-human-inbox.md for the process shape.
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for the checks.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from hivemind.cell import HoneyClearance
from hivemind.common.logging import get_logger
from hivemind.entrance.auth.confirm import expire_pending
from hivemind.entrance.auth.session import Listener
from hivemind.entrance.enrol import DeviceStatus, expire_due
from hivemind.entrance.gate import EntranceServices
from hivemind.entrance.notify import PushOutbox
from hivemind.entrance.reducer import EntranceMode, ReduceReason
from hivemind.entrance.runtime.listeners import EntranceListeners
from hivemind.entrance.runtime.recovery import submit_confirmed_goals
from hivemind.entrance.streams import CloseReason, ReduceOrderFollower, StreamHub
from hivemind.supervision import Alarm, AlarmKind, AlarmSeverity, AlarmState
from waggle.ids import new_alarm_id
from waggle.messages.supervision import AlarmContext

SWEEP_INTERVAL_S = 60.0  # How often lapsed devices and held requests are expired.
FAILURE_ACTOR = "system"  # A reduction for a failed listener is nobody's device's decision.

log = get_logger(__name__)

__all__ = ["SWEEP_INTERVAL_S", "EntranceWorkers", "HiveEntrance"]


@dataclass(frozen=True, slots=True)
class EntranceWorkers:
    """The Entrance's background work.

    Attributes:
        hub: Follows the trail for every live view.
        outbox: Delivers push notices off the callers' paths.
        follower: Obeys the Guard's reduce orders.
    """

    hub: StreamHub
    outbox: PushOutbox
    follower: ReduceOrderFollower


class HiveEntrance:
    """The running Hive Entrance: its listeners, its background work, its start-up checks."""

    def __init__(
        self, services: EntranceServices, listeners: EntranceListeners, workers: EntranceWorkers
    ) -> None:
        """Hold the Entrance's parts; nothing runs until ``run``.

        Args:
            services: What every route and view is handed.
            listeners: The loopback listener (bound) and the remote one's setup.
            workers: The hub, the outbox and the follower.
        """
        self._services = services
        self._listeners = listeners
        self._workers = workers
        self._running = asyncio.Event()
        self._stopping = asyncio.Event()
        listeners.on_failure(self._listener_failed)

    @property
    def services(self) -> EntranceServices:
        """What every route and view is handed."""
        return self._services

    @property
    def listeners(self) -> EntranceListeners:
        """The listeners (their ports, whether the remote one serves)."""
        return self._listeners

    async def run(self) -> None:
        """Check, listen and serve until ``stop``; then close every socket and listener."""
        await self._settle()
        async with asyncio.TaskGroup() as group:
            self._listeners.attach(group)
            workers = self._workers
            background = [
                group.create_task(work)
                for work in (workers.hub.run(), workers.outbox.run(), workers.follower.run())
            ]
            background.append(group.create_task(self._sweep()))
            group.create_task(self._listeners.serve_loopback())
            # A restart comes back in the mode it left: a reduced Entrance stays loopback only.
            if await self._services.reducer.start_mode() is EntranceMode.OPEN:
                await self._listeners.start()
            self._running.set()
            await self._stopping.wait()
            self._services.streams.sockets.close_all(CloseReason.SHUTTING_DOWN)
            await self._listeners.stop_all()
            for task in background:
                task.cancel()

    async def wait_running(self) -> None:
        """Return once the listeners serve (the remote one too, when it started)."""
        await self._running.wait()

    async def stop(self) -> None:
        """Ask ``run`` to close every socket and listener and return; idempotent."""
        self._stopping.set()

    async def _settle(self) -> None:
        """Settle what a restart may have left, before anything listens."""
        services = self._services
        enrolment = services.enrolment
        store = enrolment.records.store
        # Codingrules Appendix C: sessions are re-validated against device state on start.
        ended = await services.listeners[Listener.LOOPBACK].auth.sessions.revalidate()
        # ADR-0034: a subscription whose device is not APPROVED goes before the first delivery.
        approved = [device.id for device in await store.list_devices(DeviceStatus.APPROVED)]
        await services.push.dispatcher.revalidate(approved)
        await expire_due(enrolment)
        await expire_pending(enrolment.records)
        # A confirmed goal whose request a crash kept from being committed is committed now.
        recovered = await submit_confirmed_goals(services)
        # An order recorded while no Entrance followed the trail is obeyed now.
        await self._workers.follower.catch_up()
        log.info(
            "entrance.settled",
            sessions_ended=ended,
            approved_devices=len(approved),
            goals_recovered=len(recovered),
        )

    async def _sweep(self) -> None:
        """Expire lapsed devices and held requests on time, until cancelled."""
        enrolment = self._services.enrolment
        while True:
            # External wait: the sweep interval, on the Entrance's clock.
            await self._services.clock.sleep(SWEEP_INTERVAL_S)
            await expire_due(enrolment)
            await expire_pending(enrolment.records)

    async def _listener_failed(self, listener: Listener, failure: str) -> None:
        """A listener failed after start: reduce for the remote one, and tell the human."""
        log.error("entrance.listener_failed", listener=listener.value, failure=failure)
        if listener is Listener.REMOTE:
            await self._services.reducer.reduce(ReduceReason.LISTENER_FAILED, FAILURE_ACTOR)
        await self._services.queen.escalate_to_human(self._alarm(listener, failure))

    def _alarm(self, listener: Listener, failure: str) -> Alarm:
        """Build the Alarm a failed listener raises at the human."""
        clock = self._services.clock
        hive_id = self._services.enrolment.records.identity.hive_id
        after = (
            "the Entrance was reduced to loopback only; reopen it on loopback once fixed"
            if listener is Listener.REMOTE
            else "the Entrance is restarting it with backoff; check the Hive Stand if it stays down"
        )
        return Alarm(
            id=new_alarm_id(clock),
            kind=AlarmKind.OTHER,
            severity=AlarmSeverity.WARNING
            if listener is Listener.REMOTE
            else AlarmSeverity.CRITICAL,
            origin=hive_id,
            attempts=0,
            context=AlarmContext(
                task_id=None, cell_id=None, worker_id=None, event_id=None, handoff=None
            ),
            detail=f"The Hive Entrance's {listener.value} listener failed ({failure}); {after}.",
            clearance=HoneyClearance.C1,
            raised_at=clock.now(),
            state=AlarmState.HANDLING,
        )
