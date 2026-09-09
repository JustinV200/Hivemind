"""Demonstrate the phase 2 exit criteria: CLI submit, chamber-driven advance, restart, trail merge.

Phase 2 (`.claude/roadmap.md`) exits when a small task graph can be submitted from a JSON file via
the `hive` CLI, advanced through every status (`BLOCKED` and `PAUSED` included) with a test driver,
survives a restart with identical state and a complete Pheromone Trail (the Hive's append-only
audit log), and when two trail segments (one node's slice of that log) from different node ids
merge into one ordered log with no duplicates. This script is that demonstration, run with no
arguments: it writes a three-task graph (`plan` -> `build` -> `verify`, `build` and `verify`
depending on `plan`) to a temporary directory, submits it as a real `uv run --frozen hive tasks
submit` child process (STEP 1), drives `plan` and `build` to `SUCCEEDED` and cancels `verify`
through a `BroodChamber` (`hivemind.brood_chamber`, the Queen's facade over the task store) built
in-process (STEP 2), "restarts" by running `hive tasks list`/`show` and `hive trail tail` as fresh
child processes and checking they see identical state and `plan`'s complete kind sequence (STEP 3),
and merges a second node's trail segment into the first database, checking the result is ordered
and duplicate-free (STEP 4). `scripts/tests/test_brood_demo.py` exercises the STEP 2 and STEP 4
logic in-process against a `tmp_path` database, without the child-process overhead, so the default
test run stays fast (roadmap step 2.9's brief).

Fits into the Hive:
    Layer: none (a demo, not shipped code). Run by a developer and by
    scripts/tests/test_brood_demo.py. Depends on the `hivemind` and `waggle` packages being
    installed in the interpreter that runs it, and on the `hive` console script for the
    child-process steps.

Key invariants:
    - Exits 0 only when every milestone passed, in order; prints the milestone table either way.
    - Every child process runs from a fixed argument list (never a shell string), each with a
      timeout, so a hung `hive` invocation cannot hang the demo forever.

See Also:
    - .claude/roadmap.md phase 2 exit criteria, the two things this script demonstrates.
    - packages/hivemind/tests/e2e/test_phase2_exit_criteria.py for the same scenario as a pytest
      test, in-process throughout.
    - scripts/waggle_echo.py for the phase 1 exit demo this script's milestone-table shape mirrors.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from hivemind.brood_chamber import BroodChamber, ChamberIdentity, TaskOutcome, TaskStatus
from hivemind.brood_chamber.questions import Answer, AnswerSource
from hivemind.cell import HoneyClearance
from hivemind.cli.stores import open_chamber, open_trail
from hivemind.common.sqlite import connect
from hivemind.pheromone import PheromoneEvent, SqlitePheromoneTrail, TrailQuery, WardenEvent
from waggle.clock import FakeClock, SystemClock
from waggle.ids import (
    HiveId,
    NodeId,
    TaskId,
    new_cell_id,
    new_event_id,
    new_hive_id,
    new_node_id,
    new_warden_id,
    new_worker_id,
)

HIVE_TIMEOUT_S = 60.0  # A cold `uv run --frozen hive ...` resolves and starts well within this.
_INTERLEAVE_S = (
    0.005  # FakeClock step for the second node's events, so they land between the first's.
)
_POSTCONDITION: dict[str, object] = {
    "kind": "FILE_EXISTS",
    "subject": "scratch/done.txt",
    "argv": [],
    "expected": None,
}
# The complete kind sequence a fully driven `plan` task's trail must show, in order (STEP 2/3).
EXPECTED_PLAN_KINDS = (
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

__all__ = ["main"]


def build_graph_draft_json() -> dict[str, object]:
    """Build the demo's three-task graph as a plain dict: `plan` -> `build` -> `verify`.

    Returns:
        A dict matching `hivemind.brood_chamber.task.model.TaskGraphDraft`'s JSON shape, with
        `build` depending on `plan` and `verify` depending on both.
    """
    return {
        "tasks": [
            {
                "key": "plan",
                "title": "Plan",
                "objective": "Plan it.",
                "acceptance": [_POSTCONDITION],
            },
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


class DemoError(Exception):
    """Raised when a milestone's condition is false; carries the failed milestone's own name."""


@dataclass(frozen=True, slots=True)
class Milestone:
    """One row of the summary table: a named condition and whether it held."""

    name: str
    passed: bool


@dataclass(slots=True)
class Recorder:
    """Every milestone checked so far, in order; `check` is the one way to add to it."""

    milestones: list[Milestone] = field(default_factory=list)

    def check(self, name: str, condition: bool) -> None:
        """Record `name` as passed or failed, raising DemoError the moment it fails.

        Args:
            name: A short, human-readable description of the condition, becomes a table row.
            condition: The condition itself, already evaluated by the caller.

        Raises:
            DemoError: `condition` is False; carries `name` so the failure is legible on stderr.
        """
        self.milestones.append(Milestone(name, condition))
        if not condition:
            raise DemoError(f"milestone failed: {name}")


def _run_hive(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run `uv run --frozen hive <args>` as a child process and return its completed result.

    Args:
        args: The `hive` subcommand and its own arguments, e.g. `["tasks", "list", "--db", ...]`.

    Returns:
        The completed process: `returncode`, `stdout` and `stderr` as text.
    """
    argv = ["uv", "run", "--frozen", "hive", *args]
    # SAFETY: scripts/ is one of the places codingrules section 4 allows subprocess; argv is a
    # fixed list built only from this function's own arguments, never a shell string, and every
    # call carries a timeout so a hung `hive` invocation cannot hang the demo forever.
    return subprocess.run(  # noqa: S603 -- fixed argv, not a shell string; SAFETY comment above
        argv, capture_output=True, text=True, check=False, timeout=HIVE_TIMEOUT_S
    )


# ──────────────────────────────────────────────────────────────────────────────
# STEP 2: drive the graph through the chamber, in-process. Reused as-is by
# scripts/tests/test_brood_demo.py, which builds its own tmp_path chamber directly.
# ──────────────────────────────────────────────────────────────────────────────


def _step2_drive_plan(chamber: BroodChamber, plan_id: str, recorder: Recorder) -> None:
    """Advance `plan` through assign, start, progress, ask/answer, pause/resume, complete."""
    clock = SystemClock()
    warden_id, cell_id = new_warden_id(clock), new_cell_id(clock)
    plan = asyncio.run(chamber.assign(TaskId(plan_id), warden_id, cell_id, reason="placement"))
    recorder.check("plan assigned", plan.status is TaskStatus.ASSIGNED)
    plan = asyncio.run(chamber.start(TaskId(plan_id)))
    recorder.check("plan started", plan.status is TaskStatus.RUNNING)
    plan = asyncio.run(chamber.report_progress(TaskId(plan_id), "working on it", fraction_done=0.4))
    recorder.check("plan progress reported", plan.fraction_done == 0.4)
    question = asyncio.run(chamber.ask(TaskId(plan_id), new_worker_id(clock), "which approach?"))
    blocked = asyncio.run(chamber.get(TaskId(plan_id)))
    recorder.check("plan blocked on its question", blocked.status is TaskStatus.BLOCKED)
    answer = _make_human_answer(clock)
    plan = asyncio.run(chamber.answer(question.id, answer))
    recorder.check("plan answered, running again", plan.status is TaskStatus.RUNNING)
    plan = asyncio.run(chamber.pause(TaskId(plan_id), reason="provider outage"))
    recorder.check("plan paused", plan.status is TaskStatus.PAUSED)
    plan = asyncio.run(chamber.resume(TaskId(plan_id), reason="provider back"))
    recorder.check("plan resumed", plan.status is TaskStatus.RUNNING)
    outcome = TaskOutcome(status=TaskStatus.SUCCEEDED, summary="done", verified_by=warden_id)
    plan = asyncio.run(chamber.complete(TaskId(plan_id), outcome))
    recorder.check("plan succeeded", plan.status is TaskStatus.SUCCEEDED)


def _make_human_answer(clock: SystemClock) -> Answer:
    """Build a plausible, valid human Answer to plan's question."""
    return Answer(
        text="the first one",
        chosen_option=None,
        source=AnswerSource.HUMAN,
        clearance=HoneyClearance.C2,
        answered_at=clock.now(),
    )


def _step2_drive_build_and_verify(
    chamber: BroodChamber, ids: dict[str, str], recorder: Recorder
) -> None:
    """Confirm `build` is next ready, complete it, confirm `verify` is next ready, cancel it."""
    ready = asyncio.run(chamber.next_ready())
    recorder.check(
        "next_ready returns build once plan succeeds",
        ready is not None and ready.id == ids["build"],
    )
    clock = SystemClock()
    warden_id, cell_id = new_warden_id(clock), new_cell_id(clock)
    asyncio.run(chamber.assign(TaskId(ids["build"]), warden_id, cell_id, reason="placement"))
    asyncio.run(chamber.start(TaskId(ids["build"])))
    outcome = TaskOutcome(status=TaskStatus.SUCCEEDED, summary="done", verified_by=warden_id)
    build = asyncio.run(chamber.complete(TaskId(ids["build"]), outcome))
    recorder.check("build succeeded", build.status is TaskStatus.SUCCEEDED)

    ready2 = asyncio.run(chamber.next_ready())
    recorder.check(
        "next_ready returns verify once build succeeds",
        ready2 is not None and ready2.id == ids["verify"],
    )
    verify = asyncio.run(chamber.cancel(TaskId(ids["verify"]), reason="not needed for the demo"))
    recorder.check("verify cancelled", verify.status is TaskStatus.CANCELLED)


# ──────────────────────────────────────────────────────────────────────────────
# STEP 4 building blocks: also reused in-process by scripts/tests/test_brood_demo.py.
# ──────────────────────────────────────────────────────────────────────────────


async def _record_warden_events(db2: Path, identity: ChamberIdentity, clock: FakeClock) -> None:
    """Record three warden.* events on a fresh second database, timestamped by `clock`.

    Args:
        db2: A database file that does not need to exist yet; migrated here.
        identity: The second node's own identity; every event is stamped with it.
        clock: Drives every event's `at`; the caller seeds and advances it so the events land
            wherever in the first database's timeline the demo wants them to interleave.
    """
    connection = connect(db2)
    trail = await SqlitePheromoneTrail.create(connection, clock)
    warden_id = new_warden_id(clock)
    for kind in ("warden.spawned", "warden.reconnected", "warden.stopped"):
        event = WardenEvent(
            id=new_event_id(clock),
            hive_id=identity.hive_id,
            node_id=identity.node_id,
            at=clock.now(),
            actor=identity.actor,
            kind=kind,
            subject_id=warden_id,
            payload={},
        )
        await trail.record(event)
        # Small enough to likely fall within the first database's own real-time-spaced (_tick)
        # gaps between consecutive events, so the two nodes' events genuinely interleave.
        clock.advance(_INTERLEAVE_S)


def _check_merged_trail_is_ordered_without_duplicates(
    db: Path, before: Sequence[PheromoneEvent], recorder: Recorder
) -> None:
    """Check the trail on `db` grew by exactly the second node's 3 events, ordered, no dupes."""
    after = asyncio.run(open_trail(db).query(TrailQuery(limit=1000)))
    recorder.check("merge added exactly the second node's 3 events", len(after) == len(before) + 3)
    ids_seen = [event.id for event in after]
    recorder.check("merged trail has no duplicate ids", len(ids_seen) == len(set(ids_seen)))
    ordering_key = [(event.at, event.node_id) for event in after]
    recorder.check("merged trail is ordered by (at, node_id)", ordering_key == sorted(ordering_key))


# ──────────────────────────────────────────────────────────────────────────────
# STEPs 1, 3, 4 (the child-process shell): the full demo only, not the smoke test.
# ──────────────────────────────────────────────────────────────────────────────


def _step1_submit(
    db: Path, graph_file: Path, hive_id: str, node_id: str, recorder: Recorder
) -> dict[str, str]:
    """Submit the graph file through the real `hive` CLI; return key -> minted TaskId."""
    result = _run_hive(
        [
            "tasks",
            "submit",
            str(graph_file),
            "--hive-id",
            hive_id,
            "--node-id",
            node_id,
            "--db",
            str(db),
        ]
    )
    recorder.check("cli submit exits 0", result.returncode == 0)
    lines = result.stdout.strip().splitlines()
    recorder.check("cli submit prints one line per task", len(lines) == 3)
    ids = {line.split("\t")[0]: line.split("\t")[1] for line in lines}
    recorder.check(
        "submitted tasks are all PENDING", all(line.split("\t")[2] == "PENDING" for line in lines)
    )
    return ids


def _step3_restart(db: Path, ids: dict[str, str], recorder: Recorder) -> None:
    """Run `hive tasks list|show` and `hive trail tail` as fresh child processes; check state."""
    listed = _run_hive(["tasks", "list", "--db", str(db)])
    recorder.check("cli list exits 0", listed.returncode == 0)
    plan_row = next(line for line in listed.stdout.splitlines() if line.startswith(ids["plan"]))
    recorder.check("cli list shows plan SUCCEEDED", "SUCCEEDED" in plan_row)
    verify_row = next(line for line in listed.stdout.splitlines() if line.startswith(ids["verify"]))
    recorder.check("cli list shows verify CANCELLED", "CANCELLED" in verify_row)

    shown = _run_hive(["tasks", "show", ids["plan"], "--db", str(db)])
    recorder.check("cli show exits 0", shown.returncode == 0)
    recorder.check(
        "cli show reports plan SUCCEEDED", json.loads(shown.stdout)["status"] == "SUCCEEDED"
    )

    tailed = _run_hive(["trail", "tail", "--db", str(db), "-n", "100"])
    recorder.check("cli trail tail exits 0", tailed.returncode == 0)
    plan_kinds = _extract_kinds_for_subject(tailed.stdout, ids["plan"])
    recorder.check(
        "plan's full kind sequence appears in order", plan_kinds == list(EXPECTED_PLAN_KINDS)
    )


def _extract_kinds_for_subject(tail_output: str, subject_id: str) -> list[str]:
    """Pick out `kind` from every `hive trail tail` line whose subject_id is `subject_id`."""
    kinds = []
    for line in tail_output.splitlines():
        fields = line.split("  ")
        if len(fields) >= 3 and fields[2] == subject_id:
            kinds.append(fields[1])
    return kinds


def _step4_merge_segments(db: Path, hive_id: str, node_a_id: str, recorder: Recorder) -> None:
    """Export both nodes' segments via the CLI, merge the second into the first, then check it."""
    before = asyncio.run(open_trail(db).query(TrailQuery(limit=1000)))
    recorder.check("database has events to interleave with", len(before) >= 2)
    interleave_start = before[len(before) // 2].at

    segment_a_file = db.parent / "segment_a.json"
    exported_a = _run_hive(["trail", "export", node_a_id, str(segment_a_file), "--db", str(db)])
    recorder.check("cli export of the first node's own segment exits 0", exported_a.returncode == 0)

    db2 = db.parent / "second_node.sqlite3"
    node_b_id = str(new_node_id(SystemClock()))
    identity_b = ChamberIdentity(hive_id=HiveId(hive_id), node_id=NodeId(node_b_id), actor="system")
    asyncio.run(_record_warden_events(db2, identity_b, FakeClock(start=interleave_start)))

    segment_b_file = db.parent / "segment_b.json"
    exported_b = _run_hive(["trail", "export", node_b_id, str(segment_b_file), "--db", str(db2)])
    recorder.check("cli export of the second node's segment exits 0", exported_b.returncode == 0)

    merged = _run_hive(["trail", "merge", str(segment_b_file), "--db", str(db)])
    recorder.check("cli merge exits 0", merged.returncode == 0)
    recorder.check("merge reports 3 inserted", "inserted 3 events" in merged.stdout)
    _check_merged_trail_is_ordered_without_duplicates(db, before, recorder)

    merged_again = _run_hive(["trail", "merge", str(segment_b_file), "--db", str(db)])
    recorder.check(
        "re-merging the same segment inserts nothing", "inserted 0 events" in merged_again.stdout
    )


# ──────────────────────────────────────────────────────────────────────────────
# Orchestration
# ──────────────────────────────────────────────────────────────────────────────


def run_demo(tmp_dir: Path, recorder: Recorder) -> None:
    """Run every step of the scenario against a fresh database under `tmp_dir`.

    Args:
        tmp_dir: A temporary directory this call owns; every file it writes lives under it.
        recorder: Where every milestone is checked; raises DemoError from `recorder.check` the
            moment one fails, so a caller sees exactly which step broke.
    """
    db = tmp_dir / "hive.sqlite3"
    graph_file = tmp_dir / "graph.json"
    graph_file.write_text(json.dumps(build_graph_draft_json()), encoding="utf-8")
    clock = SystemClock()
    hive_id, node_id = str(new_hive_id(clock)), str(new_node_id(clock))

    ids = _step1_submit(db, graph_file, hive_id, node_id, recorder)

    identity = ChamberIdentity(hive_id=HiveId(hive_id), node_id=NodeId(node_id), actor="human")
    chamber = open_chamber(db, identity)
    _step2_drive_plan(chamber, ids["plan"], recorder)
    _step2_drive_build_and_verify(chamber, ids, recorder)

    _step3_restart(db, ids, recorder)
    _step4_merge_segments(db, hive_id, node_id, recorder)


def _print_milestones(milestones: Sequence[Milestone]) -> None:
    """Print the milestone table: one PASS/FAIL row per condition checked, in order."""
    passed = sum(1 for m in milestones if m.passed)
    print(f"brood_demo: {passed}/{len(milestones)} milestones passed")
    print("  status  milestone")
    for milestone in milestones:
        status = "PASS" if milestone.passed else "FAIL"
        print(f"  {status:<6}  {milestone.name}")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the phase 2 exit-criteria demo end to end; print the milestone table.

    Args:
        argv: Command-line arguments, excluding the program name. `None` means use
            `sys.argv[1:]` (the normal case when run as a script). Takes none of its own; parsed
            only so `--help` behaves like every other script here.

    Returns:
        0 when every milestone passed, 1 otherwise.
    """
    parser = argparse.ArgumentParser(
        description="Run the phase 2 (Brood Chamber, Pheromone Trail) exit-criteria demo."
    )
    parser.parse_args(argv)

    recorder = Recorder()
    # ignore_cleanup_errors: a child `hive` process's SQLite -wal/-shm siblings can lag their
    # removal by a moment on Windows; a leftover temp directory must never fail a passed demo.
    with tempfile.TemporaryDirectory(prefix="brood_demo_", ignore_cleanup_errors=True) as tmp:
        try:
            run_demo(Path(tmp), recorder)
        except DemoError as exc:
            print(f"brood_demo: FAILED: {exc}", file=sys.stderr)
            _print_milestones(recorder.milestones)
            return 1

    _print_milestones(recorder.milestones)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
