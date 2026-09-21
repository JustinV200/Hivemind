"""Define decide_persist: turn one path's leave verdict into persist/approved_by, for apply.py.

`hivemind.supervision.capping.apply` is the effectful edge this package's own `__init__.py` names:
it already reads the session and applies a diff to get a path's new content, so `decide_persist` is
where those already-known facts (the resolved path, the new content) turn into `matches_leaving`,
`classify_path` and `looks_executable`'s own inputs, then `decide`'s own three-way answer, and back
into `persist`/`approved_by`/`reason` for `hivemind.supervision.capping.lease_view.LeaseView.
note_restore_path`. Still pure (codingrules section 8.3): `resolved` and `content` are handed in
already computed, so this module does no I/O of its own, the same as every other module in this
package -- it lives beside `policy.py` rather than in `apply.py` itself only because `apply.py`
already carries the DIFF/COMMAND apply split and this dispatch's own wiring would push it well past
the codingrules section 5.1 size limit.

`LeaveApplyContext` bundles every already-known Cell/task fact `hivemind.supervision.capping.gate.
GateDeps` carries that `apply_action` did not need before roadmap step 5.0c (codingrules section
5.1's own "frozen dataclass for the argument group"). Roadmap step 5.0c's own three-way answer: DENY
and an unwired `leave=None` (every call site that predates this dispatch) both come out identically
-- `persist=False`, `approved_by=None`, exactly `hivemind.cell.lease.RestoreRecord`'s own validator
requires; ASK is `LeaveVerdict.ASK` recorded on `LeavePersistDecision.record` but *not turned into a
Question here* -- `hivemind.supervision.capping.checks.human.HumanCheck` (roadmap step 5.0d) does
that, called by `hivemind.supervision.capping.apply` only when `LeaveApplyContext.asker` is set
(`with_asker`, below); `decide_persist` itself stays exactly as roadmap step 5.0c left it.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.supervision.capping.
    leave`. Called by `hivemind.supervision.capping.apply._apply_diff`; `hivemind.supervision.
    capping.gate` builds a `LeaveApplyContext` from its own `GateDeps` and records `capping.
    leave_decided` from each returned `LeaveDecisionRecord`. `Asker` is read by `hivemind.
    supervision.capping.checks.human.HumanCheck` (roadmap step 5.0d), imported from here rather
    than the reverse, so this package never depends on `.checks`. Calls into `hivemind.cell`
    (OsFamily), `hivemind.cell.leavings` (ApprovedBy), this package's own sibling modules and
    waggle only.

Key invariants:
    - `decide_persist(None, ...)` always returns `LeavePersistDecision(False, None, None, None)`:
      the exact behaviour `apply.py` had before roadmap phase 5, for every call site that has not
      been wired with a `LeaveApplyContext` yet.
    - `persist` is True if and only if `approved_by` and `reason` are both set (mirrors
      `hivemind.cell.lease.RestoreRecord`'s own validator, so a caller never has to check both).
    - `record` is None only when `leave` itself is None; every real decision (ALLOW, ASK or a
      declared-but-DENYd path) produces one, so `hivemind.supervision.capping.gate` can record
      `capping.leave_decided` even for a DENY.
    - `LeaveApplyContext.asker`/`.human_timeout_s`/`.clock` default to None/a sane default/an
      unused placeholder so every 5.0c-era `build_leave_context` call keeps constructing
      unchanged; `with_asker` is the one place that sets them for real.

See Also:
    - .claude/roadmap.md step 5.0c for "The gate consults it when applying an outside_scratch_
      write," verbatim.
    - .claude/roadmap.md step 5.0d for the HUMAN rung this module's `Asker` seam feeds.
    - hivemind.supervision.capping.leave.classify, .executable, .matcher and .policy for the four
      pure functions `decide_persist` calls.
    - hivemind.supervision.capping.apply for _apply_diff, this module's one caller.
    - hivemind.supervision.capping.checks.human for HumanCheck, the one reader of `Asker` and of
      `LeaveApplyContext.asker`/`.human_timeout_s`/`.clock`.
    - hivemind.cell.lease for RestoreRecord, whose persist/approved_by/reason validator this
      module's own "persist iff approved_by and reason" invariant mirrors.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from hivemind.cell import Cell, OsFamily
from hivemind.cell.leavings import ApprovedBy
from hivemind.supervision.capping.leave.classify import classify_path
from hivemind.supervision.capping.leave.executable import looks_executable
from hivemind.supervision.capping.leave.matcher import matches_leaving
from hivemind.supervision.capping.leave.model import (
    LeaveCellFacts,
    LeaveHumanVerdict,
    LeaveRequest,
    LeaveVerdict,
    PathClass,
)
from hivemind.supervision.capping.leave.policy import decide
from hivemind.supervision.capping.leave.table import LeavePolicyTable
from waggle.clock import Clock, FakeClock
from waggle.messages import PlannedLeaving
from waggle.messages.supervision import Answer, Question

# hivemind.cell.local.source's own Cell.source value for the Hive Stand (that module's own
# _SOURCE_NAME is private): build_leave_context's "is this the Hive Stand or a borrowed device"
# input (roadmap step 5.0c) is read from Cell.source, never Cell.kind (a Swarm device is also
# CellKind.REAL, so kind alone cannot tell the two apart).
HIVE_STAND_SOURCE = "hive_stand"
# HumanCheck.ask's own default (roadmap step 5.0d): generous but bounded, so "the task blocks
# until it is answered" never means "forever" -- past this, unanswered means discard, never a
# failed task (roadmap step 5.0d's own words).
DEFAULT_HUMAN_TIMEOUT_S = 300.0

__all__ = [
    "DEFAULT_HUMAN_TIMEOUT_S",
    "HIVE_STAND_SOURCE",
    "Asker",
    "LeaveApplyContext",
    "LeaveDecisionRecord",
    "LeavePersistDecision",
    "build_leave_context",
    "decide_persist",
    "with_asker",
]


class Asker(Protocol):
    """Send a blocking Question and wait for its matching Answer (roadmap step 5.0d).

    Structurally identical to `hivemind.workers.context.QuestionChannel`, declared separately here
    because `leave` sits at Layer 2 and `workers.context` at Layer 4 (codingrules section 4:
    imports flow downward only) -- the real asker a Warden threads through (`ctx.asker`, a
    `hivemind.workers.runtime.mailbox.Mailbox`) satisfies this Protocol structurally, with no
    import needed either way.
    """

    async def ask(self, question: Question) -> Answer:
        """Send `question` and block the caller until the matching Answer arrives.

        Args:
            question: The question to ask; its `question_id` is what the eventual Answer is
                matched against, not any envelope id.

        Returns:
            The Answer whose `question_id` equals `question.question_id`.
        """
        ...


@dataclass(frozen=True, slots=True)
class LeaveApplyContext:
    """Everything `decide_persist`/`HumanCheck` need beyond the path and its new content.

    Built once per gate, by `hivemind.supervision.capping.gate` from its own `GateDeps`
    (`leave_policy`, `declared_leaves`, `keep_root`, `leave_home`) and `GateDeps.cell`, then
    (roadmap step 5.0d) rebuilt with `with_asker` once per apply, from that call's own asker.
    """

    policy: LeavePolicyTable  # supervision/defaults/leave-policy.toml, loaded once at start-up.
    declared: tuple[PlannedLeaving, ...]  # The task's own TaskAssign.leaves (roadmap step 5.0b).
    keep_root: str | None  # The manifest's [hive_stand] keep_root; None until roadmap step 5.0e.
    home: str  # The Cell's own home directory, for `~`-rooted pattern expansion.
    os_family: OsFamily  # Chooses the pure-path flavour classify_path/matches_leaving use.
    cell: LeaveCellFacts  # AccessLevel, CombShieldLevel, is_hive_stand -- never cell.kind.
    # Roadmap step 5.0d: None (build_leave_context's own default) means "no HUMAN rung available
    # this call" -- an ASK verdict then stays persist=False, exactly roadmap 5.0c's own fallback.
    asker: Asker | None = None
    human_timeout_s: float = DEFAULT_HUMAN_TIMEOUT_S
    # A harmless placeholder: with_asker always sets a real Clock alongside a real `asker`, and
    # HumanCheck is only ever invoked when `asker` is not None (module docstring's own invariant),
    # so this default is never actually read for timing.
    clock: Clock = field(default_factory=FakeClock)


@dataclass(frozen=True, slots=True)
class LeaveDecisionRecord:
    """One outside-scratch path's own leave decision, for `capping.leave_decided` on the trail."""

    path: str  # The resolved path, as text; never file contents (codingrules section 12).
    path_class: PathClass  # Which row of leave-policy.toml fired.
    verdict: LeaveVerdict  # ALLOW, ASK or DENY.
    persisted: bool  # Whether this apply actually set persist=True.
    reason: str  # The matched PlannedLeaving's own reason, or a policy-verdict fallback.
    # Roadmap step 5.0d: set only once a HumanCheck actually ran (verdict was ASK and an asker
    # was wired); None for ALLOW, DENY, or an ASK no HumanCheck could resolve.
    human_answer: LeaveHumanVerdict | None = None


@dataclass(frozen=True, slots=True)
class LeavePersistDecision:
    """What `note_restore_path` should be called with, plus the record for the trail."""

    persist: bool
    approved_by: ApprovedBy | None
    reason: str | None
    record: LeaveDecisionRecord | None


def decide_persist(
    leave: LeaveApplyContext | None, resolved: Path, content: bytes
) -> LeavePersistDecision:
    """Decide persist/approved_by/reason for `resolved` (about to hold `content`).

    Args:
        leave: This gate's leave-policy inputs, or None when no `LeaveApplyContext` is wired (every
            call site outside roadmap phase 5's own wiring): always resolves to "do not persist,"
            unchanged from `apply.py`'s own behaviour before this dispatch.
        resolved: The already-resolved, absolute path about to be written outside scratch.
        content: The new bytes about to be written there (already computed by the caller).

    Returns:
        A LeavePersistDecision; `persist` is True only for `LeaveVerdict.ALLOW`. An ASK verdict
        resolves to `persist=False` here -- `hivemind.supervision.capping.checks.human.
        HumanCheck.resolve` (roadmap step 5.0d) is what may turn that into a HUMAN-approved
        persist, called by `apply.py` only when `leave.asker` is not None.
    """
    if leave is None:
        return LeavePersistDecision(persist=False, approved_by=None, reason=None, record=None)
    path_text = str(resolved)
    matched = matches_leaving(path_text, leave.declared, leave.os_family, leave.home)
    path_class = classify_path(path_text, leave.os_family, leave.home, leave.keep_root)
    request = LeaveRequest(
        path=path_text,
        path_class=path_class,
        size=len(content),
        is_executable=looks_executable(path_text, leave.os_family, content),
    )
    verdict = decide(request, leave.cell, matched is not None, leave.policy)
    fallback = f"leave policy: {verdict.value.lower()}"
    reason_text = matched.reason if matched is not None else fallback
    record = LeaveDecisionRecord(
        path=path_text,
        path_class=path_class,
        verdict=verdict,
        persisted=verdict is LeaveVerdict.ALLOW,
        reason=reason_text,
    )
    if verdict is not LeaveVerdict.ALLOW:
        # DENY today, and ASK too (roadmap step 5.0d has not wired a Question here yet): neither
        # persists, matching RestoreRecord's own "persist=False must not set approved_by/reason."
        return LeavePersistDecision(persist=False, approved_by=None, reason=None, record=record)
    return LeavePersistDecision(
        persist=True, approved_by=ApprovedBy.POLICY, reason=reason_text, record=record
    )


def build_leave_context(
    cell: Cell,
    policy: LeavePolicyTable,
    declared: tuple[PlannedLeaving, ...],
    keep_root: Path | None,
    home: Path,
) -> LeaveApplyContext:
    """Build a LeaveApplyContext from a Cell and a gate's own leave-policy fields.

    Split out of `hivemind.supervision.capping.gate` (whose `GateDeps` these fields come from) so
    that module stays within the codingrules section 5.1 file-size limit; takes each field
    individually, never `GateDeps` itself, since `gate.py` is this package's own caller, not the
    other way around.

    Args:
        cell: The Cell this gate's proposals run on (`GateDeps.cell`).
        policy: `GateDeps.leave_policy`.
        declared: `GateDeps.declared_leaves`, this task's own `TaskAssign.leaves`.
        keep_root: `GateDeps.keep_root`.
        home: `GateDeps.leave_home`.

    Returns:
        A LeaveApplyContext ready for `decide_persist`.
    """
    facts = LeaveCellFacts(
        access_level=cell.access_level,
        comb_shield=cell.comb_shield,
        is_hive_stand=cell.source == HIVE_STAND_SOURCE,
    )
    return LeaveApplyContext(
        policy=policy,
        declared=declared,
        keep_root=str(keep_root) if keep_root is not None else None,
        home=str(home),
        os_family=cell.capabilities.os,
        cell=facts,
    )


def with_asker(
    context: LeaveApplyContext, asker: Asker | None, timeout_s: float, clock: Clock
) -> LeaveApplyContext:
    """Return a copy of `context` carrying this call's own asker, timeout and clock (roadmap 5.0d).

    `hivemind.supervision.capping.gate` calls this once per apply (never at gate-build time,
    unlike `build_leave_context`): a sub-bee's real `Asker` (`ctx.asker`, a transport-backed
    `Mailbox`) only exists once its `WorkerRuntime` has started, after the gate itself was built
    (`hivemind.workers.context`'s own module docstring), so it is threaded through per call
    instead (`hivemind.supervision.capping.gate.CappingGate.run`'s own `asker` parameter).

    Args:
        context: The LeaveApplyContext `build_leave_context` already built for this gate.
        asker: This call's own asker (`ctx.asker`), or None to leave HumanCheck unreachable.
        timeout_s: How long `HumanCheck.ask` waits before treating this as discard.
        clock: This gate's own Clock, so `HumanCheck`'s timeout is deterministic in tests.

    Returns:
        A new LeaveApplyContext, identical to `context` but for `asker`/`human_timeout_s`/`clock`.
    """
    return dataclasses.replace(context, asker=asker, human_timeout_s=timeout_s, clock=clock)
