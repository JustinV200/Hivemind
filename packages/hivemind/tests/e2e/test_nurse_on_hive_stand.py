"""End to end: a Nurse Bee rejects an attempt, a fresh Drone fixes it forward, and it passes.

ADR-0061's whole loop through every real layer but the model: the Queen plans one task, the Hive
Stand's Warden spawns a real Drone on a real lease, acceptance passes (the file exists), and the
Warden's model Nurse reads the file it wrote. A scripted model plays both bees honestly from what
each is shown: the Nurse rejects a poem with no title line, and the Drone writes one only when
its brief carries the Nurse's findings. So the goal can only succeed if the rejection reached the
Queen, her table sent the task back, the findings rode the TaskAssign into the fresh Drone's brief,
and the second Nurse round read the fixed file.

Fits into the Hive:
    Whole-Hive scenario (codingrules 14.2, `e2e`). Mirrors no single module: it exercises
    hivemind.wardens.ticks.results (the Nurse in handle_accept), hivemind.queen.autopilot.table
    and hivemind.queen.ticks.nurse (send it back), hivemind.queen.dispatcher.ready.assign (the
    findings on the TaskAssign) and hivemind.workers.roles.bounded_loop.prompt (the brief).

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/adr/0061-the-nurse-bee-reviews-an-attempt-in-a-fresh-context.md for the loop.
    - tests/unit/queen/test_queen_nurse.py for the Queen's half at the unit level.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from builders.cli import fake_manifest
from builders.llm import judge_approve_response
from e2e.kernel_helpers import (
    plan_response,
    single_task_plan,
    text_response,
    tool_response,
    tool_round_count,
    write_call,
)

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.cli.compose import GoalReport, Hive, build_hive, run_goal, run_hive
from hivemind.forage.slots import ModelSlot
from hivemind.llm import LLMRequest, LLMResponse, TextPart
from hivemind.manifest import load_manifest
from hivemind.pheromone import PheromoneEvent, TrailQuery
from waggle.clock import SystemClock

pytestmark = pytest.mark.e2e

_POEM = "poem.txt"
_TITLE = "Title: The Sea"
_PROBLEM = "The poem has no title line."
_TIMEOUT_S = 30.0  # Two attempts and two reviews; it finishes in a few seconds.


class _HonestBees:
    """A scripted model playing the planner, the Drone and both judges from what each is shown."""

    def __init__(self) -> None:
        """Start with no Drone brief seen."""
        self.briefs: list[str] = []  # Each Drone attempt's first user turn, in order.

    def responder(self, request: LLMRequest) -> LLMResponse:
        """Route one request by slot, and a JUDGE one by whether it is the Nurse's review."""
        if request.slot is ModelSlot.QUEEN:
            return plan_response(request, single_task_plan(_POEM, key="poem", title="A poem"))
        if request.slot is ModelSlot.WORKER:
            return self._drone(request)
        if request.slot is ModelSlot.JUDGE and "You are a Nurse Bee" in (request.system or ""):
            return _nurse(request)
        return judge_approve_response(request)  # The Capping gate's per-proposal judge.

    def _drone(self, request: LLMRequest) -> LLMResponse:
        """Write the poem, with a title only when a Nurse's findings asked for one; then stop."""
        brief = _text(request)
        if tool_round_count(request) > 0:
            return text_response("Wrote the poem.")
        self.briefs.append(brief)
        fixed = "<<<nurse_findings>>>" in brief and _PROBLEM in brief
        body = f"{_TITLE}\nwaves fold the grey light" if fixed else "waves fold the grey light"
        return tool_response(request, (write_call(_POEM, body),))


def _nurse(request: LLMRequest) -> LLMResponse:
    """Judge the poem the evidence shows: no title is one finding, a title is a PASS."""
    if _TITLE in _text(request):
        reply: dict[str, object] = {"outcome": "PASS", "findings": [], "notes": ""}
    else:
        finding = {"path": _POEM, "line": 1, "problem": _PROBLEM, "fix": f"Start with {_TITLE}."}
        reply = {"outcome": "CHANGES_REQUESTED", "findings": [finding], "notes": ""}
    answer = json.dumps(reply)
    if request.response_schema is not None:
        return text_response(answer)
    return text_response(f"```json\n{answer}\n```")


def _text(request: LLMRequest) -> str:
    """Every word the model is shown: the system prompt and each text part of each message."""
    parts = [part.text for m in request.messages for part in m.parts if isinstance(part, TextPart)]
    return "\n".join([request.system or "", *parts])


def test_a_nurse_rejection_is_fixed_forward_by_a_fresh_drone_and_then_passes(
    tmp_path: Path,
) -> None:
    bees = _HonestBees()
    manifest = load_manifest(fake_manifest(tmp_path), {})
    hive = build_hive(
        manifest, environ={}, clock=SystemClock(), responders={"fake": bees.responder}
    )

    report, events = asyncio.run(_run(hive))

    assert report.succeeded, report
    [task] = report.tasks
    assert task.status is TaskStatus.SUCCEEDED and task.nurse_rounds == 1
    reviews = [e.payload for e in events if e.kind == "capping.nurse_reviewed"]
    assert [(r["round"], r["outcome"]) for r in reviews] == [(1, "CHANGES_REQUESTED"), (2, "PASS")]
    assert [e.payload for e in events if e.kind == "task.nurse_round"] == [{"nurse_rounds": 1}]
    # The first Drone saw no findings; the fresh one saw them, and the trail never did.
    assert len(bees.briefs) == 2 and "nurse_findings" not in bees.briefs[0]
    assert _PROBLEM in bees.briefs[1]
    assert all(_PROBLEM not in json.dumps(e.payload) for e in events)


async def _run(hive: Hive) -> tuple[GoalReport, tuple[PheromoneEvent, ...]]:
    """Run the goal inside a running Hive; return its report and every trail event."""
    async with run_hive(hive):
        report = await run_goal(
            hive, "Write a poem about the sea", clearance=HoneyClearance.C1, timeout_s=_TIMEOUT_S
        )
    return report, tuple(await hive.stores.trail.query(TrailQuery()))
