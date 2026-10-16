"""Provide HeartbeatMonitor: judge whether each device is reachable, and record when that changes.

A device's Pollen Packet reports in on a fixed beat (`swarm.device_heartbeat`, every
`heartbeat_interval_s`), and the Hive treats silence as the signal that matters: a node is
`UNREACHABLE` when its link is lost or when `heartbeat_miss_limit` beats in a row are missing, and
`ONLINE` again on the next verified heartbeat (docs/waggle/enrolment.md section 8). This module is
both halves of that rule. `judge_liveness` is the pure judgement: given how long a device has been
silent and how often it should speak, is it alive? `HeartbeatMonitor` is the effectful apply: it
records each status change on the registry together with its `swarm.online` or
`swarm.unreachable` event (one transaction), touches the node's `last_seen_at` on every
heartbeat, stores the capacity a `forage.capacity_report` carries, and tells the hub's listeners
when a device comes or goes. The listener drives it with the link's lifecycle (`admit`,
`link_down`, `frame`), and `LivenessLoop` runs `sweep` on a timer to catch a link that is open
but silent, and a device that enrolled and never came back (the lost-accept case of
enrolment.md section 13).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.gateway`.
    Driven by the swarm listener; reads and writes the swarm registry and announces through the
    gateway hub to the Queen's swarm gate (Layer 6). Calls into hivemind.swarm, hivemind.forage,
    hivemind.manifest and waggle only.

Key invariants:
    - A status change is recorded with its trail event in one registry transaction; a change
      another writer got to first (a compare-and-set loss) is not an error and not re-recorded.
    - Every link established is on the trail as `swarm.online`: the move from UNREACHABLE with its
      status change, a link on a node that was already ONLINE as the event alone.
    - An UNREACHABLE node is restored by the next verified heartbeat or by a freshly verified
      connection, either of which is proof of life, and only the writer that wins the move
      announces it.
    - A link that ends after its replacement connected takes nothing offline, and one that ends
      while its replacement is being admitted cannot leave the device UNREACHABLE: a device's
      `admit` and `link_down` run one at a time, so each reads the status the other left.
    - The pure judgement uses the node's own interval, so a device the operator slowed down is
      not marked unreachable at the default cadence.

See Also:
    - docs/waggle/enrolment.md sections 8 and 13 for the rules this applies.
    - hivemind.swarm.node_state for the status machine whose edges it records.
    - waggle.loop for the TickLoop shape LivenessLoop follows.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar

from pydantic import JsonValue

from hivemind.common.logging import get_logger
from hivemind.forage import ForageCapacity, HostCapacity
from hivemind.manifest import SwarmSection
from hivemind.swarm.errors import NodeStatusConflictError, SwarmError
from hivemind.swarm.events import SwarmRecorder
from hivemind.swarm.gateway.hub import GatewayHub
from hivemind.swarm.gateway.link import DeviceLink
from hivemind.swarm.models import SwarmNode
from hivemind.swarm.node_state import NodeStatus
from hivemind.swarm.registry import SwarmRegistry
from waggle.clock import Clock
from waggle.envelope import Envelope
from waggle.ids import DeviceId
from waggle.loop import TickLoop
from waggle.messages.base import WaggleMessage
from waggle.messages.forage.hosting import CapacityReport
from waggle.messages.swarm import DeviceHeartbeat

SWEEP_INTERVAL_S = 5.0  # How often silence is judged: a third of the default 15 s beat.

log = get_logger(__name__)

__all__ = [
    "SWEEP_INTERVAL_S",
    "HeartbeatMonitor",
    "Liveness",
    "LivenessLoop",
    "LivenessRules",
    "judge_liveness",
]


class Liveness(Enum):
    """The verdict on one device's silence."""

    ALIVE = "ALIVE"  # It has spoken within the allowed silence.
    MISSED = "MISSED"  # It has been silent past heartbeat_miss_limit beats.


def judge_liveness(silence_s: float, interval_s: float, miss_limit: int) -> Liveness:
    """Decide whether a device that has been silent for `silence_s` is still alive.

    Args:
        silence_s: Seconds since the Hive last heard from the device.
        interval_s: How often the device is supposed to report.
        miss_limit: How many beats in a row may be missed before it counts as lost.

    Returns:
        MISSED when the silence is longer than `miss_limit` beats, else ALIVE.
    """
    return Liveness.MISSED if silence_s > interval_s * miss_limit else Liveness.ALIVE


@dataclass(frozen=True, slots=True)
class LivenessRules:
    """The standing rules of liveness: how many beats may be missed, and how often to look.

    Attributes:
        miss_limit: Beats in a row that may be missed before a device is unreachable.
        sweep_interval_s: Seconds between sweeps of the silence of every device.
    """

    miss_limit: int
    sweep_interval_s: float = SWEEP_INTERVAL_S

    @classmethod
    def from_section(cls, section: SwarmSection) -> LivenessRules:
        """Build the rules from the manifest's `[swarm]` section.

        Args:
            section: The loaded `[swarm]` section.

        Returns:
            The rules, with `heartbeat_miss_limit` as the miss limit.
        """
        return cls(miss_limit=section.heartbeat_miss_limit)


class HeartbeatMonitor:
    """Apply liveness to the registry and announce it through the hub."""

    def __init__(
        self,
        registry: SwarmRegistry,
        hub: GatewayHub,
        recorder: SwarmRecorder,
        rules: LivenessRules,
    ) -> None:
        """Build the monitor over the registry, the hub, the trail writer and the rules.

        Args:
            registry: Where node status and `last_seen_at` live.
            hub: Where links are found and listeners are told.
            recorder: The trail, clock and identity status events are written with.
            rules: How many beats may be missed, and how often a sweep looks.
        """
        self._registry = registry
        self._hub = hub
        self._recorder = recorder
        self._rules = rules
        # One status decision per device at a time. `admit` (a new link) and `link_down` (the old
        # link it replaces, dying on its own) run in two connection tasks and each reads the
        # status before moving it; unserialised, the old link's move to UNREACHABLE can commit
        # after the new link's read and leave a connected device unreachable until its next beat.
        self._status_locks: dict[DeviceId, asyncio.Lock] = {}
        # Dispatch by payload type, never by a kind string, so a new report kind is a new entry.
        self._by_payload: dict[
            type[WaggleMessage], Callable[[SwarmNode, Envelope], Awaitable[None]]
        ] = {
            DeviceHeartbeat: self._on_heartbeat,
            CapacityReport: self._on_capacity,
        }

    async def admit(self, node: SwarmNode) -> SwarmNode:
        """Record that a device's connection just verified: it is alive, so it is ONLINE.

        Args:
            node: The device's node record, as the router read it.

        Returns:
            The node as the registry holds it afterwards (ONLINE).

        Raises:
            NodeRevokedError: The node was revoked, so its connection is not admitted.
        """
        async with self._status_lock(node.device_id):
            current = await self._registry.node(node.device_id) or node
            # `touch` refuses a revoked node, which is the last word on whether to admit it.
            await self._registry.touch(node.device_id, self._recorder.clock.now())
            if current.status is NodeStatus.UNREACHABLE:
                moved = await self._move(current, NodeStatus.ONLINE, "link_connected")
                return moved or await self._registry.node(node.device_id) or current
            # Already ONLINE (its first connection after enrolment, a reconnect the sweep had
            # not yet noticed, or the Hive restarting under a node the registry still calls
            # online): no status moves, but a link was established and enrolment.md section 8
            # has every one on the trail.
            await self._record_online(current, "link_connected")
            return current

    async def link_down(self, node: SwarmNode, link: DeviceLink, reason: str) -> None:
        """Record that a device's link ended, unless a newer link has already replaced it.

        Args:
            node: The device's node record.
            link: The link that ended.
            reason: Why it ended, a stable code.
        """
        async with self._status_lock(node.device_id):
            if not self._hub.detach(node.device_id, link):
                return  # A newer link is current: the device never went offline.
            current = await self._registry.node(node.device_id) or node
            if current.status is NodeStatus.ONLINE:
                current = await self._move(current, NodeStatus.UNREACHABLE, reason) or current
        # Announced outside the lock: a listener's reaction is its own and must not hold up the
        # device's next admission.
        await self._hub.announce_offline(current, reason)

    async def frame(self, node: SwarmNode, envelope: Envelope) -> None:
        """Apply a frame nobody was waiting for, then pass it to the hub's listeners.

        Args:
            node: The device it came from.
            envelope: A verified frame; a heartbeat and a capacity report are applied here.
        """
        handler = self._by_payload.get(type(envelope.payload))
        if handler is not None:
            await handler(node, envelope)
        await self._hub.announce_frame(node, envelope)

    async def sweep(self) -> tuple[DeviceId, ...]:
        """Judge every ONLINE device's silence and mark the ones that have gone quiet.

        Returns:
            The devices marked UNREACHABLE by this sweep.
        """
        marked: list[DeviceId] = []
        for node in await self._registry.nodes(status=NodeStatus.ONLINE):
            if self._silence_verdict(node) is Liveness.ALIVE:
                continue
            link = self._hub.link(node.device_id)
            updated = await self._move(node, NodeStatus.UNREACHABLE, "missed_heartbeats")
            if updated is None:
                continue  # Another writer moved it first (a revoke, or a heartbeat just now).
            marked.append(node.device_id)
            # A link that is open but silent is announced; with no link the gate already knows
            # (it heard the link end, or the Hive just restarted and nothing has connected).
            if link is not None:
                self._hub.update_node(updated)
                await self._hub.announce_offline(updated, "missed_heartbeats")
        return tuple(marked)

    def _status_lock(self, device_id: DeviceId) -> asyncio.Lock:
        """Return the lock that serialises one device's admissions and link ends."""
        lock = self._status_locks.get(device_id)
        if lock is None:
            # One per device that ever connected: bounded by the enrolled devices.
            lock = self._status_locks[device_id] = asyncio.Lock()
        return lock

    def _silence_verdict(self, node: SwarmNode) -> Liveness:
        """Judge a device's silence from its live link, or from `last_seen_at` when it has none."""
        link = self._hub.link(node.device_id)
        clock: Clock = self._recorder.clock
        if link is not None:
            silence = clock.monotonic() - link.last_heard
        else:
            silence = (clock.now() - (node.last_seen_at or node.enrolled_at)).total_seconds()
        return judge_liveness(silence, node.heartbeat_interval_s, self._rules.miss_limit)

    async def _on_heartbeat(self, node: SwarmNode, envelope: Envelope) -> None:
        """Touch the node, and restore it to ONLINE if it was UNREACHABLE."""
        heartbeat = envelope.payload
        if not isinstance(heartbeat, DeviceHeartbeat) or heartbeat.device_id != node.device_id:
            return  # A heartbeat naming another device is not this device's word.
        current = await self._registry.node(node.device_id)
        if current is None or current.status is NodeStatus.REVOKED:
            return
        await self._registry.touch(node.device_id, self._recorder.clock.now())
        if current.status is NodeStatus.UNREACHABLE:
            restored = await self._move(current, NodeStatus.ONLINE, "heartbeat_resumed")
            link = self._hub.link(node.device_id)
            # Announced only by the writer that won the move, so the gate hears it once.
            if restored is not None and link is not None:
                self._hub.update_node(restored)
                await self._hub.announce_online(restored, link)

    async def _on_capacity(self, node: SwarmNode, envelope: Envelope) -> None:
        """Store the capacity a device's report carries, keeping what a report cannot change."""
        report = envelope.payload
        if not isinstance(report, CapacityReport) or report.cell_id != node.cell_id:
            return  # A report about another Cell is not this device's to make.
        current = await self._registry.node(node.device_id)
        if current is None or current.status is NodeStatus.REVOKED:
            return
        capacity = ForageCapacity(
            host=HostCapacity.from_wire(report.host, current.platform),
            local_seats=current.capacity.local_seats,
            max_sub_bees=report.max_sub_bees,
        )
        updated = await self._registry.update_facts(node.device_id, current.capabilities, capacity)
        self._hub.update_node(updated)

    async def _record_online(self, node: SwarmNode, reason: str) -> None:
        """Record `swarm.online` for a link established on a node that was already ONLINE."""
        payload = self._payload(node, reason)
        await self._recorder.record(
            self._recorder.swarm_event("swarm.online", node.device_id, payload)
        )

    def _payload(self, node: SwarmNode, reason: str) -> dict[str, JsonValue]:
        """The ids and the reason a status event carries: no address, key or path."""
        return {
            "device_id": node.device_id,
            "node_id": node.node_id,
            "cell_id": node.cell_id,
            "reason": reason,
        }

    async def _move(self, node: SwarmNode, new: NodeStatus, reason: str) -> SwarmNode | None:
        """Move a node's status with its trail event in one transaction.

        Returns:
            The node after the move, or None when another writer moved it first (a revoke, or the
            same move): that writer recorded its own change, so this one records nothing.
        """
        kind = "swarm.online" if new is NodeStatus.ONLINE else "swarm.unreachable"
        event = self._recorder.swarm_event(kind, node.device_id, self._payload(node, reason))
        try:
            return await self._registry.set_status(
                node.device_id, node.status, new, self._recorder.clock.now(), event
            )
        except NodeStatusConflictError:
            return None


class LivenessLoop(TickLoop):
    """Run `HeartbeatMonitor.sweep` on a timer, backing off if the registry fails."""

    _recoverable_errors: ClassVar[tuple[type[Exception], ...]] = (SwarmError, sqlite3.Error)

    def __init__(self, monitor: HeartbeatMonitor, clock: Clock, interval_s: float) -> None:
        """Build the loop; `run()` starts it and `stop()` or cancelling the task ends it.

        Args:
            monitor: The monitor whose sweep runs each tick.
            clock: Drives the wait between sweeps and any backoff.
            interval_s: Seconds between sweeps.
        """
        super().__init__(clock)
        self._monitor = monitor
        self._interval_s = interval_s

    async def _tick(self) -> None:
        """Wait one interval, then sweep."""
        await self._clock.sleep(self._interval_s)
        await self._monitor.sweep()

    async def _on_tick_failed(self, error: Exception) -> None:
        """Log a sweep that failed; the loop backs off and tries again."""
        log.warning("swarm.liveness_sweep_failed", error=type(error).__name__)
