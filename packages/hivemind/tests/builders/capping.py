"""Build valid hivemind.supervision.capping test data without repeating pydantic boilerplate.

Every builder here returns a real, validated model (codingrules 14.5: "Builders return real,
validated models; they never bypass validation to save time"), with sensible defaults for every
field a test does not care about. `FakeLeaseView` is a hand-written, honest implementation of the
`LeaseView` Protocol (codingrules 14.4: "fakes live beside the Protocol they implement" for
shipped code; this one is test-only infrastructure, so it lives here instead, matching how
`hivemind.cell.fake.FakeSession` is shipped but a purely-test double for a narrower seam is not).

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by every test under
    packages/hivemind/tests/unit/supervision/capping.

Key invariants:
    - Every builder that mints an id takes an optional `clock: Clock` (default a fresh FakeClock)
      so a test run is deterministic.
    - make_proposal's default action and postcondition already satisfy each other's cross-field
      validators (a DIFF's paths match a FILE_EXISTS postcondition's subject) with no further
      overrides needed.

See Also:
    - .claude/codingrules.md section 14.5 for the builders-over-fixtures rule this module follows.
    - hivemind.supervision.capping.proposal for Proposal.
    - hivemind.supervision.capping.tiers for TierSpec, TierTable.
    - hivemind.supervision.capping.lease_view for the LeaseView Protocol FakeLeaseView implements.
    - waggle.messages.capping for ProposedAction, ActionKind.
    - waggle.messages.labels for Postcondition, PostconditionKind.
"""

from __future__ import annotations

from pathlib import Path

from hivemind.cell import HoneyClearance
from hivemind.forage.tempo import AccuracyBar, Tempo
from hivemind.supervision.capping.proposal import Proposal
from hivemind.supervision.capping.state import ProposalState
from hivemind.supervision.capping.tiers import RiskTier, TierSpec, TierTable
from waggle.clock import Clock, FakeClock
from waggle.ids import new_cell_id, new_message_id, new_task_id, new_worker_id
from waggle.messages.capping import ActionKind, CheckKind, ProposedAction
from waggle.messages.labels import Postcondition, PostconditionKind

# Kinds whose Postcondition needs a command to run, and kinds that compare against `expected`;
# mirrors waggle.messages.labels's own _COMMAND_KINDS/_COMPARISON_KINDS partition.
_COMMAND_POSTCONDITION_KINDS = frozenset(
    {PostconditionKind.COMMAND_EXITS_ZERO, PostconditionKind.TEST_PASSES}
)
_COMPARISON_POSTCONDITION_KINDS = frozenset(
    {PostconditionKind.HTTP_STATUS, PostconditionKind.ELEMENT_TEXT, PostconditionKind.JUDGE_RUBRIC}
)

__all__ = ["FakeLeaseView", "make_action", "make_postcondition", "make_proposal", "make_tier_table"]


def make_action(kind: ActionKind = ActionKind.DIFF, **overrides: object) -> ProposedAction:
    """Build a valid ProposedAction: a one-line new-file diff to `note.txt`, by default.

    Args:
        kind: DIFF, COMMAND or ACTION_SEQUENCE; DIFF by default.
        **overrides: Field values that replace the defaults below, including `kind` itself.

    Returns:
        A validated ProposedAction.
    """
    fields: dict[str, object] = {
        "kind": kind,
        "summary": "Write a note to scratch.",
        "diff": None,
        "diff_sha256": None,
        "command": (),
        "cwd": None,
        "paths": (),
        "steps": (),
    }
    if kind is ActionKind.DIFF:
        fields["diff"] = "@@ -0,0 +1,1 @@\n+hello\n"
        fields["paths"] = ("note.txt",)
    elif kind is ActionKind.COMMAND:
        fields["command"] = ("true",)
    else:
        fields["steps"] = ("Click the confirm button.",)
    fields.update(overrides)
    return ProposedAction(**fields)


def make_postcondition(
    kind: PostconditionKind = PostconditionKind.FILE_EXISTS, **overrides: object
) -> Postcondition:
    """Build a valid Postcondition asserting `note.txt` exists, by default.

    Args:
        kind: The assertion kind; FILE_EXISTS by default.
        **overrides: Field values that replace the defaults below, including `kind` itself.

    Returns:
        A validated Postcondition.
    """
    fields: dict[str, object] = {"kind": kind, "subject": "note.txt", "argv": (), "expected": None}
    if kind in _COMMAND_POSTCONDITION_KINDS:
        fields["argv"] = ("true",)
    elif kind in _COMPARISON_POSTCONDITION_KINDS:
        fields["expected"] = "200"
    fields.update(overrides)
    return Postcondition(**fields)


def make_proposal(
    risk_tier: RiskTier = RiskTier.SCRATCH_WRITE, clock: Clock | None = None, **overrides: object
) -> Proposal:
    """Build a valid Proposal: a SCRATCH_WRITE diff to `note.txt` with one FILE_EXISTS check.

    Args:
        risk_tier: The declared RiskTier; SCRATCH_WRITE by default.
        clock: Source of every minted id; a fresh FakeClock when omitted.
        **overrides: Field values that replace the defaults below, including `risk_tier` itself.

    Returns:
        A validated Proposal, in ProposalState.PROPOSED.
    """
    active_clock = clock if clock is not None else FakeClock()
    fields: dict[str, object] = {
        "id": new_message_id(active_clock),
        "task_id": new_task_id(active_clock),
        "cell_id": new_cell_id(active_clock),
        "proposer": new_worker_id(active_clock),
        "risk_tier": risk_tier,
        "action": make_action(),
        "postconditions": (make_postcondition(),),
        "tempo": Tempo(latency_budget_s=None, accuracy=AccuracyBar.NORMAL),
        "spend_estimate_usd": 0.0,
        "clearance": HoneyClearance.C1,
        "reason": "Leave a note for the next bee.",
        "state": ProposalState.PROPOSED,
    }
    fields.update(overrides)
    return Proposal(**fields)


def make_tier_table(**overrides: object) -> TierTable:
    """Build a valid TierTable: SCRATCH_WRITE requires SCHEMA and SIZE_CAP, floor SCHEMA.

    Args:
        **overrides: Field values that replace the defaults below (typically `tiers` itself, to
            add or replace a RiskTier's TierSpec).

    Returns:
        A validated TierTable.
    """
    fields: dict[str, object] = {
        "tiers": {
            RiskTier.SCRATCH_WRITE: TierSpec(
                checks=(CheckKind.SCHEMA, CheckKind.SIZE_CAP),
                floor=(CheckKind.SCHEMA,),
                snapshot_before=False,
                max_diff_bytes=1_048_576,
            ),
        }
    }
    fields.update(overrides)
    return TierTable(**fields)


class FakeLeaseView:
    """An in-memory LeaseView: records every touched and restore-recorded path for a test to read.

    Implements `hivemind.supervision.capping.lease_view.LeaseView` structurally, the same way
    `hivemind.cell.RealCellLease` will once its own `note_restore_path` lands.
    """

    def __init__(self, scratch_root: Path, allowed_paths: tuple[Path, ...] = ()) -> None:
        """Build a FakeLeaseView scoped to `scratch_root`.

        Args:
            scratch_root: This lease's scratch directory.
            allowed_paths: Extra paths outside scratch this lease may also touch.
        """
        self._scratch_root = scratch_root
        self._allowed_paths = allowed_paths
        self.touched_paths: list[Path] = []
        self.restore_records: list[tuple[Path, bytes | None]] = []

    @property
    def scratch_root(self) -> Path:
        """This lease's scratch directory."""
        return self._scratch_root

    @property
    def allowed_paths(self) -> tuple[Path, ...]:
        """Extra paths outside scratch this lease may also touch."""
        return self._allowed_paths

    def is_path_allowed(self, path: Path) -> bool:
        """Return whether `path` is inside scratch_root or under one of allowed_paths."""
        resolved = path.resolve(strict=False)
        roots = (
            self._scratch_root.resolve(strict=False),
            *(allowed.resolve(strict=False) for allowed in self._allowed_paths),
        )
        return any(resolved == root or root in resolved.parents for root in roots)

    async def note_touched_path(self, path: Path) -> None:
        """Record `path` on `touched_paths`."""
        self.touched_paths.append(path)

    def note_restore_path(self, path: Path, prior: bytes | None) -> None:
        """Record `(path, prior)` on `restore_records`."""
        self.restore_records.append((path, prior))
