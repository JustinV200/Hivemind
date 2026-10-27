"""End to end: the Queen is killed with work on the Hive Stand and a Virtual Cell; all of it ends.

Roadmap 13.1's exit run (ADR-0062), in one process. A Hive is built over its own SQLite file with
a Virtual side whose "container" is a real in-Cell Warden in the same event loop
(`builders.virtual_cells.ContainerSpawningFakeCellBackend`, whose tasks run in a context of
their own, as a container is a process of its own). A goal of two tasks lands one on the Hive
Stand, where its Drone waits on a question, and one on a Virtual Cell, whose Worker is held
mid-call. The Queen is then killed: every connection of hers closed, as the OS closes a dead
process's sockets, and every task she started cancelled, with no teardown at all, so her Hive
Stand lease stays open and no task is given back. A second Hive comes up over the same file and
the same backend, with nothing carried over in memory: Requeening gives the dead Warden's lease
back, places the Hive Stand's task afresh (its fresh Drone asks again and is answered), and waits
for the Virtual Cell's Warden, which redials the port the first Queen had and is taken back;
released, the Cell finishes its task and reports it. Every task finishes; the trail says what
Requeening did, once, as counts.

The container and Nuc legs of the roadmap step need Docker and a Nuc; they are the operator's
run (`docs/runbooks/requeening.md`).

Fits into the Hive:
    Whole-Hive scenario (codingrules 14.2, `e2e`). Exercises hivemind.cli.compose.hive.run's
    start path, hivemind.queen.requeening and hivemind.queen.cell_gate's rearm and sticky port,
    and the in-Cell Warden's mending link (hivemind.cli.in_cell.redial).

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/adr/0062-requeening-recovery-sources-of-truth.md.
    - tests/e2e/test_interrupted_run.py for the clean stop's counterpart.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from contextvars import ContextVar
from pathlib import Path

import pytest
from builders.llm import judge_approve_response
from builders.virtual_cells import (
    DEFAULT_HAIKU_FILES,
    ContainerSpawningFakeCellBackend,
    VirtualCellsTuning,
    virtual_cells_manifest,
)
from e2e.kernel_helpers import (
    plan_response,
    text_response,
    tool_response,
    tool_round_count,
    wait_until,
    write_call,
)

from hivemind.brood_chamber import AnswerSource, Question, Task, TaskFilter, TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.cli.compose import Hive, build_hive, run_hive
from hivemind.forage.slots import ModelSlot
from hivemind.llm import LLMRequest, LLMResponse
from hivemind.manifest import HiveManifest, load_manifest
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.queen.state import QueenMode
from waggle.clock import SystemClock

pytestmark = pytest.mark.e2e

_DOOMED: ContextVar[bool] = ContextVar("doomed", default=False)  # Marks the first Queen's tasks.
_STAND_FILE = "stand.txt"  # What the Hive Stand's task writes, once answered.
_CELL_FILE = DEFAULT_HAIKU_FILES[0]  # One of the files every container's script writes.
_QUESTION = "Which season?"
_WAIT_S = 60.0  # Generous: a redial backs off up to a few seconds, every other step is local.
_KILL_S = 10.0  # Bounds the cancellation of the first Queen's tasks.


def _plan() -> dict[str, object]:
    """Two tasks: one the Hive Stand takes (`prefer = "real"`), one that must be isolated."""

    def task(key: str, path: str, needs: dict[str, object]) -> dict[str, object]:
        acceptance: dict[str, object] = {
            "kind": "FILE_EXISTS",
            "subject": path,
            "argv": [],
            "expected": None,
        }
        return {
            "key": key,
            "title": f"Write {path}",
            "objective": f"Write {path} under scratch.",
            "acceptance": [acceptance],
            "needs": needs,
            "clearance": "C1",
            "depends_on": [],
        }

    return {
        "tasks": [
            task("stand", _STAND_FILE, {}),
            task("cell", _CELL_FILE, {"isolation": "REQUIRED"}),
        ]
    }


def _respond(request: LLMRequest) -> LLMResponse:
    """The Hive's own models: the plan, and a Hive Stand Drone that asks before it writes."""
    if request.slot is ModelSlot.QUEEN:
        return plan_response(request, _plan())
    if request.slot is ModelSlot.JUDGE:
        return judge_approve_response(request)
    if request.slot is not ModelSlot.WORKER:
        return text_response(json.dumps({}))
    rounds = tool_round_count(request)
    if rounds == 0:
        return tool_response(request, (("ask_1", "ask", {"text": _QUESTION}),))
    if rounds == 1:
        return tool_response(request, (write_call(_STAND_FILE),))
    return text_response("Written.")


async def test_a_killed_queen_is_requeened_and_every_task_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _one_backend(monkeypatch)
    manifest = load_manifest(
        virtual_cells_manifest(tmp_path, tuning=VirtualCellsTuning(prefer="real")), {}
    )
    first = await _build(manifest)
    try:
        loops = await _start_doomed(first)
        await first.queen.submit_goal("Write two files.", clearance=HoneyClearance.C1)
        blocked = await _task_on(first, TaskStatus.BLOCKED)
        working = await _task_on(first, TaskStatus.RUNNING)
        # The Cell's Worker is mid-call: its task is the Cell's own now, whatever becomes of her.
        await asyncio.wait_for(backend[0].holding.wait(), _WAIT_S)
        await _kill(first, loops)

        second = await _build(manifest)
        async with run_hive(second):
            assert backend[0].hold is not None
            backend[0].hold.set()  # The Cell's Worker gets its answer, cut off or back.
            asked = await _asked_again(second, blocked)
            await second.queen.answer_question(
                asked.id, "spring", source=AnswerSource.HUMAN, clearance=HoneyClearance.C2
            )
            await _all_finished(second)
            requeened = await _requeened(second)
            mode = second.queen_deps.cluster_state.mode
    finally:
        await backend[0].aclose()

    assert working.cell_id != blocked.cell_id
    [event] = requeened
    counts = {key: event.payload[key] for key in ("given_back", "leases_released")}
    assert counts == {"given_back": 1, "leases_released": 1}
    assert (event.payload["wardens_back"], event.payload["wardens_missing"]) == (1, 0)
    assert mode is QueenMode.RUNNING
    released = await second.stores.trail.query(TrailQuery(kind="cell.released"))
    assert any(row.payload.get("requeened") is True for row in released)


def _one_backend(monkeypatch: pytest.MonkeyPatch) -> list[ContainerSpawningFakeCellBackend]:
    """Make every Hive built here use one backend, as a Docker daemon outlives the Queen."""
    made: list[ContainerSpawningFakeCellBackend] = []

    def backend(*args: object, **kwargs: object) -> ContainerSpawningFakeCellBackend:
        if made:
            made[0].rebind(kwargs["endpoint"], kwargs["gate"])  # type: ignore[arg-type]
        else:
            made.append(ContainerSpawningFakeCellBackend(*args, **kwargs))  # type: ignore[arg-type]
            made[0].hold = asyncio.Event()
        return made[0]

    monkeypatch.setattr("hivemind.cli.compose.virtual_cell_backends.FakeCellBackend", backend)
    return made


async def _build(manifest: HiveManifest) -> Hive:
    """Build a Hive over `manifest`'s own file; its own event loops, so never inside the test's."""
    return await asyncio.to_thread(
        build_hive, manifest, environ={}, clock=SystemClock(), responders={"fake": _respond}
    )


async def _start_doomed(hive: Hive) -> tuple[asyncio.Task[None], ...]:
    """Start `hive` as `run_hive` does, every task it starts marked as the first Queen's."""
    token = _DOOMED.set(True)
    try:
        await hive.queen.attach_warden(hive.warden_link)
        parts = hive.virtual_cells
        assert parts is not None
        await parts.prepare()
        await parts.listener.start(hive.queen)
        await parts.lifecycle.reconcile(hive.manifest.hive.id)
        await hive.warden.start()
        return (asyncio.ensure_future(hive.queen.run()), asyncio.ensure_future(hive.warden.run()))
    finally:
        _DOOMED.reset(token)


async def _kill(hive: Hive, loops: tuple[asyncio.Task[None], ...]) -> None:
    """Kill the first Queen as the OS kills a process: sockets closed, nothing torn down.

    `loops` are her and her Warden's own loops; every task either started is cancelled with them.
    """
    assert hive.virtual_cells is not None
    await hive.virtual_cells.listener.stop()
    current = asyncio.current_task()
    doomed = {
        task
        for task in asyncio.all_tasks()
        if task is not current and task.get_context().get(_DOOMED, False)
    } | set(loops)
    for task in doomed:
        task.cancel()
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(asyncio.gather(*doomed, return_exceptions=True), _KILL_S)


async def _task_on(hive: Hive, status: TaskStatus) -> Task:
    """Wait until a task reaches `status`, and return it."""
    found: list[Task] = []

    async def reached() -> bool:
        found[:] = [t for t in await hive.stores.chamber.list(TaskFilter()) if t.status is status]
        return bool(found)

    await wait_until(reached, timeout_s=_WAIT_S)
    return found[0]


async def _asked_again(hive: Hive, task: Task) -> Question:
    """Wait until `task`'s fresh Drone, placed afresh, has asked its question again."""
    asked: list[Question] = []

    async def waiting() -> bool:
        pending = await hive.stores.chamber.pending_questions(task.id)
        asked[:] = [q for q in pending if q.id != task.pending_question_id]
        return bool(asked)

    await wait_until(waiting, timeout_s=_WAIT_S)
    return asked[0]


async def _all_finished(hive: Hive) -> None:
    """Wait until every task in the chamber has SUCCEEDED."""

    async def finished() -> bool:
        tasks = await hive.stores.chamber.list(TaskFilter())
        return bool(tasks) and all(task.status is TaskStatus.SUCCEEDED for task in tasks)

    await wait_until(finished, timeout_s=_WAIT_S)


async def _requeened(hive: Hive) -> list[PheromoneEvent]:
    """Wait until her Requeening has settled (on a tick), and return its `queen.requeened`."""
    found: list[PheromoneEvent] = []

    async def settled() -> bool:
        found[:] = await hive.stores.trail.query(TrailQuery(kind="queen.requeened"))
        return bool(found)

    await wait_until(settled, timeout_s=_WAIT_S)
    return found
