"""Run the Entrance's two listeners: loopback always, remote only while exposed and not reduced.

The loopback listener's socket is bound before anything starts (a loopback listener that cannot
bind refuses to start ``hive serve``, and the port it got shapes the loopback origin and relying
party the applications are built with, so the applications are mounted afterwards). It serves for
as long as the Entrance runs, and because it is the only door the Hive is administered through, a
loopback listener that fails after start is restarted on the same address with bounded backoff
(half a second, doubling to at most thirty), for as long as the Entrance runs; the failure handler
is told once per outage. The remote one exists only when the exposure plan names it and the
Entrance is OPEN (ADR-0033): ``start`` builds its TLS context over a fresh revocation list, binds
its socket, serves it, and in tunnel mode starts the tunnel client as a supervised child; ``stop``
stops the server and the child together, within about a second. These two are the Entrance
Reducer's ``RemoteListenerControl``. After start, a listener that fails never stops the Queen: the
failure handler is told (the running Entrance reduces for the remote listener and raises an Alarm
to the human either way). The listeners are also the routes' ``DoorControl``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.runtime``. Built by the
    Entrance's composition; run inside the Entrance's task group. Calls into the server wrapper,
    the TLS runtime and ``hivemind.entrance.expose.TunnelSupervisor``.

Key invariants:
    - Every server and the tunnel child run as tasks of the Entrance's own task group.
    - ``stop`` is idempotent and leaves no remote server or tunnel child running.
    - The loopback listener is restarted after any failure until ``stop_all``, never stopped by one;
      every wait between restarts is on the injected clock and ends at once on ``stop_all``.

See Also:
    - hivemind.entrance.reducer for ``RemoteListenerControl``.
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, "Exposure never means
      the open internet".
"""

from __future__ import annotations

import asyncio
import socket
import ssl
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

from starlette.types import ASGIApp

from hivemind.common.errors import InvariantViolationError
from hivemind.common.logging import get_logger
from hivemind.common.tasks import reap
from hivemind.entrance.auth.session.models import Listener
from hivemind.entrance.expose import ListenerPlan, TunnelSupervisor
from hivemind.entrance.runtime.server import ListenerServer, bind_listener
from hivemind.entrance.runtime.tls import RemoteTls
from waggle.clock import Clock
from waggle.ids import DeviceId

# Told which listener failed and why (an exception's name); never raises into a server's task.
FailureHandler = Callable[[Listener, str], Awaitable[None]]
LOOPBACK_RESTART_FIRST_S = 0.5  # First wait before restarting the loopback door: it is urgent.
LOOPBACK_RESTART_MAX_S = 30.0  # The backoff's cap: a stubborn failure is retried twice a minute.

log = get_logger(__name__)

__all__ = [
    "LOOPBACK_RESTART_FIRST_S",
    "LOOPBACK_RESTART_MAX_S",
    "EntranceListeners",
    "FailureHandler",
    "RemoteSetup",
    "TunnelLaunch",
]


@dataclass(frozen=True, slots=True)
class TunnelLaunch:
    """How the tunnel client is started in tunnel mode.

    Attributes:
        argv: Its program and arguments (the plan's ``tunnel_argv``).
        env: Its whole environment, from ``tunnel_environment`` (it holds secrets).
        clock: Paces its restart backoff and its stop grace period.
    """

    argv: tuple[str, ...]
    env: Mapping[str, str] = field(repr=False)
    clock: Clock


@dataclass(frozen=True, slots=True)
class RemoteSetup:
    """What the remote listener is started from, besides its application.

    Attributes:
        plan: Where it binds (the exposure plan's remote listener).
        tls: Its TLS runtime; None only for a plan without TLS (no remote mode has none).
        tunnel: The tunnel client to supervise in tunnel mode; None otherwise.
    """

    plan: ListenerPlan
    tls: RemoteTls | None
    tunnel: TunnelLaunch | None = None


class EntranceListeners:
    """The loopback listener, and the remote one while exposed and open."""

    def __init__(self, loopback: socket.socket, remote: RemoteSetup | None, clock: Clock) -> None:
        """Hold the listeners; nothing serves until ``mount``, ``attach`` and ``serve_loopback``.

        Args:
            loopback: The loopback listener's socket, already bound.
            remote: How to start the remote listener; None in loopback mode.
            clock: Paces the loopback listener's restart backoff.
        """
        self._loopback_socket: socket.socket | None = loopback
        # Where the loopback door is rebound after a failure: the very address it first got.
        host, port = loopback.getsockname()[:2]
        self._loopback_address: tuple[str, int] = (str(host), int(port))
        self._setup = remote
        self._clock = clock
        # Set by stop_all: the loopback listener is stopped for good, never restarted.
        self._closing = asyncio.Event()
        # Replaced by the running Entrance (on_failure); until then a failure is only logged.
        self._on_failure: FailureHandler = _log_failure
        self._apps: Mapping[Listener, ASGIApp] = {}
        self._group: asyncio.TaskGroup | None = None
        self._loopback: ListenerServer | None = None
        self._remote: ListenerServer | None = None
        self._tunnel: TunnelSupervisor | None = None

    @property
    def loopback_port(self) -> int:
        """The port the loopback listener is bound to (the same across restarts)."""
        return self._loopback_address[1]

    @property
    def loopback_listening(self) -> bool:
        """Whether the loopback listener is serving right now."""
        return self._loopback is not None and self._loopback.serving

    @property
    def exposed(self) -> bool:
        """Whether the exposure plan names a remote listener at all."""
        return self._setup is not None

    @property
    def remote_listening(self) -> bool:
        """Whether the remote listener is serving right now."""
        return self._remote is not None and self._remote.serving

    @property
    def remote_port(self) -> int | None:
        """The port the remote listener is bound to, while it runs."""
        return self._remote.port if self._remote is not None else None

    def mount(self, apps: Mapping[Listener, ASGIApp]) -> None:
        """Hand the listeners their applications, built once the loopback port was known.

        Args:
            apps: Each listener's application; the remote one only when exposed.
        """
        self._apps = dict(apps)

    def on_failure(self, handler: FailureHandler) -> None:
        """Set who is told when a listener fails after start.

        Args:
            handler: The running Entrance's handler (reduce, and raise an Alarm).
        """
        self._on_failure = handler

    def attach(self, group: asyncio.TaskGroup) -> None:
        """Give the listeners the Entrance's task group, where every server task runs.

        Args:
            group: The Entrance's task group, open for as long as the Entrance runs.
        """
        self._group = group

    async def serve_loopback(self) -> None:
        """Serve the loopback listener until ``stop_all``, restarting it with backoff on failure.

        Raises:
            InvariantViolationError: No application was mounted for it.
        """
        delay, told = LOOPBACK_RESTART_FIRST_S, False
        while True:
            failure, served = await self._serve_loopback_once()
            if failure is None:
                return  # stop_all asked it to stop: the Entrance is going away.
            # A listener that had been serving starts a new outage: backoff and telling reset.
            if served:
                delay, told = LOOPBACK_RESTART_FIRST_S, False
            if not told:
                await self._on_failure(Listener.LOOPBACK, failure)
                told = True
            log.warning("entrance.loopback_restarting", failure=failure, delay_s=delay)
            if await self._pause(delay):
                return
            delay = min(delay * 2, LOOPBACK_RESTART_MAX_S)

    async def start(self) -> None:
        """Start the remote listener (and the tunnel child), if exposed and not running yet."""
        setup, group = self._setup, self._group
        if setup is None or self._remote is not None or group is None:
            return
        try:
            context = await setup.tls.listener_context() if setup.tls is not None else None
            sock = bind_listener(setup.plan.host, setup.plan.port)
        except (OSError, ssl.SSLError) as error:
            # A remote listener that cannot start reduces the Entrance; the Queen runs on.
            await self._on_failure(Listener.REMOTE, type(error).__name__)
            return
        server = ListenerServer(self._app(Listener.REMOTE), sock, context)
        self._remote = server
        group.create_task(self._serve_remote(server))
        if setup.tunnel is not None:
            launch = setup.tunnel
            self._tunnel = TunnelSupervisor(launch.argv, launch.env, launch.clock)
            group.create_task(self._tunnel.run())
        log.info("entrance.remote_listening", port=server.port, tunnel=setup.tunnel is not None)

    async def stop(self) -> None:
        """Stop the remote listener and the tunnel child together; idempotent."""
        server, self._remote = self._remote, None
        tunnel, self._tunnel = self._tunnel, None
        # Both at once: each is bounded to about a second, the Reducer's bound for the door.
        async with asyncio.TaskGroup() as group:
            if server is not None:
                group.create_task(server.stop())
            if tunnel is not None:
                group.create_task(tunnel.stop())

    async def stop_all(self) -> None:
        """Stop both listeners for good (the Entrance is shutting down)."""
        self._closing.set()
        await self.stop()
        if self._loopback is not None:
            await self._loopback.stop()
        if self._loopback_socket is not None:
            self._loopback_socket.close()

    async def device_revoked(self, device_id: DeviceId) -> None:
        """Rebuild the mutual-TLS revocation list after a revocation.

        Args:
            device_id: The device just revoked.
        """
        if await self.certificates_changed():
            log.info("entrance.tls_rebuilt", device_id=device_id)

    async def certificates_changed(self) -> bool:
        """Rebuild the remote listener's context over the current revocation list.

        Called after a revocation and after an expiry sweep moved any device; the next handshake
        uses the rebuilt context.

        Returns:
            Whether there was a TLS context to rebuild (a remote listener with TLS was planned).
        """
        if self._setup is None or self._setup.tls is None:
            return False
        await self._setup.tls.rebuild()
        return True

    def _app(self, listener: Listener) -> ASGIApp:
        """Return a listener's mounted application; serving one unmounted is a composition bug."""
        app = self._apps.get(listener)
        if app is None:
            raise InvariantViolationError(f"No application was mounted for {listener.value}.")
        return app

    async def _serve_loopback_once(self) -> tuple[str | None, bool]:
        """Serve the loopback listener once: why it ended (None when asked), and if it served."""
        try:
            # The first run serves the socket the root bound; a restart binds the same address.
            sock = self._loopback_socket or bind_listener(*self._loopback_address)
        except OSError as error:
            return type(error).__name__, False
        self._loopback_socket = None
        server = ListenerServer(self._app(Listener.LOOPBACK), sock)
        self._loopback = server
        try:
            await server.serve()
        except OSError as error:
            failure: str = type(error).__name__
        else:
            failure = "exited"
        # stop_all sets the flag before stopping it: an exit after that is the one asked for.
        return (None if self._closing.is_set() else failure), server.started

    async def _pause(self, delay_s: float) -> bool:
        """Wait out one backoff step on the clock; True when ``stop_all`` came first."""
        closing = asyncio.ensure_future(self._closing.wait())
        pause = asyncio.ensure_future(self._clock.sleep(delay_s))
        try:
            # External wait: the backoff on the injected clock, or the Entrance stopping.
            await asyncio.wait({closing, pause}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            await reap(closing)
            await reap(pause)
        return self._closing.is_set()

    async def _serve_remote(self, server: ListenerServer) -> None:
        """Serve the remote listener; a failure (or an exit nobody asked for) is reported."""
        try:
            await server.serve()
        except OSError as error:
            failure: str | None = type(error).__name__
        else:
            # stop() forgets the server before stopping it: a server still current exited alone.
            failure = "exited" if self._remote is server else None
        if failure is not None:
            await self._on_failure(Listener.REMOTE, failure)


async def _log_failure(listener: Listener, failure: str) -> None:
    """Log a listener failure; the handler until the running Entrance sets its own."""
    log.error("entrance.listener_failed", listener=listener.value, failure=failure)
