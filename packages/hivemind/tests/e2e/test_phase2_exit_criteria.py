"""End-to-end test for the phase 2 exit criteria: CLI submit, chamber-driven advance, trail merge.

Fits into the Hive:
    Exercises `hivemind.cli.tasks`, `hivemind.cli.trail` and `hivemind.brood_chamber.chamber`
    together, the way `scripts/brood_demo.py` exercises them as real child processes; this module
    stays in-process (`typer.testing.CliRunner` for the CLI steps) so it runs inside pytest's own
    event loop instead of spawning `uv run hive` subprocesses.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md phase 2 exit criteria, the two things this file proves.
    - scripts/brood_demo.py for the process-boundary version of this same scenario.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from builders.cli import fake_manifest
from typer.testing import CliRunner

from hivemind.brood_chamber import (
    BroodChamber,
    ChamberIdentity,
    SqliteTaskStore,
    TaskOutcome,
    TaskStatus,
)
from hivemind.brood_chamber.questions import Answer, AnswerSource
from hivemind.cell import HoneyClearance
from hivemind.cli.app import app
from hivemind.cli.stores import open_chamber, open_trail
from hivemind.common.sqlite import connect
from hivemind.pheromone import SqlitePheromoneTrail, TrailQuery
from waggle.clock import FakeClock, SystemClock
from waggle.ids import (
    HiveId,
    NodeId,
    TaskId,
    new_cell_id,
    new_hive_id,
    new_node_id,
    new_warden_id,
    new_worker_id,
)

_STEP_SECONDS = 1.0  # Advances the driving FakeClock between transitions so trail order is exact.

pytestmark = pytest.mark.e2e

runner = CliRunner()

_POSTCONDITION: dict[str, object] = {
    "kind": "FILE_EXISTS",
    "subject": "scratch/done.txt",
    "argv": [],
    "expected": None,
}
# plan -> build -> verify, matching scripts/brood_demo.py's own graph shape.
_GRAPH = {
    "tasks": [
        {"key": "plan", "title": "Plan", "objective": "Plan it.", "acceptance": [_POSTCONDITION]},
        {
            "key": "build",
            "title": "Build",
            "objective": "Build it.",
            "acceptance": [_POSTCONDITION],
            "depends_on": ["plan"],
        },
        {
            "key": "verify",
            "title": "Verify",
            "objective": "Verify it.",
            "acceptance": [_POSTCONDITION],
            "depends_on": ["plan", "build"],
        },
    ]
}

# The complete kind sequence a fully driven `plan` task's trail must show, in order.
_EXPECTED_PLAN_KINDS = (
    "task.submitted",
    "task.assigned",
    "task.started",
    "task.progressed",
    "task.blocked",
    "task.answered",
    "task.paused",
    "task.resumed",
    "task.succeeded",
)


def _submit_via_cli(hive_dir: Path, hive_id: str, node_id: str) -> dict[str, str]:
    """Write the graph file and submit it through the CLI; return key -> minted TaskId.

    `hive_dir` is one Hive's own directory: `hive tasks submit` takes no `--db`
    (codingrules 5.1's parameter cap), so each Hive in a test needs its own manifest,
    and fake_manifest writes one per directory with `[hive] db` under it.
    """
    hive_dir.mkdir(parents=True, exist_ok=True)
    graph_file = hive_dir / "graph.json"
    graph_file.write_text(json.dumps(_GRAPH), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "tasks",
            "submit",
            str(graph_file),
            "--hive-id",
            hive_id,
            "--node-id",
            node_id,
            "--manifest",
            str(fake_manifest(hive_dir)),
        ],
    )
    assert result.exit_code == 0

    ids: dict[str, str] = {}
    for line in result.stdout.strip().splitlines():
        key, task_id, status = line.split("\t")
        assert status == "PENDING"
        ids[key] = task_id
    return ids


async def _open_driving_chamber(
    db: Path, identity: ChamberIdentity, clock: FakeClock
) -> BroodChamber:
    """Build a BroodChamber directly (not through `open_chamber`) so a FakeClock drives it.

    A FakeClock only moves on `advance()`, which is what lets the caller guarantee every
    transition below lands at a strictly later `at` than the one before it -- trail order across
    two events that share the same `at` and `node_id` is documented as approximate
    (codingrules section 12), so a deterministic assertion on the exact kind sequence needs
    strictly increasing timestamps rather than SystemClock's sub-millisecond bursts.
    """
    connection = connect(db)
    await SqlitePheromoneTrail.create(connection, clock)
    store = await SqliteTaskStore.create(connection, clock)
    return BroodChamber(store, clock, identity)


async def _drive_plan_through_every_transition(
    chamber: BroodChamber, task_id: str, clock: FakeClock
) -> None:
    """Advance `plan` through every transition the exit criteria require.

    BLOCKED and PAUSED included, ending SUCCEEDED.
    """
    warden_id, cell_id = new_warden_id(clock), new_cell_id(clock)
    await chamber.assign(TaskId(task_id), warden_id, cell_id, reason="placement")
    clock.advance(_STEP_SECONDS)
    await chamber.start(TaskId(task_id))
    clock.advance(_STEP_SECONDS)
    await chamber.report_progress(TaskId(task_id), "working on it", fraction_done=0.3)
    clock.advance(_STEP_SECONDS)
    question = await chamber.ask(TaskId(task_id), new_worker_id(clock), "which approach?")
    clock.advance(_STEP_SECONDS)
    answer = Answer(
        text="the first one",
        chosen_option=None,
        source=AnswerSource.HUMAN,
        clearance=HoneyClearance.C2,
        answered_at=clock.now(),
    )
    await chamber.answer(question.id, answer)
    clock.advance(_STEP_SECONDS)
    await chamber.pause(TaskId(task_id), reason="provider outage")
    clock.advance(_STEP_SECONDS)
    await chamber.resume(TaskId(task_id), reason="provider back")
    clock.advance(_STEP_SECONDS)
    outcome = TaskOutcome(status=TaskStatus.SUCCEEDED, summary="done", verified_by=warden_id)
    await chamber.complete(TaskId(task_id), outcome)


async def _drive_build_then_cancel_verify(
    chamber: BroodChamber, ids: dict[str, str], clock: FakeClock
) -> None:
    """Assign, start and complete `build`, confirm `verify` becomes ready, then cancel it."""
    ready_after_plan = await chamber.next_ready()
    assert ready_after_plan is not None
    assert ready_after_plan.id == ids["build"]

    warden_id, cell_id = new_warden_id(clock), new_cell_id(clock)
    await chamber.assign(TaskId(ids["build"]), warden_id, cell_id, reason="placement")
    clock.advance(_STEP_SECONDS)
    await chamber.start(TaskId(ids["build"]))
    clock.advance(_STEP_SECONDS)
    outcome = TaskOutcome(status=TaskStatus.SUCCEEDED, summary="done", verified_by=warden_id)
    await chamber.complete(TaskId(ids["build"]), outcome)
    clock.advance(_STEP_SECONDS)

    ready_after_build = await chamber.next_ready()
    assert ready_after_build is not None
    assert ready_after_build.id == ids["verify"]
    await chamber.cancel(TaskId(ids["verify"]), reason="no longer needed")


def _assert_restart_sees_identical_state(
    db: Path, hive_id: str, node_id: str, ids: dict[str, str]
) -> None:
    """Reopen the store on the same file (a fresh BroodChamber, a "restart") and re-check state."""
    identity = ChamberIdentity(hive_id=HiveId(hive_id), node_id=NodeId(node_id), actor="human")
    reopened = open_chamber(db, identity)

    plan_after = asyncio.run(reopened.get(TaskId(ids["plan"])))
    assert plan_after.status is TaskStatus.SUCCEEDED
    build_after = asyncio.run(reopened.get(TaskId(ids["build"])))
    assert build_after.status is TaskStatus.SUCCEEDED
    verify_after = asyncio.run(reopened.get(TaskId(ids["verify"])))
    assert verify_after.status is TaskStatus.CANCELLED

    # And through the CLI, which also opens a brand-new store on the same file.
    shown = runner.invoke(app, ["tasks", "show", ids["plan"], "--db", str(db)])
    assert json.loads(shown.stdout)["status"] == "SUCCEEDED"


def _assert_trail_is_complete_for_plan(db: Path, plan_id: str) -> None:
    """Check the merged/restarted trail carries every expected kind, for `plan`, in order."""
    trail = open_trail(db)
    events = asyncio.run(trail.query(TrailQuery(subject_id=plan_id, limit=1000)))
    assert tuple(event.kind for event in events) == _EXPECTED_PLAN_KINDS


def test_submit_advance_restart_and_see_identical_state_and_a_complete_trail(
    tmp_path: Path,
) -> None:
    # One Hive, in its own directory, so the manifest fake_manifest writes names this db.
    db = tmp_path / "data" / "hive.sqlite3"
    system_clock = SystemClock()
    hive_id, node_id = str(new_hive_id(system_clock)), str(new_node_id(system_clock))

    ids = _submit_via_cli(tmp_path, hive_id, node_id)

    identity = ChamberIdentity(hive_id=HiveId(hive_id), node_id=NodeId(node_id), actor="human")
    # Seeded from the real submit time so every driven event's `at` still lands after each
    # task's own `created_at`; see _open_driving_chamber's docstring for why a FakeClock drives
    # the rest of this scenario instead of open_chamber's own SystemClock.
    clock = FakeClock(start=system_clock.now())
    chamber = asyncio.run(_open_driving_chamber(db, identity, clock))
    asyncio.run(_drive_plan_through_every_transition(chamber, ids["plan"], clock))
    asyncio.run(_drive_build_then_cancel_verify(chamber, ids, clock))

    _assert_restart_sees_identical_state(db, hive_id, node_id, ids)
    _assert_trail_is_complete_for_plan(db, ids["plan"])


def test_two_trail_segments_merge_into_one_ordered_log_with_no_duplicates(tmp_path: Path) -> None:
    # Two nodes of one Hive, each in its own directory with its own manifest and database,
    # so the segments this test merges really do come from two separate trails.
    dir_a, dir_b = tmp_path / "node_a", tmp_path / "node_b"
    db_a, db_b = dir_a / "data" / "hive.sqlite3", dir_b / "data" / "hive.sqlite3"
    clock = SystemClock()
    hive_id = str(new_hive_id(clock))
    node_a, node_b = str(new_node_id(clock)), str(new_node_id(clock))
    _submit_via_cli(dir_a, hive_id, node_a)
    _submit_via_cli(dir_b, hive_id, node_b)

    segment_b_file = tmp_path / "segment_b.json"
    exported = runner.invoke(
        app, ["trail", "export", node_b, str(segment_b_file), "--db", str(db_b)]
    )
    assert exported.exit_code == 0

    merged = runner.invoke(app, ["trail", "merge", str(segment_b_file), "--db", str(db_a)])
    assert merged.exit_code == 0
    assert "inserted 3 events" in merged.stdout  # node_b's own three task.submitted events

    trail_a = open_trail(db_a)
    events = asyncio.run(trail_a.query(TrailQuery(limit=1000)))
    ids_seen = [event.id for event in events]
    assert len(ids_seen) == len(set(ids_seen))  # no duplicates
    assert {event.node_id for event in events} == {node_a, node_b}
    # Trail order is (at, node_id) with ties in recorded order; the id is never a tiebreaker.
    ordering_key = [(event.at, event.node_id) for event in events]
    assert ordering_key == sorted(ordering_key)

    merged_again = runner.invoke(app, ["trail", "merge", str(segment_b_file), "--db", str(db_a)])
    assert "inserted 0 events" in merged_again.stdout
