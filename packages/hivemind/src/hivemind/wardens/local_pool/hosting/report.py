"""Tell the Queen, and the trail, what a Warden's model server loaded or lost.

A Warden (a Cell's always-on supervisor) that starts a model server on its own Cell owes the Queen
(the central orchestrator) two things: a ``forage.source_added`` Waggle event (the Hive's
bee-to-bee wire protocol) when a model becomes servable, so she can enter it in her Forage map and
seat ledger, and a ``forage.source_removed`` event when it goes, so she drops it again. Both are
also recorded on the Pheromone Trail (the append-only audit log) from the Warden's side, so a
Cell whose trail is shipped later (a Virtual Cell) still shows what its server did. ``ReportPort``
bundles the six collaborators a report needs, built once from a ``WardenDeps``, so each function
here takes a port and the facts, not a dozen arguments. A server that never became healthy loaded
nothing, so the Queen never heard of its sources: ``record_never_loaded`` writes the trail row
alone (cause ``SERVER_DIED``) and sends nothing.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    local_pool.hosting sub-package. Called by ``host``. Calls into ``hivemind.wardens.links``
    (the guarded send), ``hivemind.pheromone`` (ForageEvent), ``hivemind.memory`` (identity),
    ``sources`` and waggle only.

Key invariants:
    - A trail row is written whether or not the Queen's link is still up: a closed link loses the
      wire event (``send_guarded`` returns False), never the audit record.
    - A trail payload holds ids, counts and enum values only (codingrules section 12); the free
      text ``reason`` rides on the wire message and not on the trail.
    - Every row's ``subject_id`` is the Cell's id, so a Night Veil Cell's rows route to its own
      segment (``hivemind.pheromone.retention``).

See Also:
    - waggle.messages.forage.sources for the two messages sent.
    - hivemind.queen.ticks.sources for the Queen's handler of what this module sends.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import JsonValue

from hivemind.forage import ModelSource
from hivemind.memory import MemoryIdentity
from hivemind.pheromone import ForageEvent, PheromoneTrail
from hivemind.wardens.links import send_guarded
from hivemind.wardens.local_pool.hosting.sources import added_message, removed_message
from waggle.clock import Clock
from waggle.envelope import Hop, wrap
from waggle.ids import CellId, WardenId, new_event_id
from waggle.messages.forage import SourceRemovalCause
from waggle.transport.base import Transport

REPORTER = "warden"  # Which side recorded the row: the Queen records the same kinds as "queen".

__all__ = ["REPORTER", "ReportPort", "announce_added", "announce_removed", "record_never_loaded"]


@dataclass(frozen=True, slots=True)
class ReportPort:
    """Everything a model-server report is sent and recorded with.

    Attributes:
        queen_link: The Warden's own end of its link to the Queen.
        hop: The Warden's address, the Queen's and the node id stamped on every envelope.
        trail: The Pheromone Trail ``forage.source_*`` rows are recorded to.
        identity: The Hive, node and actor those rows are stamped with.
        clock: Source of every event id, timestamp and envelope.
        warden_id: This Warden, named in each row.
        cell_id: The Cell the server runs on.
    """

    queen_link: Transport
    hop: Hop
    trail: PheromoneTrail
    identity: MemoryIdentity
    clock: Clock
    warden_id: WardenId
    cell_id: CellId


async def announce_added(
    port: ReportPort, server_id: str, source: ModelSource, reason: str
) -> None:
    """Send ``forage.source_added`` for ``source`` and record it on the trail.

    Args:
        port: Where to send and record.
        server_id: The ``[llm.servers]`` key of the server that loaded it.
        source: The loaded source, its ``host_cell_id`` already this Cell.
        reason: Why it was loaded, within which ceilings; wire only.
    """
    message = added_message(port.cell_id, server_id, source, reason)
    # Sent first: the Queen learns of it as early as the trail does; a gone link only skips this.
    await send_guarded(port.queen_link, wrap(message, port.hop, clock=port.clock))
    await _record(
        port,
        "forage.source_added",
        {"server_id": server_id, "source_id": source.source_id, "seats_total": source.spec.seats},
    )


async def announce_removed(
    port: ReportPort,
    server_id: str,
    source: ModelSource,
    cause: SourceRemovalCause,
    reason: str,
) -> None:
    """Send ``forage.source_removed`` for ``source`` and record it on the trail.

    Args:
        port: Where to send and record.
        server_id: The ``[llm.servers]`` key of the server it left.
        source: The source that is gone.
        cause: Which event removed it.
        reason: Why, in the Warden's words; wire only.
    """
    message = removed_message(port.cell_id, server_id, source, cause, reason)
    await send_guarded(port.queen_link, wrap(message, port.hop, clock=port.clock))
    await _record(
        port,
        "forage.source_removed",
        {"server_id": server_id, "source_id": source.source_id, "cause": cause.value},
    )


async def record_never_loaded(
    port: ReportPort, server_id: str, source_ids: tuple[str, ...], cause: SourceRemovalCause
) -> None:
    """Record, on the trail only, that a server's planned sources never loaded.

    Args:
        port: Where to record.
        server_id: The server that failed to come up.
        source_ids: The sources it would have loaded.
        cause: Why they did not (``SERVER_DIED`` for a server that never became healthy).
    """
    # One row per planned source, so an audit sees every model that was promised and not served;
    # nothing is sent, because the Queen never learned of any of them.
    for source_id in source_ids:
        await _record(
            port,
            "forage.source_removed",
            {
                "server_id": server_id,
                "source_id": source_id,
                "cause": cause.value,
                "loaded": False,
            },
        )


async def _record(port: ReportPort, kind: str, payload: dict[str, JsonValue]) -> None:
    """Record one ``forage.source_*`` row about this Warden's Cell."""
    event = ForageEvent(
        id=new_event_id(port.clock),
        hive_id=port.identity.hive_id,
        node_id=port.identity.node_id,
        at=port.clock.now(),
        actor=port.identity.actor,
        kind=kind,
        subject_id=port.cell_id,
        payload={"warden_id": port.warden_id, "reporter": REPORTER, **payload},
    )
    await port.trail.record(event)
