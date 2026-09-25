"""End-to-end: a grant with no bee waits out a busy moment, and is denied in seconds when it must.

`.claude/phase-4-handoff.md` section 4.2 item 1, found for real on 2026-09-20: low free RAM sized a
grant to `max_sub_bees = 0`; `hivemind.queen.dispatcher.ready.assign.send_grant_and_assign` sent it
to the Warden anyway, the Warden raised GRANT_EXCEEDED ("allows zero sub-bees"), the Queen escalated
that to the human inbox, and the task sat RUNNING for the whole 900s timeout with no Drone and
nothing in `hive run`'s own view saying why. The first scenario is the one real-task proof
(codingrules section 1's own "prefer a real task over a synthetic one") that the fix still holds one
layer up from `tests.unit.queen.test_queen_dispatch`'s own unit tests: a manifest whose reserve
claims every seat is a lasting shortfall, so the goal must resolve to FAILED with a readable reason
in a handful of seconds, never a timeout.

The other two are the zero-grant fix's own proof, on the same composition root `hive run` uses
(`hivemind.cli.compose.build_hive`/`run_hive`/`run_goal`) with the Hive Stand's real probe: the
host's one-minute load average is faked through `os.getloadavg` (the reading
`hivemind.cell.local.probe` takes on every dispatch pass) at nearly all of the Hive Stand's cores,
so a Drone's grant has no free core. A goal must then wait, one `forage.denied` with `deferred =
true` saying so, and finish once the load drops -- lowered by the test the moment that event
lands, through `run_goal`'s own `on_event` -- or, if the load never drops, fail with the figures
once `[forage] zero_grant_patience_s` runs out. Both run on a FakeClock driven by
`builders.cli.pump_until_done`, so no scenario sleeps to synchronise.

`builders.cli.fake_manifest` exposes no `[forage.reserve]` knob of its own, so `tests.e2e.
kernel_helpers.set_forage_reserve_seats` patches the written manifest directly; that builder's own
`_forage_section` sets `[forage.map.<source>] seats = 4` to *avoid* the reserve trap for every
other scenario in this suite, and the first scenario claims all four through `[forage.reserve]
seats = 4` on purpose.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/phase-4-handoff.md section 4.2 item 1 for the defect, verbatim.
    - hivemind.queen.dispatcher.zero_grant for the wait-or-deny rule these scenarios prove.
    - tests.unit.queen.dispatcher.test_ready_waits for the unit-level proof of every cause.
    - tests.e2e.kernel_helpers for `set_forage_reserve_seats` and `HaikuScript`.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from builders.cli import HIVE_STAND_CORES, fake_manifest, pump_until_done
from e2e.kernel_helpers import HaikuScript, default_worker_turn, set_forage_reserve_seats

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.cli.compose import GoalReport, Hive, build_hive, run_goal, run_hive
from hivemind.manifest import load_manifest
from hivemind.pheromone import PheromoneEvent, TrailQuery
from waggle.clock import FakeClock, SystemClock

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
# A one-minute load of all but a tenth of the Hive Stand's pinned cores: 0.1 cores free, under
# the half core fake_manifest's Drone needs. Then the busy moment passes: nearly every core free.
_BUSY_LOAD = HIVE_STAND_CORES - 0.1
_QUIET_LOAD = 1.0
# FakeClock seconds a waiting goal may take, well inside pump_until_done's own 40 fake seconds.
_WAIT_TIMEOUT_S = 30.0
# A patience a FakeClock pump crosses in fifty steps, standing in for the manifest's five minutes.
_SHORT_PATIENCE_S = 1.0


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
    # (hivemind.queen.dispatcher.zero_grant.deny_zero_grant); task.failed is what ends the goal.
    assert "forage.denied" in kinds
    assert "task.failed" in kinds
    # A lasting shortfall never waits: its one denial is a final one.
    [denial] = [event for event in events if event.kind == "forage.denied"]
    assert denial.payload["deferred"] is False
    assert denial.payload["limited_by"] == "seats"
    return report


class _HostLoad:
    """The one-minute load average `os.getloadavg` reports here; the test moves it."""

    def __init__(self, load: float) -> None:
        """Start the host at `load`."""
        self.load = load

    def read(self) -> tuple[float, float, float]:
        """Return the load as `os.getloadavg` does: the 1-, 5- and 15-minute figures."""
        return (self.load, self.load, self.load)


def _busy_hive(tmp_path: Path, clock: FakeClock, patience_s: float | None = None) -> Hive:
    """Build a Hive over `fake_manifest`, optionally with a patience of `patience_s` seconds."""
    manifest_path = fake_manifest(tmp_path, clock=clock)
    if patience_s is not None:
        # A super-table after its sub-tables is valid TOML; fake_manifest writes no [forage] keys.
        text = manifest_path.read_text(encoding="utf-8")
        patch = f"\n[forage]\nzero_grant_patience_s = {patience_s}\n"
        manifest_path.write_text(text + patch, encoding="utf-8")
    script = HaikuScript(default_worker_turn)
    manifest = load_manifest(manifest_path, {})
    return build_hive(manifest, environ={}, clock=clock, responders={"fake": script.responder})


def test_a_goal_waits_out_a_busy_hive_stand_and_finishes_once_the_load_drops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The defect: at a load near its cores the Hive Stand used to fail every goal at once."""
    host = _HostLoad(_BUSY_LOAD)
    monkeypatch.setattr(os, "getloadavg", host.read, raising=False)  # Absent on Windows.
    clock = FakeClock()
    hive = _busy_hive(tmp_path, clock)

    report, events = asyncio.run(pump_until_done(clock, _wait_out_the_busy_moment(hive, host)))

    assert report.succeeded is True, report
    [wait] = [event for event in events if event.kind == "forage.denied"]
    assert wait.payload["deferred"] is True
    assert wait.payload["limited_by"] == "free_cores"
    assert wait.payload["patience_s"] == 300.0  # The manifest's own default.
    assert wait.payload["cores"] == HIVE_STAND_CORES
    assert wait.payload["cpu_load"] == pytest.approx(_BUSY_LOAD / HIVE_STAND_CORES)
    kinds = [event.kind for event in events]
    # It waited PENDING, placed nowhere, until the load dropped; then it ran like any other goal.
    assert kinds.index("forage.denied") < kinds.index("queen.placed")
    assert "forage.granted" in kinds
    assert "task.failed" not in kinds


async def _wait_out_the_busy_moment(
    hive: Hive, host: _HostLoad
) -> tuple[GoalReport, tuple[PheromoneEvent, ...]]:
    """Run the goal, easing the host's load the moment the Queen says the task waits."""

    def on_event(event: PheromoneEvent) -> None:
        """Let the busy moment pass once the wait is on the trail, as a real one would."""
        if event.kind == "forage.denied" and event.payload.get("deferred") is True:
            host.load = _QUIET_LOAD

    async with run_hive(hive):
        report = await run_goal(
            hive, _GOAL, clearance=HoneyClearance.C1, timeout_s=_WAIT_TIMEOUT_S, on_event=on_event
        )
        events = await hive.stores.trail.query(TrailQuery())
    return report, events


def test_a_hive_stand_that_stays_busy_fails_the_goal_with_the_figures_after_its_patience(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing waits for ever unseen: past the patience, the wait ends in a denial with figures."""
    # raising=False: Windows has no os.getloadavg; the probe reads it with getattr.
    monkeypatch.setattr(os, "getloadavg", _HostLoad(_BUSY_LOAD).read, raising=False)
    clock = FakeClock()
    hive = _busy_hive(tmp_path, clock, patience_s=_SHORT_PATIENCE_S)

    report, events = asyncio.run(pump_until_done(clock, _run_busy_goal(hive)))

    assert report.timed_out is False
    [task] = report.tasks
    assert task.status is TaskStatus.FAILED
    assert task.outcome is not None
    assert "after waiting" in task.outcome.summary
    wait, denial = [event for event in events if event.kind == "forage.denied"]
    assert wait.payload["deferred"] is True
    assert denial.payload["deferred"] is False
    assert denial.payload["limited_by"] == "free_cores"
    waited_s = denial.payload["waited_s"]
    assert isinstance(waited_s, float) and waited_s >= _SHORT_PATIENCE_S


async def _run_busy_goal(hive: Hive) -> tuple[GoalReport, tuple[PheromoneEvent, ...]]:
    """Run the goal to its end on a host whose load never drops, and read the trail after."""
    async with run_hive(hive):
        report = await run_goal(hive, _GOAL, clearance=HoneyClearance.C1, timeout_s=_WAIT_TIMEOUT_S)
        events = await hive.stores.trail.query(TrailQuery())
    return report, events
