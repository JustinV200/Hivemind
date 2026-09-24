"""Read the Pheromone Trail for the Guard Bee: rebuild its windows on start, then follow every node.

The Guard Bee (the Hive's security watcher, roadmap step 10.6) keeps no table of its own: its
windows are **rebuilt from the trail** on its first round after every start, kind by kind over the
longest window any rule counts that kind in (a day for the kinds its episode index joins through).
The trail is already the durable record of every event a rule counts and of every report the Guard
Bee filed (`guard.alert`, which names the rule, the key and the moment the finding counted up to),
so a restart loses nothing a second store would have kept: a burst that straddles the restart is
still on the trail, and its first half is back in the window when its second half arrives; what
was already reported comes back as the Guard Bee's own alerts, which this module returns from the
rebuild so the rounds after it neither count those events again nor file those requests again. A
store of its own would only be a copy of the trail that could drift from it.

Following is per node. A node's events reach the Queen's trail in that node's own order (the Hive
Stand's as they happen, a Virtual Cell Warden's in the segments it ships), but not in trail order:
a segment merged late carries times older than events already read. So besides one read of
everything since a little before the newest time seen (`LATE_LAG_S`: a Cell's Warden ships on
every heartbeat, so a new node's first segment lands well inside it), the watch keeps a position per
remote node, and reads a node seen for the first time back over the whole horizon. A late segment
from a known node is read on the next round, however late; one from a new node as soon as any of
its events falls inside the lag (a Cell's first segment, landing after the Hive Stand has moved on,
can hold nothing newer than what was read, and without the lag would go unseen until its next).

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.guard_bee`. Owned by
    `.bee.GuardBee`, which calls `read` once per round; `.evaluate` reads the facts through
    `facts_of` and `resolve`. Calls into `hivemind.pheromone` (PheromoneTrail, TrailQuery),
    `.facts` and `.rules`.

Key invariants:
    - Every event is kept at most once, whichever read returned it.
    - Only the Guard Bee's own node's `guard.alert` events are returned by the rebuild: an alert
      merged from another node's segment can never make it believe it already filed something.
    - Nothing older than its kind's horizon is held; the facts per kind are bounded.

See Also:
    - hivemind.pheromone.trail.tail for `follow`, the single-cursor reader this generalises.
    - hivemind.workers.roles.guard_bee.facts for what one event becomes.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from hivemind.common.logging import get_logger
from hivemind.pheromone import PheromoneEvent, PheromoneTrail, TrailQuery
from hivemind.workers.roles.guard_bee.facts import (
    INDEX_KINDS,
    EpisodeIndex,
    TrailFact,
    fact_from_event,
)
from hivemind.workers.roles.guard_bee.rules import GuardRules

PAGE_SIZE = 2_000  # Rows per trail read: a sub-second local read, and few pages for any window.
INDEX_HORIZON_S = 86_400.0  # A day of joins: a task attempt older than a day loses its bee.
ALERT_KIND = "guard.alert"  # What the Guard Bee records for every finding.
MAX_FACTS_PER_KIND = 50_000  # Past this the oldest facts of one kind are let go (logged once).
# How far behind the newest time seen every round reads again, for a node not yet heard from: a
# Virtual Cell's Warden ships its segment on every heartbeat (15 s by default in a Cell), so its
# first segment lands well inside this.
LATE_LAG_S = 120.0

log = get_logger(__name__)

__all__ = ["ALERT_KIND", "INDEX_HORIZON_S", "LATE_LAG_S", "PAGE_SIZE", "TrailWatch", "read_pages"]


class TrailWatch:
    """What the Guard Bee has read of the trail: its read positions, its facts, and its joins.

    Owns its own mutable state (codingrules 8.5): read positions, the facts per kind (sorted on
    demand) and the episode index change only through `read`, and shrink only as time passes.
    """

    def __init__(self, rules: GuardRules, own_node: str, alert_horizon_s: float) -> None:
        """Build a watch that has read nothing yet; its first `read` rebuilds.

        Args:
            rules: The rules whose kinds and fields are kept.
            own_node: The node the Guard Bee records on; only its alerts are its own.
            alert_horizon_s: How far back the rebuild reads the Guard Bee's own alerts: at least
                its longest window, its coalescing window and the hour its cap counts.
        """
        horizons = rules.horizons()
        for kind in INDEX_KINDS:
            horizons[kind] = max(horizons.get(kind, 0.0), INDEX_HORIZON_S)
        self._horizons = horizons
        self._alert_horizon_s = alert_horizon_s
        self._watched = rules.watched_fields()
        self._own_node = own_node
        self._facts: dict[str, list[TrailFact]] = {}
        self._unsorted: set[str] = set()
        self._seen: dict[str, datetime] = {}
        self._since: datetime | None = None  # None until the first read has rebuilt.
        self._nodes: dict[str, datetime] = {}  # Remote node -> the newest time read from it.
        self._trimmed_warned = False
        self.index = EpisodeIndex()

    @property
    def horizon_s(self) -> float:
        """The longest horizon of any kind kept."""
        return max(self._horizons.values(), default=0.0)

    async def read(self, trail: PheromoneTrail, now: datetime) -> tuple[PheromoneEvent, ...]:
        """Read what is new on the trail; the first call rebuilds and returns the own alerts.

        Args:
            trail: The Queen's trail.
            now: This round's time; horizons and positions are measured from it.

        Returns:
            On the first call, the Guard Bee's own `guard.alert` events inside the alert horizon,
            oldest first; on every later call, nothing (its own later alerts are already known).
        """
        if self._since is None:
            return await self._rebuild(trail, now)
        await self._follow(trail)
        self._prune(now)
        return ()

    def facts_of(self, kind: str) -> Sequence[TrailFact]:
        """Return every kept fact of `kind`, oldest first."""
        facts = self._facts.get(kind, [])
        if kind in self._unsorted:
            facts.sort(key=lambda fact: (fact.at, fact.event_id))
            self._unsorted.discard(kind)
        return facts

    def resolve(self, fact: TrailFact) -> dict[str, str]:
        """Return `fact`'s ids joined through the episode index (see `EpisodeIndex.resolve`)."""
        return self.index.resolve(fact)

    async def _rebuild(self, trail: PheromoneTrail, now: datetime) -> tuple[PheromoneEvent, ...]:
        """Read every kept kind back over its horizon, then the Guard Bee's own alerts."""
        for kind, horizon_s in self._horizons.items():
            since = now - timedelta(seconds=horizon_s)
            for event in await read_pages(trail, TrailQuery(kind=kind, since=since)):
                self._keep(event)
        since = now - timedelta(seconds=self._alert_horizon_s)
        alerts = await read_pages(trail, TrailQuery(kind=ALERT_KIND, since=since))
        self._since = now
        return tuple(alert for alert in alerts if alert.node_id == self._own_node)

    async def _follow(self, trail: PheromoneTrail) -> None:
        """Read everything since just before the newest time seen, then each remote node's own."""
        assert self._since is not None  # noqa: S101 - _follow only runs after _rebuild set it.
        newest = self._since
        # Read again from a lag back, so a new node's first segment, shipped late, is heard from;
        # what was already kept is skipped by id.
        since = self._since - timedelta(seconds=LATE_LAG_S)
        for event in await read_pages(trail, TrailQuery(since=since)):
            if event.node_id != self._own_node and event.node_id not in self._nodes:
                # A node heard from for the first time: its earlier events may predate this read.
                await self._backfill(trail, event.node_id, event.at)
            self._keep(event)
            newest = max(newest, event.at)
        self._since = newest
        # A segment merged late from a node already known: read it from that node's position.
        for node, since in tuple(self._nodes.items()):
            for event in await read_pages(trail, TrailQuery(node_id=node, since=since)):
                self._keep(event)

    async def _backfill(self, trail: PheromoneTrail, node: str, at: datetime) -> None:
        """Read a newly seen node's events back over the whole horizon before its newest one."""
        since = at - timedelta(seconds=self.horizon_s)
        for event in await read_pages(trail, TrailQuery(node_id=node, since=since)):
            self._keep(event)

    def _keep(self, event: PheromoneEvent) -> None:
        """Advance the event's node position and keep it as a fact, once, if a rule needs it."""
        if event.node_id != self._own_node:
            known = self._nodes.get(event.node_id, event.at)
            self._nodes[event.node_id] = max(known, event.at)
        if event.id in self._seen or event.kind not in self._horizons:
            return
        fact = fact_from_event(event, self._watched)
        self._seen[event.id] = event.at
        self.index.learn(fact)
        self._facts.setdefault(event.kind, []).append(fact)
        self._unsorted.add(event.kind)

    def _prune(self, now: datetime) -> None:
        """Let go of what every horizon has passed, and of nodes silent for the whole horizon."""
        for kind in tuple(self._facts):
            cutoff = now - timedelta(seconds=self._horizons[kind])
            kept = [fact for fact in self.facts_of(kind) if fact.at >= cutoff]
            if len(kept) > MAX_FACTS_PER_KIND:
                kept = self._trim(kind, kept)
            self._facts[kind] = kept
        oldest = now - timedelta(seconds=self.horizon_s)
        self._seen = {event_id: at for event_id, at in self._seen.items() if at >= oldest}
        self._nodes = {node: at for node, at in self._nodes.items() if at >= oldest}
        self.index.prune(now - timedelta(seconds=INDEX_HORIZON_S))

    def _trim(self, kind: str, facts: list[TrailFact]) -> list[TrailFact]:
        """Keep the newest `MAX_FACTS_PER_KIND` facts of `kind`, warning once that it had to."""
        if not self._trimmed_warned:
            # Logged once: a trail this busy is worth knowing about, but not on every round.
            log.warning("guard_bee.facts_trimmed", kind=kind, kept=MAX_FACTS_PER_KIND)
            self._trimmed_warned = True
        return facts[-MAX_FACTS_PER_KIND:]


async def read_pages(trail: PheromoneTrail, query: TrailQuery) -> list[PheromoneEvent]:
    """Read every event `query` matches from its `since` on, a page at a time, each once.

    Args:
        trail: The trail to read.
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
