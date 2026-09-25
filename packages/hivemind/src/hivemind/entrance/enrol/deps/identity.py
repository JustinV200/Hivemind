"""Define EntranceIdentity: who the Entrance records its guard.entrance_* events as, and how.

Every enrolment step leaves a ``guard.entrance_*`` event on the Pheromone Trail (the Hive's
append-only audit log, codingrules section 12): an invite minted, a request waiting, an approval, a
lock, a refused redemption. Each event needs the Hive it belongs to, the node whose trail segment
it is written to (the Queen's process, where the Hive Entrance runs) and an actor (who did it).
``EntranceIdentity`` carries the first two and a default actor for events no person decided (an
expiry sweep, a refused redemption: ``"system"``), and ``event`` is the one place such an event is
built, so no flow assembles a ``GuardEvent`` by hand. It mirrors
``hivemind.brood_chamber.chamber.base.ChamberIdentity`` and ``hivemind.cell.CellIdentity``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.enrol.deps``. Built by
    a composition root (the Entrance app, a ``hive entrance`` command, a test); held by
    ``EnrolmentRecords`` and ``ConsoleDeps``. Calls into ``hivemind.pheromone`` (GuardEvent) and
    waggle (ids, the clock).

Key invariants:
    - Every event built here carries this identity's Hive and node and a freshly minted id.
    - ``event`` validates like any ``GuardEvent``: an unknown kind, a malformed actor or subject,
      or a payload carrying text-like keys is refused before anything is written.

See Also:
    - hivemind.pheromone.events.families for the ``guard`` kinds.
    - hivemind.entrance.enrol.record for the flows that record these events.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import JsonValue

from hivemind.pheromone import GuardEvent
from waggle.clock import Clock
from waggle.ids import HiveId, NodeId, new_event_id

__all__ = ["EntranceIdentity"]


@dataclass(frozen=True, slots=True)
class EntranceIdentity:
    """The Hive, node and default actor every ``guard.entrance_*`` event is stamped with.

    Attributes:
        hive_id: The Hive the events belong to; also the Hive a device's enrolment signature
            names (``hivemind.entrance.auth.canonical.enrol_string``).
        node_id: The node whose trail segment the events are written to (the Queen's).
        actor: Who events are recorded as when no person or device decided them: ``"system"``
            for the Entrance process, ``"human"`` for a ``hive entrance`` command at the Hive
            Stand.
    """

    hive_id: HiveId
    node_id: NodeId
    actor: str

    def event(
        self,
        clock: Clock,
        kind: str,
        subject_id: str,
        payload: Mapping[str, JsonValue],
        actor: str | None = None,
    ) -> GuardEvent:
        """Build one ``guard`` event stamped now, from this identity.

        Args:
            clock: Mints the event's id and timestamp.
            kind: The ``guard.*`` kind, e.g. ``"guard.entrance_approved"``.
            subject_id: What it is about: a device id, or the Hive id when no device is known.
            payload: Identifiers, counts and reasons; never a code, a key or a password.
            actor: Who did it (a device id, ``"human"`` or ``"system"``); None for this
                identity's own actor.

        Returns:
            The validated event, not yet recorded.

        Raises:
            pydantic.ValidationError: The kind, actor, subject or payload is not valid for a
                trail event.
        """
        return GuardEvent(
            id=new_event_id(clock),
            hive_id=self.hive_id,
            node_id=self.node_id,
            at=clock.now(),
            actor=actor if actor is not None else self.actor,
            kind=kind,
            subject_id=subject_id,
            payload=dict(payload),
        )
