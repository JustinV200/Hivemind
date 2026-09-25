"""Read the Pheromone Trail for the Guard Bee: rebuild its windows on start, then follow every node.

The Guard Bee (the Hive's security watcher, roadmap step 10.6) keeps no table of its own: its
windows are **rebuilt from the trail** on its first round after every start, kind by kind over the
longest window any rule counts that kind in (a day for the kinds its episode index joins through,
and a week back for what the index learns who owns). The trail is already the durable record of
every event a rule counts and of every report the Guard Bee filed (`guard.alert`, which names the
rule, the key and the moment the finding counted up to), so a restart loses nothing a second store
would have kept: a burst that straddles the restart is still on the trail, and its first half is
back in the window when its second half arrives; what was already reported comes back as the Guard
Bee's own alerts, which this module returns from the rebuild so the rounds after it neither count
those events again nor file those requests again. A store of its own would only be a copy of the
trail that could drift from it.

Following is per node. A node's events reach the Queen's trail in that node's own order (the Hive
Stand's as they happen, a Virtual Cell Warden's in the segments it ships), but not in trail order:
a segment merged late carries times older than events already read. So besides one read of
everything since a little before the newest time seen (`LATE_LAG_S`: a Cell's Warden ships on
every heartbeat, so a new node's first segment lands well inside it), the watch keeps a position per
remote node, and reads a node seen for the first time back over the whole horizon. A late segment
from a known node is read on the next round, however late; one from a new node as soon as any of
its events falls inside the lag (a Cell's first segment, landing after the Hive Stand has moved on,
can hold nothing newer than what was read, and without the lag would go unseen until its next).

Night Veil (codingrules section 12): with a Virtual side the Queen records through the boundary's
`VeiledTrail`, whose reads see the durable trail alone, while every record about a living Night
Veil Cell (its Warden's shipped segments, the Queen's own detail about it, the Guard Bee's alerts)
lives only in that Cell's ephemeral segment. So every round also follows each held segment
(`segments_of`), with positions of its own per Cell, and keeps what it reads in memory only; once a
segment is taken at teardown, every fact read from it is forgotten with it.

Attribution (`.facts`): the watch counts only what it may attribute, a fact the Hive Stand recorded
or one a Cell's node recorded about what that Cell owns. A fact a Cell's node recorded about
another Cell's bee, task, grant, proposal or Cell is never counted as what it says: it becomes a
derived fact of kind `SUBJECT_FORGED_KIND`, about the recording node's Cell alone, which the shipped
`subject_forgery` rule counts.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.guard_bee`. Owned by
    `.bee.GuardBee`, which calls `read` once per round; `.evaluate` reads the facts through
    `facts_of` and `resolve`. Calls into `hivemind.common.logging`, `hivemind.pheromone`
    (PheromoneTrail, TrailQuery, EphemeralSegments, segments_of), `.facts` and `.rules`.

Key invariants:
    - Every event is kept at most once, whichever read returned it.
    - Only the Guard Bee's own node's `guard.alert` events are returned by the rebuild: an alert
      merged from another node's segment can never make it believe it already filed something.
    - Nothing older than its kind's horizon is held; the facts per kind are bounded.
    - `facts_of` returns only facts attributed OWN; a FORGED fact is counted only as evidence
      about its recording node's Cell, and never names what it forged.
    - Nothing read from a Night Veil Cell's segment is written anywhere, or held past the segment.

See Also:
    - hivemind.pheromone.trail.tail for `follow`, the single-cursor reader this generalises.
    - hivemind.workers.roles.guard_bee.facts for what one event becomes, and its attribution.
    - hivemind.pheromone.retention for the Night Veil segments read here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from hivemind.common.logging import get_logger
from hivemind.pheromone import (
    EphemeralSegments,
    PheromoneEvent,
    PheromoneTrail,
    TrailQuery,
    segments_of,
)
from hivemind.workers.roles.guard_bee.facts import (
    INDEX_KINDS,
    Attribution,
    EpisodeIndex,
    TrailFact,
    fact_from_event,
)
from hivemind.workers.roles.guard_bee.rules import DERIVED_KINDS, SUBJECT_FORGED_KIND, GuardRules
from waggle.ids import CellId

PAGE_SIZE = 2_000  # Rows per trail read: a sub-second local read, and few pages for any window.
INDEX_HORIZON_S = 86_400.0  # A day of facts the index joins through, and of Capping proposals.
# A week: how long a bee, task or grant stays attributed and joined, so a long task's Cell is still
# heard from; what learns it is a handful of rows per task.
OWNERSHIP_HORIZON_S = 604_800.0
ALERT_KIND = "guard.alert"  # What the Guard Bee records for every finding.
MAX_FACTS_PER_KIND = 50_000  # Past this the oldest facts of one kind are let go (logged once).
# How far behind the newest time seen every round reads again, for a node not yet heard from: a
# Virtual Cell's Warden ships its segment on every heartbeat (15 s by default in a Cell), so its
# first segment lands well inside this.
LATE_LAG_S = 120.0
# How far back the rebuild reads each kind the index learns from (a proposal lives minutes).
_LEARN_HORIZONS = dict.fromkeys(INDEX_KINDS, OWNERSHIP_HORIZON_S) | {
    "capping.proposed": INDEX_HORIZON_S
}

log = get_logger(__name__)

__all__ = [
    "ALERT_KIND",
    "INDEX_HORIZON_S",
    "LATE_LAG_S",
    "OWNERSHIP_HORIZON_S",
    "PAGE_SIZE",
    "TrailReader",
    "TrailWatch",
    "read_pages",
]


class TrailReader(Protocol):
    """What the watch reads: the Queen's trail, or one Night Veil Cell's segment."""

    async def query(self, query: TrailQuery) -> tuple[PheromoneEvent, ...]:
        """Return the events `query` matches, in trail order."""
        ...


@dataclass
class _Cursor:
    """How far one source (the durable trail, or one segment) is read: overall, and per node."""

    since: datetime | None = None  # None until the source's first read.
    nodes: dict[str, datetime] = field(default_factory=dict)  # Remote node -> newest time read.


@dataclass(frozen=True, slots=True)
class _SegmentReader:
    """One living Night Veil Cell's ephemeral segment, read like a trail."""

    segments: EphemeralSegments
    cell_id: CellId

    async def query(self, query: TrailQuery) -> tuple[PheromoneEvent, ...]:
        """Read the Cell's held segment; see `EphemeralSegments.query`."""
        return await self.segments.query(self.cell_id, query)


class TrailWatch:
    """What the Guard Bee has read of the trail: its read positions, its facts, and its joins.

    Owns its own mutable state (codingrules 8.5): read positions, the facts per kind (sorted on
    demand), their attributions and the episode index change only through `read`, and shrink only
    as time passes or a Night Veil Cell's segment is taken.
    """

    def __init__(self, rules: GuardRules, own_node: str, alert_horizon_s: float) -> None:
        """Build a watch that has read nothing yet; its first `read` rebuilds.

        Args:
            rules: The rules whose kinds and fields are kept.
            own_node: The Hive Stand's node the Guard Bee records on: only its alerts are its own,
                and only its records bind what belongs to which Cell.
            alert_horizon_s: How far back the rebuild reads the Guard Bee's own alerts: at least
                its longest window, its coalescing window and the hour its cap counts.
        """
        horizons = {k: s for k, s in rules.horizons().items() if k not in DERIVED_KINDS}
        for kind in INDEX_KINDS:
            horizons[kind] = max(horizons.get(kind, 0.0), INDEX_HORIZON_S)
        self._horizons = horizons
        self._alert_horizon_s = alert_horizon_s
        self._watched = rules.watched_fields()
        self._own_node = own_node
        self._facts: dict[str, list[TrailFact]] = {}
        self._unsorted: set[str] = set()
        self._seen: dict[str, datetime] = {}
        self._durable = _Cursor()
        self._veiled: dict[str, _Cursor] = {}  # A held Night Veil Cell -> its segment's cursor.
        self._veiled_ids: dict[str, set[str]] = {}  # That Cell -> the events kept from it.
        self._trimmed_warned = False
        self.index = EpisodeIndex(own_node)
        self._verdicts = _Verdicts(self.index)

    @property
    def horizon_s(self) -> float:
        """The longest horizon of any kind kept."""
        return max(self._horizons.values(), default=0.0)

    async def read(self, trail: PheromoneTrail, now: datetime) -> tuple[PheromoneEvent, ...]:
        """Read what is new on the trail and in every held segment; the first call rebuilds.

        Args:
            trail: The Queen's trail (a `VeiledTrail` with a Virtual side).
            now: This round's time; horizons and positions are measured from it.

        Returns:
            On the first call, the Guard Bee's own `guard.alert` events inside the alert horizon,
            oldest first; on every later call, nothing (its own later alerts are already known).
        """
        alerts: tuple[PheromoneEvent, ...] = ()
        if self._durable.since is None:
            alerts = await self._rebuild(trail, now)
        else:
            await self._follow(trail, self._durable, None)
        await self._follow_segments(segments_of(trail), now)
        if not alerts:
            self._prune(now)
        return alerts

    def facts_of(self, kind: str) -> Sequence[TrailFact]:
        """Return every kept fact of `kind` the Guard Bee may count, oldest first.

        For `SUBJECT_FORGED_KIND`, every forged fact, each about its recording node's Cell.
        """
        if kind == SUBJECT_FORGED_KIND:
            facts = (fact for kept in tuple(self._facts) for fact in self._sorted(kept))
            return self._verdicts.forged(facts)
        return [
            fact for fact in self._sorted(kind) if self._verdicts.of(fact)[0] is Attribution.OWN
        ]

    def resolve(self, fact: TrailFact) -> dict[str, str]:
        """Return `fact`'s ids joined through the episode index (see `EpisodeIndex.resolve`)."""
        return self.index.resolve(fact)

    def _sorted(self, kind: str) -> list[TrailFact]:
        """Every kept fact of `kind`, whatever its attribution, oldest first."""
        facts = self._facts.get(kind, [])
        if kind in self._unsorted:
            facts.sort(key=lambda fact: (fact.at, fact.event_id))
            self._unsorted.discard(kind)
        return facts

    async def _rebuild(self, trail: PheromoneTrail, now: datetime) -> tuple[PheromoneEvent, ...]:
        """Relearn who owns what, read every kept kind back over its horizon, then own alerts."""
        for kind, horizon_s in _LEARN_HORIZONS.items():
            since = now - timedelta(seconds=horizon_s)
            for event in await read_pages(trail, TrailQuery(kind=kind, since=since)):
                self.index.learn(fact_from_event(event, self._watched))
        for kind, horizon_s in self._horizons.items():
            since = now - timedelta(seconds=horizon_s)
            for event in await read_pages(trail, TrailQuery(kind=kind, since=since)):
                self._keep(event, self._durable, None)
        since = now - timedelta(seconds=self._alert_horizon_s)
        alerts = await read_pages(trail, TrailQuery(kind=ALERT_KIND, since=since))
        self._durable.since = now
        return tuple(alert for alert in alerts if alert.node_id == self._own_node)

    async def _follow_segments(self, segments: EphemeralSegments | None, now: datetime) -> None:
        """Follow every held Night Veil segment; forget each one taken since the last round."""
        held = set(segments.held_cells()) if segments is not None else set()
        for cell in tuple(self._veiled):
            if cell not in held:
                self._forget(cell)  # Taken at teardown: nothing read from it outlives it.
        if segments is None:
            return
        for cell in held:
            # A segment seen for the first time is read back over the whole horizon.
            start = now - timedelta(seconds=self.horizon_s)
            cursor = self._veiled.setdefault(cell, _Cursor(since=start))
            await self._follow(_SegmentReader(segments, cell), cursor, cell)

    async def _follow(self, source: TrailReader, cursor: _Cursor, origin: str | None) -> None:
        """Read everything since just before the newest time seen, then each remote node's own."""
        assert cursor.since is not None  # noqa: S101 - set by the rebuild, or a segment's start.
        newest = cursor.since
        # Read again from a lag back, so a new node's first segment, shipped late, is heard from;
        # what was already kept is skipped by id.
        since = cursor.since - timedelta(seconds=LATE_LAG_S)
        for event in await read_pages(source, TrailQuery(since=since)):
            if event.node_id != self._own_node and event.node_id not in cursor.nodes:
                # A node heard from for the first time: its earlier events may predate this read.
                await self._backfill(source, cursor, event, origin)
            self._keep(event, cursor, origin)
            newest = max(newest, event.at)
        cursor.since = newest
        # A segment merged late from a node already known: read it from that node's position.
        for node, since in tuple(cursor.nodes.items()):
            for event in await read_pages(source, TrailQuery(node_id=node, since=since)):
                self._keep(event, cursor, origin)

    async def _backfill(
        self, source: TrailReader, cursor: _Cursor, event: PheromoneEvent, origin: str | None
    ) -> None:
        """Read a newly seen node's events back over the whole horizon before its newest one."""
        since = event.at - timedelta(seconds=self.horizon_s)
        query = TrailQuery(node_id=event.node_id, since=since)
        for earlier in await read_pages(source, query):
            self._keep(earlier, cursor, origin)

    def _keep(self, event: PheromoneEvent, cursor: _Cursor, origin: str | None) -> None:
        """Advance the node's position, teach the index, and keep the event as a fact once."""
        if event.node_id != self._own_node:
            known = cursor.nodes.get(event.node_id, event.at)
            cursor.nodes[event.node_id] = max(known, event.at)
        fact = fact_from_event(event, self._watched) if event.kind in INDEX_KINDS else None
        if fact is not None:
            self.index.learn(fact)  # Idempotent: every copy of an event teaches it once.
        if event.id in self._seen or event.kind not in self._horizons:
            return
        self._seen[event.id] = event.at
        self._facts.setdefault(event.kind, []).append(fact or fact_from_event(event, self._watched))
        self._unsorted.add(event.kind)
        if origin is not None:
            self._veiled_ids.setdefault(origin, set()).add(event.id)

    def _forget(self, cell: str) -> None:
        """Let go of a taken Night Veil segment's cursor and of every fact read from it."""
        self._veiled.pop(cell, None)
        read = self._veiled_ids.pop(cell, set())
        for kind, facts in self._facts.items():
            self._facts[kind] = [fact for fact in facts if fact.event_id not in read]
        for event_id in read:
            self._seen.pop(event_id, None)
        self._verdicts.keep(self._seen)

    def _prune(self, now: datetime) -> None:
        """Let go of what every horizon has passed, and of nodes silent for the whole horizon."""
        for kind in tuple(self._facts):
            cutoff = now - timedelta(seconds=self._horizons[kind])
            kept = [fact for fact in self._sorted(kind) if fact.at >= cutoff]
            if len(kept) > MAX_FACTS_PER_KIND:
                kept = self._trim(kind, kept)
            self._facts[kind] = kept
        oldest = now - timedelta(seconds=self.horizon_s)
        self._seen = {event_id: at for event_id, at in self._seen.items() if at >= oldest}
        self._verdicts.keep(self._seen)
        self._veiled_ids = {
            c: {e for e in ids if e in self._seen} for c, ids in self._veiled_ids.items()
        }
        for cursor in (self._durable, *self._veiled.values()):
            cursor.nodes = {node: at for node, at in cursor.nodes.items() if at >= oldest}
        self.index.prune(
            now - timedelta(seconds=OWNERSHIP_HORIZON_S),
            proposals_before=now - timedelta(seconds=INDEX_HORIZON_S),
        )

    def _trim(self, kind: str, facts: list[TrailFact]) -> list[TrailFact]:
        """Keep the newest `MAX_FACTS_PER_KIND` facts of `kind`, warning once that it had to."""
        if not self._trimmed_warned:
            # Logged once: a trail this busy is worth knowing about, but not on every round.
            log.warning("guard_bee.facts_trimmed", kind=kind, kept=MAX_FACTS_PER_KIND)
            self._trimmed_warned = True
        return facts[-MAX_FACTS_PER_KIND:]


class _Verdicts:
    """Each kept fact's attribution, judged once it can be (`.facts.EpisodeIndex.attribute`).

    Owns its own map (codingrules 8.5): `of` grows it, `keep` shrinks it to the facts still held.
    """

    def __init__(self, index: EpisodeIndex) -> None:
        """Judge through `index`, which knows who owns what."""
        self._index = index
        self._known: dict[str, tuple[Attribution, str | None]] = {}

    def of(self, fact: TrailFact) -> tuple[Attribution, str | None]:
        """Attribute `fact` once it can be; an UNKNOWN one is asked again the next round."""
        known = self._known.get(fact.event_id)
        if known is not None:
            return known
        verdict = self._index.attribute(fact)
        if verdict[0] is not Attribution.UNKNOWN:
            self._known[fact.event_id] = verdict
        if verdict[0] is Attribution.FORGED:
            # Logged once, when first judged: a Cell's node spoke for what another Cell owns.
            log.warning(
                "guard_bee.forged_fact", kind=fact.kind, node_id=fact.node_id, suspect=verdict[1]
            )
        return verdict

    def forged(self, facts: Iterable[TrailFact]) -> list[TrailFact]:
        """Every forged fact among `facts`, as evidence about its recording node's Cell alone."""
        found: list[TrailFact] = []
        for fact in facts:
            verdict, suspect = self.of(fact)
            if verdict is Attribution.FORGED and suspect is not None:
                found.append(_forged(fact, suspect))
        found.sort(key=lambda fact: (fact.at, fact.event_id))
        return found

    def keep(self, held: Mapping[str, datetime]) -> None:
        """Forget the attribution of every fact no longer held."""
        self._known = {event: v for event, v in self._known.items() if event in held}


def _forged(fact: TrailFact, suspect: str) -> TrailFact:
    """The evidence a forged fact is: the same event and moment, about the suspect Cell alone."""
    return TrailFact(
        event_id=fact.event_id,
        at=fact.at,
        kind=SUBJECT_FORGED_KIND,
        node_id=fact.node_id,
        ids={"cell": suspect},
        fields={"forged_kind": fact.kind},
    )


async def read_pages(trail: TrailReader, query: TrailQuery) -> list[PheromoneEvent]:
    """Read every event `query` matches from its `since` on, a page at a time, each once.

    Args:
        trail: The trail (or segment) to read.
        query: The filter; its `limit` is replaced by `PAGE_SIZE` and its `since` advanced.

    Returns:
        Every matching event, in trail order, without duplicates.
    """
    events: list[PheromoneEvent] = []
    ids: set[str] = set()
    since = query.since
    while True:
        # Latency: one indexed local read per page (the trail's own lock, a worker thread).
        page = await trail.query(query.model_copy(update={"since": since, "limit": PAGE_SIZE}))
        fresh = [event for event in page if event.id not in ids]
        events.extend(fresh)
        ids.update(event.id for event in fresh)
        # A short page is the end; a page with nothing new means every row shares one moment.
        if len(page) < PAGE_SIZE or not fresh:
            return events
        since = page[-1].at
