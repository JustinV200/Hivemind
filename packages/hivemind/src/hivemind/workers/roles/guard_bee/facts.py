"""Turn trail events into the facts the Guard Bee's rules count, and join facts into episodes.

A Pheromone Trail event (one row of the Hive's append-only audit log) names what it is about by
ids: its subject (a worker, a Cell, a proposal, a device, ...) and a few payload fields
(`task_id`, `cell_id`, `grant_id`, `principal_id`, `consumer`, `address`, a Capping `tier`).
`fact_from_event` keeps exactly that, classified by id prefix, plus the payload values the rules'
matchers read, and nothing else: rules count ids and kinds, never content. `EpisodeIndex` joins
what one event cannot say by itself, from the events that do: a Capping proposal's task, Cell and
tier (from `capping.proposed`), a task's bee (from `worker.spawned`, the newest spawn at or before
the moment asked about, since a worker id is minted per attempt), a bee's task, and a task's Cell
(`queen.assigned`) and grant (`forage.granted`). So a `capping.rejected`, whose only id is its
proposal, still names the bee whose episode it happened in.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.guard_bee`. Called by
    `.watch`, which turns every event it reads into a fact and teaches the index; `.evaluate`
    resolves each fact it counts through the index. Calls into `hivemind.pheromone` (LlmEvent,
    PheromoneEvent) and the standard library only.

Key invariants:
    - A fact carries ids, enum-like payload values, a kind and a time; never a payload string a
      rule does not read, and never free text. An id is kept only when it is well formed (its
      prefix and a ULID), so a report built from facts always validates.
    - `EpisodeIndex.resolve` only fills in ids the fact itself lacks; it never overrides one.
    - A task's bee is the newest spawn at or before the fact's own time, never a later attempt.

See Also:
    - hivemind.workers.roles.guard_bee.rules for the matchers whose fields a fact keeps.
    - hivemind.pheromone.events.families for the payload each kind carries.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from hivemind.pheromone import LlmEvent, PheromoneEvent

# Which id kind a prefixed id names, as the Guard Bee's rules group by it; `msg_` is a Capping
# proposal's id, which the rules never group by but the index joins through.
_PREFIXES: tuple[tuple[str, str], ...] = (
    ("worker_", "bee"),
    ("warden_", "bee"),
    ("task_", "task"),
    ("cell_", "cell"),
    ("grant_", "grant"),
    ("device_", "device"),
    ("msg_", "proposal"),
)
# The payload fields that hold an id of one of the kinds above, on the kinds the rules read.
_ID_FIELDS = ("principal_id", "consumer", "task_id", "cell_id", "grant_id")
# Every Hive id's ULID half: a payload value that only looks like an id is never taken for one.
_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
CAPABILITY_FAMILY = "capability_family"  # Derived: a guard.denied capability's family.
# The kinds the index learns from, and the one kind whose payload tier is a Capping risk tier.
INDEX_KINDS = frozenset({"capping.proposed", "worker.spawned", "queen.assigned", "forage.granted"})
_CAPPING_FAMILY = "capping"

__all__ = ["CAPABILITY_FAMILY", "INDEX_KINDS", "EpisodeIndex", "TrailFact", "fact_from_event"]


@dataclass(frozen=True, slots=True)
class TrailFact:
    """One trail event as the Guard Bee's rules see it: ids, a few values, a kind and a time.

    Attributes:
        event_id: The event's own id, cited by a report.
        at: When it was recorded.
        kind: Its trail kind.
        node_id: The node whose segment recorded it.
        ids: The ids it names itself, by id kind (bee, task, cell, grant, device, proposal),
            plus `address` and, for a Capping event, `tier`.
        fields: The payload values the rules read, as strings.
        amount: The model spend it carries (an llm.call's cost), else 0.0.
    """

    event_id: str
    at: datetime
    kind: str
    node_id: str
    ids: Mapping[str, str]
    fields: Mapping[str, str] = field(default_factory=dict)
    amount: float = 0.0


def fact_from_event(event: PheromoneEvent, watched: frozenset[str]) -> TrailFact:
    """Keep what the Guard Bee's rules may read of one trail event.

    Args:
        event: One event read from the trail.
        watched: The payload fields any rule's matcher reads.

    Returns:
        The event's ids, its watched values as strings and its spend; nothing else.
    """
    return TrailFact(
        event_id=event.id,
        at=event.at,
        kind=event.kind,
        node_id=event.node_id,
        ids=_ids(event),
        fields=_fields(event, watched),
        amount=_amount(event),
    )


class EpisodeIndex:
    """Join proposals, bees, tasks, Cells and grants from the events that name them together.

    Owns its own mutable maps (codingrules 8.5): `learn` is the only way they grow and `prune`
    the only way they shrink, so what it can answer is always what the trail said, within the
    horizon the Guard Bee keeps it for.
    """

    def __init__(self) -> None:
        """Build an index that knows nothing yet."""
        self._proposals: dict[str, tuple[datetime, Mapping[str, str]]] = {}
        self._spawns: dict[str, list[tuple[datetime, str]]] = {}
        self._links: dict[tuple[str, str], tuple[datetime, str]] = {}

    def learn(self, fact: TrailFact) -> None:
        """Remember what `fact` joins, when it is one of `INDEX_KINDS`.

        Args:
            fact: A fact read from the trail; any other kind is ignored.
        """
        ids = fact.ids
        if fact.kind == "capping.proposed" and "proposal" in ids:
            joined = {key: ids[key] for key in ("task", "cell", "tier") if key in ids}
            self._proposals[ids["proposal"]] = (fact.at, joined)
        elif fact.kind == "worker.spawned" and "bee" in ids and "task" in ids:
            # Kept in time order, so resolve() can pick the newest spawn before a moment.
            bisect.insort(self._spawns.setdefault(ids["task"], []), (fact.at, ids["bee"]))
            self._links[("bee_task", ids["bee"])] = (fact.at, ids["task"])
        elif fact.kind == "queen.assigned" and "task" in ids and "cell" in ids:
            self._links[("task_cell", ids["task"])] = (fact.at, ids["cell"])
        elif fact.kind == "forage.granted" and "task" in ids and "grant" in ids:
            self._links[("task_grant", ids["task"])] = (fact.at, ids["grant"])
            self._links[("grant_task", ids["grant"])] = (fact.at, ids["task"])

    def resolve(self, fact: TrailFact) -> dict[str, str]:
        """Return `fact`'s ids with every id the index can join filled in.

        Args:
            fact: A fact being counted.

        Returns:
            Its own ids, plus its proposal's task, Cell and tier, its grant's or bee's task, its
            task's bee at the fact's own moment, and its task's Cell and grant, where known.
        """
        ids = dict(fact.ids)
        proposal = self._proposals.get(ids.get("proposal", ""))
        if proposal is not None:
            for key, value in proposal[1].items():
                ids.setdefault(key, value)
        self._fill(ids, "task", ("grant_task", ids.get("grant")), ("bee_task", ids.get("bee")))
        if "bee" not in ids and "task" in ids:
            ids.update(_bee_at(self._spawns.get(ids["task"], []), fact.at))
        self._fill(ids, "cell", ("task_cell", ids.get("task")))
        self._fill(ids, "grant", ("task_grant", ids.get("task")))
        return ids

    def prune(self, before: datetime) -> None:
        """Forget every join learnt before `before`: the Guard Bee's index horizon.

        Args:
            before: The oldest moment still worth joining through.
        """
        self._proposals = {key: v for key, v in self._proposals.items() if v[0] >= before}
        self._links = {key: v for key, v in self._links.items() if v[0] >= before}
        spawns = {task: [s for s in seen if s[0] >= before] for task, seen in self._spawns.items()}
        self._spawns = {task: seen for task, seen in spawns.items() if seen}

    def _fill(self, ids: dict[str, str], key: str, *links: tuple[str, str | None]) -> None:
        """Set `ids[key]` from the first link the index knows, unless the fact already names it."""
        for relation, source in links:
            if key in ids:
                return  # Named already, by the fact itself or by an earlier link.
            if source is None:
                continue  # This link's own end is unknown; the next one may still join.
            known = self._links.get((relation, source))
            if known is not None:
                ids[key] = known[1]


def _bee_at(spawns: list[tuple[datetime, str]], at: datetime) -> dict[str, str]:
    """Return the bee of the newest spawn at or before `at`, as `{"bee": id}`, or nothing."""
    # bisect over (time, id) pairs: every spawn at or before `at` sorts before (at, "~").
    position = bisect.bisect_right(spawns, (at, "~"))
    return {"bee": spawns[position - 1][1]} if position else {}


def _ids(event: PheromoneEvent) -> dict[str, str]:
    """Classify the event's subject and id-bearing payload fields by id kind, subject first."""
    ids: dict[str, str] = {}
    candidates = [event.subject_id, *(event.payload.get(name) for name in _ID_FIELDS)]
    for value in candidates:
        kind = _id_kind(value) if isinstance(value, str) else None
        if kind is not None:
            ids.setdefault(kind, str(value))
    address = event.payload.get("address")
    if isinstance(address, str):
        ids["address"] = address
    tier = event.payload.get("tier")
    # Only a Capping event's tier is a risk tier: the scanner's `tier` is a Comb Shield tier.
    if event.family == _CAPPING_FAMILY and isinstance(tier, str):
        ids["tier"] = tier
    return ids


def _id_kind(value: str) -> str | None:
    """Return which id kind a well-formed prefixed id is, or None for any other value."""
    for prefix, kind in _PREFIXES:
        if value.startswith(prefix) and _ULID.match(value.removeprefix(prefix)):
            return kind
    return None


def _fields(event: PheromoneEvent, watched: frozenset[str]) -> dict[str, str]:
    """Return the watched payload values as strings, plus the derived capability family."""
    fields: dict[str, str] = {}
    for name in watched:
        value = event.payload.get(name)
        # Scalars only: a rule matches enum-like values, never a list or a nested object.
        if isinstance(value, bool):
            fields[name] = "true" if value else "false"
        elif isinstance(value, str | int | float):
            fields[name] = str(value)
    capability = event.payload.get("capability")
    if CAPABILITY_FAMILY in watched and isinstance(capability, str):
        fields[CAPABILITY_FAMILY] = capability.partition(":")[0]
    return fields


def _amount(event: PheromoneEvent) -> float:
    """Return the model spend an llm.call event carries, or 0.0 for any other event."""
    if isinstance(event, LlmEvent) and event.usage is not None:
        return event.usage.cost_usd
    return 0.0
