"""Define EphemeralSegments: the Queen-side store of a Night Veil Cell's records until teardown.

Codingrules section 12: "a Night Veil Cell's execution records live in an ephemeral segment keyed
to the Cell and purged at teardown". This is that segment, held in the Queen's own memory and never
on disk: one `MemoryPheromoneTrail` per Night Veil Cell still alive, holding the segment its Warden
ships (`hivemind.queen.trail.sync` hands each shipped segment to `merge` instead of merging it into
the trail) and every record the Queen herself makes about the Cell or its tasks
(`hivemind.pheromone.retention.trail.VeiledTrail` hands each to `keep`), queryable per Cell while
it lives and taken whole by `take` at teardown (`hivemind.pheromone.retention.purge`). A Queen that
stops loses all of it, which is the outcome a purge would have had.

It also owns the index that says what belongs to which Cell: the Cell's own id, and every id filed
under it since, namely its Warden, the nodes its segments came from, the tasks bound to it, and
the grants, workers, alarms and leases the Queen's own veiled records were about. A task a human
asked for at Night Veil is `expect`-ed before any Cell exists, so what the Queen records about it
before placement is veiled too. The index outlives the segment: once a Cell is taken, a late record
naming one of its ids is still veiled, and only its skeleton copy survives.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.pheromone.retention`.
    Built once per Queen by the composition root (`hivemind.cli.compose.night_veil`); fed by
    `hivemind.hive.night_veil.boundary` (`open`), `hivemind.queen.attach` (`open`, `file`),
    `hivemind.queen.goal_submission` (`expect`), `hivemind.queen.cell_gate.provider` (`bind`) and
    `hivemind.queen.trail.sync` (`merge`); emptied by `hivemind.pheromone.retention.purge`
    (`take`). Calls into `hivemind.common.logging`, `hivemind.pheromone.events`,
    `hivemind.pheromone.trail` and waggle only.

Key invariants:
    - Nothing here ever reaches a disk: every segment is a `MemoryPheromoneTrail`.
    - A Cell's segment is opened at most once: a Cell once taken is never held again, whatever
      reopens it, so a late sync can never rebuild what a purge removed.
    - An id is filed under one Cell, first come; a Cell id is never filed under another Cell.
    - `take` returns every event the Cell's segment held, whatever its size (per-node exports, no
      query limit), and the ids of the Cell's own nodes, never the Queen's.

See Also:
    - .claude/codingrules.md section 12 for the boundary this store keeps.
    - hivemind.pheromone.retention.trail for VeiledTrail, which decides what reaches the trail.
    - hivemind.pheromone.retention.purge for NightVeilTeardownPurge, which takes the segment.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import JsonValue

from hivemind.common.logging import get_logger
from hivemind.pheromone.events import PheromoneEvent
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.pheromone.trail.protocol import TrailQuery, TrailSegment
from waggle.clock import Clock
from waggle.ids import CellId, NodeId

# The subject kinds a veiled event files under its Cell, so a later record about the same grant,
# worker, alarm or lease is veiled too. A cell_ subject is the Cell itself or another Cell, and a
# hive_/node_/event_ subject names nothing that belongs to one Cell, so none of those is filed.
_FILED_SUBJECT_PREFIXES = ("grant_", "alarm_", "worker_", "warden_", "lease_", "task_")

log = get_logger(__name__)

__all__ = ["EphemeralSegments", "TakenSegment", "Veiling"]


@dataclass(frozen=True, slots=True)
class Veiling:
    """Which Night Veil Cell an event belongs to, and whether that Cell's segment is still held.

    Attributes:
        cell_id: The Cell the event is about; None for a task expected at Night Veil that no
            Cell holds yet.
        held: True while the Cell is alive and its segment keeps the event whole.
    """

    cell_id: CellId | None
    held: bool


@dataclass(frozen=True, slots=True)
class TakenSegment:
    """Everything one Night Veil Cell's segment held when it was taken at teardown.

    Attributes:
        events: Every event the segment held, in no particular order.
        node_ids: The Cell's own nodes (its Warden, once per boot) whose segments shipped here.
    """

    events: tuple[PheromoneEvent, ...]
    node_ids: frozenset[NodeId]


class EphemeralSegments:
    """Hold each living Night Veil Cell's segment in memory, and know which ids belong to it.

    Owns its mutable state in place (codingrules 8.5); every method runs on the Queen's one event
    loop, and each segment's own `MemoryPheromoneTrail` serialises its writes under its own lock.
    """

    def __init__(self, clock: Clock) -> None:
        """Build a store holding nothing yet.

        Args:
            clock: Handed to every segment's `MemoryPheromoneTrail` (its export timestamps).
        """
        self._clock = clock
        self._segments: dict[CellId, MemoryPheromoneTrail] = {}
        self._writers: dict[CellId, set[NodeId]] = {}  # Every node with an event in the segment.
        self._cell_nodes: dict[CellId, set[NodeId]] = {}  # The Cell's own shipping nodes only.
        self._owner: dict[str, CellId] = {}
        self._taken: set[CellId] = set()
        self._expected: set[str] = set()

    def open(self, cell_id: CellId) -> None:
        """Start holding `cell_id`'s segment; a no-op for a Cell already held or ever taken."""
        if cell_id in self._segments or cell_id in self._taken:
            return
        self._segments[cell_id] = MemoryPheromoneTrail(self._clock)
        self._writers[cell_id] = set()
        self._cell_nodes[cell_id] = set()
        self._owner[cell_id] = cell_id

    def file(self, member_id: str, cell_id: CellId) -> None:
        """File `member_id` (a Warden, node, grant, ...) under `cell_id`, unless already filed.

        A no-op when `cell_id` was never opened or taken here: only a Night Veil Cell has members.
        """
        if not self.owns(cell_id) or member_id in self._owner:
            return
        self._owner[member_id] = cell_id

    def bind(self, task_id: str, cell_id: CellId) -> None:
        """Bind a task to the Night Veil Cell it now runs on; a no-op unless that Cell is held.

        Unlike `file`, a bind moves a task already filed under a Cell that was taken since: a task
        placed again after its first Cell ended is veiled into the Cell it runs on now.
        """
        if cell_id in self._segments:
            self._owner[task_id] = cell_id

    def expect(self, task_id: str) -> None:
        """Veil `task_id` before any Cell holds it: a task the human asked for at Night Veil."""
        self._expected.add(task_id)

    def holds(self, cell_id: CellId) -> bool:
        """Return whether `cell_id`'s segment is held right now (opened, not yet taken)."""
        return cell_id in self._segments

    def owns(self, cell_id: CellId) -> bool:
        """Return whether `cell_id` is a Night Veil Cell this store has held or taken."""
        return cell_id in self._segments or cell_id in self._taken

    def held_cells(self) -> tuple[CellId, ...]:
        """Return every Cell whose segment is held right now, in opening order."""
        return tuple(self._segments)

    def veiling(self, event: PheromoneEvent) -> Veiling | None:
        """Return the Night Veil Cell `event` is about, or None when it is about none.

        An event is about a Cell when its subject or any id anywhere in its payload was filed
        under that Cell; about an expected task when one of those ids is a task expected here.
        """
        members = _members(event)
        for member in members:
            owner = self._owner.get(member)
            if owner is not None:
                return Veiling(cell_id=owner, held=owner in self._segments)
        if any(member in self._expected for member in members):
            return Veiling(cell_id=None, held=False)
        return None

    async def keep(self, veiling: Veiling, event: PheromoneEvent) -> bool:
        """Keep `event` whole in its Cell's segment while held; withhold it otherwise.

        Returns:
            True when the event was kept; False when no held segment could take it (a Cell
            already taken, or a task no Cell holds yet), so the event survives nowhere.
        """
        cell_id = veiling.cell_id
        # A grant, worker or alarm this event is about belongs to the same Cell from now on, so a
        # later record naming only that id (a grant's revocation, say) is veiled as well.
        if cell_id is not None and event.subject_id.startswith(_FILED_SUBJECT_PREFIXES):
            self.file(event.subject_id, cell_id)
        if cell_id is None or cell_id not in self._segments:
            # Withheld on purpose: only its skeleton copy, if its kind has one, reached the trail.
            log.debug("night_veil.withheld", kind=event.kind, cell_id=cell_id)
            return False
        await self._segments[cell_id].record(event)
        self._writers[cell_id].add(event.node_id)
        return True

    async def merge(self, cell_id: CellId, segment: TrailSegment) -> int | None:
        """Merge a segment `cell_id`'s Warden shipped, when this store owns that Cell.

        Returns:
            None when `cell_id` is no Night Veil Cell known here (the caller merges it into the
            trail as usual); otherwise how many events were new to the held segment, 0 for a
            Cell already taken, whose late segment is dropped.
        """
        if not self.owns(cell_id):
            return None
        self.file(segment.node_id, cell_id)
        held = self._segments.get(cell_id)
        if held is None:
            # Dropped on purpose: a sync racing the purge never rebuilds what the purge took.
            log.debug("night_veil.late_segment", cell_id=cell_id, events=len(segment.events))
            return 0
        self._cell_nodes[cell_id].add(segment.node_id)
        self._writers[cell_id].add(segment.node_id)
        return await held.merge_segment(segment)

    async def query(self, cell_id: CellId, query: TrailQuery) -> tuple[PheromoneEvent, ...]:
        """Read `cell_id`'s held segment, with the trail's own query semantics; empty if none."""
        held = self._segments.get(cell_id)
        return await held.query(query) if held is not None else ()

    async def take(self, cell_id: CellId) -> TakenSegment:
        """Remove `cell_id`'s segment whole and return what it held; the Cell stays owned.

        Taking a Cell never held here (a restart's sweep, an offline Absconding) returns nothing
        but still marks it taken, so any later record naming it is veiled.
        """
        held = self._segments.pop(cell_id, None)
        writers = self._writers.pop(cell_id, set())
        nodes = frozenset(self._cell_nodes.pop(cell_id, set()))
        self._taken.add(cell_id)
        self._owner.setdefault(cell_id, cell_id)
        if held is None:
            return TakenSegment(events=(), node_ids=nodes)
        # Per-node exports carry no query limit, so a long-lived Cell's segment is taken whole.
        events = [
            event for node in sorted(writers) for event in (await held.export_segment(node)).events
        ]
        return TakenSegment(events=tuple(events), node_ids=nodes)


def _members(event: PheromoneEvent) -> tuple[str, ...]:
    """Return the ids `event` names: its subject, and every string anywhere in its payload."""
    found = [event.subject_id]
    # Every level, not only the top: an id nested in a list or an object is still the Cell's, and
    # a payload is small by construction (sixteen kibibytes at most), so the walk stays cheap.
    pending: list[JsonValue] = list(event.payload.values())
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, dict):
            pending.extend(value.values())
    return tuple(found)
