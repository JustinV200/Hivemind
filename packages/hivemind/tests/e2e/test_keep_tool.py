"""End-to-end: the `keep` tool and `keep_root`, roadmap step 5.0e's own exit criterion.

"Leavings, on a real run against the Hive Stand: a goal that asks for a file to be kept leaves it
under `keep_root` after release with a ledger row and a `cell.left` event, and scratch is still
gone; the same goal without that ask leaves nothing" (.claude/roadmap.md, phase 5's exit
criteria). Both scenarios here are one real, unpatched Drone calling the real `keep` tool through
`hivemind.cli.compose.build_hive`'s own composition root, the same shape
`tests.e2e.test_leaving_ask` uses for the ASK rung. Every path this module touches (`scratch_root`,
`keep_root`, and `Path.home()` is never used here) lives under `tmp_path`.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md lines ~1023-1029 for the exit criterion this module proves.
    - tests.e2e.test_leaving_ask for the ASK rung's own end-to-end scenario, this module's sibling.
    - hivemind.workers.tools.keep for keep, the tool both scenarios drive.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest
from builders.cli import fake_manifest
from e2e.kernel_helpers import (
    HaikuScript,
    text_response,
    tool_response,
    tool_round_count,
    wait_until,
)

from hivemind.brood_chamber import TaskFilter, TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.cli.compose import Hive, build_hive, run_goal, run_hive
from hivemind.llm import LLMRequest, LLMResponse
from hivemind.manifest import load_manifest
from hivemind.pheromone import TrailQuery
from waggle.clock import SystemClock

pytestmark = pytest.mark.e2e

_TIMEOUT_S = 10.0
_SOURCE_IN_SCRATCH = "artifact_src.txt"
_CONTENT = "kept content"


def _patch_hive_stand(manifest_path: Path, *, keep_root: Path | None) -> None:
    """Patch `[hive_stand]` onto an already-written manifest: FULL access, and `keep_root` if given.

    Mirrors `tests.e2e.test_leaving_ask._set_full_access`: `builders.cli.fake_manifest` has no
    knob for either key yet, so this is the same after-the-fact text patch that module's own
    docstring explains.
    """
    extra = 'access_level = "FULL"\n'
    if keep_root is not None:
        extra += f'keep_root = "{keep_root.as_posix()}"\n'
    text = manifest_path.read_text(encoding="utf-8")
    manifest_path.write_text(
        text.replace("[hive_stand]\n", f"[hive_stand]\n{extra}", 1), encoding="utf-8"
    )


def _plan(destination: str, *, declare: bool) -> dict[str, object]:
    """A single-task plan that writes `_SOURCE_IN_SCRATCH` then keeps it at `destination`."""
    task: dict[str, object] = {
        "key": "keep_it",
        "title": "Keep the artifact",
        "objective": f"Write the artifact and keep it at {destination}.",
        "acceptance": [
            {"kind": "FILE_EXISTS", "subject": destination, "argv": [], "expected": None}
        ],
        "needs": {},
        "clearance": "C1",
        "depends_on": [],
    }
    if declare:
        task["leaves"] = [{"pattern": destination, "reason": "the goal asks for it to remain"}]
    return {"tasks": [task]}


def _worker_turn(destination: str) -> Callable[[LLMRequest], LLMResponse]:
    """Build a two-round worker_turn: write the source into scratch, then `keep` it."""

    def turn(request: LLMRequest) -> LLMResponse:
        rounds = tool_round_count(request)
        if rounds == 0:
            return tool_response(
                request,
                (
                    (
                        f"write_{_SOURCE_IN_SCRATCH}",
                        "write_file",
                        {"path": _SOURCE_IN_SCRATCH, "content": _CONTENT},
                    ),
                ),
            )
        if rounds == 1:
            keep_args = {"source": _SOURCE_IN_SCRATCH, "destination": destination}
            return tool_response(request, (("keep_it", "keep", keep_args),))
        return text_response("Kept the artifact.")

    return turn


def _hive(manifest_path: Path, destination: str, *, declare: bool) -> Hive:
    manifest = load_manifest(manifest_path, {})
    script = HaikuScript(_worker_turn(destination), plan=_plan(destination, declare=declare))
    return build_hive(
        manifest, environ={}, clock=SystemClock(), responders={"fake": script.responder}
    )


async def _task_is(hive: Hive, status: TaskStatus) -> bool:
    tasks = await hive.stores.chamber.list(TaskFilter())
    return bool(tasks) and tasks[0].status is status


async def _run_goal(hive: Hive) -> None:
    async with run_hive(hive):
        report = await run_goal(
            hive, "keep an artifact", clearance=HoneyClearance.C1, timeout_s=_TIMEOUT_S
        )
        await wait_until(lambda: _task_is(hive, TaskStatus.SUCCEEDED), timeout_s=_TIMEOUT_S)
    assert report.succeeded, report


def test_a_declared_leaving_under_keep_root_survives_release_with_a_ledger_row(
    tmp_path: Path,
) -> None:
    """A destination under `keep_root`, declared by the plan, stays after release."""
    keep_root = tmp_path / "keep"
    keep_root.mkdir()
    destination = str(keep_root / "artifact.txt")
    manifest_path = fake_manifest(tmp_path, capabilities="full")
    _patch_hive_stand(manifest_path, keep_root=keep_root)
    hive = _hive(manifest_path, destination, declare=True)

    asyncio.run(_run_goal(hive))

    assert Path(destination).read_text(encoding="utf-8") == _CONTENT
    cell = asyncio.run(hive.source.cells())[0]
    leavings = asyncio.run(hive.stores.leavings.list_leavings(cell.id))
    assert len(leavings) == 1
    assert leavings[0].path == Path(destination).resolve(strict=False)
    left_events = [
        event
        for event in asyncio.run(hive.stores.trail.query(TrailQuery()))
        if event.kind == "cell.left"
    ]
    assert left_events, "cell.left never appeared on the trail"
    scratch_root = tmp_path / "scratch"
    assert list(scratch_root.iterdir()) == [], "scratch was not removed wholesale on release"


def test_an_undeclared_keep_destination_leaves_nothing_after_release(tmp_path: Path) -> None:
    """The same shape, but the plan never declares the destination: roadmap 5.0b's hard rule."""
    keep_root = tmp_path / "keep"
    keep_root.mkdir()
    destination = str(keep_root / "artifact.txt")
    manifest_path = fake_manifest(tmp_path, capabilities="full")
    _patch_hive_stand(manifest_path, keep_root=keep_root)
    hive = _hive(manifest_path, destination, declare=False)

    asyncio.run(_run_goal(hive))

    # DENY (undeclared): the write applied and was then restored on release -- nothing was there
    # before this run, so "restored" means removed.
    assert not Path(destination).exists()
    cell = asyncio.run(hive.source.cells())[0]
    leavings = asyncio.run(hive.stores.leavings.list_leavings(cell.id))
    assert leavings == ()
    scratch_root = tmp_path / "scratch"
    assert list(scratch_root.iterdir()) == [], "scratch was not removed wholesale on release"
