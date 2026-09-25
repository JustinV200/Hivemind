"""Apply a CAPPED proposal's action: DIFF via unified diff, COMMAND via exec, COPY via digest.

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
was touched). An ACTION_SEQUENCE reaches this module only as a network step on the NETWORK_EGRESS
tier (roadmap step 10.3: the HTTP tool's one shape, which `checks.deterministic.SchemaCheck` passes
there alone): applying it is the authorisation itself, a success that touches nothing, after which
the tool makes its one request; any other ACTION_SEQUENCE is rejected by SchemaCheck during
CHECKING, before the gate ever applies anything.

Roadmap step 5.0e: a COPY action (the `keep` tool) is applied by `_apply_copy`, the same leave-
decision shape as `_apply_diff`'s own outside-scratch branch, plus two things a diff never needs:
the source's bytes are read from scratch and checked against the proposal's own `copy_sha256`/
`copy_size` before anything is written (a mismatch is an apply failure, not an exception -- the
source may simply have changed since the tool proposed it), and once the destination is written
the source is deleted from scratch (`keep` moves a file, it does not duplicate it). Both the
source's original bytes and the destination's prior content are recorded on `ApplyResult.touched`,
so a rolled-back COPY restores the source and removes the half-written destination exactly like
any other rollback. `disk_reserve_mb`, when given, refuses a COPY that would drop the destination
filesystem's free space below the manifest's own `[hive_stand] disk_reserve_mb` (the same reserve
a new lease is refused under, `hivemind.cell.local.HiveStandSource._prepare_scratch_root`) with a
plain `ApplyResult.failure_reason` the gate turns into a rolled-back proposal's own reason -- never
an unhandled exception.

Roadmap step 5.0e (continued): a COMMAND action cannot carry a restore record the way a DIFF or
COPY's own target path can -- a shell command is a black box until it exits. `_apply_command`
closes that gap the only way it can: it scans the task's declared `leaves` patterns
(`hivemind.supervision.capping.leave.scan.scan_declared_leaves`) before running the command and
again after, and ledgers (through the same `_ledger_write` helper `_apply_diff`/`_apply_copy` use)
every file that appeared or changed; a file the scan finds unchanged is left alone, and anything a
command touches outside every declared root is not scanned at all -- it stays only whatever
`cell.touched_outside_scratch` events a `CellSession` write already produces, unchanged. Unlike a
DIFF or COPY, this ledgering happens strictly *after* the command has already run (there is no way
to know what it will write beforehand), so a crash mid-command, before the after-scan completes,
leaves no restore record at all for whatever it half-wrote -- the roadmap step's own "cannot carry
a restore record" limitation, not a gap this dispatch introduces.

Roadmap step 5.0c: an outside-scratch write's own `persist`/`approved_by` is no longer always
`False` -- `hivemind.supervision.capping.leave.decide_persist` (given an optional
`LeaveApplyContext` the gate builds from its own `GateDeps`) decides it, and `hivemind.
supervision.capping.gate` reads each `ApplyResult.leave_decisions` entry back to record
`capping.leave_decided`. `leave=None` (every call site outside `hivemind.supervision.capping.gate`'s
own wiring, including every test that predates this dispatch) reproduces exactly the old,
unconditional `persist=False` behaviour. Roadmap step 5.0d: an ASK verdict is resolved right here,
by `hivemind.supervision.capping.checks.human.HumanCheck.resolve`, before `note_restore_path` is
ever called -- so the task genuinely blocks on the human's answer mid-apply, the same way any other
external await in this codebase does.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the supervision package. Called
    by `hivemind.supervision.capping.gate.CappingGate` once a proposal reaches CAPPED. Calls into
    `hivemind.cell` (CellSession, ExecSpec, run), `hivemind.supervision.capping.checks.human`
    (HumanCheck, roadmap step 5.0d), `hivemind.supervision.capping.diff`, `hivemind.supervision.
    capping.errors`, `hivemind.supervision.capping.leave` (roadmap step 5.0c), `hivemind.
    supervision.capping.lease_view` and `hivemind.supervision.capping.tiers` (RiskTier) only.

Key invariants:
    - Every TouchedPath in an ApplyResult carries the *resolved* path and the bytes it held before
      this apply, so the gate's REVERSE_DIFF rollback can restore or delete it without re-resolving.
    - `note_restore_path`/`note_touched_path` are called before `put_file`, never after: an apply
      that crashes mid-write must still leave a restore record behind.
    - A COMMAND action never touches ApplyResult.touched (nothing to undo for the command itself,
      codingrules section 8.12's roadmap note); its own exit code decides success.
    - `leave_decisions` carries one entry per outside-scratch path only when `leave` is not None;
      it is always empty for a COMMAND action and for a DIFF action with nothing outside scratch.
    - `_apply_copy` never trusts `ProposedAction.copy_sha256`/`copy_size`: it re-hashes and
      re-measures the source it actually reads from scratch and fails (not raises) on a mismatch,
      the same fail-closed posture `checks.deterministic.DiffSizeCapCheck` already takes for a
      by-digest diff it cannot verify.

See Also:
    - .claude/codingrules.md section 8.3 for "pure core, effectful edges."
    - .claude/codingrules.md section 8.7 for the restore-record rule this module's DIFF path
      follows for an outside-scratch write.
    - .claude/roadmap.md step 3.17 for the exact restore-path sentence this module implements.
    - .claude/roadmap.md step 5.0c for "The gate consults it when applying an outside_scratch_
      write," this module's own new behaviour.
    - hivemind.supervision.capping.diff for apply_unified_diff, the pure function this module's
      DIFF path is built on.
    - hivemind.supervision.capping.leave for LeaveApplyContext and decide_persist.
    - hivemind.supervision.capping.gate for CappingGate, this module's one caller.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from hivemind.cell import CellSession, ExecSpec, run
from hivemind.supervision.capping.checks.human import HumanCheck
from hivemind.supervision.capping.diff import apply_unified_diff
from hivemind.supervision.capping.errors import CappingError
from hivemind.supervision.capping.gui import GuiSurface
from hivemind.supervision.capping.lease_view import LeaseView
from hivemind.supervision.capping.leave import (
    LeaveApplyContext,
    LeaveDecisionRecord,
    ScannedFile,
    decide_persist,
    scan_declared_leaves,
)
from hivemind.supervision.capping.proposal import Proposal
from hivemind.supervision.capping.tiers import RiskTier
from waggle.messages.capping import ActionKind

DEFAULT_COMMAND_TIMEOUT_S = (
    120.0  # Generous for a proposed one-off command; a longer job is a task.
)
# A generous bound; a filesystem root always terminates the walk (mirrors hivemind.cell.local.
# probe._MAX_DISK_PROBE_ANCESTORS, the same walk-until-something-exists shape).
_MAX_DISK_PROBE_ANCESTORS = 32

__all__ = ["ApplyExtras", "ApplyResult", "TouchedPath", "apply_action"]


@dataclass(frozen=True, slots=True)
class TouchedPath:
    """One resolved path this apply wrote to, and the bytes it held before, for rollback."""

    path: Path  # Resolved (absolute, ".."-free) path.
    prior: bytes | None  # What it held before; None means it did not exist before this apply.


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """What applying a proposal's action actually did."""

    succeeded: bool  # False for a COMMAND that exited non-zero, or a COPY that failed to verify.
    touched: tuple[TouchedPath, ...]  # Empty for a COMMAND action; one entry per DIFF target path.
    exit_code: int | None = None  # Set only for a COMMAND action.
    # Roadmap step 5.0c: one entry per outside-scratch path decide_persist actually ran for;
    # empty whenever `leave` was None (module docstring's own backward-compatible default).
    leave_decisions: tuple[LeaveDecisionRecord, ...] = ()
    # Roadmap step 5.0e: set whenever `succeeded` is False for a reason other than a COMMAND's own
    # exit code (a COPY's hash/size mismatch, or a disk-reserve refusal); the gate's own rollback
    # reason prefers this over the exit-code text when it is set (hivemind.supervision.capping.
    # gate._apply_and_verify), so a refusal reaches the tool's caller in plain language, never an
    # exception.
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ApplyExtras:
    """apply_action's optional, roadmap-phase-5 inputs, bundled to stay within codingrules 5.1.

    `session`, `lease`, `proposal` and `scratch_root` are apply_action's own core four
    parameters; `leave` and `disk_reserve_mb` are additive (roadmap steps 5.0c and 5.0e), so
    bundling them is what keeps apply_action itself at five parameters instead of six.
    """

    leave: LeaveApplyContext | None = None
    disk_reserve_mb: int | None = None
    gui: GuiSurface | None = None  # Roadmap step 6.5: applies an ActionKind.GUI proposal.


async def apply_action(
    session: CellSession,
    lease: LeaseView,
    proposal: Proposal,
    scratch_root: Path,
    extras: ApplyExtras | None = None,
) -> ApplyResult:
    """Apply `proposal.action` and report what happened.

    Args:
        session: The Cell session to read and write through.
        lease: Where an outside-scratch write records its restore path and touched path.
        proposal: The CAPPED proposal being applied; only `.action` and `.risk_tier` are read.
        scratch_root: The session's scratch directory, for resolving relative paths.
        extras: `leave` (the leave-policy inputs an outside-scratch write is decided against,
            roadmap step 5.0c) and `disk_reserve_mb` (a COPY's destination is refused against it,
            roadmap step 5.0e); None (the default) is `ApplyExtras()`, reproducing the pre-phase-5
            behaviour of never persisting and never reserve-checking.

    Returns:
        What was written or run, and whether it succeeded.

    Raises:
        CappingError: `proposal.action.kind` is ACTION_SEQUENCE on any tier but NETWORK_EGRESS,
            which SchemaCheck should already have rejected, or GUI with no `extras.gui`, which the
            gate refuses before any check -- reaching here means one of those was bypassed, a bug
            in the caller, not a normal apply failure.
    """
    extras = extras if extras is not None else ApplyExtras()
    if proposal.action.kind is ActionKind.DIFF:
        return await _apply_diff(session, lease, proposal, scratch_root, extras.leave)
    if proposal.action.kind is ActionKind.COMMAND:
        return await _apply_command(session, lease, proposal, extras)
    if proposal.action.kind is ActionKind.COPY:
        return await _apply_copy(session, lease, proposal, scratch_root, extras)
    if proposal.action.kind is ActionKind.GUI and extras.gui is not None:
        # Typed steps through the attached Exoskeleton (ADR-0032); nothing on disk to reverse.
        applied = await extras.gui.apply(proposal)
        return ApplyResult(
            succeeded=applied.succeeded, touched=(), failure_reason=applied.failure_reason
        )
    if proposal.risk_tier is RiskTier.NETWORK_EGRESS:
        # Roadmap step 10.3: a network step's apply is its authorisation; the tool sends it.
        return ApplyResult(succeeded=True, touched=())
    raise CappingError(
        f"apply_action cannot apply an {proposal.action.kind.value} action (unsupported in v0); "
        "SchemaCheck should have rejected this proposal before it reached CAPPED."
    )


@dataclass(frozen=True, slots=True)
class _LedgerContext:
    """What every outside-scratch write's own leave-decision call shares (codingrules 5.1).

    `_apply_diff`, `_apply_copy` and `_apply_command`'s own scan loop each decide persistence for
    one or more resolved paths against the same `lease`/`leave`/`proposal`, varying only the path,
    its prior bytes and its new content -- bundling the three shared values keeps `_ledger_write`
    itself at four parameters instead of six.
    """

    lease: LeaseView
    leave: LeaveApplyContext | None
    proposal: Proposal


async def _ledger_write(
    ctx: _LedgerContext, resolved: Path, prior: bytes | None, content: bytes
) -> LeaveDecisionRecord | None:
    """Decide persistence for one outside-scratch path and record it on the lease.

    Shared by `_apply_diff`, `_apply_copy` and `_apply_command`'s own before/after scan: all three
    reduce to "one resolved path, its prior bytes, its new content" once their own kind-specific
    work (a diff, a verified copy, a command's own scan) has produced those three values.
    """
    # Roadmap step 5.0c: the leave policy decides persist/approved_by; leave=None keeps the
    # pre-phase-5 behaviour of never persisting (decide_persist's own module docstring).
    decision = decide_persist(ctx.leave, resolved, content)
    if ctx.leave is not None:
        # Roadmap step 5.0d: an ASK verdict is resolved by asking a human; resolve() is a no-op
        # for anything else (HumanCheck's own "Key invariants").
        decision = await HumanCheck(ctx.leave.clock, ctx.leave.human_timeout_s).resolve(
            ctx.leave, decision, ctx.proposal
        )
    # Recorded BEFORE a DIFF/COPY's own write (codingrules section 8.7); a COMMAND's own scan
    # calls this only after the command has already run, since there is no way to know what it
    # will write beforehand (module docstring's own roadmap-5.0e paragraph).
    ctx.lease.note_restore_path(
        resolved,
        prior,
        persist=decision.persist,
        approved_by=decision.approved_by,
        reason=decision.reason,
    )
    await ctx.lease.note_touched_path(resolved)
    return decision.record


async def _apply_diff(
    session: CellSession,
    lease: LeaseView,
    proposal: Proposal,
    scratch_root: Path,
    leave: LeaveApplyContext | None,
) -> ApplyResult:
    """Apply a DIFF action: read prior bytes, compute new bytes, write, recording as needed."""
    touched: list[TouchedPath] = []
    decisions: list[LeaveDecisionRecord] = []
    ctx = _LedgerContext(lease=lease, leave=leave, proposal=proposal)
    diff_text = proposal.action.diff or ""
    for raw_path in proposal.action.paths:
        path = Path(raw_path)
        prior = await _read_prior(session, path)
        new_content = apply_unified_diff(prior, diff_text, path=raw_path)
        resolved = _resolve(scratch_root, path)
        if proposal.risk_tier is RiskTier.OUTSIDE_SCRATCH_WRITE or _is_outside_scratch(
            resolved, scratch_root
        ):
            record = await _ledger_write(ctx, resolved, prior, new_content)
            if record is not None:
                decisions.append(record)
        await session.put_file(path, new_content)
        touched.append(TouchedPath(path=resolved, prior=prior))
    return ApplyResult(succeeded=True, touched=tuple(touched), leave_decisions=tuple(decisions))


async def _apply_command(
    session: CellSession, lease: LeaseView, proposal: Proposal, extras: ApplyExtras
) -> ApplyResult:
    """Apply a COMMAND action: scan declared leaves before and after, then run argv.

    Roadmap step 5.0e: `run_command` effects cannot carry a restore record the way a DIFF or COPY
    can, so the task's own declared `leaves` patterns are scanned before the command runs and
    ledgered against a second scan once it exits -- whatever appeared or changed is decided and
    recorded exactly like any other outside-scratch write (`_ledger_write`); anything unchanged,
    or outside every declared root, is left alone.
    """
    leave = extras.leave
    before = scan_declared_leaves(leave.declared, Path(leave.home)) if leave is not None else {}
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
    decisions = await _ledger_command_scan(lease, leave, proposal, before)
    return ApplyResult(
        succeeded=completed.exit_code == 0,
        touched=(),
        exit_code=completed.exit_code,
        leave_decisions=decisions,
    )


async def _ledger_command_scan(
    lease: LeaseView,
    leave: LeaveApplyContext | None,
    proposal: Proposal,
    before: dict[Path, ScannedFile],
) -> tuple[LeaveDecisionRecord, ...]:
    """Re-scan the declared roots `before` came from and ledger every appeared or changed file."""
    if leave is None:
        return ()  # No LeaveApplyContext wired: nothing to scan against (module docstring).
    after = scan_declared_leaves(leave.declared, Path(leave.home))
    ctx = _LedgerContext(lease=lease, leave=leave, proposal=proposal)
    decisions: list[LeaveDecisionRecord] = []
    for resolved, scanned in after.items():
        prior_entry = before.get(resolved)
        if prior_entry is not None and prior_entry.content == scanned.content:
            continue  # Unchanged: roadmap 5.0e ledgers only what "appeared or changed".
        prior_content = prior_entry.content if prior_entry is not None else None
        record = await _ledger_write(ctx, resolved, prior_content, scanned.content)
        if record is not None:
            decisions.append(record)
    return tuple(decisions)


async def _apply_copy(
    session: CellSession,
    lease: LeaseView,
    proposal: Proposal,
    scratch_root: Path,
    extras: ApplyExtras,
) -> ApplyResult:
    """Apply a COPY action: verify the source, decide persistence, write, then delete the source.

    `ProposedAction`'s own validator (waggle.messages.capping.action) already guarantees `paths`
    holds exactly `(source, destination)` and both `copy_sha256`/`copy_size` are set.
    """
    source, destination = (Path(p) for p in proposal.action.paths)
    verified = await _verify_copy_source(session, proposal, source)
    if isinstance(verified, str):
        return ApplyResult(succeeded=False, touched=(), failure_reason=verified)
    dest_resolved = _resolve(scratch_root, destination)
    reserve_problem = _check_disk_reserve(dest_resolved, len(verified), extras.disk_reserve_mb)
    if reserve_problem is not None:
        return ApplyResult(succeeded=False, touched=(), failure_reason=reserve_problem)
    dest_prior = await _read_prior(session, destination)
    ctx = _LedgerContext(lease=lease, leave=extras.leave, proposal=proposal)
    record = await _ledger_write(ctx, dest_resolved, dest_prior, verified)
    await session.put_file(destination, verified)
    # keep() moves a file rather than duplicating it: the source is removed from scratch once its
    # bytes are safely at the destination. Its own prior content (what it held before this apply,
    # which is `verified` itself) rides on ApplyResult.touched, so a rollback restores it.
    source_resolved = _resolve(scratch_root, source)
    await session.delete_file(source)
    touched = (
        TouchedPath(path=dest_resolved, prior=dest_prior),
        TouchedPath(path=source_resolved, prior=verified),
    )
    decisions = (record,) if record is not None else ()
    return ApplyResult(succeeded=True, touched=touched, leave_decisions=decisions)


async def _verify_copy_source(
    session: CellSession, proposal: Proposal, source: Path
) -> bytes | str:
    """Read `source` and check it against the proposal's own copy_sha256/copy_size.

    Returns:
        The source's verified bytes; or a one-line failure reason (module docstring's own
        "fail closed, not raise" rule) when the source is missing or no longer matches.
    """
    try:
        content = await session.get_file(source)
    except FileNotFoundError:
        return f"no file at {source} to keep"
    # The source may have changed (or the caller may be lying) since the tool proposed it; fail
    # closed rather than write bytes nobody actually verified (module docstring's own invariant).
    actual_sha256 = hashlib.sha256(content).hexdigest()
    if actual_sha256 != proposal.action.copy_sha256 or len(content) != proposal.action.copy_size:
        return f"{source} no longer matches the proposed sha256/size"
    return content


def _check_disk_reserve(destination: Path, size: int, reserve_mb: int | None) -> str | None:
    """Return a plain refusal reason if writing `size` bytes at `destination` breaks the reserve.

    Mirrors `hivemind.cell.local.HiveStandSource._prepare_scratch_root`'s own reserve check, on
    whichever existing ancestor directory `destination` would land under (it, or its parent
    directories, may not exist yet -- `keep`'s destination is often a fresh path under `keep_root`).

    Args:
        destination: The already-resolved path a COPY is about to write.
        size: How many bytes that write will add.
        reserve_mb: The manifest's own `[hive_stand] disk_reserve_mb`; None skips the check
            entirely (every call site outside `hivemind.supervision.capping.gate`'s own wiring).

    Returns:
        None when the write is safe (or unchecked); otherwise a one-line, human-readable reason.
    """
    if reserve_mb is None:
        return None
    bytes_per_mb = 1024 * 1024
    reserve_bytes = reserve_mb * bytes_per_mb
    candidate = destination if destination.exists() else destination.parent
    for _ in range(_MAX_DISK_PROBE_ANCESTORS):
        try:
            free_bytes = shutil.disk_usage(candidate).free
            break
        except OSError:
            parent = candidate.parent
            if parent == candidate:
                return None  # Reached the filesystem root with nothing mounted; nothing to check.
            candidate = parent
    else:
        return None
    if free_bytes - size < reserve_bytes:
        return (
            f"keeping {size} bytes at {destination} would leave free disk under the configured "
            f"reserve of {reserve_bytes} bytes"
        )
    return None


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
