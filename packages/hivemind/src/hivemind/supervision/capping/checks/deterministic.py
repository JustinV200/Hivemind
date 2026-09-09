"""Deterministic Capping checks: schema, path/command allowlist, diff size -- no model, no network.

Codingrules section 8.12: "deterministic validators in autopilot (schema, lint, types, allowlists,
size caps)." `SchemaCheck` confirms a proposal's action kind is one v0 can actually apply (waggle's
own `ProposedAction` validators already enforce that the right field is populated for its kind).
`PathAllowlistCheck` and `CommandAllowlistCheck` enforce codingrules section 15's least privilege
on the paths and command a proposal touches. `DiffSizeCapCheck` enforces a tier's `max_diff_bytes`.
waggle's `CheckKind` has one `ALLOWLIST` member for both paths and commands, so `deterministic_
checks()` -- the composition root's registry, `Mapping[CheckKind, Check]` -- can register only one
`Check` under it; `_AllowlistCheck` composes the two so each stays independently testable while
still fitting that one slot.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the supervision package. Built
    by whichever composition root constructs a `hivemind.supervision.capping.gate.GateDeps` (a
    Warden, roadmap step 3.19); run by `hivemind.supervision.capping.gate.CappingGate`. Calls into
    `hivemind.guard` (Capability, CapabilityFamily), `hivemind.supervision.capping.checks.base`,
    `hivemind.supervision.capping.tiers` (RiskTier) and waggle only.

Key invariants:
    - No class here awaits a model or the network (codingrules section 8.12: these are the
      autopilot rungs).
    - Every path a proposal touches is resolved (join under scratch when relative, then
      `Path.resolve(strict=False)`, collapsing ".." segments) before either reachability check
      runs, so `scratch/../etc/passwd` cannot slip past `PathAllowlistCheck` unresolved
      (codingrules section 15: a path is validated before it reaches a filesystem call).

See Also:
    - .claude/codingrules.md section 8.12 for "deterministic validators in autopilot."
    - .claude/codingrules.md section 15 for "Least privilege is code, not policy" and "Subprocess
      calls... argument lists," which CommandAllowlistCheck enforces at the proposal level.
    - hivemind.cell.session.resolve_scratch_path for the equivalent normalise-then-check a
      CellSession runs on put_file/get_file; PathAllowlistCheck mirrors its normalisation step.
    - hivemind.supervision.capping.checks.base for Check, CheckContext and CheckResultRecord.
    - hivemind.supervision.capping.tiers for TierSpec.max_diff_bytes, which DiffSizeCapCheck reads.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from hivemind.guard import Capability, CapabilityFamily
from hivemind.supervision.capping.checks.base import Check, CheckContext, CheckResultRecord
from hivemind.supervision.capping.tiers import RiskTier
from waggle.messages.capping import ActionKind, CheckKind, CheckOutcome

# ActionKind.ACTION_SEQUENCE has no applier yet (roadmap 3.17: "ACTION_SEQUENCE -> REJECTED with
# reason 'unsupported in v0'"); SchemaCheck is where that rejection actually happens, before the
# gate ever reaches apply.py.
_SUPPORTED_ACTION_KINDS = frozenset({ActionKind.DIFF, ActionKind.COMMAND})

__all__ = [
    "CommandAllowlistCheck",
    "DiffSizeCapCheck",
    "PathAllowlistCheck",
    "SchemaCheck",
    "deterministic_checks",
]


class SchemaCheck:
    """Confirm a proposal's action kind is one this gate can actually apply.

    waggle's own `ProposedAction` validators already enforce field-matches-kind at construction
    time (`waggle.messages.capping.action`); the one thing left to check is whether v0's `apply.py`
    can act on this kind at all.
    """

    kind: ClassVar[CheckKind] = CheckKind.SCHEMA

    async def run(self, context: CheckContext) -> CheckResultRecord:
        """Pass DIFF and COMMAND actions; fail ACTION_SEQUENCE as unsupported in v0."""
        action_kind = context.proposal.action.kind
        if action_kind not in _SUPPORTED_ACTION_KINDS:
            return CheckResultRecord(
                kind=CheckKind.SCHEMA,
                outcome=CheckOutcome.FAILED,
                reason=f"{action_kind.value} unsupported in v0",
            )
        return CheckResultRecord(
            kind=CheckKind.SCHEMA, outcome=CheckOutcome.PASSED, reason="action kind is supported"
        )


class PathAllowlistCheck:
    """Require every path a proposal's action touches to be reachable and capability-granted.

    A path is reachable when it resolves under scratch or the lease's own `allowed_paths`
    (`LeaseView.is_path_allowed`); it is granted when some held capability's scope actually covers
    the resolved path. Both must hold.
    """

    kind: ClassVar[CheckKind] = CheckKind.ALLOWLIST

    async def run(self, context: CheckContext) -> CheckResultRecord:
        """Check every `action.paths` entry against the lease and the held capabilities."""
        # READ_ONLY proposals read paths; every other tier's paths are written to (a DIFF's
        # target, or the file an OUTSIDE_SCRATCH_WRITE touches), so the family to require differs.
        family = (
            CapabilityFamily.FS_READ
            if context.proposal.risk_tier is RiskTier.READ_ONLY
            else CapabilityFamily.FS_WRITE
        )
        for raw_path in context.proposal.action.paths:
            resolved = _normalise(context.scratch_root, Path(raw_path))
            if not _is_reachable(context, resolved):
                return CheckResultRecord(
                    kind=CheckKind.ALLOWLIST,
                    outcome=CheckOutcome.FAILED,
                    reason=f"{resolved} is outside scratch and outside every allowed path",
                )
            needed = Capability(family=family, scope=resolved.as_posix())
            if not context.capabilities.allows(needed):
                return CheckResultRecord(
                    kind=CheckKind.ALLOWLIST,
                    outcome=CheckOutcome.FAILED,
                    reason=f"no capability allows touching {resolved}",
                )
        return CheckResultRecord(
            kind=CheckKind.ALLOWLIST, outcome=CheckOutcome.PASSED, reason="every path is allowed"
        )


class CommandAllowlistCheck:
    """Require a COMMAND action's program (argv[0]) to be granted by an exec capability."""

    kind: ClassVar[CheckKind] = CheckKind.ALLOWLIST

    async def run(self, context: CheckContext) -> CheckResultRecord:
        """Pass trivially when the action carries no command; else check argv[0]."""
        command = context.proposal.action.command
        if not command:
            return CheckResultRecord(
                kind=CheckKind.ALLOWLIST, outcome=CheckOutcome.PASSED, reason="no command to check"
            )
        program = command[0]
        needed = Capability(family=CapabilityFamily.EXEC, scope=program)
        if not context.capabilities.allows(needed):
            return CheckResultRecord(
                kind=CheckKind.ALLOWLIST,
                outcome=CheckOutcome.FAILED,
                reason=f"no exec capability allows running {program!r}",
            )
        return CheckResultRecord(
            kind=CheckKind.ALLOWLIST, outcome=CheckOutcome.PASSED, reason=f"{program!r} is allowed"
        )


class DiffSizeCapCheck:
    """Require a DIFF action's inline diff to fit the tier's max_diff_bytes, when one is set."""

    kind: ClassVar[CheckKind] = CheckKind.SIZE_CAP

    async def run(self, context: CheckContext) -> CheckResultRecord:
        """Pass trivially for a non-DIFF action or an uncapped tier; else measure the diff."""
        action = context.proposal.action
        cap = context.tier.max_diff_bytes
        if action.kind is not ActionKind.DIFF or cap is None:
            return CheckResultRecord(
                kind=CheckKind.SIZE_CAP, outcome=CheckOutcome.PASSED, reason="no size cap applies"
            )
        if action.diff is None:
            # A by-digest diff (too large for the wire, ProposedAction.diff_sha256 set instead) is
            # written to scratch; this deterministic check has no CellSession to read it back with
            # (CheckContext carries none), so it fails closed (codingrules 8.12) rather than
            # guessing the size.
            return CheckResultRecord(
                kind=CheckKind.SIZE_CAP,
                outcome=CheckOutcome.FAILED,
                reason="diff is by digest; size cannot be verified without scratch (unsupported "
                "in v0)",
            )
        size = len(action.diff.encode("utf-8"))
        if size > cap:
            return CheckResultRecord(
                kind=CheckKind.SIZE_CAP,
                outcome=CheckOutcome.FAILED,
                reason=f"diff is {size} bytes, over the {cap}-byte cap for this tier",
            )
        return CheckResultRecord(
            kind=CheckKind.SIZE_CAP,
            outcome=CheckOutcome.PASSED,
            reason=f"diff is {size} bytes, within the {cap}-byte cap",
        )


def deterministic_checks() -> Mapping[CheckKind, Check]:
    """Build the composition root's deterministic check registry: SCHEMA, ALLOWLIST, SIZE_CAP.

    Returns:
        Every CheckKind v0's deterministic checks implement, mapped to a fresh instance.
    """
    return {
        CheckKind.SCHEMA: SchemaCheck(),
        CheckKind.ALLOWLIST: _AllowlistCheck(PathAllowlistCheck(), CommandAllowlistCheck()),
        CheckKind.SIZE_CAP: DiffSizeCapCheck(),
    }


@dataclass(frozen=True, slots=True)
class _AllowlistCheck:
    """Compose PathAllowlistCheck and CommandAllowlistCheck under the gate's one ALLOWLIST slot.

    waggle.messages.capping.CheckKind has a single ALLOWLIST member for both a proposal's paths
    and its command, so `deterministic_checks()`'s Mapping[CheckKind, Check] can register only one
    check per kind. This class runs both, reporting the first failure, so PathAllowlistCheck and
    CommandAllowlistCheck stay independently testable while still fitting that one slot.
    """

    path_check: PathAllowlistCheck
    command_check: CommandAllowlistCheck
    kind: ClassVar[CheckKind] = CheckKind.ALLOWLIST

    async def run(self, context: CheckContext) -> CheckResultRecord:
        """Run the path check first (cheaper, no capability construction if it fails first)."""
        path_result = await self.path_check.run(context)
        if path_result.outcome is not CheckOutcome.PASSED:
            return path_result
        return await self.command_check.run(context)


def _normalise(scratch_root: Path, path: Path) -> Path:
    """Join `path` under `scratch_root` if relative, then resolve it (collapsing ".." segments)."""
    joined = path if path.is_absolute() else scratch_root / path
    return joined.resolve(strict=False)


def _is_reachable(context: CheckContext, resolved: Path) -> bool:
    """Return whether `resolved` is under scratch_root or explicitly allowed by the lease."""
    scratch_resolved = context.scratch_root.resolve(strict=False)
    under_scratch = resolved == scratch_resolved or scratch_resolved in resolved.parents
    return under_scratch or context.lease.is_path_allowed(resolved)
