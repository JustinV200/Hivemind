"""Unit tests for the Guard's floors in front of run_command, write_file and keep (roadmap 10.3a).

The Capping gate checks a command's `exec` and a write's `fs:write` against the held set, so these
tools ask the Guard's floors alone first (`hivemind.workers.tools.authorize.floor_refusal_text`):
a command that runs the Hive's own entry point, and a write or a keep that lands on the Hive's own
state, are refused as `guard.denied` before anything is proposed, whatever the Worker holds
(ADR-0033). What the floors leave alone still goes through the gate exactly as before.

Fits into the Hive:
    Mirrors src/hivemind/workers/tools/session.py and keep.py (codingrules section 5.1: one
    concern's tests split by feature, here the floors both tools now share).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.tools.session and .keep for the modules under test.
    - hivemind.guard.policy.floors.hive_state for the floor that refuses.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from builders.workers import make_assignment, make_context

from hivemind.cell import CellIdentity
from hivemind.guard import CapabilitySet, Enforcer, load_guard_policy
from hivemind.guard.policy import HiveState
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.workers.context import WorkerContext
from hivemind.workers.tools.keep import keep
from hivemind.workers.tools.registry import ToolInvocation
from hivemind.workers.tools.session import run_command, write_file

# The Hive's own state, as a composition root would state it, under an absolute test directory.
_STATE_DIR = Path("hive-state").resolve()
_DB = _STATE_DIR / "hive.db"
_EVERYTHING = CapabilitySet.parse(
    "fs:read:**", "fs:write:**", "cell:outside_scratch:**", "exec:*", "tool:*"
)


def _context() -> WorkerContext:
    """A Worker holding every Cell effect, whose Enforcer knows the Hive's own state paths."""
    base = make_context(capabilities=_EVERYTHING)
    policy = dataclasses.replace(
        load_guard_policy(), hive_state=HiveState.of(db=_DB, secrets_dir=_STATE_DIR / "secrets")
    )
    identity = CellIdentity(
        hive_id=base.identity.hive_id, node_id=base.identity.node_id, actor="system"
    )
    return dataclasses.replace(base, enforcer=Enforcer(policy, base.trail, base.clock, identity))


def _invocation(ctx: WorkerContext) -> ToolInvocation:
    """This Worker's invocation for one tool call."""
    return ToolInvocation(ctx=ctx, assignment=make_assignment())


async def _kinds(ctx: WorkerContext) -> list[str]:
    """Every trail kind this call left, oldest first."""
    return [event.kind for event in await ctx.trail.query(TrailQuery())]


async def _denial(ctx: WorkerContext) -> PheromoneEvent:
    """The one `guard.denied` row this call left."""
    [denial] = await ctx.trail.query(TrailQuery(kind="guard.denied"))
    return denial


@pytest.mark.parametrize(
    "program", ["hive", "/opt/hive/.venv/bin/hive", "hivemind-in-cell", r"C:\venv\hive.exe"]
)
async def test_run_command_refuses_the_hives_own_entry_points_before_proposing(
    program: str,
) -> None:
    ctx = _context()

    result = await run_command(_invocation(ctx), {"argv": [program, "tasks", "list"]})

    assert result.startswith("refused by the Guard (guard.state_floor.entry_points)")
    assert "capping.proposed" not in await _kinds(ctx)
    assert (await _denial(ctx)).payload["capability"] == f"exec:{program}"


async def test_run_command_leaves_any_other_program_to_the_gate() -> None:
    # FakeSession answers an unscripted command with exit 127: the gate ran it, the floors let it.
    ctx = _context()

    result = await run_command(_invocation(ctx), {"argv": ["git", "status"]})

    assert "state=ROLLED_BACK" in result
    assert "guard.denied" not in await _kinds(ctx)


@pytest.mark.parametrize("target", [_DB, Path(f"{_DB}-wal"), _STATE_DIR / "secrets" / "k"])
async def test_write_file_refuses_the_hives_own_state_before_reading_or_proposing(
    target: Path,
) -> None:
    ctx = _context()

    result = await write_file(_invocation(ctx), {"path": str(target), "content": "x"})

    assert result.startswith("refused by the Guard (guard.state_floor.state_paths)")
    assert "capping.proposed" not in await _kinds(ctx)
    assert (await _denial(ctx)).payload["point"] == "session_outside_scratch"


async def test_write_file_inside_scratch_is_untouched_by_the_floors() -> None:
    ctx = _context()

    result = await write_file(_invocation(ctx), {"path": "notes.txt", "content": "x"})

    assert "state=VERIFIED" in result


async def test_keep_refuses_a_destination_that_is_the_hives_own_state() -> None:
    ctx = _context()
    await ctx.session.put_file(Path("payload.bin"), b"data")

    result = await keep(_invocation(ctx), {"source": "payload.bin", "destination": str(_DB)})

    assert result.startswith("refused by the Guard (guard.state_floor.state_paths)")
    assert "capping.proposed" not in await _kinds(ctx)
    assert (await _denial(ctx)).payload["point"] == "session_outside_scratch"
