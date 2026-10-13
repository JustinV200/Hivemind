"""Define SwarmRecorder: build and record the trail events the Swarm writes about its devices.

The Pheromone Trail (the Hive's append-only audit log) records every state-changing action
(codingrules section 12), and a Swarm device is acted on from many places: the enrolment door
records an invite and an enrolment, the heartbeat monitor records a device coming and going, the
Propolis checks record whether a tunnel held, a session records each command sent. Each of them
needs the same three things to write one event: the trail, a clock that mints the event's id and
time, and the identity (which Hive, which node, which actor) it records as. `SwarmRecorder`
bundles the three so every constructor stays within five parameters, and builds the two families
of event the Swarm writes (`swarm.*` about devices and `guard.*` about its link defences) with the
validation `hivemind.pheromone` already does. The registry takes a built event and writes it in
the same transaction as the state change it records; everything else calls `record`.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down). Constructed once by the composition
    root and handed to the door, the gateway, the source and the shield. Calls into
    hivemind.cell (CellIdentity), hivemind.pheromone and waggle only.

Key invariants:
    - A payload carries identifiers, counts and reason codes only: never an invite token, a key,
      a command's arguments, a path or file content (codingrules sections 12 and 15). The trail's
      own payload validator rejects the obvious text keys; this module is where the rest is
      kept out by what callers are given to pass.
    - A subject is always a waggle id (a device, a Cell, a lease or the Hive), because the trail
      indexes events by it.
    - Building an event writes nothing; only `record` does, and a failure there propagates, so a
      refusal that could not be recorded is never returned as if it had been.

See Also:
    - .claude/codingrules.md section 12 for what the trail records and what it never holds.
    - hivemind.guard.enforcer for the same shape of effectful adapter.
    - hivemind.pheromone.events.families for the `swarm` and `guard` vocabulary.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import JsonValue

from hivemind.cell import CellIdentity
from hivemind.pheromone import GuardEvent, PheromoneEvent, PheromoneTrail, SwarmEvent
from waggle.clock import Clock
from waggle.ids import new_event_id

__all__ = ["SwarmRecorder"]


@dataclass(frozen=True, slots=True)
class SwarmRecorder:
    """The trail, the clock and the identity the Swarm writes its events with.

    Attributes:
        trail: Where an event is recorded; this node's own trail segment.
        clock: Mints each event's id and timestamp.
        identity: The Hive, node and actor events are recorded as (the Hive Stand process, acting
            as "system" or, for an operator command, "human").
    """

    trail: PheromoneTrail
    clock: Clock
    identity: CellIdentity

    def swarm_event(
        self,
        kind: str,
        subject_id: str,
        payload: Mapping[str, JsonValue],
        actor: str | None = None,
    ) -> SwarmEvent:
        """Build one `swarm.*` event stamped now, without writing it.

        Args:
            kind: The `swarm.*` kind, e.g. `"swarm.enrolled"`.
            subject_id: What it is about: a device, Cell or lease id, or the Hive's own id.
            payload: Identifiers, counts and reason codes; never a token, key, argument or path.
            actor: Who did it; None for this recorder's own identity.

        Returns:
            The validated event, ready for a registry to write with a state change or for
            `record`.

        Raises:
            pydantic.ValidationError: The kind, actor, subject or payload is not valid for a
                trail event.
        """
        return SwarmEvent(
            id=new_event_id(self.clock),
            hive_id=self.identity.hive_id,
            node_id=self.identity.node_id,
            at=self.clock.now(),
            actor=actor if actor is not None else self.identity.actor,
            kind=kind,
            subject_id=subject_id,
            payload=dict(payload),
        )

    def guard_event(
        self, kind: str, subject_id: str, payload: Mapping[str, JsonValue]
    ) -> GuardEvent:
        """Build one `guard.*` event stamped now, without writing it.

        Args:
            kind: The `guard.*` kind, e.g. `"guard.frame_ceiling"`.
            subject_id: What it is about: the node's id, or the Hive's own id when none is known.
            payload: Identifiers and reason codes only.

        Returns:
            The validated event, recorded as the system (the Guard acts without a person).

        Raises:
            pydantic.ValidationError: The kind, subject or payload is not valid for a trail event.
        """
        return GuardEvent(
            id=new_event_id(self.clock),
            hive_id=self.identity.hive_id,
            node_id=self.identity.node_id,
            at=self.clock.now(),
            actor="system",
            kind=kind,
            subject_id=subject_id,
            payload=dict(payload),
        )

    async def record(self, event: PheromoneEvent) -> None:
        """Write `event` to the trail.

        Args:
            event: An event built by `swarm_event` or `guard_event`.

        Raises:
            Whatever the trail's `record` raises: the caller never believes an unrecorded action
            was recorded.
        """
        # A local trail write (milliseconds, no network); a failure propagates to the caller.
        await self.trail.record(event)
