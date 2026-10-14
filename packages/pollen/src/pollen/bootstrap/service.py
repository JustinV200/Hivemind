"""Answer the Queen's swarm.colonize: prepare the runtime, start the Warden, or withdraw it.

``swarm.colonize`` is one request with three actions (ADR-0052), and ``ColonizeService`` is how the
Pollen Packet (the thin gateway program on a device the Hive borrows) serves it. It is a
``DeviceExecutor`` like the others, in two steps: ``prepare`` checks the order is for this device,
Cell and Warden, that a pinned version looks like a version and that the listener address is one a
Warden may dial, and refuses it with a ``control.error`` otherwise; ``run`` then takes the order on
as a task of the packet and returns at once. That matters: PREPARE can run for minutes (a pip
install on a small board), and the reader that dispatches frames must keep serving the leases the
Hive Stand's Warden is still using the device through. The task does the step under the order's own
``deadline_s``, one step at a time, and answers with a correlated ``swarm.colonized``: the outcome
the action produces, or FAILED with a sentence that says why, the level the device is on afterwards,
the runtime version now installed or running, and the Warden's pid. The reply is sent durably, so a
link that drops while the step runs still delivers it after the reconnection.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. A DeviceExecutor consulted by
    ``pollen.agent.dispatch`` (a Queen-only kind, admitted by the dispatcher before this sees it);
    calls into ``pollen.bootstrap.runtime`` and ``pollen.bootstrap.warden`` and waggle only.

Key invariants:
    - The order's ids must be this device's own; an order for another is refused outright.
    - Nothing the order says is a place to fetch code from: it pins a version, and the source is
      the one the operator recorded at install time.
    - One step at a time; an order that arrives while another is running is answered FAILED at
      once, not queued behind a step the Queen may already have given up on.
    - A step is bounded by its deadline and never leaves a program running past it.
    - A failure's sentence names no path, host, argument or output (``MAX_REASON_CHARS`` at most).

See Also:
    - docs/waggle/gateway-contract.md section 17.
    - waggle.messages.swarm.ladder for Colonize and Colonized.
"""

from __future__ import annotations

import asyncio
from functools import partial
from typing import ClassVar

from pollen.bootstrap.runtime import VERSION_PATTERN, installed_version, prepare_runtime
from pollen.bootstrap.warden import WardenKeeper
from pollen.errors import ColonizeFailedError, InvalidRequestError, StateError
from pollen.executors.base import ExecutorDeps, Prepared, payload_of
from waggle.envelope import Envelope
from waggle.messages.base import MAX_REASON_CHARS, WaggleMessage
from waggle.messages.swarm import Colonize, ColonizeAction, Colonized, ColonizeOutcome
from waggle.uris import check_waggle_uri

__all__ = ["ColonizeService"]


class ColonizeService:
    """Serves ``swarm.colonize``: PREPARE, START and WITHDRAW, one at a time."""

    handles: ClassVar[tuple[type[WaggleMessage], ...]] = (Colonize,)

    def __init__(self, deps: ExecutorDeps, keeper: WardenKeeper) -> None:
        """Bind the service to the packet's collaborators and the Warden's keeper.

        Args:
            deps: State, reporter, clock, platform shim, data layout and the task scope.
            keeper: Starts, stops and tracks the on-device Warden.
        """
        self._deps = deps
        self._keeper = keeper
        self._busy = asyncio.Lock()  # Held for the whole of a step; see the module docstring.

    def prepare(self, envelope: Envelope) -> Prepared:
        """Check an order without changing anything.

        Args:
            envelope: A ``swarm.colonize``.

        Returns:
            Nothing is needed; the work is handed to a task as soon as it runs.

        Raises:
            InvalidRequestError: The order names another device, Cell or Warden, pins a version
                that is not one, or names an address a Warden may not dial.
        """
        order = payload_of(envelope, Colonize)
        state = self._deps.state.current
        ids = (order.device_id, order.cell_id, order.warden_id)
        if ids != (state.device_id, state.cell_id, state.warden_id):
            raise InvalidRequestError(f"Order {envelope.id} names another device, Cell or Warden.")
        if order.runtime_version is not None and not VERSION_PATTERN.fullmatch(
            order.runtime_version
        ):
            raise InvalidRequestError(f"Order {envelope.id} pins a version that is not a version.")
        if order.warden_url is not None:
            try:
                check_waggle_uri(order.warden_url)
            except ValueError:
                raise InvalidRequestError(
                    f"Order {envelope.id} names an address a Warden may not dial."
                ) from None
        return Prepared(run=partial(self._accept, envelope, order))

    async def _accept(self, envelope: Envelope, order: Colonize) -> None:
        """Take the order on as a task of the packet, so the reader is free at once."""
        self._deps.scope.spawn(self._carry_out(envelope, order), "colonize")

    async def _carry_out(self, envelope: Envelope, order: Colonize) -> None:
        """Do the step and answer it, whatever happens."""
        reply = await self._outcome(order)
        await self._deps.reporter.send_durable(
            reply, to=envelope.sender, correlation_id=envelope.id
        )

    async def _outcome(self, order: Colonize) -> Colonized:
        """Run the step under its deadline and turn how it ended into the reply."""
        if self._busy.locked():
            return self._failed(order, "Another colonization step is still running.")
        async with self._busy:
            try:
                async with asyncio.timeout(order.deadline_s):
                    return await self._step(order)
            except TimeoutError:
                return self._failed(order, f"The step took longer than {order.deadline_s:g} s.")
            except (ColonizeFailedError, StateError) as error:
                return self._failed(order, str(error))
            except OSError:
                # A full disk, a directory that cannot be made: the Queen is owed an answer, and
                # an unhandled task would give her none; the cause (a path) stays on the device.
                return self._failed(
                    order,
                    "The device refused a file the step needs: check its disk and permissions.",
                )

    async def _step(self, order: Colonize) -> Colonized:
        """Carry out the order's action and describe its success."""
        if order.action is ColonizeAction.PREPARE:
            return await self._prepare(order)
        if order.action is ColonizeAction.START:
            return await self._start(order)
        await self._keeper.withdraw()
        return self._reply(order, ColonizeOutcome.WITHDRAWN, "The Warden was withdrawn.")

    async def _prepare(self, order: Colonize) -> Colonized:
        """Install the pinned runtime; refuse to change one a Warden is running from."""
        version = order.runtime_version
        if version is None:  # The wire validator requires one for PREPARE; this is for the type.
            raise ColonizeFailedError("The order pins no version.")
        if self._keeper.running() is not None and await installed_version(self._deps) != version:
            raise ColonizeFailedError(
                "A Warden is running from the installed runtime; withdraw it before preparing "
                "another version."
            )
        await prepare_runtime(self._deps, version)
        return self._reply(
            order, ColonizeOutcome.PREPARED, "The runtime is installed.", version=version
        )

    async def _start(self, order: Colonize) -> Colonized:
        """Start the Warden (or find the one already running) and report its pid."""
        if order.warden_url is None:  # The wire validator requires one for START.
            raise ColonizeFailedError("The order names no listener address.")
        record = await self._keeper.start(order.warden_url, order.runtime_version)
        return self._reply(
            order,
            ColonizeOutcome.STARTED,
            "The Warden is running.",
            version=record.runtime_version,
            pid=record.pid,
        )

    def _reply(
        self,
        order: Colonize,
        outcome: ColonizeOutcome,
        reason: str,
        *,
        version: str | None = None,
        pid: int | None = None,
    ) -> Colonized:
        """Build the Colonized for ``order``, at the level the device is on now."""
        return Colonized(
            cell_id=order.cell_id,
            device_id=order.device_id,
            action=order.action,
            outcome=outcome,
            level=self._deps.state.current.level,
            runtime_version=version,
            warden_pid=pid,
            reason=reason,
        )

    def _failed(self, order: Colonize, reason: str) -> Colonized:
        """Build the FAILED reply: why, and the level the device is still on."""
        return self._reply(order, ColonizeOutcome.FAILED, reason[:MAX_REASON_CHARS])
