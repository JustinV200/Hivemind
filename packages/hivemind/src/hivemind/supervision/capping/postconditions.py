"""Check whether one Postcondition holds, after a proposal's action has been applied.

Codingrules section 8.12: "The proposer never verifies its own work." `check_postcondition` is run
by the gate, never by the bee that proposed the action, against `waggle.messages.labels.
Postcondition` (carried directly -- a value model with no behaviour, codingrules section 6.1).
v0 supports the machine-checkable kinds a `CellSession` alone can answer: `FILE_EXISTS`/
`FILE_ABSENT` via `get_file`, `COMMAND_EXITS_ZERO`/`TEST_PASSES` via `cell.run`. The remaining
kinds (`HTTP_STATUS`, `ELEMENT_TEXT`, `JUDGE_RUBRIC`) need a browser, an HTTP client or a judge
model this gate does not have in v0; they are honestly reported as unsupported rather than silently
treated as passing, matching the CheckKind ladder's own fail-closed shape.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the supervision package. Called
    by `hivemind.supervision.capping.gate.CappingGate` once per postcondition after applying a
    proposal, and by `hivemind.wardens.acceptance` (roadmap step 3.18) for a task's acceptance
    criteria, which share this same Postcondition shape. Calls into `hivemind.cell` (CellSession,
    ExecSpec, run) and waggle only.

Key invariants:
    - check_postcondition never raises for an unsupported kind or a failed assertion; both are
      `has_held=False` results a caller can act on, not exceptions.
    - Only FileNotFoundError from `CellSession.get_file` is caught for FILE_EXISTS/FILE_ABSENT;
      any other exception (a closed session, a path outside reach) propagates, because those are
      programming errors in the caller, not a postcondition that simply did not hold.

See Also:
    - .claude/codingrules.md section 8.12 for "The proposer never verifies its own work."
    - waggle.messages.labels for Postcondition and PostconditionKind, the shapes this module reads.
    - hivemind.cell for CellSession, ExecSpec and run, which the COMMAND_EXITS_ZERO/TEST_PASSES
      path uses.
    - hivemind.supervision.capping.gate for CappingGate, this function's main caller.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import CellSession, ExecSpec, run
from waggle.messages.labels import Postcondition, PostconditionKind

# The kinds this v0 gate can actually check with only a CellSession; every other kind is honestly
# reported as unsupported (roadmap 3.17's postconditions.py note: "the other kinds return
# has_held=False, observed='unsupported in v0'").
_COMMAND_KINDS = frozenset({PostconditionKind.COMMAND_EXITS_ZERO, PostconditionKind.TEST_PASSES})
_FILE_KINDS = frozenset({PostconditionKind.FILE_EXISTS, PostconditionKind.FILE_ABSENT})

__all__ = ["PostconditionOutcome", "check_postcondition"]


class PostconditionOutcome(BaseModel):
    """Whether one postcondition (or acceptance criterion) held, and what was observed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    index: int = Field(ge=0, description="Position in the proposal's postconditions list.")
    kind: PostconditionKind = Field(description="What was asserted.")
    has_held: bool = Field(description="Whether the assertion held.")
    observed: str = Field(default="", description="What was actually found.")


async def check_postcondition(
    session: CellSession, index: int, pc: Postcondition
) -> PostconditionOutcome:
    """Check whether `pc` holds, using only `session`.

    Args:
        session: The Cell session to check against; the same one the proposal's action ran on.
        index: `pc`'s position in the proposal's postconditions list, carried through to the
            result so a caller can report which one failed.
        pc: The assertion to check.

    Returns:
        Whether it held and what was observed; FILE_EXISTS/FILE_ABSENT/COMMAND_EXITS_ZERO/
        TEST_PASSES are checked for real, every other kind reports `has_held=False` with
        `observed="unsupported in v0"`.
    """
    if pc.kind in _FILE_KINDS:
        return await _check_file(session, index, pc)
    if pc.kind in _COMMAND_KINDS:
        return await _check_command(session, index, pc)
    return PostconditionOutcome(
        index=index, kind=pc.kind, has_held=False, observed="unsupported in v0"
    )


async def _check_file(session: CellSession, index: int, pc: Postcondition) -> PostconditionOutcome:
    """Check FILE_EXISTS/FILE_ABSENT by attempting a read; a missing file raises, not returns."""
    try:
        await session.get_file(Path(pc.subject))
        exists = True
    except FileNotFoundError:
        exists = False
    held = exists if pc.kind is PostconditionKind.FILE_EXISTS else not exists
    observed = "file exists" if exists else "file not found"
    return PostconditionOutcome(index=index, kind=pc.kind, has_held=held, observed=observed)


async def _check_command(
    session: CellSession, index: int, pc: Postcondition
) -> PostconditionOutcome:
    """Check COMMAND_EXITS_ZERO/TEST_PASSES by running pc.argv and reading its exit code."""
    # A postcondition command is expected to be quick (a test suite, a lint pass); the session's
    # own DEFAULT_EXEC_TIMEOUT_S applies, and a hung command surfaces as CommandTimeoutError to
    # whichever caller awaits this rather than being swallowed here.
    completed = await run(session, ExecSpec(argv=pc.argv))
    held = completed.exit_code == 0
    return PostconditionOutcome(
        index=index, kind=pc.kind, has_held=held, observed=f"exit code {completed.exit_code}"
    )
