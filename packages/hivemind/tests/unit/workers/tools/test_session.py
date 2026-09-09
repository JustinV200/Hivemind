"""Unit tests for hivemind.workers.tools.session: run_command, read_file, write_file."""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.capping import FakeLeaseView
from builders.workers import make_assignment, make_context

from hivemind.cell import CompletedCommand, PathNotAllowedError
from hivemind.cell.fake import FakeSession
from hivemind.guard import CapabilitySet
from hivemind.workers.tools.errors import UnreachablePathError
from hivemind.workers.tools.registry import ToolInvocation
from hivemind.workers.tools.session import (
    MAX_TOOL_RESULT_CHARS,
    read_file,
    run_command,
    write_file,
)
from waggle.clock import FakeClock

_SCRATCH_DIR = Path("scratch")
_OUTSIDE_PATH = "../outside/note.txt"  # Resolves outside _SCRATCH_DIR via its own ".." segment.
# Resolved once at import time (a plain Path.resolve(), not inside an async test body: ASYNC240
# flags a blocking pathlib call directly inside an async function).
_OUTSIDE_DIR = Path("outside").resolve()


async def test_write_file_inside_scratch_is_verified_and_creates_the_file() -> None:
    ctx = make_context()
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment())
    haiku = "old pond\na frog jumps in\nsound of water"

    result = await write_file(invocation, {"path": "haiku1.txt", "content": haiku})

    assert "state=VERIFIED" in result
    data = await ctx.session.get_file(Path("haiku1.txt"))
    assert data.decode("utf-8") == haiku


async def test_write_file_overwrites_existing_content_with_a_replacement_hunk() -> None:
    ctx = make_context()
    await ctx.session.put_file(Path("note.txt"), b"first draft")
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment())

    result = await write_file(invocation, {"path": "note.txt", "content": "second draft"})

    assert "state=VERIFIED" in result
    data = await ctx.session.get_file(Path("note.txt"))
    assert data.decode("utf-8") == "second draft"


async def test_write_file_outside_scratch_without_capability_is_rejected() -> None:
    """Roadmap 3.22 scenario (f): an outside-scratch write with no capability is rejected."""
    ctx = make_context()
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment())

    result = await write_file(invocation, {"path": _OUTSIDE_PATH, "content": "should not land"})

    assert "state=REJECTED" in result
    with pytest.raises((FileNotFoundError, PathNotAllowedError)):
        await ctx.session.get_file(Path(_OUTSIDE_PATH))


async def test_write_file_outside_scratch_with_capability_records_the_restore_path() -> None:
    clock = FakeClock()
    session = FakeSession(scratch_dir=_SCRATCH_DIR, clock=clock, allowed_paths=(_OUTSIDE_DIR,))
    lease = FakeLeaseView(_SCRATCH_DIR, allowed_paths=(_OUTSIDE_DIR,))
    capabilities = CapabilitySet.parse(
        f"fs:write:{_SCRATCH_DIR.as_posix()}/**",
        "fs:read:**",
        "exec:*",
        "tool:*",
        f"fs:write:{_OUTSIDE_DIR.as_posix()}/**",
    )
    ctx = make_context(clock=clock, session=session, lease=lease, capabilities=capabilities)
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment(clock=clock))

    result = await write_file(invocation, {"path": _OUTSIDE_PATH, "content": "allowed"})

    assert "state=VERIFIED" in result
    assert lease.restore_records  # the outside-scratch write recorded what to restore on release()


async def test_run_command_is_verified_when_the_scripted_exit_code_is_zero() -> None:
    clock = FakeClock()
    session = FakeSession(
        scratch_dir=_SCRATCH_DIR,
        clock=clock,
        responder={"true": CompletedCommand(exit_code=0, stdout=b"", stderr=b"", duration_s=0.0)},
    )
    ctx = make_context(clock=clock, session=session)
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment(clock=clock))

    result = await run_command(invocation, {"argv": ["true"]})

    assert "state=VERIFIED" in result


async def test_run_command_names_the_exit_code_when_the_gate_rolls_back() -> None:
    # FakeSession's default responder answers every unscripted command with exit code 127.
    ctx = make_context()
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment())

    result = await run_command(invocation, {"argv": ["not-a-real-command"]})

    assert "state=ROLLED_BACK" in result
    assert "command exited 127" in result


async def test_run_command_rejects_a_malformed_argv_without_proposing() -> None:
    ctx = make_context()
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment())

    result = await run_command(invocation, {"argv": []})

    assert result == "argv must be a non-empty list of strings."


async def test_read_file_reads_scratch_with_no_extra_capability_needed() -> None:
    ctx = make_context()
    await ctx.session.put_file(Path("note.txt"), b"hello world")
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment())

    result = await read_file(invocation, {"path": "note.txt"})

    assert result == "hello world"


async def test_read_file_outside_scratch_requires_an_fs_read_capability() -> None:
    # Reachable at the session/lease level (allowed_paths covers it) but withheld at the
    # capability level (no fs:read grant at all, not even the default "fs:read:**" wildcard), so
    # the failure is specifically the capability check, not the session's own reachability one.
    clock = FakeClock()
    session = FakeSession(scratch_dir=_SCRATCH_DIR, clock=clock, allowed_paths=(_OUTSIDE_DIR,))
    capabilities = CapabilitySet.parse(f"fs:write:{_SCRATCH_DIR.as_posix()}/**", "exec:*", "tool:*")
    ctx = make_context(clock=clock, session=session, capabilities=capabilities)
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment(clock=clock))

    result = await read_file(invocation, {"path": _OUTSIDE_PATH})

    assert "no fs:read capability covers" in result


async def test_read_file_outside_scratch_unreachable_from_the_session_raises() -> None:
    # Neither reachable at the session level nor granted a capability: this is the harder session
    # boundary UnreachablePathError names, distinct from the capability-only rejection above.
    ctx = make_context()  # Default fs:read:** grants everywhere, so this proves the session limit.
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment())

    with pytest.raises(UnreachablePathError):
        await read_file(invocation, {"path": _OUTSIDE_PATH})


async def test_read_file_reports_a_missing_file() -> None:
    ctx = make_context()
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment())

    result = await read_file(invocation, {"path": "never-written.txt"})

    assert "no file at" in result


async def test_read_file_truncates_long_content_with_a_marker() -> None:
    ctx = make_context()
    long_text = "x" * (MAX_TOOL_RESULT_CHARS + 500)
    await ctx.session.put_file(Path("big.txt"), long_text.encode("utf-8"))
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment())

    result = await read_file(invocation, {"path": "big.txt"})

    assert len(result) < len(long_text)
    assert "truncated" in result
