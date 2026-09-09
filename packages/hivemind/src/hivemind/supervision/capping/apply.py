"""Apply a CAPPED proposal's action: DIFF via unified diff, COMMAND via exec.

`apply_action` is the effectful edge behind `hivemind.supervision.capping.diff`'s pure diff
applier (codingrules section 8.3). For a DIFF action it reads each target path's prior bytes (None
on FileNotFoundError, a new file), builds the new bytes with `apply_unified_diff`, and writes them
back; when the resolved path lands outside the lease's scratch directory (an
OUTSIDE_SCRATCH_WRITE, or any tier whose normalised path happens to be outside scratch), it first
records the prior bytes on the lease (`LeaseView.note_restore_path`) and marks the path touched
(`note_touched_path`) *before* writing, so a crash between the two still leaves a restore record
`release()` can replay (codingrules section 8.7, roadmap step 3.17's note about the operator's
restore-path sentence). For a COMMAND action it runs the argv through the session and reports the
exit code; a non-zero exit is an apply failure the gate rolls back with nothing to undo (no file
was touched). ACTION_SEQUENCE never reaches this module: `checks.deterministic.SchemaCheck`
rejects it during CHECKING, before the gate ever applies anything.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the supervision package. Called
    by `hivemind.supervision.capping.gate.CappingGate` once a proposal reaches CAPPED. Calls into
    `hivemind.cell` (CellSession, ExecSpec, run), `hivemind.supervision.capping.diff`,
    `hivemind.supervision.capping.errors`, `hivemind.supervision.capping.lease_view` and
    `hivemind.supervision.capping.tiers` (RiskTier) only.

Key invariants:
    - Every TouchedPath in an ApplyResult carries the *resolved* path and the bytes it held before
      this apply, so the gate's REVERSE_DIFF rollback can restore or delete it without re-resolving.
    - `note_restore_path`/`note_touched_path` are called before `put_file`, never after: an apply
      that crashes mid-write must still leave a restore record behind.
    - A COMMAND action never touches ApplyResult.touched (nothing to undo for the command itself,
      codingrules section 8.12's roadmap note); its own exit code decides success.

See Also:
    - .claude/codingrules.md section 8.3 for "pure core, effectful edges."
    - .claude/codingrules.md section 8.7 for the restore-record rule this module's DIFF path
      follows for an outside-scratch write.
    - .claude/roadmap.md step 3.17 for the exact restore-path sentence this module implements.
    - hivemind.supervision.capping.diff for apply_unified_diff, the pure function this module's
      DIFF path is built on.
    - hivemind.supervision.capping.gate for CappingGate, this module's one caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hivemind.cell import CellSession, ExecSpec, run
from hivemind.supervision.capping.diff import apply_unified_diff
from hivemind.supervision.capping.errors import CappingError
from hivemind.supervision.capping.lease_view import LeaseView
from hivemind.supervision.capping.proposal import Proposal
from hivemind.supervision.capping.tiers import RiskTier
from waggle.messages.capping import ActionKind

DEFAULT_COMMAND_TIMEOUT_S = (
    120.0  # Generous for a proposed one-off command; a longer job is a task.
)

__all__ = ["ApplyResult", "TouchedPath", "apply_action"]


@dataclass(frozen=True, slots=True)
class TouchedPath:
    """One resolved path this apply wrote to, and the bytes it held before, for rollback."""

    path: Path  # Resolved (absolute, ".."-free) path.
    prior: bytes | None  # What it held before; None means it did not exist before this apply.


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """What applying a proposal's action actually did."""

    succeeded: bool  # False only for a COMMAND action that exited non-zero.
    touched: tuple[TouchedPath, ...]  # Empty for a COMMAND action; one entry per DIFF target path.
    exit_code: int | None = None  # Set only for a COMMAND action.


async def apply_action(
    session: CellSession, lease: LeaseView, proposal: Proposal, scratch_root: Path
) -> ApplyResult:
    """Apply `proposal.action` and report what happened.

    Args:
        session: The Cell session to read and write through.
        lease: Where an outside-scratch write records its restore path and touched path.
        proposal: The CAPPED proposal being applied; only `.action` and `.risk_tier` are read.
        scratch_root: The session's scratch directory, for resolving relative paths.

    Returns:
        What was written or run, and whether it succeeded.

    Raises:
        CappingError: `proposal.action.kind` is ACTION_SEQUENCE, which SchemaCheck should already
            have rejected before the gate ever calls this function -- reaching here means that
            check was bypassed, a bug in the caller, not a normal apply failure.
    """
    if proposal.action.kind is ActionKind.DIFF:
        return await _apply_diff(session, lease, proposal, scratch_root)
    if proposal.action.kind is ActionKind.COMMAND:
        return await _apply_command(session, proposal)
    raise CappingError(
        f"apply_action cannot apply an {proposal.action.kind.value} action (unsupported in v0); "
        "SchemaCheck should have rejected this proposal before it reached CAPPED."
    )


async def _apply_diff(
    session: CellSession, lease: LeaseView, proposal: Proposal, scratch_root: Path
) -> ApplyResult:
    """Apply a DIFF action: read prior bytes, compute new bytes, write, recording as needed."""
    touched: list[TouchedPath] = []
    diff_text = proposal.action.diff or ""
    for raw_path in proposal.action.paths:
        path = Path(raw_path)
        prior = await _read_prior(session, path)
        new_content = apply_unified_diff(prior, diff_text, path=raw_path)
        resolved = _resolve(scratch_root, path)
        if proposal.risk_tier is RiskTier.OUTSIDE_SCRATCH_WRITE or _is_outside_scratch(
            resolved, scratch_root
        ):
            # Recorded BEFORE the write: a crash between the two still leaves a restore record
            # release() can replay (codingrules section 8.7).
            lease.note_restore_path(resolved, prior)
            await lease.note_touched_path(resolved)
        await session.put_file(path, new_content)
        touched.append(TouchedPath(path=resolved, prior=prior))
    return ApplyResult(succeeded=True, touched=tuple(touched))


async def _apply_command(session: CellSession, proposal: Proposal) -> ApplyResult:
    """Apply a COMMAND action: run argv and report success by exit code."""
    action = proposal.action
    spec = ExecSpec(
        argv=action.command,
        cwd=Path(action.cwd) if action.cwd is not None else None,
        timeout_s=DEFAULT_COMMAND_TIMEOUT_S,
    )
    # Bounded by spec.timeout_s; a command that overruns it raises CommandTimeoutError to the
    # gate, which is not caught here (a timeout is a genuine failure, not an apply outcome to
    # roll back from -- there is nothing this function could do differently).
    completed = await run(session, spec)
    return ApplyResult(
        succeeded=completed.exit_code == 0, touched=(), exit_code=completed.exit_code
    )


async def _read_prior(session: CellSession, path: Path) -> bytes | None:
    """Read `path`'s current bytes through `session`, or None when it does not exist yet."""
    try:
        return await session.get_file(path)
    except FileNotFoundError:
        return None


def _resolve(scratch_root: Path, path: Path) -> Path:
    """Join `path` under `scratch_root` if relative, then resolve it (collapsing ".." segments)."""
    joined = path if path.is_absolute() else scratch_root / path
    return joined.resolve(strict=False)


def _is_within(path: Path, root: Path) -> bool:
    """Return whether `path` equals `root` or is somewhere underneath it."""
    return path == root or root in path.parents


def _is_outside_scratch(resolved: Path, scratch_root: Path) -> bool:
    """Return whether `resolved` lands outside `scratch_root`.

    A plain (non-async) helper, like `hivemind.cell.lease.RealCellLease._resolve_touched`: the
    `Path.resolve()` call here is fast and in-memory-mostly, but flake8-async (ASYNC240) still
    flags a blocking pathlib call written directly inside an async function's body, so it lives
    here instead of inline in `_apply_diff`.
    """
    return not _is_within(resolved, scratch_root.resolve(strict=False))
