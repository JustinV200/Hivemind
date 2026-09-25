"""Tests for hivemind.queen.ticks.guard_bee.tick: the Guard Bee's round on the Queen's own tick.

A Queen with no Guard Bee runs as she did; one with a Guard Bee runs its round from her
housekeeping, beside the House Bee's sweep; a failed round is logged and her tick carries on; and
nothing but the Guard Bee's own error is swallowed.

Fits into the Hive:
    Mirrors src/hivemind/queen/ticks/guard_bee/tick.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.guard_bee for the Guard Bee under the tick.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
import structlog
from builders.guard_bee import RecordingDoor, TrailSeeder, guard_bee_for_queen, seed_episode
from builders.queen import make_queen_deps

from hivemind.cell import CellIdentity
from hivemind.pheromone import MemoryPheromoneTrail, PheromoneEvent, TrailQuery
from hivemind.queen.deps import QueenDeps
from hivemind.queen.ticks.guard_bee import run_guard_bee
from hivemind.queen.ticks.housekeeping import run_housekeeping
from waggle.clock import FakeClock


class _BrokenTrail(MemoryPheromoneTrail):
    """A trail whose reads fail: every Guard Bee round over it fails."""

    def __init__(self, clock: FakeClock, error: BaseException) -> None:
        super().__init__(clock)
        self.error = error

    async def query(self, query: TrailQuery) -> tuple[PheromoneEvent, ...]:
        raise self.error


def _with_guard_bee(deps: QueenDeps, door: RecordingDoor) -> QueenDeps:
    return replace(deps, guard_bee=guard_bee_for_queen(deps, door))


async def _flagged_denial(deps: QueenDeps) -> None:
    """Record an injection flag then a refusal in one episode, on the Queen's own trail."""
    identity = CellIdentity(deps.identity.hive_id, deps.identity.node_id, "system")
    assert isinstance(deps.clock, FakeClock)
    seed = TrailSeeder(deps.trail, deps.clock, identity)
    episode = await seed_episode(seed)
    await seed.injection(episode.bee, episode.task)
    await seed.denied(episode.bee)


async def test_a_queen_without_a_guard_bee_records_nothing_for_one() -> None:
    deps, _, _ = make_queen_deps()
    await _flagged_denial(deps)
    before = await deps.trail.query(TrailQuery(limit=1_000))

    await run_guard_bee(deps)

    assert deps.guard_bee is None
    assert await deps.trail.query(TrailQuery(limit=1_000)) == before


async def test_her_housekeeping_runs_the_guard_bees_round() -> None:
    door = RecordingDoor()
    deps, _, _ = make_queen_deps()
    deps = _with_guard_bee(deps, door)
    await _flagged_denial(deps)

    await run_housekeeping(deps, (), deps.cluster_state)

    [filed] = door.filed
    assert filed.rule == "injection_then_denial"


async def test_a_failed_round_is_logged_and_her_tick_carries_on() -> None:
    clock = FakeClock()
    deps, _, _ = make_queen_deps(clock, trail=_BrokenTrail(clock, OSError("disk gone")))
    deps = _with_guard_bee(deps, RecordingDoor())

    with structlog.testing.capture_logs() as captured:
        await run_guard_bee(deps)

    [failed] = [entry for entry in captured if entry["event"] == "queen.guard_bee_round_failed"]
    assert failed["log_level"] == "error" and "OSError" in failed["error"]


async def test_cancellation_is_never_swallowed() -> None:
    clock = FakeClock()
    deps, _, _ = make_queen_deps(clock, trail=_BrokenTrail(clock, asyncio.CancelledError()))
    deps = _with_guard_bee(deps, RecordingDoor())

    with pytest.raises(asyncio.CancelledError):
        await run_guard_bee(deps)
