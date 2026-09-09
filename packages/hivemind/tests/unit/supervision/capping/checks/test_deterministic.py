"""Unit tests for hivemind.supervision.capping.checks.deterministic: the four v0 rungs."""

from __future__ import annotations

from pathlib import Path

from builders.capping import FakeLeaseView, make_action, make_proposal, make_tier_table

from hivemind.guard import CapabilitySet
from hivemind.supervision.capping.checks.base import CheckContext
from hivemind.supervision.capping.checks.deterministic import (
    CommandAllowlistCheck,
    DiffSizeCapCheck,
    PathAllowlistCheck,
    SchemaCheck,
    deterministic_checks,
)
from hivemind.supervision.capping.tiers import RiskTier
from waggle.messages.capping import ActionKind, CheckKind, CheckOutcome, ProposedAction


def _context(
    tmp_path: Path,
    *,
    action: ProposedAction | None = None,
    capabilities: CapabilitySet | None = None,
    lease: FakeLeaseView | None = None,
    risk_tier: RiskTier = RiskTier.SCRATCH_WRITE,
) -> CheckContext:
    """Build a CheckContext scoped to tmp_path, defaulting to a scratch-write diff proposal."""
    scratch_root = tmp_path / "scratch"
    proposal = make_proposal(
        risk_tier=risk_tier, action=action if action is not None else make_action()
    )
    tier = make_tier_table().tiers[RiskTier.SCRATCH_WRITE]
    return CheckContext(
        proposal=proposal,
        capabilities=capabilities if capabilities is not None else CapabilitySet.parse(),
        lease=lease if lease is not None else FakeLeaseView(scratch_root),
        scratch_root=scratch_root,
        tier=tier,
    )


# ──────────────────────────────────────────────────────────────────────────────
# SchemaCheck
# ──────────────────────────────────────────────────────────────────────────────


async def test_schema_check_passes_a_diff_action(tmp_path: Path) -> None:
    result = await SchemaCheck().run(_context(tmp_path, action=make_action(ActionKind.DIFF)))

    assert result.outcome is CheckOutcome.PASSED


async def test_schema_check_passes_a_command_action(tmp_path: Path) -> None:
    result = await SchemaCheck().run(_context(tmp_path, action=make_action(ActionKind.COMMAND)))

    assert result.outcome is CheckOutcome.PASSED


async def test_schema_check_rejects_an_action_sequence(tmp_path: Path) -> None:
    action = make_action(ActionKind.ACTION_SEQUENCE)

    result = await SchemaCheck().run(_context(tmp_path, action=action))

    assert result.outcome is CheckOutcome.FAILED
    assert "unsupported in v0" in result.reason
    assert result.kind is CheckKind.SCHEMA


# ──────────────────────────────────────────────────────────────────────────────
# PathAllowlistCheck
# ──────────────────────────────────────────────────────────────────────────────


async def test_path_allowlist_check_passes_a_path_inside_scratch_with_capability(
    tmp_path: Path,
) -> None:
    scratch_root = tmp_path / "scratch"
    action = make_action(ActionKind.DIFF, paths=("note.txt",))
    capabilities = CapabilitySet.parse(f"fs:write:{scratch_root.as_posix()}/**")

    result = await PathAllowlistCheck().run(
        _context(tmp_path, action=action, capabilities=capabilities)
    )

    assert result.outcome is CheckOutcome.PASSED


async def test_path_allowlist_check_rejects_a_dotdot_traversal_outside_scratch(
    tmp_path: Path,
) -> None:
    # "../etc/passwd" joins under scratch_root first (it is relative), then Path.resolve(
    # strict=False) collapses the ".." -- landing one directory above scratch_root, outside it.
    action = make_action(ActionKind.DIFF, paths=("../etc/passwd",))
    capabilities = CapabilitySet.parse("fs:write:**")  # Would allow it, if reachability held.

    result = await PathAllowlistCheck().run(
        _context(tmp_path, action=action, capabilities=capabilities)
    )

    assert result.outcome is CheckOutcome.FAILED
    assert "outside scratch" in result.reason


async def test_path_allowlist_check_rejects_a_path_missing_its_capability(tmp_path: Path) -> None:
    action = make_action(ActionKind.DIFF, paths=("note.txt",))
    # No capability at all: the path is reachable (inside scratch) but nothing grants it.
    result = await PathAllowlistCheck().run(
        _context(tmp_path, action=action, capabilities=CapabilitySet.parse())
    )

    assert result.outcome is CheckOutcome.FAILED
    assert "no capability allows" in result.reason


async def test_path_allowlist_check_allows_a_lease_allowed_path_outside_scratch(
    tmp_path: Path,
) -> None:
    scratch_root = tmp_path / "scratch"
    outside = tmp_path / "outside" / "config.toml"
    action = make_action(ActionKind.DIFF, paths=(str(outside),))
    lease = FakeLeaseView(scratch_root, allowed_paths=(outside.parent,))
    capabilities = CapabilitySet.parse(f"fs:write:{outside.as_posix()}")

    result = await PathAllowlistCheck().run(
        _context(tmp_path, action=action, capabilities=capabilities, lease=lease)
    )

    assert result.outcome is CheckOutcome.PASSED


async def test_path_allowlist_check_uses_fs_read_for_a_read_only_proposal(tmp_path: Path) -> None:
    action = make_action(ActionKind.DIFF, paths=("note.txt",))
    scratch_root = tmp_path / "scratch"
    write_only = CapabilitySet.parse(f"fs:write:{scratch_root.as_posix()}/**")

    result = await PathAllowlistCheck().run(
        _context(
            tmp_path,
            action=action,
            capabilities=write_only,
            risk_tier=RiskTier.READ_ONLY,
        )
    )

    # A READ_ONLY proposal needs fs:read, not fs:write, so a write-only grant fails it.
    assert result.outcome is CheckOutcome.FAILED


async def test_path_allowlist_check_passes_trivially_with_no_paths(tmp_path: Path) -> None:
    action = make_action(ActionKind.COMMAND)

    result = await PathAllowlistCheck().run(
        _context(tmp_path, action=action, capabilities=CapabilitySet.parse())
    )

    assert result.outcome is CheckOutcome.PASSED


# ──────────────────────────────────────────────────────────────────────────────
# CommandAllowlistCheck
# ──────────────────────────────────────────────────────────────────────────────


async def test_command_allowlist_check_passes_trivially_for_a_diff(tmp_path: Path) -> None:
    result = await CommandAllowlistCheck().run(
        _context(tmp_path, action=make_action(ActionKind.DIFF), capabilities=CapabilitySet.parse())
    )

    assert result.outcome is CheckOutcome.PASSED
    assert "no command" in result.reason


async def test_command_allowlist_check_passes_an_allowed_program(tmp_path: Path) -> None:
    action = make_action(ActionKind.COMMAND, command=("true",))
    capabilities = CapabilitySet.parse("exec:true")

    result = await CommandAllowlistCheck().run(
        _context(tmp_path, action=action, capabilities=capabilities)
    )

    assert result.outcome is CheckOutcome.PASSED


async def test_command_allowlist_check_rejects_a_disallowed_program(tmp_path: Path) -> None:
    action = make_action(ActionKind.COMMAND, command=("rm",))

    result = await CommandAllowlistCheck().run(
        _context(tmp_path, action=action, capabilities=CapabilitySet.parse())
    )

    assert result.outcome is CheckOutcome.FAILED
    assert "no exec capability" in result.reason


# ──────────────────────────────────────────────────────────────────────────────
# DiffSizeCapCheck
# ──────────────────────────────────────────────────────────────────────────────


async def test_diff_size_cap_check_passes_a_non_diff_action(tmp_path: Path) -> None:
    result = await DiffSizeCapCheck().run(
        _context(tmp_path, action=make_action(ActionKind.COMMAND))
    )

    assert result.outcome is CheckOutcome.PASSED


async def test_diff_size_cap_check_passes_within_the_cap(tmp_path: Path) -> None:
    action = make_action(ActionKind.DIFF, diff="@@ -0,0 +1,1 @@\n+x\n")

    result = await DiffSizeCapCheck().run(_context(tmp_path, action=action))

    assert result.outcome is CheckOutcome.PASSED


async def test_diff_size_cap_check_fails_over_the_cap(tmp_path: Path) -> None:
    scratch_root = tmp_path / "scratch"
    proposal = make_proposal(
        risk_tier=RiskTier.SCRATCH_WRITE,
        action=make_action(ActionKind.DIFF, diff="+" + ("x" * 10)),
    )
    tiny_tier = (
        make_tier_table().tiers[RiskTier.SCRATCH_WRITE].model_copy(update={"max_diff_bytes": 4})
    )
    context = CheckContext(
        proposal=proposal,
        capabilities=CapabilitySet.parse(),
        lease=FakeLeaseView(scratch_root),
        scratch_root=scratch_root,
        tier=tiny_tier,
    )

    result = await DiffSizeCapCheck().run(context)

    assert result.outcome is CheckOutcome.FAILED
    assert "byte cap" in result.reason


async def test_diff_size_cap_check_fails_closed_for_a_by_digest_diff(tmp_path: Path) -> None:
    action = make_action(ActionKind.DIFF, diff=None, diff_sha256="ab" * 32, paths=("big.diff",))

    result = await DiffSizeCapCheck().run(_context(tmp_path, action=action))

    assert result.outcome is CheckOutcome.FAILED
    assert "unsupported in v0" in result.reason


# ──────────────────────────────────────────────────────────────────────────────
# deterministic_checks
# ──────────────────────────────────────────────────────────────────────────────


def test_deterministic_checks_registers_exactly_the_v0_kinds() -> None:
    checks = deterministic_checks()

    assert set(checks) == {CheckKind.SCHEMA, CheckKind.ALLOWLIST, CheckKind.SIZE_CAP}


async def test_deterministic_checks_allowlist_entry_runs_both_path_and_command(
    tmp_path: Path,
) -> None:
    checks = deterministic_checks()
    action = make_action(ActionKind.COMMAND, command=("rm",))

    result = await checks[CheckKind.ALLOWLIST].run(
        _context(tmp_path, action=action, capabilities=CapabilitySet.parse())
    )

    # A COMMAND action has no paths, so the path half passes trivially and the failure comes
    # from the command half -- proving both halves actually ran under the one ALLOWLIST slot.
    assert result.outcome is CheckOutcome.FAILED
    assert "no exec capability" in result.reason
