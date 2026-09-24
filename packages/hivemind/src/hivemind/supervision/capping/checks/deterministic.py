"""Deterministic Capping checks: schema, path/command allowlist, diff size -- no model, no network.

Codingrules section 8.12: "deterministic validators in autopilot (schema, lint, types, allowlists,
size caps)." `SchemaCheck` confirms a proposal's action kind is one v0 can actually apply (waggle's
own `ProposedAction` validators already enforce that the right field is populated for its kind).
`PathAllowlistCheck` and `CommandAllowlistCheck` enforce codingrules section 15's least privilege
on the paths and command a proposal touches. Roadmap step 10.3 (ADR-0031) adds three things: a
write outside scratch needs `cell:outside_scratch:<path>` as well as `fs:write:<path>`; a network
step -- one `"<METHOD> <url>"` step proposed on the `NETWORK_EGRESS` tier, the HTTP tool's only
shape, whose apply is the authorisation itself -- passes `SchemaCheck` when well formed, and
`NetworkAllowlistCheck` then requires `net:<host>` for it (the tier's own ALLOWLIST rung); and every
capability refusal names the missing capability on `CheckResultRecord.denied_capability`, which the
Worker records as `guard.denied`. `DiffSizeCapCheck` enforces a tier's `max_diff_bytes`.
waggle's `CheckKind` has one `ALLOWLIST` member for both paths and commands, so `deterministic_
checks()` -- the composition root's registry, `Mapping[CheckKind, Check]` -- can register only one
`Check` under it; `_AllowlistCheck` composes the three so each stays independently testable while
still fitting that one slot.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the supervision package. Built
    by whichever composition root constructs a `hivemind.supervision.capping.gate.GateDeps` (a
    Warden, roadmap step 3.19); run by `hivemind.supervision.capping.gate.CappingGate`. Calls into
    `hivemind.guard` (Capability, CapabilityFamily, InvalidCapabilityError), `hivemind.
    supervision.capping.checks.base`,
    `hivemind.supervision.capping.tiers` (RiskTier) and waggle only.

Key invariants:
    - No class here awaits a model or the network (codingrules section 8.12: these are the
      autopilot rungs).
    - Every path a proposal touches is resolved (join under scratch when relative, then
      `Path.resolve(strict=False)`, collapsing ".." segments) before either reachability check
      runs, so `scratch/../etc/passwd` cannot slip past `PathAllowlistCheck` unresolved
      (codingrules section 15: a path is validated before it reaches a filesystem call).
    - An `ACTION_SEQUENCE` passes `SchemaCheck` only as a well-formed network step on the
      `NETWORK_EGRESS` tier; every other unsupported shape is still refused before the gate could
      ever apply it.

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
from urllib.parse import urlsplit

from hivemind.guard import Capability, CapabilityFamily, InvalidCapabilityError
from hivemind.supervision.capping.checks.base import Check, CheckContext, CheckResultRecord
from hivemind.supervision.capping.tiers import RiskTier
from waggle.messages.capping import ActionKind, CheckKind, CheckOutcome, ProposedAction

# ActionKind.ACTION_SEQUENCE has no general applier (roadmap 3.17: "ACTION_SEQUENCE -> REJECTED
# with reason 'unsupported in v0'"); SchemaCheck is where that rejection actually happens, before
# the gate ever reaches apply.py. COPY (roadmap step 5.0e, the `keep` tool) does have an applier
# (hivemind.supervision.capping.apply._apply_copy).
_SUPPORTED_ACTION_KINDS = frozenset({ActionKind.DIFF, ActionKind.COMMAND, ActionKind.COPY})
# Roadmap step 10.3: the one tier an ACTION_SEQUENCE is supported on, the HTTP tool's network step
# (hivemind.workers.tools.http), whose apply is a no-op authorisation the tool then acts on.
_SEQUENCE_TIERS = frozenset({RiskTier.NETWORK_EGRESS})

__all__ = [
    "CommandAllowlistCheck",
    "DiffSizeCapCheck",
    "NetworkAllowlistCheck",
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
        """Pass DIFF, COMMAND and COPY, and a well-formed network step on its own tier only."""
        action_kind = context.proposal.action.kind
        network_step = (
            context.proposal.risk_tier in _SEQUENCE_TIERS
            and _network_host(context.proposal.action) is not None
        )
        if action_kind not in _SUPPORTED_ACTION_KINDS and not network_step:
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
    the resolved path -- and, for a write outside scratch (roadmap step 10.3), when a held
    `cell:outside_scratch` covers it too. Both must hold.
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
            # Every capability this path needs, in order; the first one not held refuses it.
            for needed in _path_needs(family, resolved, _under_scratch(context, resolved)):
                if not context.capabilities.allows(needed):
                    return CheckResultRecord(
                        kind=CheckKind.ALLOWLIST,
                        outcome=CheckOutcome.FAILED,
                        reason=f"no capability allows touching {resolved} ({needed.family.value})",
                        denied_capability=str(needed),
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
                denied_capability=str(needed),
            )
        return CheckResultRecord(
            kind=CheckKind.ALLOWLIST, outcome=CheckOutcome.PASSED, reason=f"{program!r} is allowed"
        )


class NetworkAllowlistCheck:
    """Require a network step's destination host to be granted by a `net` capability.

    Roadmap step 10.3: the NETWORK_EGRESS tier's own ALLOWLIST rung (`capping-tiers.toml`: "the
    allowlist check still enforces a net: capability for the destination"), so the gate never
    passes a network step on the tool's own word alone. Any other action carries no destination.
    """

    kind: ClassVar[CheckKind] = CheckKind.ALLOWLIST

    async def run(self, context: CheckContext) -> CheckResultRecord:
        """Pass trivially for anything but an ACTION_SEQUENCE; else check its host's `net`."""
        action = context.proposal.action
        if action.kind is not ActionKind.ACTION_SEQUENCE:
            return CheckResultRecord(
                kind=CheckKind.ALLOWLIST, outcome=CheckOutcome.PASSED, reason="no destination"
            )
        # A host the `net` grammar cannot hold (or no host at all) is refused, never raised.
        try:
            needed = Capability.parse(f"{CapabilityFamily.NET.value}:{_network_host(action)}")
        except InvalidCapabilityError:
            return CheckResultRecord(
                kind=CheckKind.ALLOWLIST,
                outcome=CheckOutcome.FAILED,
                reason="the network step names no valid host",
            )
        if not context.capabilities.allows(needed):
            return CheckResultRecord(
                kind=CheckKind.ALLOWLIST,
                outcome=CheckOutcome.FAILED,
                reason=f"no net capability allows reaching {needed.scope!r}",
                denied_capability=str(needed),
            )
        return CheckResultRecord(
            kind=CheckKind.ALLOWLIST, outcome=CheckOutcome.PASSED, reason="the host is allowed"
        )


class DiffSizeCapCheck:
    """Require a DIFF's inline diff, or a COPY's source size, to fit the tier's own byte cap.

    One check class fills the gate's single SIZE_CAP slot for both size-bounded action kinds
    (mirroring `_AllowlistCheck`'s own one-slot-two-checks shape below), since neither cap ever
    applies to the other kind's own field.
    """

    kind: ClassVar[CheckKind] = CheckKind.SIZE_CAP

    async def run(self, context: CheckContext) -> CheckResultRecord:
        """Dispatch to the diff or copy size check by `action.kind`; pass trivially for neither."""
        action = context.proposal.action
        if action.kind is ActionKind.DIFF:
            return _check_diff_size(action, context.tier.max_diff_bytes)
        if action.kind is ActionKind.COPY:
            return _check_copy_size(action, context.tier.max_copy_bytes)
        return CheckResultRecord(
            kind=CheckKind.SIZE_CAP, outcome=CheckOutcome.PASSED, reason="no size cap applies"
        )


def deterministic_checks() -> Mapping[CheckKind, Check]:
    """Build the composition root's deterministic check registry: SCHEMA, ALLOWLIST, SIZE_CAP.

    Returns:
        Every CheckKind v0's deterministic checks implement, mapped to a fresh instance.
    """
    return {
        CheckKind.SCHEMA: SchemaCheck(),
        CheckKind.ALLOWLIST: _AllowlistCheck(
            PathAllowlistCheck(), CommandAllowlistCheck(), NetworkAllowlistCheck()
        ),
        CheckKind.SIZE_CAP: DiffSizeCapCheck(),
    }


@dataclass(frozen=True, slots=True)
class _AllowlistCheck:
    """Compose the path, command and network checks under the gate's one ALLOWLIST slot.

    waggle.messages.capping.CheckKind has a single ALLOWLIST member for a proposal's paths, its
    command and (roadmap step 10.3) its network destination, so `deterministic_checks()`'s
    Mapping[CheckKind, Check] can register only one check per kind. This class runs all three,
    reporting the first failure, so each stays independently testable while fitting that one slot.
    """

    path_check: PathAllowlistCheck
    command_check: CommandAllowlistCheck
    network_check: NetworkAllowlistCheck
    kind: ClassVar[CheckKind] = CheckKind.ALLOWLIST

    async def run(self, context: CheckContext) -> CheckResultRecord:
        """Run the path check first (cheaper, no capability construction if it fails first)."""
        path_result = await self.path_check.run(context)
        if path_result.outcome is not CheckOutcome.PASSED:
            return path_result
        command_result = await self.command_check.run(context)
        if command_result.outcome is not CheckOutcome.PASSED:
            return command_result
        return await self.network_check.run(context)


def _check_diff_size(action: ProposedAction, cap: int | None) -> CheckResultRecord:
    """Measure a DIFF action's inline diff against `cap`, when one is set."""
    if cap is None:
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
            reason="diff is by digest; size cannot be verified without scratch (unsupported in v0)",
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


def _check_copy_size(action: ProposedAction, cap: int | None) -> CheckResultRecord:
    """Measure a COPY action's own declared copy_size against `cap` (roadmap step 5.0e)."""
    if cap is None:
        return CheckResultRecord(
            kind=CheckKind.SIZE_CAP, outcome=CheckOutcome.PASSED, reason="no size cap applies"
        )
    # ProposedAction's own validator (waggle.messages.capping.action) already requires copy_size
    # to be set for a COPY action, so this is never None here; the actual bytes are re-measured
    # and re-checked against this same cap again at apply time (hivemind.supervision.capping.
    # apply._apply_copy), since a declared size could lie about what scratch really holds.
    size = action.copy_size or 0
    if size > cap:
        return CheckResultRecord(
            kind=CheckKind.SIZE_CAP,
            outcome=CheckOutcome.FAILED,
            reason=f"copy source is {size} bytes, over the {cap}-byte cap for this tier",
        )
    return CheckResultRecord(
        kind=CheckKind.SIZE_CAP,
        outcome=CheckOutcome.PASSED,
        reason=f"copy source is {size} bytes, within the {cap}-byte cap",
    )


def _normalise(scratch_root: Path, path: Path) -> Path:
    """Join `path` under `scratch_root` if relative, then resolve it (collapsing ".." segments)."""
    joined = path if path.is_absolute() else scratch_root / path
    return joined.resolve(strict=False)


def _under_scratch(context: CheckContext, resolved: Path) -> bool:
    """Return whether `resolved` is the scratch root itself or somewhere beneath it."""
    scratch_resolved = context.scratch_root.resolve(strict=False)
    return resolved == scratch_resolved or scratch_resolved in resolved.parents


def _is_reachable(context: CheckContext, resolved: Path) -> bool:
    """Return whether `resolved` is under scratch_root or explicitly allowed by the lease."""
    return _under_scratch(context, resolved) or context.lease.is_path_allowed(resolved)


def _network_host(action: ProposedAction) -> str | None:
    """Return a network step's host: one `"<METHOD> <url>"` step naming one; else None."""
    if action.kind is not ActionKind.ACTION_SEQUENCE or len(action.steps) != 1:
        return None
    method, _, url = action.steps[0].partition(" ")
    if not method.isalpha() or not method.isupper():
        return None
    try:
        return urlsplit(url).hostname or None
    except ValueError:  # urlsplit refuses some malformed URLs (an unclosed IPv6 bracket, ...).
        return None


def _path_needs(
    family: CapabilityFamily, resolved: Path, inside_scratch: bool
) -> tuple[Capability, ...]:
    """Return what touching `resolved` needs: its `fs` family, and `cell:outside_scratch` too.

    The second only for a write outside scratch (roadmap step 10.3: leaving scratch is its own,
    separate grant).
    """
    scope = resolved.as_posix()
    needs = [Capability(family=family, scope=scope)]
    if family is CapabilityFamily.FS_WRITE and not inside_scratch:
        needs.append(Capability(family=CapabilityFamily.CELL_OUTSIDE_SCRATCH, scope=scope))
    return tuple(needs)
