"""Unit tests for hivemind.supervision.capping.checks.deterministic: the v0 rungs.

Roadmap step 10.3 adds: a write outside scratch needs `cell:outside_scratch` too, every capability
refusal names the missing capability, and a well-formed network step passes SCHEMA on its own tier
while `NetworkAllowlistCheck` requires `net:<host>` for it.
"""

from __future__ import annotations

from pathlib import Path

from builders.capping import FakeLeaseView, make_action, make_proposal, make_tier_table

from hivemind.guard import CapabilitySet
from hivemind.supervision.capping.checks.base import CheckContext
from hivemind.supervision.capping.checks.deterministic import (
    CommandAllowlistCheck,
    DiffSizeCapCheck,
    GuiAllowlistCheck,
    NetworkAllowlistCheck,
    PathAllowlistCheck,
    SchemaCheck,
    deterministic_checks,
)
from hivemind.supervision.capping.tiers import RiskTier
from waggle.messages.capping import (
    ActionKind,
    CheckKind,
    CheckOutcome,
    GuiOp,
    GuiStep,
    ProposedAction,
)


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
    capabilities = CapabilitySet.parse(
        f"fs:write:{outside.as_posix()}", f"cell:outside_scratch:{outside.as_posix()}"
    )

    result = await PathAllowlistCheck().run(
        _context(tmp_path, action=action, capabilities=capabilities, lease=lease)
    )

    assert result.outcome is CheckOutcome.PASSED


async def test_path_allowlist_check_requires_cell_outside_scratch_to_write_outside_scratch(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside" / "config.toml"
    action = make_action(ActionKind.DIFF, paths=(str(outside),))
    lease = FakeLeaseView(tmp_path / "scratch", allowed_paths=(outside.parent,))
    capabilities = CapabilitySet.parse(f"fs:write:{outside.as_posix()}")  # No cell:outside_...

    result = await PathAllowlistCheck().run(
        _context(tmp_path, action=action, capabilities=capabilities, lease=lease)
    )

    assert result.outcome is CheckOutcome.FAILED
    assert result.denied_capability == f"cell:outside_scratch:{outside.as_posix()}"


async def test_path_allowlist_check_names_the_missing_fs_capability(tmp_path: Path) -> None:
    action = make_action(ActionKind.DIFF, paths=("note.txt",))

    result = await PathAllowlistCheck().run(_context(tmp_path, action=action))

    note = (tmp_path / "scratch" / "note.txt").resolve(strict=False)
    assert result.denied_capability == f"fs:write:{note.as_posix()}"


async def test_path_allowlist_check_names_no_capability_for_an_unreachable_path(
    tmp_path: Path,
) -> None:
    # Beyond every root the lease reaches: a boundary, not a missing capability.
    action = make_action(ActionKind.DIFF, paths=("/elsewhere/note.txt",))

    result = await PathAllowlistCheck().run(
        _context(tmp_path, action=action, capabilities=CapabilitySet.parse("fs:write:**"))
    )

    assert result.outcome is CheckOutcome.FAILED
    assert result.denied_capability is None


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
    assert result.denied_capability == "exec:rm"


# ──────────────────────────────────────────────────────────────────────────────
# A network step (roadmap step 10.3): SchemaCheck and NetworkAllowlistCheck
# ──────────────────────────────────────────────────────────────────────────────


def _network_step(step: str = "GET https://example.com/data") -> ProposedAction:
    """The HTTP tool's own shape: one `"<METHOD> <url>"` step in an ACTION_SEQUENCE."""
    return make_action(ActionKind.ACTION_SEQUENCE, steps=(step,))


async def test_schema_check_passes_a_well_formed_network_step_on_its_own_tier(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, action=_network_step(), risk_tier=RiskTier.NETWORK_EGRESS)

    result = await SchemaCheck().run(context)

    assert result.outcome is CheckOutcome.PASSED


async def test_schema_check_rejects_a_network_step_on_any_other_tier(tmp_path: Path) -> None:
    context = _context(tmp_path, action=_network_step(), risk_tier=RiskTier.DEVICE_COMMAND)

    result = await SchemaCheck().run(context)

    assert result.outcome is CheckOutcome.FAILED


async def test_schema_check_rejects_a_sequence_that_is_no_network_step(tmp_path: Path) -> None:
    action = _network_step("Click the confirm button.")

    result = await SchemaCheck().run(
        _context(tmp_path, action=action, risk_tier=RiskTier.NETWORK_EGRESS)
    )

    assert result.outcome is CheckOutcome.FAILED


async def test_network_allowlist_check_requires_net_for_the_host(tmp_path: Path) -> None:
    context = _context(tmp_path, action=_network_step(), risk_tier=RiskTier.NETWORK_EGRESS)

    result = await NetworkAllowlistCheck().run(context)

    assert result.outcome is CheckOutcome.FAILED
    assert result.denied_capability == "net:example.com"


async def test_network_allowlist_check_passes_a_held_host(tmp_path: Path) -> None:
    context = _context(
        tmp_path,
        action=_network_step(),
        capabilities=CapabilitySet.parse("net:example.com"),
        risk_tier=RiskTier.NETWORK_EGRESS,
    )

    result = await NetworkAllowlistCheck().run(context)

    assert result.outcome is CheckOutcome.PASSED


async def test_network_allowlist_check_passes_trivially_for_a_diff(tmp_path: Path) -> None:
    result = await NetworkAllowlistCheck().run(_context(tmp_path))

    assert result.outcome is CheckOutcome.PASSED


async def test_the_registered_allowlist_runs_the_network_check_too(tmp_path: Path) -> None:
    allowlist = deterministic_checks()[CheckKind.ALLOWLIST]
    context = _context(tmp_path, action=_network_step(), risk_tier=RiskTier.NETWORK_EGRESS)

    result = await allowlist.run(context)

    assert result.outcome is CheckOutcome.FAILED
    assert result.denied_capability == "net:example.com"


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


# ──────────────────────────────────────────────────────────────────────────────
# GuiAllowlistCheck: a file URL must stay inside scratch
# ──────────────────────────────────────────────────────────────────────────────


def _navigate(url: str) -> ProposedAction:
    """A GUI proposal's action: one browser navigation to `url`."""
    return ProposedAction(
        kind=ActionKind.GUI,
        summary=f"navigate to {url}",
        diff=None,
        diff_sha256=None,
        command=(),
        cwd=None,
        paths=(),
        steps=(),
        gui=(GuiStep(op=GuiOp.NAVIGATE, url=url),),
    )


async def test_gui_allowlist_refuses_a_file_url_outside_scratch_whatever_is_granted(
    tmp_path: Path,
) -> None:
    # Arrange: every grant a browser read could plausibly lean on, the disk included.
    outside = (tmp_path / "elsewhere" / "id_rsa").as_uri()
    granted = CapabilitySet.parse("exoskeleton:browser", f"fs:read:{tmp_path.as_posix()}/**")
    context = _context(
        tmp_path,
        action=_navigate(outside),
        capabilities=granted,
        risk_tier=RiskTier.OUTSIDE_SCRATCH_WRITE,
    )

    result = await GuiAllowlistCheck().run(context)

    assert result.outcome is CheckOutcome.FAILED
    assert result.reason == "a file URL outside scratch is never loaded"


async def test_gui_allowlist_passes_a_file_url_inside_scratch_with_the_browser_grant(
    tmp_path: Path,
) -> None:
    inside = (tmp_path / "scratch" / "site" / "login.html").as_uri()
    granted = CapabilitySet.parse("exoskeleton:browser")

    result = await GuiAllowlistCheck().run(
        _context(tmp_path, action=_navigate(inside), capabilities=granted)
    )

    assert result.outcome is CheckOutcome.PASSED


async def test_the_allowlist_slot_runs_the_file_url_rule_too(tmp_path: Path) -> None:
    escaping = f"{(tmp_path / 'scratch').as_uri()}/%2E%2E/elsewhere/id_rsa"
    granted = CapabilitySet.parse("exoskeleton:browser")
    context = _context(tmp_path, action=_navigate(escaping), capabilities=granted)

    result = await deterministic_checks()[CheckKind.ALLOWLIST].run(context)

    assert result.outcome is CheckOutcome.FAILED
