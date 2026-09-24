"""Define GateDeps and GateOutcome: a CappingGate's own collaborators and its terminal result.

Split out of `hivemind.supervision.capping.gate.core` (which carries `CappingGate` and the
step-by-step free functions) so neither file crosses the codingrules section 5.1 file-size limit;
`gate` became a package, not a bare module, so this split costs nothing at the `capping/` package's
own directory fan-out (codingrules 5.6: a sub-package counts once, never against its parent, and
`capping/` already sits at its own ten-module cap).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.supervision.capping.
    gate`. Read and built by `hivemind.supervision.capping.gate.core`; re-exported by `hivemind.
    supervision.capping.gate`'s own `__init__.py`. Calls into `hivemind.cell`, `hivemind.
    supervision.capping.checks`, `.leave`, `.postconditions`, `.state`, `.tiers` and waggle only.

Key invariants:
    - `GateDeps` and `GateOutcome` carry no behaviour of their own; every state transition and
      every side effect lives in `gate.core`.
    - `GateOutcome.state` is always one of `_TERMINAL_OUTCOMES` (its own validator enforces it).

See Also:
    - .claude/codingrules.md section 5.6 for the fan-out rule this split satisfies.
    - hivemind.supervision.capping.gate.core for CappingGate, this module's one reader.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hivemind.cell import Cell, CellIdentity, CellSession, Snapshotter
from hivemind.pheromone import PheromoneTrail
from hivemind.supervision.capping.checks import Check, CheckResultRecord, JudgeVerdict
from hivemind.supervision.capping.gui import GuiSurface
from hivemind.supervision.capping.leave import (
    DEFAULT_HUMAN_TIMEOUT_S,
    LeaveDecisionRecord,
    LeavePolicyTable,
    load_leave_policy,
)
from hivemind.supervision.capping.postconditions import PostconditionOutcome
from hivemind.supervision.capping.state import ProposalState
from hivemind.supervision.capping.tiers import TierTable
from waggle.clock import Clock
from waggle.ids import MessageId
from waggle.messages import PlannedLeaving
from waggle.messages.capping import CheckKind

__all__ = ["GateDeps", "GateOutcome"]

# Terminal outcomes CappingGate.run may return; PROPOSED/CHECKING/CAPPED/APPLIED are only ever
# in-flight states, never what run() hands back.
_TERMINAL_OUTCOMES = frozenset(
    {ProposalState.VERIFIED, ProposalState.REJECTED, ProposalState.ROLLED_BACK}
)


@dataclass(frozen=True, slots=True)
class GateDeps:
    """Everything one CappingGate needs, wired by its composition root (a Warden, step 3.19)."""

    session: CellSession  # The Cell session proposals apply and verify against.
    snapshotter: Snapshotter  # NoopSnapshotter on a Real Cell; a real one on a Virtual Cell.
    cell: Cell  # The Cell this gate's proposals run on, passed to the snapshotter.
    tiers: TierTable  # supervision/defaults/capping-tiers.toml, loaded once at start-up.
    trail: PheromoneTrail  # Where every capping.* event lands.
    identity: CellIdentity  # hive_id/node_id/actor stamped on every event this gate records.
    clock: Clock  # Source of every minted event id and timestamp.
    checks: Mapping[CheckKind, Check]  # deterministic_checks() in v0; a later phase adds rungs.
    # Roadmap step 5.0c (leave policy): additive fields, every one defaulted so a GateDeps built
    # before this dispatch (every existing test) keeps constructing unchanged. declared_leaves
    # defaults to (): an empty `declared` set always reads as "undeclared" (roadmap 5.0b's hard
    # rule), so an outside-scratch write never persists until a real caller sets this from its own
    # TaskAssign.leaves.
    leave_policy: LeavePolicyTable = field(default_factory=load_leave_policy)
    declared_leaves: tuple[PlannedLeaving, ...] = ()
    keep_root: Path | None = None  # The manifest's [hive_stand] keep_root; roadmap step 5.0e.
    leave_home: Path = field(default_factory=Path.home)  # The Hive Stand's own home, v0.
    # Roadmap step 5.0d: how long HumanCheck.ask waits for an Answer before treating an ASK
    # verdict as discard; additive, defaulted like every field above it.
    human_timeout_s: float = DEFAULT_HUMAN_TIMEOUT_S
    # Roadmap step 5.0e: the manifest's own [hive_stand] disk_reserve_mb, a `keep` COPY's
    # destination is refused against (apply._check_disk_reserve); None (every GateDeps built
    # before this dispatch) skips the check entirely.
    disk_reserve_mb: int | None = None
    # Roadmap step 6.5 (ADR-0032): the attached Exoskeleton's surface, through which a GUI
    # proposal is applied, verified and rolled back; None (a Cell with no Exoskeleton, and every
    # GateDeps built before phase 6) rejects a GUI proposal before any check runs.
    gui: GuiSurface | None = None
    # The objective of the task this gate's proposals serve (its TaskAssign.objective), handed to
    # the real-time judge through CheckContext.goal; None where no task stands behind the gate.
    goal: str | None = None


class GateOutcome(BaseModel):
    """CappingGate.run's result: the proposal's terminal state, every check and postcondition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal_id: MessageId = Field(description="The proposal this outcome belongs to.")
    state: ProposalState = Field(
        description="The terminal state reached: VERIFIED, REJECTED or ROLLED_BACK."
    )
    checks: tuple[CheckResultRecord, ...] = Field(description="Every check that actually ran.")
    postconditions: tuple[PostconditionOutcome, ...] = Field(
        description="Every postcondition checked after applying; empty for REJECTED."
    )
    reason: str = Field(description="Why, in one line; human-readable, never written to the trail.")
    # Roadmap step 5.0e: lets a tool (workers.tools.keep, and every other outside-scratch tool)
    # tell the model plainly whether its write will actually remain, and why -- describe() reads
    # this the same way it already reads checks/postconditions, never the underlying diff or
    # command text (codingrules section 12: the trail never carries text; this mirrors that rule
    # for tool-result text).
    leave_decisions: tuple[LeaveDecisionRecord, ...] = Field(
        default=(),
        description="One entry per outside-scratch path this apply decided a leave verdict for; "
        "empty for REJECTED and for any apply with nothing outside scratch.",
    )
    review: JudgeVerdict | None = Field(
        default=None,
        description="The judge's verdict on an applied irreversible GUI action, reviewed before "
        "the bee's next step (roadmap step 6.6, ADR-0032); None when no such review ran.",
    )

    @model_validator(mode="after")
    def _state_is_terminal(self) -> GateOutcome:
        """Require `state` to be one of the three terminal outcomes run() may reach."""
        if self.state not in _TERMINAL_OUTCOMES:
            raise ValueError(
                "GateOutcome.state must be VERIFIED, REJECTED or ROLLED_BACK, got "
                f"{self.state.name}."
            )
        return self
