"""Define what the Entrance reads directly: the Hive's stores, and the Queen's live tables.

Reads never change state, so the Hive Entrance reads the stores directly instead of asking the
Queen (ADR-0040): the Brood Chamber, the Pheromone Trail, memory (episode records), the Queen's
goal-request table and chat log, and her Forage ledger. Two things the Hive keeps have no store:
which Wardens are attached, with their pulse (the Queen is the only global view, codingrules 8.8,
and keeps them in memory), and each Warden's newest Heartbeat (it never reaches the trail).
``HiveCensus`` is the Queen's own read-only view of the first (``Queen.wardens`` and
``Queen.liveness`` satisfy it as they are, with no method that acts); the telemetry board the
composition root wires to her ``on_heartbeat`` hook holds the second. ``LlmReads`` is what the
LLM view shows: the providers' names and kinds from the manifest, the slot bindings, and the
Queen's Clustering state and health poller (read, never probed). ``VirtualCellCensus`` is the
Virtual Cell lifecycle's live table, present only when the Hive has a Virtual side.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.gate``. Built by the
    composition root (``hive serve``, the test rig); read by the read routes, the read side
    (``hivemind.entrance.reads``) and the live views. Calls into the Hive's store and record
    types only.

Key invariants:
    - Everything here is only ever read; no method on these protocols changes anything.
    - No field holds an API key or a secret: providers are names and kinds.

See Also:
    - docs/adr/0040-hive-entrance-http-websocket-api-and-human-inbox.md, "The Entrance lives in the
      Queen's process and every write goes through her".
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from hivemind.brood_chamber import BroodChamber
from hivemind.forage import SlotBinding
from hivemind.hive import LiveVirtualCell
from hivemind.memory import MemoryStore
from hivemind.pheromone import PheromoneTrail
from hivemind.queen import (
    ChatLog,
    ClusterState,
    ForageLedger,
    GoalRequestStore,
    HealthPoller,
    WardenLink,
    WardenLiveness,
)
from waggle.ids import WardenId

if TYPE_CHECKING:
    # Type-only: the streams package imports the gate, so a runtime import here cycles.
    from hivemind.entrance.streams.telemetry import TelemetryBoard

__all__ = ["HiveCensus", "HiveReads", "LlmReads", "VirtualCellCensus"]


class HiveCensus(Protocol):
    """The Queen's live view of her attached Wardens; ``hivemind.queen.Queen`` satisfies it."""

    @property
    def wardens(self) -> tuple[WardenLink, ...]:
        """Every Warden attached now, in attachment order, each with its Cell."""
        ...

    @property
    def liveness(self) -> Mapping[WardenId, WardenLiveness]:
        """Each attached Warden's pulse: last Heartbeat, misses, offline."""
        ...


class VirtualCellCensus(Protocol):
    """The Virtual Cell lifecycle's live table; ``hivemind.hive.CellLifecycle`` satisfies it."""

    def live_cells(self) -> tuple[LiveVirtualCell, ...]:
        """Every Virtual Cell the lifecycle tracks, dormant and provisioning ones included."""
        ...


@dataclass(frozen=True, slots=True)
class LlmReads:
    """What the LLM view shows: providers, bindings, and the Queen's judgement of their health.

    Attributes:
        providers: Every ``[llm.providers.<name>]`` key mapped to its kind, in manifest order.
        bindings: Every ``[llm.slots]`` row, forage-side (provider, model, fallback, effort).
        cluster: The Queen's mode and the providers Clustering has paused.
        health: The Queen's health poller: consecutive DOWN readings per provider.
    """

    providers: Mapping[str, str]
    bindings: tuple[SlotBinding, ...]
    cluster: ClusterState
    health: HealthPoller


@dataclass(frozen=True, slots=True)
class HiveReads:
    """The Hive's stores and live tables the Entrance reads directly (never through the Queen).

    Attributes:
        goal_requests: The Queen's goal-request table.
        chat: The Queen's chat log.
        chamber: The Brood Chamber (tasks, and questions waiting on the human).
        trail: The Pheromone Trail.
        memory: Memory: episode records (thoughts).
        ledger: The Queen's Forage ledger.
        census: The Queen's attached Wardens and their pulse.
        telemetry: Each Warden's newest Heartbeat, fed by the Queen's ``on_heartbeat`` hook.
        llm: The providers, the slot bindings and the Queen's judgement of their health.
        virtual_cells: The Virtual Cell lifecycle's table; None for a Hive with no Virtual side.
    """

    goal_requests: GoalRequestStore
    chat: ChatLog
    chamber: BroodChamber
    trail: PheromoneTrail
    memory: MemoryStore
    ledger: ForageLedger
    census: HiveCensus
    telemetry: TelemetryBoard
    llm: LlmReads
    virtual_cells: VirtualCellCensus | None = field(default=None)
