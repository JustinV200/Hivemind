"""Write the trail events the Guard Bee's rules count, as their real producers write them.

`TrailSeeder` records the events the Guard Bee reads, with the payloads their real producers write:
the Enforcer's `guard.denied`, the scanner's `guard.injection_suspected`, the Capping gate's
`capping.*`, a lease's `cell.touched_outside_scratch`, a Warden's `alarm.raised`, the Fanner's
`llm.call` and the Entrance's `guard.entrance_*`. `seed_episode` records the joins the episode
index learns from: a worker spawned for a task, the task assigned to a Cell, a grant issued for
it. Every id is minted by the seeder's clock, so a run is deterministic.

A Virtual Cell's own records come from its own node (roadmap step 10.6's attribution):
`bind_cell` records what the Queen records as its Warden attaches (`warden.spawned`, naming the
Cell and the node its link proved), and `cell_episode` places a task on it and grants it on the
Queen's side, then spawns its bee on the Cell's own node, the way a real Virtual Cell's episode
reaches the Queen's trail.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used through `builders.guard_bee`.

Key invariants:
    - Every event recorded is valid for its family and carries no content.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from hivemind.cell import CellIdentity
from hivemind.pheromone import (
    AlarmEvent,
    CappingEvent,
    CellEvent,
    ForageEvent,
    GuardEvent,
    LlmEvent,
    LlmUsage,
    PheromoneEvent,
    PheromoneTrail,
    QueenEvent,
    WardenEvent,
    WorkerEvent,
)
from waggle.clock import Clock
from waggle.ids import (
    new_alarm_id,
    new_cell_id,
    new_device_id,
    new_event_id,
    new_grant_id,
    new_message_id,
    new_task_id,
    new_warden_id,
    new_worker_id,
)

__all__ = ["Episode", "TrailSeeder", "bind_cell", "cell_episode", "seed_episode", "spawn"]

_LEASE = "lease_01HZZZZZZZZZZZZZZZZZZZZZZZ"  # The lease every outside-scratch touch names.
_CELL_NODE = "node_01HZZZZZZZZZZZZZZZZZZZZZZZ"  # The node every Cell gate refusal's link proved.


class TrailSeeder:
    """Record the events the Guard Bee's rules count, stamped as one node by one clock."""

    def __init__(self, trail: PheromoneTrail, clock: Clock, identity: CellIdentity) -> None:
        """Seed `trail` as `identity`'s node, stamped by `clock`."""
        self.trail, self.clock, self.identity = trail, clock, identity

    async def record(
        self, family: type[PheromoneEvent], kind: str, subject: str, payload: Mapping[str, object]
    ) -> PheromoneEvent:
        """Record one event of `family` about `subject` and return it."""
        event = family.model_validate(
            {
                "id": new_event_id(self.clock),
                "hive_id": self.identity.hive_id,
                "node_id": self.identity.node_id,
                "at": self.clock.now(),
                "actor": self.identity.actor,
                "kind": kind,
                "subject_id": subject,
                "payload": dict(payload),
            }
        )
        await self.trail.record(event)
        return event

    async def injection(self, bee: str, task: str) -> PheromoneEvent:
        """The scanner's flag on outside text `bee` read for `task` (roadmap 10.6b)."""
        payload = {"source": "session_output", "consumer": bee, "action": "drop", "task_id": task}
        return await self.record(GuardEvent, "guard.injection_suspected", bee, payload)

    async def denied(
        self, bee: str, capability: str = "tool:http_request", kind: str = "worker"
    ) -> PheromoneEvent:
        """The Enforcer's refusal of `bee` needing `capability`."""
        payload = {"principal_kind": kind, "principal_id": bee, "capability": capability}
        payload |= {"point": "tool_invocation", "rule": "guard.not_held"}
        return await self.record(GuardEvent, "guard.denied", bee, payload)

    async def proposed(self, task: str, cell: str, tier: str) -> str:
        """A proposal for `task` on `cell` at `tier`; returns the proposal id."""
        proposal = new_message_id(self.clock)
        payload = {"task_id": task, "cell_id": cell, "tier": tier}
        await self.record(CappingEvent, "capping.proposed", proposal, payload)
        return proposal

    async def capping(self, proposal: str, kind: str, **payload: object) -> PheromoneEvent:
        """One capping.* step for `proposal` (rejected, applied, rolled_back, audited)."""
        return await self.record(CappingEvent, kind, proposal, dict(payload))

    async def touched(self, cell: str) -> PheromoneEvent:
        """A lease on `cell` touching a path outside its scratch directory."""
        payload = {"lease_id": _LEASE, "path": "/etc/hosts"}
        return await self.record(CellEvent, "cell.touched_outside_scratch", cell, payload)

    async def alarm(self, task: str, kind: str = "GRANT_EXCEEDED") -> PheromoneEvent:
        """An Alarm of `kind` raised for `task`."""
        payload = {"kind": kind, "severity": "WARNING", "attempts": 0, "task_id": task}
        return await self.record(AlarmEvent, "alarm.raised", new_alarm_id(self.clock), payload)

    async def llm_call(self, grant: str, cost_usd: float) -> PheromoneEvent:
        """One model call under `grant` costing `cost_usd`."""
        usage = LlmUsage(input_tokens=100, output_tokens=50, cached_tokens=0, cost_usd=cost_usd)
        event = LlmEvent(
            id=new_event_id(self.clock),
            hive_id=self.identity.hive_id,
            node_id=self.identity.node_id,
            at=self.clock.now(),
            actor=self.identity.actor,
            kind="llm.call",
            subject_id=new_event_id(self.clock),
            payload={"grant_id": grant, "latency_s": 0.5},
            slot="WORKER",
            provider="fake",
            usage=usage,
        )
        await self.trail.record(event)
        return event

    async def refused(self, kind: str, cell: str, reason: str) -> PheromoneEvent:
        """The Cell gate's refusal of a frame or segment on `cell`'s own link (roadmap 10.6)."""
        payload = {"cell_id": cell, "node_id": _CELL_NODE, "reason": reason}
        return await self.record(GuardEvent, kind, cell, payload)

    async def entrance(self, kind: str, subject: str | None = None, **payload: object) -> str:
        """One guard.entrance_* event about `subject` (a fresh device by default); its subject."""
        device = subject if subject is not None else new_device_id(self.clock)
        await self.record(GuardEvent, kind, device, dict(payload))
        return device


@dataclass(frozen=True, slots=True)
class Episode:
    """One bee's attempt at one task, on one Cell, under one grant."""

    bee: str
    task: str
    cell: str
    grant: str


async def seed_episode(seed: TrailSeeder, task: str | None = None) -> Episode:
    """Record a fresh bee spawned for a task (a new one by default), assigned and granted."""
    clock = seed.clock
    episode = Episode(
        bee=new_worker_id(clock),
        task=task if task is not None else new_task_id(clock),
        cell=new_cell_id(clock),
        grant=new_grant_id(clock),
    )
    spawned = {"task_id": episode.task, "role": "DRONE"}
    await seed.record(WorkerEvent, "worker.spawned", episode.bee, spawned)
    await seed.record(QueenEvent, "queen.assigned", episode.task, {"cell_id": episode.cell})
    await seed.record(ForageEvent, "forage.granted", episode.grant, {"task_id": episode.task})
    return episode


async def bind_cell(stand: TrailSeeder, node: str, cell: str | None = None) -> str:
    """Record, as the Queen, a Warden attaching for a Cell whose link proved `node`; the Cell."""
    cell_id = cell if cell is not None else new_cell_id(stand.clock)
    payload = {"cell_id": cell_id, "node_id": node}
    await stand.record(WardenEvent, "warden.spawned", new_warden_id(stand.clock), payload)
    return cell_id


async def spawn(seed: TrailSeeder, task: str) -> str:
    """Record, as `seed`'s node, a Warden spawning a bee for `task`; the bee's id."""
    bee = new_worker_id(seed.clock)
    await seed.record(WorkerEvent, "worker.spawned", bee, {"task_id": task, "role": "DRONE"})
    return bee


async def cell_episode(stand: TrailSeeder, cell_seed: TrailSeeder, cell: str) -> Episode:
    """A fresh task the Queen places on `cell` and grants, its bee spawned on the Cell's node."""
    task, grant = new_task_id(stand.clock), new_grant_id(stand.clock)
    await stand.record(QueenEvent, "queen.assigned", task, {"cell_id": cell})
    await stand.record(ForageEvent, "forage.granted", grant, {"task_id": task})
    bee = await spawn(cell_seed, task)
    return Episode(bee=bee, task=task, cell=cell, grant=grant)
