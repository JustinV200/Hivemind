"""Run the Entrance's two listeners: loopback always, remote only while exposed and not reduced.

The loopback listener's socket is bound before anything starts (a loopback listener that cannot
bind refuses to start ``hive serve``, and the port it got shapes the loopback origin and relying
party the applications are built with, so the applications are mounted afterwards). It serves for
as long as the Entrance runs. The remote one exists only when the exposure plan names it and the
Entrance is OPEN (ADR-0033): ``start`` builds its TLS context over a fresh revocation list, binds
its socket, serves it, and in tunnel mode starts the tunnel client as a supervised child; ``stop``
stops the server and the child together, within about a second. These two are the Entrance
Reducer's ``RemoteListenerControl``. After start, a listener that fails never stops the Queen: the
failure handler is told (the running Entrance reduces for the remote listener and raises an Alarm
to the human). The listeners are also the routes' ``DoorControl``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.runtime``. Built by the
    Entrance's composition; run inside the Entrance's task group. Calls into the server wrapper,
    the TLS runtime and ``hivemind.entrance.expose.TunnelSupervisor``.

Key invariants:
    - Every server and the tunnel child run as tasks of the Entrance's own task group.
    - ``stop`` is idempotent and leaves no remote server or tunnel child running.

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
from hivemind.entrance.auth.session.models import Listener
from hivemind.entrance.expose import ListenerPlan, TunnelSupervisor
from hivemind.entrance.runtime.server import ListenerServer, bind_listener
from hivemind.entrance.runtime.tls import RemoteTls
from waggle.clock import Clock
from waggle.ids import DeviceId

# Told which listener failed and why (an exception's name); never raises into a server's task.
FailureHandler = Callable[[Listener, str], Awaitable[None]]

log = get_logger(__name__)

__all__ = ["EntranceListeners", "FailureHandler", "RemoteSetup", "TunnelLaunch"]


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

    def __init__(self, loopback: socket.socket, remote: RemoteSetup | None) -> None:
        """Hold the listeners; nothing serves until ``mount``, ``attach`` and ``serve_loopback``.

        Args:
            loopback: The loopback listener's socket, already bound.
            remote: How to start the remote listener; None in loopback mode.
        """
        self._loopback_socket = loopback
        self._setup = remote
        # Replaced by the running Entrance (on_failure); until then a failure is only logged.
        self._on_failure: FailureHandler = _log_failure
        self._apps: Mapping[Listener, ASGIApp] = {}
        self._group: asyncio.TaskGroup | None = None
        self._loopback: ListenerServer | None = None
        self._remote: ListenerServer | None = None
        self._tunnel: TunnelSupervisor | None = None

    @property
    def loopback_port(self) -> int:
        """The port the loopback listener is bound to."""
        return int(self._loopback_socket.getsockname()[1])

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
        """Serve the loopback listener until it stops; a failure is reported, never raised.

        Raises:
            InvariantViolationError: No application was mounted for it.
        """
        self._loopback = ListenerServer(self._app(Listener.LOOPBACK), self._loopback_socket)
        try:
            await self._loopback.serve()
        except OSError as error:
            await self._on_failure(Listener.LOOPBACK, type(error).__name__)

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
        """Stop both listeners (the Entrance is shutting down)."""
        await self.stop()
        if self._loopback is not None:
            await self._loopback.stop()
        else:
            self._loopback_socket.close()

    async def device_revoked(self, device_id: DeviceId) -> None:
        """Rebuild the mutual-TLS revocation list after a revocation.

        Args:
            device_id: The device just revoked.
        """
        if self._setup is not None and self._setup.tls is not None:
            await self._setup.tls.rebuild()
            log.info("entrance.tls_rebuilt", device_id=device_id)

    def _app(self, listener: Listener) -> ASGIApp:
        """Return a listener's mounted application; serving one unmounted is a composition bug."""
        app = self._apps.get(listener)
        if app is None:
            raise InvariantViolationError(f"No application was mounted for {listener.value}.")
        return app

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
