"""Define CellLink: connect this Cell out to the Queen, announce it, heartbeat, stop on order.

Roadmap step 5.5's own entry-point loop: "connects OUT over the WebSocket transport..., sends
CellReady with the probed platform/capabilities/ForageCapacity, then heartbeats, and runs the
standard loop until Shutdown/CellTeardownRequest." `CellLink` is deliberately its own small
`waggle.loop.TickLoop` (codingrules section 11's shared loop shape), not
`hivemind.wardens.warden.Warden`: a real `Warden` drains its Queen link for `TaskAssign`/
`GrantIssued`/`Intervene` and dispatches them through `hivemind.wardens.autopilot`, which has no
case for `control.shutdown` or `cell.teardown_request` today (neither message is read anywhere in
`hivemind.wardens` as of this dispatch -- confirmed by grep before writing this module), and this
dispatch may not add one (`wardens/warden.py`, `wardens/ticks/` and `wardens/autopilot/` are out
of scope for roadmap step 5.5). `CellLink` owns the transport exclusively instead: one small loop
that announces, heartbeats and watches for exactly the two messages the roadmap step names. Once
the Queen side exists to grant work over this same link (roadmap steps 5.4/5.6, see this
dispatch's own report), wiring a real `Warden` underneath `CellLink` -- or replacing it -- is the
natural next step; `hivemind.wardens.spawn.in_cell.InCellSpawnSource` is already built so a
`WardenDeps.source` for that Warden needs no new code when it arrives.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.in_cell`. Calls into
    `hivemind.cell` (Cell), `hivemind.common.tasks` (reap, reaping) and waggle
    (envelope, ids, loop, messages, transport) only. Built and run by
    `hivemind.cli.in_cell.main.run_in_cell_warden`.

Key invariants:
    - `announce()` must be called (and awaited) exactly once, before `run()`: it connects the
      transport and sends the one `CellReady` this Cell ever sends.
    - `run()` (inherited from `TickLoop`) never returns while the stop flag is clear; a `Shutdown`
      or `CellTeardownRequest` envelope, or the link ending, sets it.
    - `close()` reaps every task this loop still owns before closing the transport, so nothing is
      ever left cancelled-but-unawaited when the event loop closing (codingrules section 11).

See Also:
    - .claude/roadmap.md step 5.5 for the entry point's own description.
    - .claude/codingrules.md section 11 for the TickLoop shape and the reap/reaping helpers.
    - waggle.messages.cell.status for CellReady/CellHeartbeat, the two messages this loop sends.
    - waggle.messages.control.protocol for Shutdown, and waggle.messages.cell.leases for
      CellTeardownRequest, the two messages that stop this loop.
    - hivemind.cli.in_cell.config for CellLinkConfig, this module's own inputs.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

from hivemind.cell.models import Cell
from hivemind.cell.tiers import CombShieldLevel
from hivemind.common.tasks import reap, reaping
from waggle.clock import Clock
from waggle.envelope import Envelope, Hop, wrap
from waggle.errors import CodecError, ConnectionLostError, InvalidPayloadError, SignatureError
from waggle.ids import HiveId, NodeId, WardenId
from waggle.loop import TickLoop
from waggle.messages.cell.leases import CellTeardownRequest
from waggle.messages.cell.status import CellHeartbeat, CellMode, CellReady
from waggle.messages.control.protocol import Shutdown
from waggle.transport.base import Transport

__all__ = ["CellLink", "CellLinkDeps"]


@dataclass(frozen=True, slots=True)
class CellLinkDeps:
    """Everything one CellLink is built with (codingrules section 5.1).

    Attributes:
        transport: This Cell's own dial-out connection to the Queen; unconnected until
            `CellLink.announce()` calls `transport.connect()`.
        cell: This Cell's own probed description (roadmap step 5.5's `InCellSpawnSource.cells()`),
            what `CellReady` and every `CellHeartbeat` describe.
        warden_id: This Warden's own freshly minted id; the `sender` of every envelope this loop
            sends.
        hive_id: The Queen's own bee address; the `recipient` of every envelope this loop sends.
        node_id: This Cell's own freshly minted node id; stamped on every envelope's `node_id`
            (the identity the Codec's Signer signs as).
        clock: Injected time source for every sleep, mint and timestamp.
        heartbeat_interval_s: How often `CellHeartbeat` is sent; also the value it reports.
        runtime_version: The installed `hivemind` distribution version, carried on `CellReady`.
    """

    transport: Transport
    cell: Cell
    warden_id: WardenId
    hive_id: HiveId
    node_id: NodeId
    clock: Clock
    heartbeat_interval_s: float
    runtime_version: str


class CellLink(TickLoop):
    """Announce this Cell to the Queen, heartbeat on an interval, stop on Shutdown or teardown."""

    def __init__(self, deps: CellLinkDeps) -> None:
        """Build a CellLink; call `announce()` before `run()`.

        Args:
            deps: Every collaborator this loop needs.
        """
        super().__init__(deps.clock)
        self._deps = deps
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._receive_task: asyncio.Task[Envelope | None] | None = None
        self._receive_iter: AsyncIterator[Envelope] = deps.transport.receive()

    async def announce(self) -> None:
        """Connect this Cell's transport and send its one CellReady. Call once before `run()`."""
        await self._deps.transport.connect()
        await self._deps.transport.send(
            wrap(_build_cell_ready(self._deps), self._hop(), clock=self._deps.clock)
        )

    async def close(self) -> None:
        """Reap every task this loop still owns, then close the transport. Idempotent-safe."""
        if self._heartbeat_task is not None:
            await reap(self._heartbeat_task)
        if self._receive_task is not None:
            await reap(self._receive_task)
        await self._deps.transport.close()

    async def _tick(self) -> None:
        """Wait for whichever comes first -- the heartbeat deadline, an envelope or stop()."""
        heartbeat_task = self._heartbeat_deadline_task()
        receive_task = self._receive_task_handle()
        # Throwaway: only wakes this wait early when stop() is called mid-tick (matches Warden's
        # own _run_tick shape, codingrules section 11).
        stop_task: asyncio.Task[bool] = asyncio.ensure_future(self._stop.wait())
        async with reaping(stop_task):
            done, _pending = await asyncio.wait(
                {heartbeat_task, receive_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
            )
        if stop_task in done:
            return
        if heartbeat_task in done:
            self._heartbeat_task = None
            await self._send_heartbeat()
        if receive_task in done:
            self._receive_task = None
            envelope = receive_task.result()
            if envelope is None:
                self.stop()  # The link ended; nothing more will ever arrive on it.
                return
            self._handle_envelope(envelope)

    def _handle_envelope(self, envelope: Envelope) -> None:
        """Stop this loop on a Shutdown or CellTeardownRequest; every other kind is ignored.

        Roadmap step 5.5 names exactly these two stop conditions; anything else (a future
        `TaskAssign`, say) is for the real Warden this CellLink stands in for (module docstring).
        """
        if isinstance(envelope.payload, Shutdown | CellTeardownRequest):
            self.stop()

    async def _send_heartbeat(self) -> None:
        """Send one CellHeartbeat reporting this Cell as ACTIVE with no leases or workers yet."""
        heartbeat = CellHeartbeat(
            cell_id=self._deps.cell.id,
            mode=CellMode.ACTIVE,
            lease_ids=(),
            worker_count=0,
            # A receiver rule (waggle.messages.cell.status's own docstring): always true for
            # MEADOW; this Cell has no tiered attestation to report otherwise (_build_cell_ready).
            is_shield_verified=self._deps.cell.comb_shield is CombShieldLevel.MEADOW,
            interval_s=self._deps.heartbeat_interval_s,
        )
        await self._deps.transport.send(wrap(heartbeat, self._hop(), clock=self._deps.clock))

    def _hop(self) -> Hop:
        """This loop's own addressing: this Warden to the Queen, from this Cell's own node."""
        return Hop(
            sender=self._deps.warden_id, recipient=self._deps.hive_id, node_id=self._deps.node_id
        )

    def _heartbeat_deadline_task(self) -> asyncio.Task[None]:
        """Return the in-flight heartbeat-deadline task, starting one if none is pending."""
        if self._heartbeat_task is None:
            self._heartbeat_task = asyncio.ensure_future(
                self._deps.clock.sleep(self._deps.heartbeat_interval_s)
            )
        return self._heartbeat_task

    def _receive_task_handle(self) -> asyncio.Task[Envelope | None]:
        """Return the in-flight receive task, starting one if none is pending."""
        if self._receive_task is None:
            self._receive_task = asyncio.ensure_future(self._next_or_none())
        return self._receive_task

    async def _next_or_none(self) -> Envelope | None:
        """Return the next decoded Envelope, or None once nothing more will ever arrive."""
        try:
            return await anext(self._receive_iter)
        except InvalidPayloadError:
            # The pair stays open per the Transport contract; ask for the next frame instead.
            return await self._next_or_none()
        except (StopAsyncIteration, ConnectionLostError, CodecError, SignatureError):
            return None


def _build_cell_ready(deps: CellLinkDeps) -> CellReady:
    """Build this Cell's one CellReady from its probed capabilities and this loop's own ids."""
    platform, capabilities = deps.cell.capabilities.to_wire()
    return CellReady(
        cell_id=deps.cell.id,
        warden_id=deps.warden_id,
        platform=platform,
        capabilities=capabilities,
        # .to_wire(): Cell.access_level/comb_shield are hivemind's own mirror enums
        # (hivemind.cell.tiers), never the waggle.messages.labels wire form an Envelope carries.
        access_level=deps.cell.access_level.to_wire(),
        comb_shield=deps.cell.comb_shield.to_wire(),
        # Empty exactly when comb_shield is MEADOW (CellReady's own validator); this dispatch's
        # Cell is always provisioned at MEADOW (hivemind.cli.in_cell.config), so there is nothing
        # to report here yet -- Night Veil/Propolis attestation is a later roadmap step (5.7b).
        attestation=(),
        runtime_version=deps.runtime_version,
    )
