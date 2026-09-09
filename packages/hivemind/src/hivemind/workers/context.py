"""Define GrantSlice, QuestionChannel and WorkerContext: everything one Worker's role may use.

`WorkerContext` is the one bundle `hivemind.workers.base.Worker.run` receives: its Cell, an open
session on it, the model it is bound to, its own slice of the Warden's `ForageGrant`
(`GrantSlice`), its `CapabilitySet` (`hivemind.workers.capabilities.worker_capabilities` computes
it), where to write memory and the trail, a way to ask a blocking `Question`
(`QuestionChannel`) and its mutable telemetry. It never carries a provider, a subprocess handle,
or the Cell's `kind` (codingrules section 8.7: "branch on capabilities, never on kind") -- a role
reads `ctx.cell.capabilities`, never `ctx.cell.kind`. `GrantSlice` is deliberately nothing
model-shaped: no provider, no model id, because `ctx.bound` (a `hivemind.llm.BoundModel`) already
names the model this Worker calls, and `GrantSlice` only ever answers "how much" (spend, tokens,
which named bindings it may still fall back to), the one Worker's share of the `ForageGrant`
`hivemind.wardens` (roadmap step 3.19) carves it from.

Fits into the Hive:
    Layer 4 (roles that do the work). `WorkerContext` is constructed by `hivemind.wardens.spawn`
    (roadmap step 3.19), which fills `asker` with its own transport-backed implementation before
    `hivemind.workers.runtime.WorkerRuntime` replaces it with the runtime's own mailbox
    (`hivemind.workers.runtime.mailbox.Mailbox`, which satisfies `QuestionChannel` structurally);
    read by `hivemind.workers.base.Worker.run` implementations (the Drone, roadmap step 3.16).
    Calls into `hivemind.cell`, `hivemind.guard`, `hivemind.llm`, `hivemind.memory`,
    `hivemind.pheromone`, `hivemind.workers.telemetry` and waggle only.

Key invariants:
    - GrantSlice is frozen and forbids extras like every boundary value in this repository; its
      three budget/allowance fields are never negative.
    - WorkerContext is a frozen, slotted dataclass (codingrules section 8.5: "internal values are
      @dataclass(frozen=True, slots=True)"): it is not itself read from or written to JSON/TOML,
      so it is a dataclass, not a pydantic BaseModel, matching `hivemind.llm.slots.BoundModel`.
    - Nothing in this module imports `hivemind.supervision.capping` (roadmap step 3.17): `gate`
      (the Capping gate) and `lease` (a LeaseView) are added to WorkerContext once that lands
      (roadmap step 3.16), not here.

See Also:
    - .claude/codingrules.md section 8.7 for "branch on capabilities, never on kind".
    - .claude/codingrules.md section 15 for "Capabilities and Forage only attenuate down the tree",
      the rule `GrantSlice` and `hivemind.workers.capabilities.worker_capabilities` both uphold.
    - hivemind.workers.telemetry for TelemetryTracker, the mutable object `WorkerContext.telemetry`
      names.
    - hivemind.workers.runtime for WorkerRuntime, which builds a WorkerContext's real `asker`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import Cell, CellSession
from hivemind.guard import CapabilitySet
from hivemind.llm import BoundModel
from hivemind.memory import MemoryIdentity, MemoryStore
from hivemind.pheromone import PheromoneTrail
from hivemind.workers.telemetry import TelemetryTracker
from waggle.clock import Clock
from waggle.ids import GrantId, WorkerId
from waggle.messages.supervision import Answer, Question

__all__ = ["GrantSlice", "QuestionChannel", "WorkerContext"]


class GrantSlice(BaseModel):
    """One Worker's share of a Warden's ForageGrant: budgets and allowed bindings, nothing more.

    Carries no provider, no model id and no `hivemind.forage.ModelSlot` (the module docstring
    explains why): `ctx.bound` already says which model this Worker calls, so this model only
    answers "how much is left" and "what could this Worker still fall back to by name".
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    grant_id: GrantId = Field(description="The ForageGrant this slice was carved from.")
    spend_budget_usd: float = Field(
        ge=0, description="The most this Worker may spend across its whole attempt, in USD."
    )
    token_budget: int = Field(
        ge=0, description="The most tokens this Worker may consume across its whole attempt."
    )
    allowed_bindings: tuple[str, ...] = Field(
        default=(),
        description="[llm.slots] manifest keys this Worker may still be rebound to; the "
        "Warden's own allowed bindings, attenuated to this one Worker.",
    )


class QuestionChannel(Protocol):
    """Ask a blocking Question and wait for its matching Answer.

    Satisfied structurally by `hivemind.workers.runtime.mailbox.Mailbox`, which sends the Question
    to the Worker's Warden over its own transport and resolves the returned Answer once one with
    the same `question_id` arrives back on the same mailbox.
    """

    async def ask(self, question: Question) -> Answer:
        """Send `question` and block the caller until the matching Answer arrives.

        Args:
            question: The question to ask; its `question_id` is what the eventual Answer is
                matched against, not any envelope id (it survives re-wrapping at each hop).

        Returns:
            The Answer whose `question_id` equals `question.question_id`.
        """
        ...


@dataclass(frozen=True, slots=True)
class WorkerContext:
    """Everything a Worker's role may use; never a provider, a subprocess handle or a Cell's kind.

    Attributes:
        worker_id: This Worker's own id; the `subject_id` of every `worker.*` trail event its
            runtime writes.
        cell: The Cell this Worker runs on. A role reads `cell.capabilities`, never `cell.kind`
            (codingrules section 8.7).
        session: An already-open terminal session on `cell`.
        bound: The model this Worker is bound to right now (`hivemind.llm.BoundModel`); a rebind
            (`hivemind.supervision.intervention.Rebind`) replaces it with a fresh WorkerContext,
            never mutates this one in place.
        grant: This Worker's own slice of its Warden's ForageGrant.
        capabilities: What this Worker may do (a tool, a path, a network scope, a command, a
            device, a spend ceiling); computed by `hivemind.workers.capabilities.
            worker_capabilities` and never wider than the Warden's own set.
        memory: Where this Worker reads and writes Pins, Notes, Handoffs and episodes.
        trail: The Pheromone Trail this Worker's runtime records every `worker.*` event to.
        clock: Injected time source for every id minted and every timestamp written.
        asker: Sends a blocking Question and waits for its Answer.
        identity: The Hive, node and actor this Worker stamps on every trail event and memory
            write it makes.
        telemetry: This Worker's own mutable `TelemetryTracker`, written by the role between
            turns and read by the runtime for every Heartbeat.
        handoff_threshold: The manifest's `[memory] handoff_threshold` fraction; a role compares
            it against `telemetry.should_hand_off` to decide when to checkpoint on its own.

    A later roadmap step (3.16, once Capping (3.17) lands) adds two further fields here: `gate`
    (the Capping gate a tool's side effect proposes through) and `lease` (a read-only view of the
    Warden's Real Cell lease). Neither exists yet; this module does not import
    `hivemind.supervision.capping` (codingrules section 4: workers may import supervision, but
    nothing here needs the Capping gate until a role's tools do).
    """

    worker_id: WorkerId
    cell: Cell
    session: CellSession
    bound: BoundModel
    grant: GrantSlice
    capabilities: CapabilitySet
    memory: MemoryStore
    trail: PheromoneTrail
    clock: Clock
    asker: QuestionChannel
    identity: MemoryIdentity
    telemetry: TelemetryTracker
    handoff_threshold: float
