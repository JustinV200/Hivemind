"""Smoke test for scripts/brood_demo.py: run its milestone functions in-process, no subprocess.

Fits into the Hive:
    Layer: none (a test for a demo script). Calls brood_demo's STEP 2 and STEP 4 functions
    directly against a tmp_path SQLite database, through hivemind.cli.stores.open_chamber/
    open_trail (plain local functions, not subprocesses), so the default test run stays fast
    (roadmap step 2.9's brief); running the real `uv run --frozen hive ...` child processes
    STEP 1 and STEP 3 use is scripts/brood_demo.py's own job, exercised by actually running it as
    one of the phase 2 gates, not by this file.

Key invariants:
    - None: this module holds tests, not behaviour.

See Also:
    - scripts/brood_demo.py, the module under test.
"""

# codingrules 6.2: a sentence-shaped test name is its own docstring, and `assert` is how pytest
# reports failure. pyproject.toml's [tool.ruff.lint.per-file-ignores] grants S101/D103 to
# scripts/tests/**, so nothing is silenced here.

import asyncio
from pathlib import Path

import brood_demo

from hivemind.brood_chamber import ChamberIdentity, TaskGraphDraft
from hivemind.cli.stores import open_chamber, open_trail
from hivemind.pheromone import TrailQuery
from waggle.clock import FakeClock, SystemClock
from waggle.ids import new_hive_id, new_node_id


def _fresh_identity() -> ChamberIdentity:
    clock = SystemClock()
    return ChamberIdentity(hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system")


def test_step2_drives_plan_and_build_to_succeeded_and_cancels_verify(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"
    chamber = open_chamber(db, _fresh_identity())
    draft = TaskGraphDraft.model_validate(brood_demo.build_graph_draft_json())
    tasks = asyncio.run(chamber.submit(draft))
    ids = {"plan": str(tasks[0].id), "build": str(tasks[1].id), "verify": str(tasks[2].id)}

    recorder = brood_demo.Recorder()
    brood_demo._step2_drive_plan(chamber, ids["plan"], recorder)
    brood_demo._step2_drive_build_and_verify(chamber, ids, recorder)

    assert recorder.milestones  # something was actually checked, not an empty no-op run
    assert all(milestone.passed for milestone in recorder.milestones)


def test_step4_merge_building_blocks_report_an_ordered_duplicate_free_trail(
    tmp_path: Path,
) -> None:
    db = tmp_path / "hive.sqlite3"
    before = asyncio.run(open_trail(db).query(TrailQuery(limit=1000)))  # migrates db, empty trail

    db2 = tmp_path / "second_node.sqlite3"
    identity_b = _fresh_identity()
    asyncio.run(brood_demo._record_warden_events(db2, identity_b, FakeClock()))
    segment = asyncio.run(open_trail(db2).export_segment(identity_b.node_id))

    inserted = asyncio.run(open_trail(db).merge_segment(segment))
    assert inserted == 3

    recorder = brood_demo.Recorder()
    brood_demo._check_merged_trail_is_ordered_without_duplicates(db, before, recorder)

    assert recorder.milestones
    assert all(milestone.passed for milestone in recorder.milestones)

    # Idempotence, mirroring the full demo's own STEP 4 re-merge check.
    assert asyncio.run(open_trail(db).merge_segment(segment)) == 0
