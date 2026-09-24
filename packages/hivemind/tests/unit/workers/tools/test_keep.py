"""Unit tests for hivemind.workers.tools.keep: refusals, the happy path, the DENY message."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from builders.capping import FakeLeaseView, RepeatingJudgeReviewer
from builders.cells import make_capabilities, make_cell
from builders.workers import make_assignment, make_context

from hivemind.cell import CellIdentity, CellKind, NoopSnapshotter, OsFamily
from hivemind.cell.fake import FakeSession
from hivemind.cell.tiers import AccessLevel
from hivemind.guard import CapabilitySet
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.supervision.capping import (
    CappingGate,
    GateDeps,
    deterministic_checks,
    judge_checks,
    load_judge_rubrics,
    load_tiers,
)
from hivemind.workers.tools.keep import keep
from hivemind.workers.tools.registry import ToolInvocation
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id
from waggle.messages import PlannedLeaving

_SCRATCH_DIR = Path("scratch")
_KEEP_ROOT = Path("keep").resolve()  # Resolved at import time: ASYNC240 (module docstring).
_DEST = _KEEP_ROOT / "checker.exe"
_OUTSIDE_UNDECLARED = (Path("elsewhere") / "note.txt").resolve()
# The leave policy's own matcher/classifier compares paths through PureWindowsPath/PurePosixPath,
# chosen by the Cell's own OsFamily (never the host's actual OS) -- but these tests build real,
# host-resolved Path objects, so the fake Cell's own OsFamily must actually match this host or the
# comparison parses a Windows path's backslashes as PurePosixPath and never matches at all.
_HOST_OS_FAMILY = OsFamily.WINDOWS if os.name == "nt" else OsFamily.LINUX


def _gate(
    session: FakeSession, *, keep_root: Path | None = None, leaves: tuple[PlannedLeaving, ...] = ()
) -> CappingGate:
    """Build a real CappingGate at FULL access, wired the way `_build_capping_gate` wires one."""
    clock = FakeClock()
    cell = make_cell(
        kind=CellKind.REAL,
        clock=clock,
        access_level=AccessLevel.FULL,
        capabilities=make_capabilities(os=_HOST_OS_FAMILY),
    )
    identity = CellIdentity(hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system")
    return CappingGate(
        GateDeps(
            session=session,
            snapshotter=NoopSnapshotter(),
            cell=cell,
            tiers=load_tiers(),
            trail=MemoryPheromoneTrail(clock),
            identity=identity,
            clock=clock,
            checks={
                **deterministic_checks(),
                **judge_checks(RepeatingJudgeReviewer(), load_judge_rubrics()),
            },
            declared_leaves=leaves,
            keep_root=keep_root,
        )
    )


def _capabilities(*extra_scopes: str) -> CapabilitySet:
    # A keep() proposal's own paths carry both source (inside scratch) and destination (outside
    # it) under one OUTSIDE_SCRATCH_WRITE tier, so PathAllowlistCheck's capability check runs
    # against source too; the resolved, absolute scratch scope is what production actually grants
    # (hivemind.guard.access.ceiling_for's own `fs:write` scope is built from the lease's already-
    # absolute scratch_root), unlike the plain relative "scratch/**" other builders' tests use for
    # a scratch-internal SCRATCH_WRITE tier, which never runs ALLOWLIST at all.
    absolute_scratch = (Path.cwd() / _SCRATCH_DIR).resolve().as_posix()
    return CapabilitySet.parse(
        f"fs:write:{_SCRATCH_DIR.as_posix()}/**",
        f"fs:write:{absolute_scratch}/**",
        "fs:read:**",
        "exec:*",
        "tool:*",
        *extra_scopes,
    )


async def test_keep_rejects_a_missing_source() -> None:
    ctx = make_context()
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment())

    result = await keep(invocation, {"destination": str(_DEST)})

    assert result == "source must be a non-empty string."


async def test_keep_rejects_a_missing_destination() -> None:
    ctx = make_context()
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment())

    result = await keep(invocation, {"source": "installer.exe"})

    assert result == "destination must be a non-empty string."


async def test_keep_rejects_a_relative_destination() -> None:
    ctx = make_context()
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment())

    result = await keep(invocation, {"source": "installer.exe", "destination": "relative/path"})

    assert "absolute" in result


async def test_keep_rejects_a_destination_inside_scratch() -> None:
    ctx = make_context()
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment())
    inside = (Path.cwd() / _SCRATCH_DIR / "kept.txt").resolve()

    result = await keep(invocation, {"source": "installer.exe", "destination": str(inside)})

    assert "resolves inside scratch" in result


async def test_keep_rejects_a_source_outside_scratch() -> None:
    ctx = make_context()
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment())

    result = await keep(invocation, {"source": "../outside.txt", "destination": str(_DEST)})

    assert "must resolve inside" in result


async def test_keep_reports_a_missing_source_file() -> None:
    clock = FakeClock()
    session = FakeSession(scratch_dir=_SCRATCH_DIR, clock=clock, allowed_paths=(_KEEP_ROOT,))
    ctx = make_context(
        clock=clock,
        session=session,
        lease=FakeLeaseView(_SCRATCH_DIR, allowed_paths=(_KEEP_ROOT,)),
        capabilities=_capabilities(f"fs:write:{_KEEP_ROOT.as_posix()}/**"),
        capping=_gate(session, keep_root=_KEEP_ROOT),
    )
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment(clock=clock))

    result = await keep(invocation, {"source": "never-written.exe", "destination": str(_DEST)})

    assert "no file at" in result


async def test_keep_moves_a_file_declared_under_keep_root_and_it_remains() -> None:
    clock = FakeClock()
    session = FakeSession(scratch_dir=_SCRATCH_DIR, clock=clock, allowed_paths=(_KEEP_ROOT,))
    await session.put_file(Path("installer.exe"), b"checker bytes")
    leaves = (PlannedLeaving(pattern=str(_DEST), reason="the goal asks for it to remain"),)
    ctx = make_context(
        clock=clock,
        session=session,
        lease=FakeLeaseView(_SCRATCH_DIR, allowed_paths=(_KEEP_ROOT,)),
        capabilities=_capabilities(f"fs:write:{_KEEP_ROOT.as_posix()}/**"),
        capping=_gate(session, keep_root=_KEEP_ROOT, leaves=leaves),
    )
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment(clock=clock, leaves=leaves))

    result = await keep(invocation, {"source": "installer.exe", "destination": str(_DEST)})

    assert "state=VERIFIED" in result
    assert "will remain" in result
    assert (await session.get_file(_DEST)) == b"checker bytes"
    with pytest.raises(FileNotFoundError):
        await session.get_file(Path("installer.exe"))  # keep() moves the source, never copies it.


async def test_keep_applies_but_will_be_removed_for_an_undeclared_destination() -> None:
    """Roadmap 5.0b's hard rule: undeclared is DENY even for a path under keep_root."""
    clock = FakeClock()
    session = FakeSession(scratch_dir=_SCRATCH_DIR, clock=clock, allowed_paths=(_KEEP_ROOT,))
    await session.put_file(Path("installer.exe"), b"checker bytes")
    ctx = make_context(
        clock=clock,
        session=session,
        lease=FakeLeaseView(_SCRATCH_DIR, allowed_paths=(_KEEP_ROOT,)),
        capabilities=_capabilities(f"fs:write:{_KEEP_ROOT.as_posix()}/**"),
        capping=_gate(session, keep_root=_KEEP_ROOT),  # No declared leaves at all.
    )
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment(clock=clock))

    result = await keep(invocation, {"source": "installer.exe", "destination": str(_DEST)})

    assert "state=VERIFIED" in result  # The write itself still happens and its postcondition holds.
    assert "will be removed on release" in result
    assert (await session.get_file(_DEST)) == b"checker bytes"  # Still there until release() runs.


async def test_keep_rejects_an_undeclared_destination_with_no_capability() -> None:
    """Roadmap 3.22 scenario (f)'s own shape: no capability at all is a plain REJECTED."""
    ctx = make_context()
    await ctx.session.put_file(Path("installer.exe"), b"checker bytes")
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment())

    result = await keep(
        invocation, {"source": "installer.exe", "destination": str(_OUTSIDE_UNDECLARED)}
    )

    assert "state=REJECTED" in result
