"""Define CappingGate: propose, check, cap, apply, verify -- nothing lands uncapped.

Codingrules section 8.12: "Every action with a side effect outside a lease's scratch directory...
is a Proposal... The CappingGate runs the checks the tier requires, applies the proposal, verifies
the postconditions, and rolls back on failure." `CappingGate.propose` stores a `Proposal` in an
in-memory table (the SQLite capping table is a later phase; the Pheromone Trail is the durable
record for now) and records `capping.proposed`. `CappingGate.run` walks
`PROPOSED -> CHECKING -> CAPPED -> APPLIED -> {VERIFIED | ROLLED_BACK}` (or `CHECKING ->
REJECTED`) through `hivemind.supervision.capping.state`, running every check the proposal's tier
requires (fail closed on one the gate's `Mapping[CheckKind, Check]` does not implement), applying
through `hivemind.supervision.capping.apply`, and verifying every postcondition through
`hivemind.supervision.capping.postconditions`. Every transition writes a `capping.*` trail event
with `subject_id` set to the proposal id and a payload of ids, tier and outcome enums or counts
only -- never the human-readable reason, which stays in the returned `GateOutcome` (codingrules
section 12: the trail never carries text). The step-by-step work (`_run_checks`,
`_apply_and_verify`, `_roll_back`, `_restore`, `_reject`, `_record_event`) is written as
module-level functions taking `GateDeps` explicitly rather than `CappingGate` methods, so the
class itself (codingrules section 5.1: class bodies stay under 200 lines) stays a thin shell
around the one thing it actually owns: the in-memory proposal table and its transitions.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the supervision package. Built
    by a Warden (roadmap step 3.19) from a `GateDeps`; called by Worker tools
    (`hivemind.workers.tools`, roadmap step 3.16) through `propose`, and by the Warden through
    `run` once it holds the proposing bee's `CapabilitySet` and its own lease. Calls into
    `hivemind.cell` (Cell, CellSession, Snapshotter, SnapshotUnsupportedError),
    `hivemind.pheromone` (CappingEvent, PheromoneTrail), `hivemind.supervision.capping.apply`,
    `.checks`, `.errors`, `.lease_view`, `.postconditions`, `.proposal`, `.state`, `.tiers` and
    waggle only.

Key invariants:
    - Every proposal state change goes through `hivemind.supervision.capping.state.
      assert_transition`; nothing in this module compares ProposalState values directly.
    - A `capping.*` event's payload never carries the human-readable `reason` a GateOutcome
      returns; only ids, enum values (`.value`) and counts (codingrules section 12).
    - `run` never raises for an ordinary proposal failure (a check failing, a postcondition not
      holding): those are `GateOutcome`s with `state` REJECTED or ROLLED_BACK, not exceptions.
    - Every module-level helper below is private (leading underscore, not in `__all__`); a caller
      outside this module only ever sees `CappingGate`, `GateDeps` and `GateOutcome`.

See Also:
    - .claude/codingrules.md section 8.12 for the Capping gate's whole shape.
    - .claude/codingrules.md Appendix C, "Proposal" row, for the state machine `run` walks.
    - docs/adr/0018-capping-gate-postconditions-and-risk-tiers.md for why checks run
      cheapest-first, why an unavailable check fails closed, and REVERSE_DIFF vs snapshot rollback.
    - hivemind.supervision.capping.apply for apply_action, called once a proposal is CAPPED.
    - hivemind.supervision.capping.postconditions for check_postcondition, called after applying.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from hivemind.cell import (
    Cell,
    CellIdentity,
    CellSession,
    SnapshotId,
    Snapshotter,
    SnapshotUnsupportedError,
)
from hivemind.guard import CapabilitySet
from hivemind.pheromone import CappingEvent, PheromoneTrail
from hivemind.supervision.capping.apply import ApplyResult, apply_action
from hivemind.supervision.capping.checks import Check, CheckContext, CheckResultRecord
from hivemind.supervision.capping.errors import UnknownProposalError
from hivemind.supervision.capping.lease_view import LeaseView
from hivemind.supervision.capping.postconditions import PostconditionOutcome, check_postcondition
from hivemind.supervision.capping.proposal import Proposal
from hivemind.supervision.capping.state import ProposalState, assert_transition, is_terminal
from hivemind.supervision.capping.tiers import TierTable
from waggle.clock import Clock
from waggle.ids import MessageId, new_event_id
from waggle.messages.capping import CheckKind, CheckOutcome, RollbackMethod

__all__ = ["CappingGate", "GateDeps", "GateOutcome"]

# Terminal outcomes CappingGate.run may return; PROPOSED/CHECKING/CAPPED/APPLIED are only ever
# in-flight states, never what run() hands back.
_TERMINAL_OUTCOMES = frozenset(
    {ProposalState.VERIFIED, ProposalState.REJECTED, ProposalState.ROLLED_BACK}
)
# A bound CappingGate._transition, threaded through the free functions below so they can advance
# a proposal's state without themselves being methods (and so without counting toward the class's
# own line budget, codingrules section 5.1).
_Transition = Callable[[Proposal, ProposalState], Proposal]


@dataclass(frozen=True, slots=True)
class GateDeps:
    """Everything one CappingGate needs, wired by its composition root (a Warden, step 3.19)."""

    session: CellSession  # The Cell session proposals apply and verify against.
    snapshotter: Snapshotter  # NoopSnapshotter on a Real Cell; a real one on a Virtual Cell.
    cell: Cell  # The Cell this gate's proposals run on, passed to the snapshotter.
    tiers: TierTable  # docs/supervision/capping-tiers.toml, loaded once at start-up.
    trail: PheromoneTrail  # Where every capping.* event lands.
    identity: CellIdentity  # hive_id/node_id/actor stamped on every event this gate records.
    clock: Clock  # Source of every minted event id and timestamp.
    checks: Mapping[CheckKind, Check]  # deterministic_checks() in v0; a later phase adds rungs.


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

    @model_validator(mode="after")
    def _state_is_terminal(self) -> GateOutcome:
        """Require `state` to be one of the three terminal outcomes run() may reach."""
        if self.state not in _TERMINAL_OUTCOMES:
            raise ValueError(
                "GateOutcome.state must be VERIFIED, REJECTED or ROLLED_BACK, got "
                f"{self.state.name}."
            )
        return self


class CappingGate:
    """Propose, check, cap, apply, verify: the gate nothing lands uncapped through.

    Owns the in-memory proposal table (codingrules section 8.5: a class that owns its own mutable
    state, documented): `_proposals` grows on `propose` and its entries are replaced (never
    mutated) on every state transition. Instantiate one per Cell, from a `GateDeps` a Warden wires.
    Everything past "which state is this proposal in" is delegated to this module's free
    functions, which take `self._deps` and `self._transition` explicitly.
    """

    def __init__(self, deps: GateDeps) -> None:
        """Build a CappingGate with no proposals yet.

        Args:
            deps: This gate's collaborators: the session, snapshotter, Cell, tier table, trail,
                identity, clock and check registry.
        """
        self._deps = deps
        self._proposals: dict[MessageId, Proposal] = {}

    async def propose(self, proposal: Proposal) -> MessageId:
        """Store `proposal` and record capping.proposed.

        Args:
            proposal: A freshly built proposal, in ProposalState.PROPOSED.

        Returns:
            `proposal.id`, for a caller that wants to `run` it next.
        """
        self._proposals[proposal.id] = proposal
        await _record_event(
            self._deps,
            proposal.id,
            "capping.proposed",
            {
                "task_id": proposal.task_id,
                "cell_id": proposal.cell_id,
                "tier": proposal.risk_tier.value,
            },
        )
        return proposal.id

    def get(self, proposal_id: MessageId) -> Proposal:
        """Return the proposal stored under `proposal_id`.

        Args:
            proposal_id: The id `propose` returned.

        Returns:
            The proposal, in whatever state it last transitioned to.

        Raises:
            UnknownProposalError: No proposal with this id has been `propose`d.
        """
        try:
            return self._proposals[proposal_id]
        except KeyError as exc:
            raise UnknownProposalError(proposal_id) from exc

    def pending(self) -> tuple[Proposal, ...]:
        """Return every stored proposal that has not yet reached a terminal state.

        Returns:
            Proposals for `hive capping queue`, in no particular order.
        """
        return tuple(p for p in self._proposals.values() if not is_terminal(p.state))

    async def run(
        self, proposal_id: MessageId, capabilities: CapabilitySet, lease: LeaseView
    ) -> GateOutcome:
        """Walk `proposal_id` through checking, capping, applying and verifying.

        Args:
            proposal_id: A proposal already stored by `propose`, still in PROPOSED.
            capabilities: The proposing bee's Warden's CapabilitySet, checked by the allowlist
                checks.
            lease: The lease this Cell's session runs under, for path reachability and restore
                bookkeeping.

        Returns:
            The terminal outcome: VERIFIED, REJECTED or ROLLED_BACK.

        Raises:
            UnknownProposalError: `proposal_id` was never `propose`d.
        """
        ops = _Ops(self._deps, self._transition)
        proposal = self._transition(self.get(proposal_id), ProposalState.CHECKING)
        capped = await _check_and_cap(ops, proposal, capabilities, lease)
        if isinstance(capped, GateOutcome):
            return capped  # REJECTED: no configured tier, a failed check, or one unavailable.
        proposal, results, snapshot_id = capped
        return await _apply_and_verify(ops, proposal, lease, results, snapshot_id)

    def _transition(self, proposal: Proposal, to_state: ProposalState) -> Proposal:
        """Advance `proposal` to `to_state`, storing the new value and returning it."""
        assert_transition(proposal.state, to_state, proposal_id=proposal.id)
        updated = proposal.model_copy(update={"state": to_state})
        self._proposals[proposal.id] = updated
        return updated


# ──────────────────────────────────────────────────────────────────────────────
# Free functions: the step-by-step work, kept out of CappingGate itself (codingrules 5.1)
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Ops:
    """Bundles `GateDeps` and the bound `CappingGate._transition`, as one parameter.

    Every free function below takes this instead of `(deps, transition)` separately, which is
    what keeps each of them within the five-parameter limit (codingrules section 5.1) once its
    own domain arguments are added.
    """

    deps: GateDeps
    transition: _Transition


@dataclass(frozen=True, slots=True)
class _ApplyOutcome:
    """What `_apply_and_verify` hands to `_roll_back`: the checks, ApplyResult and snapshot id.

    Bundled (codingrules section 5.1: "Introduce a frozen dataclass for the argument group") so
    `_roll_back` stays within the five-parameter limit.
    """

    checks: tuple[CheckResultRecord, ...]
    apply_result: ApplyResult
    snapshot_id: SnapshotId | None


async def _record_event(
    deps: GateDeps, subject_id: MessageId, kind: str, payload: Mapping[str, JsonValue]
) -> None:
    """Build and record a CappingEvent for `subject_id`, stamped with `deps.identity`."""
    event = CappingEvent(
        id=new_event_id(deps.clock),
        hive_id=deps.identity.hive_id,
        node_id=deps.identity.node_id,
        at=deps.clock.now(),
        actor=deps.identity.actor,
        kind=kind,
        subject_id=subject_id,
        payload=dict(payload),
    )
    await deps.trail.record(event)


async def _run_checks(
    deps: GateDeps, context: CheckContext
) -> tuple[tuple[CheckResultRecord, ...], CheckKind | None, str | None]:
    """Run every check the tier requires, recording capping.checked for each that runs.

    Returns:
        The results run so far, the failing check's kind (None if all passed or none ran), and a
        failure reason (None if all passed).
    """
    required = tuple(
        kind for kind in CheckKind if kind in (set(context.tier.checks) | set(context.tier.floor))
    )
    results: list[CheckResultRecord] = []
    for kind in required:
        check = deps.checks.get(kind)
        if check is None:
            # Fail closed (codingrules 8.12): a tier naming a check this gate cannot run is a
            # rejection, never a silently skipped rung.
            return tuple(results), kind, "check unavailable"
        result = await check.run(context)
        results.append(result)
        await _record_event(
            deps,
            context.proposal.id,
            "capping.checked",
            {"check": kind.value, "outcome": result.outcome.value},
        )
        if result.outcome is not CheckOutcome.PASSED:
            return tuple(results), kind, result.reason
    return tuple(results), None, None


async def _check_and_cap(
    ops: _Ops, proposal: Proposal, capabilities: CapabilitySet, lease: LeaseView
) -> GateOutcome | tuple[Proposal, tuple[CheckResultRecord, ...], SnapshotId | None]:
    """Look up the tier, run its checks, and cap the proposal -- or reject it.

    Split out of `CappingGate.run` (codingrules section 5.1: functions stay under 50 lines).

    Returns:
        A REJECTED GateOutcome when the tier is unconfigured or a check fails or is unavailable;
        otherwise the CAPPED proposal, its check results, and a snapshot id (None unless the
        tier's `snapshot_before` is set).
    """
    tier = ops.deps.tiers.tiers.get(proposal.risk_tier)
    if tier is None:
        return await _reject(
            ops, proposal, failing_check=None, reason="tier not configured", checks=()
        )
    context = CheckContext(
        proposal=proposal,
        capabilities=capabilities,
        lease=lease,
        scratch_root=ops.deps.session.scratch_dir,
        tier=tier,
    )
    results, failing_check, failure_reason = await _run_checks(ops.deps, context)
    if failure_reason is not None:
        return await _reject(
            ops, proposal, failing_check=failing_check, reason=failure_reason, checks=results
        )
    proposal = ops.transition(proposal, ProposalState.CAPPED)
    await _record_event(
        ops.deps,
        proposal.id,
        "capping.capped",
        {"tier": proposal.risk_tier.value, "checks_passed": len(results)},
    )
    snapshot_id = (
        await ops.deps.snapshotter.snapshot(ops.deps.cell) if tier.snapshot_before else None
    )
    return proposal, results, snapshot_id


async def _apply_and_verify(
    ops: _Ops,
    proposal: Proposal,
    lease: LeaseView,
    checks: tuple[CheckResultRecord, ...],
    snapshot_id: SnapshotId | None,
) -> GateOutcome:
    """Apply the CAPPED proposal, then verify its postconditions or roll back."""
    deps = ops.deps
    apply_result = await apply_action(deps.session, lease, proposal, deps.session.scratch_dir)
    proposal = ops.transition(proposal, ProposalState.APPLIED)
    await _record_event(
        deps, proposal.id, "capping.applied", {"action_kind": proposal.action.kind.value}
    )
    outcome = _ApplyOutcome(checks=checks, apply_result=apply_result, snapshot_id=snapshot_id)
    if not apply_result.succeeded:
        # A COMMAND's own non-zero exit is itself the failure; nothing was written that needs
        # undoing (apply_action never touches files for a COMMAND action).
        reason = f"command exited {apply_result.exit_code}"
        return await _roll_back(ops, proposal, outcome, (), reason=reason)
    outcomes = tuple(
        [
            await check_postcondition(deps.session, i, pc)
            for i, pc in enumerate(proposal.postconditions)
        ]
    )
    if all(pc.has_held for pc in outcomes):
        proposal = ops.transition(proposal, ProposalState.VERIFIED)
        await _record_event(
            deps, proposal.id, "capping.verified", {"postconditions_held": len(outcomes)}
        )
        return GateOutcome(
            proposal_id=proposal.id,
            state=ProposalState.VERIFIED,
            checks=checks,
            postconditions=outcomes,
            reason="every postcondition held",
        )
    return await _roll_back(ops, proposal, outcome, outcomes, reason="a postcondition failed")


async def _roll_back(
    ops: _Ops,
    proposal: Proposal,
    outcome: _ApplyOutcome,
    postconditions: tuple[PostconditionOutcome, ...],
    *,
    reason: str,
) -> GateOutcome:
    """Restore what apply changed, transition to ROLLED_BACK, and record the outcome."""
    method = await _restore(ops.deps, outcome.apply_result, outcome.snapshot_id)
    proposal = ops.transition(proposal, ProposalState.ROLLED_BACK)
    payload: dict[str, JsonValue] = {"method": method.value}
    if outcome.apply_result.exit_code is not None:
        payload["exit_code"] = outcome.apply_result.exit_code
    await _record_event(ops.deps, proposal.id, "capping.rolled_back", payload)
    return GateOutcome(
        proposal_id=proposal.id,
        state=ProposalState.ROLLED_BACK,
        checks=outcome.checks,
        postconditions=postconditions,
        reason=reason,
    )


async def _restore(
    deps: GateDeps, apply_result: ApplyResult, snapshot_id: SnapshotId | None
) -> RollbackMethod:
    """Roll the Cell back by snapshot when one was taken, else REVERSE_DIFF, else NONE."""
    if snapshot_id is not None:
        try:
            await deps.snapshotter.rollback(deps.cell, snapshot_id)
            return RollbackMethod.SNAPSHOT
        except SnapshotUnsupportedError:
            pass  # NoopSnapshotter (Real Cells): fall through to REVERSE_DIFF below.
    if not apply_result.touched:
        return RollbackMethod.NONE  # A COMMAND action touches no files; nothing to reverse.
    for touched in apply_result.touched:
        if touched.prior is None:
            await deps.session.delete_file(touched.path)
        else:
            await deps.session.put_file(touched.path, touched.prior)
    return RollbackMethod.REVERSE_DIFF


async def _reject(
    ops: _Ops,
    proposal: Proposal,
    *,
    failing_check: CheckKind | None,
    reason: str,
    checks: tuple[CheckResultRecord, ...],
) -> GateOutcome:
    """Transition to REJECTED and record the outcome."""
    proposal = ops.transition(proposal, ProposalState.REJECTED)
    payload: dict[str, JsonValue] = {"tier": proposal.risk_tier.value, "checks_run": len(checks)}
    if failing_check is not None:
        payload["failing_check"] = failing_check.value
    await _record_event(ops.deps, proposal.id, "capping.rejected", payload)
    return GateOutcome(
        proposal_id=proposal.id,
        state=ProposalState.REJECTED,
        checks=checks,
        postconditions=(),
        reason=reason,
    )
