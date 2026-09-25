"""Turn trail events into the facts the Guard Bee's rules count, join them, and attribute them.

A Pheromone Trail event (one row of the Hive's append-only audit log) names what it is about by
ids: its subject (a worker, a Cell, a proposal, a device, ...) and a few payload fields
(`task_id`, `cell_id`, `grant_id`, `principal_id`, `consumer`, `node_id`, `address`, a Capping
`tier`). `fact_from_event` keeps exactly that, classified by id prefix, plus the payload values the
rules' matchers read, and nothing else: rules count ids and kinds, never content. `EpisodeIndex`
joins what one event cannot say by itself, from the events that do: a Capping proposal's task,
Cell and tier (from `capping.proposed`), a task's bee (from `worker.spawned`, the newest spawn at
or before the moment asked about, since a worker id is minted per attempt), a bee's task, and a
task's Cell (`queen.assigned`) and grant (`forage.granted`). So a `capping.rejected`, whose only id
is its proposal, still names the bee whose episode it happened in.

It also attributes every fact (roadmap step 10.6): a Virtual Cell's Warden ships its own trail
segment, so a compromised Cell can record anything on its own node, naming any bee, task or Cell.
The index knows who owns what from the Queen's own records, learnt from the Hive Stand's node
alone: `warden.spawned` binds a Warden and the node its link proved to a Cell, `queen.assigned` a
task to every Cell it was placed on, `forage.granted` a grant to its task. A bee or a proposal is
owned through the claim its Cell's node made (`worker.spawned`, `capping.proposed`), which holds
only when the task it names was placed on that Cell. A fact the Hive Stand recorded is OWN; one a
Cell's node recorded is OWN when that Cell owns everything it names, FORGED when another Cell owns
any of it (evidence against the recording node's Cell, never against the one it names), and
UNKNOWN while nothing says yet. A fact from a Cell joins only through its own Cell's claims.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.guard_bee`. Called by
    `.watch`, which turns every event it reads into a fact, teaches the index and counts only what
    it attributes; `.evaluate` resolves each fact it counts through the index. Calls into
    `hivemind.pheromone` (LlmEvent, PheromoneEvent) and the standard library only.

Key invariants:
    - A fact carries ids, enum-like payload values, a kind and a time; never a payload string a
      rule does not read, and never free text. An id is kept only when it is well formed (its
      prefix and a ULID), so a report built from facts always validates.
    - `EpisodeIndex.resolve` only fills in ids the fact itself lacks; it never overrides one.
    - A task's bee is the newest spawn at or before the fact's own time, never a later attempt.
    - Only the Hive Stand's node binds a Warden, node, task or grant; a Cell's own claim binds a
      bee or proposal to that Cell alone, and a fact is FORGED only against the Cell whose node
      recorded it.

See Also:
    - hivemind.workers.roles.guard_bee.rules for the matchers whose fields a fact keeps.
    - hivemind.pheromone.events.families for the payload each kind carries.
    - hivemind.queen.attach for `warden.spawned`, which names the node a link proved.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from hivemind.pheromone import LlmEvent, PheromoneEvent

# Which id kind a prefixed id names, as the Guard Bee's rules group by it; `msg_` is a Capping
# proposal's id, which the rules never group by but the index joins through, and `node_` the node
# a Warden speaks as, which `warden.spawned` binds to its Cell.
_PREFIXES: tuple[tuple[str, str], ...] = (
    ("worker_", "bee"),
    ("warden_", "bee"),
    ("task_", "task"),
    ("cell_", "cell"),
    ("grant_", "grant"),
    ("device_", "device"),
    ("msg_", "proposal"),
    ("node_", "node"),
)
# The payload fields that hold an id of one of the kinds above, on the kinds the rules read.
_ID_FIELDS = ("principal_id", "consumer", "task_id", "cell_id", "grant_id", "node_id")
# Every Hive id's ULID half: a payload value that only looks like an id is never taken for one.
_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
CAPABILITY_FAMILY = "capability_family"  # Derived: a guard.denied capability's family.
# The kinds the index learns from, and the one kind whose payload tier is a Capping risk tier.
INDEX_KINDS = frozenset(
    {"capping.proposed", "worker.spawned", "queen.assigned", "forage.granted", "warden.spawned"}
)
_CAPPING_FAMILY = "capping"
# The Queen's own records of what belongs where: believed from the Hive Stand's node alone.
BINDING_KINDS = frozenset({"warden.spawned", "queen.assigned", "forage.granted"})
# The id kinds one Cell owns: itself, its Warden and bees, its tasks, their grants, its proposals.
OWNED_KINDS = ("cell", "bee", "task", "grant", "proposal")
# What a claim introduces rather than names: the claim is what gives it an owner.
_CLAIMED = {"worker.spawned": "bee", "capping.proposed": "proposal"}
_CLAIM_KEYS = ("task", "cell", "tier")  # What a claim says its bee or proposal belongs to.
_PROPOSAL = "msg_"  # A Capping proposal's id prefix: its claims are kept for the short horizon.
MAX_BINDINGS = 10_000  # Nodes and Wardens bound at once: far more Cells than any Hive attaches.

__all__ = [
    "BINDING_KINDS",
    "CAPABILITY_FAMILY",
    "INDEX_KINDS",
    "MAX_BINDINGS",
    "OWNED_KINDS",
    "Attribution",
    "EpisodeIndex",
    "TrailFact",
    "fact_from_event",
]

# One node's claim on a bee or proposal: when, and what it said the id belongs to.
Claim = tuple[datetime, Mapping[str, str]]


class Attribution(Enum):
    """Whether the Guard Bee may count a fact, by which node recorded it about what (10.6)."""

    OWN = "own"  # The Hive Stand's record, or one by the node of the Cell owning all it names.
    FORGED = "forged"  # A Cell's node naming what another Cell owns: evidence against that Cell.
    UNKNOWN = "unknown"  # Nothing says yet which Cell the node is, or who owns what it names.


@dataclass(frozen=True, slots=True)
class TrailFact:
    """One trail event as the Guard Bee's rules see it: ids, a few values, a kind and a time.

    Attributes:
        event_id: The event's own id, cited by a report.
        at: When it was recorded.
        kind: Its trail kind.
        node_id: The node whose segment recorded it.
        ids: The ids it names itself, by id kind (bee, task, cell, grant, device, proposal, node),
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
    """Join proposals, bees, tasks, Cells and grants, and say which Cell owns each (module doc).

    Owns its own mutable maps (codingrules 8.5): `learn` is the only way they grow and `prune`
    the only way they shrink, so what it can answer is always what the trail said, within the
    horizon the Guard Bee keeps it for.
    """

    def __init__(self, stand_node: str) -> None:
        """Build an index that knows nothing yet.

        Args:
            stand_node: The Hive Stand's own node: the one whose records are trusted and bind.
        """
        self._stand = stand_node
        self._claims: dict[str, dict[str, Claim]] = {}  # A bee or proposal -> node -> its claim.
        self._spawns: dict[str, list[tuple[datetime, str, str]]] = {}  # Task -> (at, bee, node).
        self._links: dict[tuple[str, str], tuple[datetime, str]] = {}
        self._task_cells: dict[str, dict[str, datetime]] = {}  # Task -> each Cell it was placed on.
        self._cells: dict[str, str] = {}  # A node or Warden -> the Cell it speaks for.
        self._learnt: dict[str, datetime] = {}  # Event id -> its time: learning is idempotent.

    def learn(self, fact: TrailFact) -> None:
        """Remember what `fact` joins or binds, when it is one of `INDEX_KINDS`.

        Args:
            fact: A fact read from the trail. Any other kind is ignored, and so is a binding kind
                recorded on any node but the Hive Stand's: only the Queen's own record binds.
        """
        if fact.event_id in self._learnt or (
            fact.kind in BINDING_KINDS and fact.node_id != self._stand
        ):
            return
        self._learnt[fact.event_id] = fact.at
        ids, claimed = fact.ids, _CLAIMED.get(fact.kind)
        if claimed is not None and claimed in ids:
            self._claim(fact, ids[claimed])
        elif fact.kind == "queen.assigned" and "task" in ids and "cell" in ids:
            self._task_cells.setdefault(ids["task"], {})[ids["cell"]] = fact.at
            self._links[("task_cell", ids["task"])] = (fact.at, ids["cell"])
        elif fact.kind == "forage.granted" and "task" in ids and "grant" in ids:
            self._links[("task_grant", ids["task"])] = (fact.at, ids["grant"])
            self._links[("grant_task", ids["grant"])] = (fact.at, ids["task"])
        elif fact.kind == "warden.spawned" and "bee" in ids and "cell" in ids:
            for member in (ids["bee"], ids.get("node")):
                if member is not None:
                    self._cells.setdefault(member, ids["cell"])  # A node speaks for one Cell.
            while len(self._cells) > MAX_BINDINGS:
                del self._cells[next(iter(self._cells))]  # The oldest binding goes first.

    def resolve(self, fact: TrailFact) -> dict[str, str]:
        """Return `fact`'s ids with every id the index can join filled in.

        A Cell's fact joins only through its own Cell's claims; the Hive Stand's through the one
        Cell holding a claim, when exactly one does.

        Args:
            fact: A fact being counted.

        Returns:
            Its own ids, plus its proposal's task, Cell and tier, its grant's or bee's task, its
            task's bee at the fact's own moment, and its task's Cell and grant, where known.
        """
        ids = dict(fact.ids)
        for key, value in self._claim_of(ids.get("proposal"), fact.node_id).items():
            ids.setdefault(key, value)
        self._fill(ids, "task", ("grant_task", ids.get("grant")))
        for key, value in self._claim_of(ids.get("bee"), fact.node_id).items():
            ids.setdefault(key, value)
        if "bee" not in ids and "task" in ids:
            ids.update(self._bee_at(ids["task"], fact))
        self._fill(ids, "cell", ("task_cell", ids.get("task")))
        self._fill(ids, "grant", ("task_grant", ids.get("task")))
        return ids

    def attribute(self, fact: TrailFact) -> tuple[Attribution, str | None]:
        """Say whether the Guard Bee may count `fact` (module docstring).

        Args:
            fact: A fact read from the trail.

        Returns:
            OWN or UNKNOWN with None; FORGED with the Cell whose node recorded the fact.
        """
        if fact.node_id == self._stand:
            return Attribution.OWN, None
        home = self._cells.get(fact.node_id)
        introduced = _CLAIMED.get(fact.kind)
        named = [(kind, fact.ids[kind]) for kind in OWNED_KINDS if kind in fact.ids]
        named = [(kind, value) for kind, value in named if kind != introduced]
        if home is None or not named:
            return Attribution.UNKNOWN, None
        owned = [self._owned(home, kind, value) for kind, value in named]
        if False in owned:
            return Attribution.FORGED, home  # Another Cell owns something it names.
        return (Attribution.OWN, None) if all(owned) else (Attribution.UNKNOWN, None)

    def prune(self, before: datetime, proposals_before: datetime | None = None) -> None:
        """Forget every join learnt before `before`, and every proposal before its own horizon.

        The Warden and node bindings stay, bounded by count alone: a Cell attached long ago
        still speaks through them.

        Args:
            before: The oldest moment a bee, task or grant is still attributed and joined by.
            proposals_before: The same for a Capping proposal, which lives minutes, not days;
                `before` when omitted.
        """
        short = before if proposals_before is None else proposals_before
        self._learnt = {event: at for event, at in self._learnt.items() if at >= short}
        self._links = {key: v for key, v in self._links.items() if v[0] >= before}
        self._claims = {
            claimed: kept
            for claimed, claims in self._claims.items()
            if (kept := _since(claims, short if claimed.startswith(_PROPOSAL) else before))
        }
        spawns = {task: [s for s in seen if s[0] >= before] for task, seen in self._spawns.items()}
        self._spawns = {task: seen for task, seen in spawns.items() if seen}
        placed = {task: _since_at(cells, before) for task, cells in self._task_cells.items()}
        self._task_cells = {task: cells for task, cells in placed.items() if cells}

    def _claim(self, fact: TrailFact, claimed_id: str) -> None:
        """Keep the claim `fact`'s node makes on `claimed_id`: a node's first claim stands."""
        joined = {key: fact.ids[key] for key in _CLAIM_KEYS if key in fact.ids}
        self._claims.setdefault(claimed_id, {}).setdefault(fact.node_id, (fact.at, joined))
        task = joined.get("task")
        if fact.kind == "worker.spawned" and task is not None:
            spawns, spawn = self._spawns.setdefault(task, []), (fact.at, claimed_id, fact.node_id)
            if spawn not in spawns:
                # Kept in time order, so a task's bee can be the newest spawn before a moment.
                bisect.insort(spawns, spawn)

    def _holder(self, node: str, claim: Mapping[str, str]) -> str | None:
        """The Cell holding `node`'s claim: its own, when the task it names was placed there."""
        home = self._cells.get(node)
        if node == self._stand:
            # Trusted: the Hive Stand's Warden spawns on the Hive Stand's own Cell, named by its
            # node until the Queen's record of that Cell has been read.
            return home if home is not None else node
        task, cell = claim.get("task"), claim.get("cell")
        placed = home is not None and home in self._task_cells.get(task or "", {})
        return home if placed and cell in (None, home) else None

    def _holders(self, claimed_id: str) -> dict[str, str]:
        """Every node whose claim on `claimed_id` holds, and the Cell holding it."""
        claims = self._claims.get(claimed_id, {})
        held = {node: self._holder(node, claim) for node, (_, claim) in claims.items()}
        return {node: cell for node, cell in held.items() if cell is not None}

    def _owned(self, home: str, kind: str, value: str) -> bool | None:
        """Whether `home` owns `value`: True, False (another Cell does) or None (unknown)."""
        if kind == "cell":
            return value == home
        if kind in ("task", "grant"):
            granted = self._links.get(("grant_task", value)) if kind == "grant" else None
            placed = self._task_cells.get(granted[1] if granted is not None else value)
            return None if not placed else home in placed
        if value.startswith("warden_"):
            warden_cell = self._cells.get(value)
            return None if warden_cell is None else warden_cell == home
        holders = set(self._holders(value).values())
        if home not in holders:
            return False if holders else None
        # Claimed by two Cells: nothing on the trail says which claim is true, so neither counts.
        return True if len(holders) == 1 else None

    def _claim_of(self, claimed_id: str | None, node: str) -> Mapping[str, str]:
        """What `node` may join `claimed_id` through: its own holding claim, or the only one."""
        claims = self._claims.get(claimed_id or "", {})
        holders = self._holders(claimed_id or "")
        if node in holders:
            return claims[node][1]
        if node != self._stand or len(set(holders.values())) != 1:
            return {}  # A Cell's fact joins only through its own claims (module docstring).
        return claims[next(iter(holders))][1]

    def _bee_at(self, task: str, fact: TrailFact) -> dict[str, str]:
        """The bee of `task`'s newest spawn at or before `fact`, among claims it may join."""
        spawns = [
            (at, bee)
            for at, bee, node in self._spawns.get(task, [])
            if node in self._holders(bee) and fact.node_id in (node, self._stand)
        ]
        # bisect over (time, id) pairs: every spawn at or before the fact sorts before (at, "~").
        position = bisect.bisect_right(spawns, (fact.at, "~"))
        return {"bee": spawns[position - 1][1]} if position else {}

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


def _since(claims: Mapping[str, Claim], before: datetime) -> dict[str, Claim]:
    """The claims made at or after `before`."""
    return {node: claim for node, claim in claims.items() if claim[0] >= before}


def _since_at(placed: Mapping[str, datetime], before: datetime) -> dict[str, datetime]:
    """The placements made at or after `before`."""
    return {cell: at for cell, at in placed.items() if at >= before}


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
