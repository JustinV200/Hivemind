"""Answer the housekeeping messages: ping, node update and the Queen's relocation notice.

Three small messages need no lease and no executor of their own. A ``control.ping`` from the Queen
(the Hive's central orchestrator) or the Warden (the per-Cell supervisor) is answered with a
``control.pong`` carrying the device's arrival time; the pong's envelope ``version`` is how the
peer learns the minor the Pollen Packet (the thin gateway program on a device the Hive borrows)
speaks. A ``swarm.node_update`` replaces the node's standing terms (access level, tier, granted
capabilities, dead-man limit, heartbeat interval, Warden, the directories the Warden watches) and
is persisted before it applies, from the *next* request: a request already running is not
interrupted. A ``control.queen_moved`` is the Queen announcing that the Hive Stand, the machine she
runs on, is moving; the packet honours it only by the rules of ``pollen.agent.moved`` and, when it
does, swaps the keys its verifier trusts. The runtime level in a node update is ignored: only a
colonization step (``pollen.bootstrap``, gateway-contract section 17) changes the level this packet
is on, because the level must agree with whether a Warden is actually running on the device.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. A DeviceExecutor consulted by
    pollen.agent.dispatch; changes the state store and the link's live verifier; calls into
    pollen.agent.moved, pollen.agent.link and waggle only.

Key invariants:
    - Only an accepted ``swarm.node_update`` or ``control.queen_moved`` changes what the packet may
      do or whom it trusts, and both are Queen-only kinds the dispatcher admitted.
    - A node update for another device or Cell is refused with ``waggle.codec.invalid_payload``.
    - The verifier is replaced only after the new state reached the disk.

See Also:
    - docs/waggle/gateway-contract.md section 9.5 and docs/waggle/enrolment.md sections 10 and 12.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from functools import partial
from typing import ClassVar

from pollen.agent.link import LiveVerifier
from pollen.agent.moved import accept_moved, trusted_keys
from pollen.errors import InvalidRequestError
from pollen.executors.base import ExecutorDeps, Prepared, payload_of
from pollen.state import PacketState
from waggle.envelope import Envelope
from waggle.messages.base import WaggleMessage
from waggle.messages.control import Ping, Pong, QueenMoved
from waggle.messages.swarm import NodeUpdate

log = logging.getLogger(__name__)

__all__ = ["ControlService"]


class ControlService:
    """Serves ``control.ping``, ``swarm.node_update`` and ``control.queen_moved``."""

    handles: ClassVar[tuple[type[WaggleMessage], ...]] = (Ping, NodeUpdate, QueenMoved)

    def __init__(self, deps: ExecutorDeps, verifier: LiveVerifier) -> None:
        """Bind the service to the shared collaborators and the link's live verifier.

        Args:
            deps: State, reporter and clock.
            verifier: The key set every link verifies with; swapped when a relocation is accepted.
        """
        self._deps = deps
        self._verifier = verifier
        self._preparers: dict[type[WaggleMessage], Callable[[Envelope], Prepared]] = {
            Ping: self._prepare_ping,
            NodeUpdate: self._prepare_update,
            QueenMoved: self._prepare_moved,
        }

    def prepare(self, envelope: Envelope) -> Prepared:
        """Read a housekeeping message without changing anything.

        Args:
            envelope: A ping, a node update or a relocation notice.

        Returns:
            Nothing is needed; the work is quick and runs inline.

        Raises:
            InvalidRequestError: A node update names another device or Cell.
        """
        return self._preparers[type(envelope.payload)](envelope)

    def _prepare_ping(self, envelope: Envelope) -> Prepared:
        """Answer with the time the ping arrived, read now and not when the reply is built."""
        payload_of(envelope, Ping)
        return Prepared(run=partial(self._pong, envelope, self._deps.clock.now()))

    def _prepare_update(self, envelope: Envelope) -> Prepared:
        """Refuse an update that is not for this node; otherwise apply it."""
        update = payload_of(envelope, NodeUpdate)
        state = self._deps.state.current
        if update.device_id != state.device_id or update.cell_id != state.cell_id:
            raise InvalidRequestError(f"Update {envelope.id} names another device or Cell.")
        return Prepared(run=partial(self._apply_update, update))

    def _prepare_moved(self, envelope: Envelope) -> Prepared:
        """Accept the notice by the relocation rules."""
        payload_of(envelope, QueenMoved)
        return Prepared(run=partial(self._move, envelope))

    async def _pong(self, ping: Envelope, received_at: datetime) -> None:
        """Send the pong, correlated to the ping."""
        reply = Pong(received_at=received_at)
        await self._deps.reporter.send(reply, to=ping.sender, correlation_id=ping.id)

    async def _apply_update(self, update: NodeUpdate) -> None:
        """Persist the new terms; they apply from the next request."""
        # The level is deliberately not copied: only colonization (pollen.bootstrap) moves a device
        # up the ladder, and it moves it together with the record of the Warden it started.
        await self._deps.state.update(
            warden_id=update.warden_id,
            access_level=update.access_level,
            comb_shield=update.comb_shield,
            granted_capabilities=update.granted_capabilities,
            offline_limit_s=update.offline_limit_s,
            heartbeat_interval_s=update.heartbeat_interval_s,
            # Kept for the Warden the packet starts; the packet reads none of these directories.
            watch_roots=update.watch_roots,
        )
        log.info("pollen.node_updated access=%s", update.access_level.value)

    async def _move(self, envelope: Envelope) -> None:
        """Persist an accepted relocation, then trust the new node's key."""
        notice = payload_of(envelope, QueenMoved)
        now = self._deps.clock.now()

        def change(state: PacketState) -> PacketState | None:
            return accept_moved(state, notice, envelope.node_id, now)

        # Decided and written under the store's lock, so two notices cannot race each other.
        updated = await self._deps.state.transform(change)
        if updated is None:
            log.info("pollen.queen_moved_ignored sequence=%d", notice.sequence)
            return
        self._verifier.replace(trusted_keys(updated))
        log.info("pollen.queen_moved_accepted sequence=%d", notice.sequence)
