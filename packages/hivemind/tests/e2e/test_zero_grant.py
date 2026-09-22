"""End-to-end: a manifest reserve that zeroes the grant fails the goal in seconds, not a timeout.

`.claude/phase-4-handoff.md` section 4.2 item 1, found for real on 2026-09-20: low free RAM sized
a grant to `max_sub_bees = 0`; `hivemind.queen.dispatcher._send_grant_and_assign` sent it to the
Warden anyway, the Warden raised GRANT_EXCEEDED ("allows zero sub-bees"), the Queen escalated that
to the human inbox, and the task sat RUNNING for the whole 900s timeout with no Drone and nothing
in `hive run`'s own view saying why. This is the one real-task proof (codingrules section 1's own
"prefer a real task over a synthetic one") that the fix holds one layer up from `tests.unit.queen.
test_queen_dispatch`'s own unit tests: a real manifest, loaded for real, run through the same
composition root `hive run` itself uses (`hivemind.cli.compose.build_hive`/`run_hive`/`run_goal`),
must resolve to a FAILED task with a readable reason in a handful of seconds, never a timeout.

`builders.cli.fake_manifest` exposes no `[forage.reserve]` knob of its own (that builder is
outside this dispatch's own file list to extend), so `tests.e2e.kernel_helpers.
set_forage_reserve_seats` patches the written manifest directly -- mirroring `set_budget_fraction`,
the same module's own established pattern for a one-off knob a shared builder does not carry. That
builder's own `_forage_section` already deliberately sets `[forage.map.<source>] seats = 4` to
*avoid* this exact trap for every other e2e scenario in this suite (its own docstring: a bare
`seats = 1` would always zero `max_sub_bees` against `RoyalReserve`'s default `seats = 1`); this
scenario claims all four of those seats through `[forage.reserve] seats = 4` instead, reproducing
the trap on purpose.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/phase-4-handoff.md section 4.2 item 1 for the defect, verbatim.
    - hivemind.queen.dispatcher for `_send_grant_and_assign`'s own zero-grant fix (module
      docstring), the choke point this scenario proves end to end.
    - tests.unit.queen.test_queen_dispatch for the unit-level proof of the same fix, including the
      resumed-task (`resume_from`) variant this e2e scenario does not repeat.
    - tests.e2e.kernel_helpers for `set_forage_reserve_seats`, this scenario's own manifest patch.
    - tests.e2e.test_phase4_exit_criteria for `test_phase_3_scenarios_pass_at_a_quarter_budget`,
      the plain-`build_hive`-over-`asyncio.run` shape this scenario mirrors.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.cli import fake_manifest
from e2e.kernel_helpers import HaikuScript, default_worker_turn, set_forage_reserve_seats

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.cli.compose import GoalReport, Hive, build_hive, run_goal, run_hive
from hivemind.manifest import load_manifest
from hivemind.pheromone import TrailQuery
from waggle.clock import SystemClock

pytestmark = pytest.mark.e2e

_GOAL = "write a haiku"
# Generous relative to how fast this actually resolves -- the fix denies the grant and fails the
# task within the same dispatch call submit_goal makes (module docstring), so run_goal's own poll
# loop sees FAILED on close to its first iteration -- but still far under the old symptom: the
# task used to sit RUNNING for the manifest's own grant lifetime/goal timeout instead.
_TIMEOUT_S = 10.0
# builders.cli's own _forage_section writes exactly one [forage.map] source with `seats = 4`
# (that function's own docstring); claiming all four here through [forage.reserve] is what
# reproduces the fixture trap on purpose rather than sidestepping it.
_RESERVE_SEATS_THAT_EXHAUST_THE_MAP = 4


def test_a_manifest_reserve_that_zeroes_the_grant_fails_the_goal_in_seconds(tmp_path: Path) -> None:
    """A grant denied at dispatch time ends the goal FAILED in seconds, not after `_TIMEOUT_S`.

    A plain (non-async) test, like every other manifest-driven scenario in this suite
    (`test_phase4_exit_criteria.py`'s own `test_phase_3_scenarios_pass_at_a_quarter_budget`):
    `build_hive`'s own default `stores=None` opens real SQLite via its own internal `asyncio.run`,
    which must run outside any event loop.
    """
    manifest_path = fake_manifest(tmp_path, capabilities="full")
    set_forage_reserve_seats(manifest_path, _RESERVE_SEATS_THAT_EXHAUST_THE_MAP)
    manifest = load_manifest(manifest_path, {})
    script = HaikuScript(default_worker_turn)
    hive = build_hive(
        manifest, environ={}, clock=SystemClock(), responders={"fake": script.responder}
    )

    report = asyncio.run(_run_goal_and_check_the_trail(hive))

    # The operator-visible half of the fix: hive run ends fast, with the cause on the task itself,
    # not a timeout that leaves an operator guessing why nothing ran.
    assert report.timed_out is False
    assert report.elapsed_s < _TIMEOUT_S / 2  # Denied at dispatch time, not after any real wait.
    assert report.succeeded is False
    assert len(report.tasks) == 1
    task = report.tasks[0]
    assert task.status is TaskStatus.FAILED
    assert task.outcome is not None
    # The exact text hivemind.cli.run._print_summary would put on an operator's own screen, under
    # the task's title (Task.outcome.summary is what that command prints there).
    assert "zero sub-bees" in task.outcome.summary


async def _run_goal_and_check_the_trail(hive: Hive) -> GoalReport:
    """Run `hive`'s one goal to its terminal status, and assert the trail carries the cause."""
    async with run_hive(hive):
        report = await run_goal(hive, _GOAL, clearance=HoneyClearance.C1, timeout_s=_TIMEOUT_S)
        events = await hive.stores.trail.query(TrailQuery())
    kinds = [event.kind for event in events]
    # forage.denied carries the allocator's own reason plus the figures that produced zero
    # (hivemind.queen.dispatcher._deny_zero_grant); task.failed is what ends the goal.
    assert "forage.denied" in kinds
    assert "task.failed" in kinds
    return report
