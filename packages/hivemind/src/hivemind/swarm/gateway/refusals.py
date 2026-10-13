"""Provide TransportRefusals: record what the swarm listener's transport limits refused.

Waggle (the Hive's bee-to-bee wire protocol) protects the Hive Stand's listener at the transport:
a connection that sends frames faster than its ceiling is closed with code 4029, and an envelope
whose id was already seen inside the replay window is dropped before anything handles it. Waggle
writes no trail, so each refusal is reported to a callback, and this is the swarm listener's
callback (ADR-0055). A flood becomes `guard.frame_ceiling` and a repeated frame becomes
`guard.replay_refused`, each naming the node (the Guard Bee, the Hive's security watcher, counts
them as a pattern; it never sees a frame). One duplicate is expected and not recorded: a device
that reconnects replays the frames its outbox still held when the link dropped, and the first of
those after a reconnect are duplicates of frames the Hive already handled and acknowledged too
late. So a duplicate inside a short grace after the node's link attached is dropped silently
(ADR-0055: the reconnection grace), and any other is a refusal on the record.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.gateway`.
    Built by the swarm listener and handed to waggle's `WebSocketServer` as `on_refusal`; reads
    the gateway hub for when a node's link attached and records through the swarm recorder.

Key invariants:
    - The callback never raises for a recorder failure: it runs on a connection's receive path,
      and a trail that is down must not turn a flood into a crash. The failure is logged.
    - An event names the node and the envelope id, never a frame or anything inside one.
    - A flood is always recorded; only a duplicate inside the reconnection grace is silent.

See Also:
    - docs/adr/0055-waggle-replay-window-and-frame-ceiling.md for the rules recorded here.
    - waggle.transport.limits for the ConnectionRefusal this receives.
    - hivemind.queen.cell_gate.refusals for the same shape of record for a Virtual Cell's link.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable
from datetime import timedelta

from pydantic import ValidationError

from hivemind.common.errors import HiveMindError
from hivemind.common.logging import get_logger
from hivemind.swarm.events import SwarmRecorder
from hivemind.swarm.gateway.hub import GatewayHub
from waggle.ids import HiveId
from waggle.transport.limits import ConnectionRefusal, RefusalKind

# How long after a node's link attaches a repeated envelope id counts as the outbox replay a
# reconnection produces. Two minutes covers a device that reconnects, resends its backlog and
# is slow to drain it; a replay attack later than that is on the record.
RECONNECT_GRACE_S = 120.0

FRAME_CEILING_KIND = "guard.frame_ceiling"
REPLAY_REFUSED_KIND = "guard.replay_refused"

log = get_logger(__name__)

__all__ = ["FRAME_CEILING_KIND", "RECONNECT_GRACE_S", "REPLAY_REFUSED_KIND", "TransportRefusals"]


class TransportRefusals:
    """Record each refusal the swarm listener's frame ceiling and replay window make."""

    def __init__(
        self,
        recorder: SwarmRecorder,
        hub: GatewayHub,
        hive_id: HiveId,
        grace_s: float = RECONNECT_GRACE_S,
    ) -> None:
        """Build the callback over the trail writer and the hub.

        Args:
            recorder: The trail, clock and identity the events are recorded with.
            hub: Says when a node's link last attached, which the reconnection grace reads.
            hive_id: The subject of an event about a connection that named no node yet.
            grace_s: Seconds after a link attaches in which a repeated id is expected.
        """
        self._recorder = recorder
        self._hub = hub
        self._hive_id = hive_id
        self._grace = timedelta(seconds=grace_s)
        # Dispatch by the refusal's kind through a table, so a new kind is a new entry and the
        # handler never branches on it.
        self._by_kind: dict[RefusalKind, Callable[[ConnectionRefusal], Awaitable[None]]] = {
            RefusalKind.REPLAY: self._replay,
            RefusalKind.FRAME_CEILING: self._ceiling,
        }

    async def __call__(self, refusal: ConnectionRefusal) -> None:
        """Handle one refusal: record it unless it is an expected duplicate.

        Args:
            refusal: What the transport refused, and for which node.
        """
        await self._by_kind[refusal.kind](refusal)

    async def _replay(self, refusal: ConnectionRefusal) -> None:
        """Record a repeated envelope id, unless it follows a reconnection closely."""
        if self._in_grace(refusal):
            return
        await self._record(REPLAY_REFUSED_KIND, refusal)

    async def _ceiling(self, refusal: ConnectionRefusal) -> None:
        """Record a connection closed for flooding."""
        await self._record(FRAME_CEILING_KIND, refusal)

    def _in_grace(self, refusal: ConnectionRefusal) -> bool:
        """Whether the node's link attached recently enough for a duplicate to be expected."""
        if refusal.node_id is None:
            return False
        attached = self._hub.attached_at_for_node(refusal.node_id)
        return attached is not None and refusal.at - attached <= self._grace

    async def _record(self, kind: str, refusal: ConnectionRefusal) -> None:
        """Write the guard event for a refusal; a failure is logged, never raised."""
        subject = refusal.node_id if refusal.node_id is not None else self._hive_id
        try:
            event = self._recorder.guard_event(
                kind,
                subject,
                {"node_id": refusal.node_id, "envelope_id": refusal.envelope_id},
            )
            await self._recorder.record(event)
        except (HiveMindError, sqlite3.Error, ValidationError) as exc:
            # The refusal itself is already enforced by the transport; only its record is lost.
            log.error("swarm.refusal_not_recorded", kind=kind, error=type(exc).__name__)
