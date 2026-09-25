"""End-to-end: a declared leaving marked ASK blocks the task until `hive inbox answer` resumes it.

Roadmap steps 5.0c/5.0d, the suite's own "Tests" section: "one end-to-end scenario... where a
declared leaving marked ASK blocks until answered through the same path `hive inbox answer` uses."
Built the same way as `tests.e2e.test_kernel_on_hive_stand`'s own scenario (d) (a real `hive
run`-shaped `Hive`, a real SQLite-backed Brood Chamber, `hive inbox`/`hive inbox answer` driven for
real through `typer.testing.CliRunner`), because that is the one composition root
(`hivemind.cli.compose.build_hive`) whose Brood Chamber a separate `hive inbox answer` process can
actually reach by `--manifest` path (`hivemind.cli.compose.build_hive` always opens real, on-disk
SQLite stores; `tests.e2e.kernel_helpers.build_cluster_pair`'s own in-memory chamber has no
manifest a CLI invocation could ever open).

Roadmap step 5.0c's own suggested default table only ever answers ASK for a path a plan declared
(roadmap 5.0b's hard rule) that is either executable outside `keep_root`, a startup/system
location, or an over-threshold home file -- never a plain small file under home on the Hive Stand
itself (that is ALLOW, no question asked). This scenario uses an executable-suffixed target
(`.exe` on Windows, `.sh` elsewhere) under the operator's own home directory, so the ASK verdict is
deterministic on any host `deterministic_checks()` runs on, without depending on the shipped
`max_home_bytes` threshold.

Roadmap step 5.0e: this scenario now drives the real `keep` tool through a real, unpatched Drone --
`hivemind.workers.tools.keep.keep` -- rather than the test-only `_LeavingWorker` stand-in and
`_WideningLeaseView` this dispatch's predecessor needed: `hivemind.wardens.spawn.spawn.
_widen_lease_reachability` now widens the Warden's one shared lease for a task's own declared
`leaves` (and the manifest's `keep_root`) the moment a sub-bee spawns, under `AccessLevel.FULL`,
and `hivemind.cell.local.session.LocalProcessSession` now reads `lease.allowed_paths` live on every
call instead of a snapshot taken at `Warden.start()` -- so no `hivemind/` source needs a test-only
patch to make an outside-scratch write reachable any more, and the `keep` tool itself is what a
real Drone calls to propose one. The scripted Worker turn writes the checker into scratch first,
then calls `keep(source, destination)` on it -- `keep`'s own precondition (`source` must resolve
inside scratch) means there is no shorter script.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md steps 5.0c/5.0d/5.0e for this scenario's own requirement, verbatim.
    - tests.e2e.test_kernel_on_hive_stand for scenario (d) (the `hive inbox answer` pattern this
      module reuses).
    - hivemind.workers.tools.keep for keep, the tool this scenario now drives for real.
    - hivemind.wardens.spawn.spawn for _widen_lease_reachability, the fix that closes the
      capability/reachability gap this module's predecessor worked around by hand.
    - hivemind.supervision.capping.checks.human for HumanCheck, the HUMAN rung this scenario
      proves end to end, including the `Answer.chosen_option` roundtrip through a real `hive
      inbox answer --option` -> `Note` -> `sync_answers_from_chamber` -> wire `Answer` hop.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from builders.cli import fake_manifest, printed_object
from e2e.kernel_helpers import (
    HaikuScript,
    text_response,
    tool_response,
    tool_round_count,
    wait_until,
    write_call,
)
from typer.testing import CliRunner

from hivemind.brood_chamber import TaskFilter, TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.cli.app import app
from hivemind.cli.compose import Hive, build_hive, run_goal, run_hive
from hivemind.llm import LLMRequest, LLMResponse
from hivemind.manifest import load_manifest
from hivemind.pheromone import TrailQuery
from hivemind.queen.chat import ChatKind, ChatQuery
from waggle.clock import SystemClock

pytestmark = pytest.mark.e2e

_GOAL = "install a licence checker that must stay on this machine"
_TIMEOUT_S = 10.0
_EXECUTABLE_SUFFIX = ".exe" if os.name == "nt" else ".sh"
_TARGET = str(Path.home() / f"hivemind_e2e_leaving{_EXECUTABLE_SUFFIX}")
_SOURCE_IN_SCRATCH = "checker_src.exe"
_LEAVE_REASON = "the goal itself asks for the checker to remain installed"

runner = CliRunner()


def _leaving_plan() -> dict[str, object]:
    """A single-task plan declaring `_TARGET` as a leaving, with acceptance on the same path."""
    return {
        "tasks": [
            {
                "key": "install",
                "title": "Install the checker",
                "objective": f"Leave the checker installed at {_TARGET}.",
                "acceptance": [
                    {"kind": "FILE_EXISTS", "subject": _TARGET, "argv": [], "expected": None}
                ],
                "needs": {},
                "clearance": "C1",
                "depends_on": [],
                "leaves": [{"pattern": _TARGET, "reason": _LEAVE_REASON}],
            }
        ]
    }


def _worker_turn(request: LLMRequest) -> LLMResponse:
    """Write the checker into scratch, then `keep` it at `_TARGET` -- a real Drone, two rounds.

    `keep`'s own precondition (module docstring: source must already resolve inside scratch) is
    why this cannot be a single-round script: the file has to exist in scratch before `keep` can
    propose moving it out.
    """
    rounds = tool_round_count(request)
    if rounds == 0:
        return tool_response(request, (write_call(_SOURCE_IN_SCRATCH, "checker"),))
    if rounds == 1:
        keep_call = ("keep_checker", "keep", {"source": _SOURCE_IN_SCRATCH, "destination": _TARGET})
        return tool_response(request, (keep_call,))
    return text_response("Installed the checker.")


def _set_full_access(manifest_path: Path) -> None:
    """Patch `[hive_stand] access_level = "FULL"` onto an already-written manifest.

    `builders.cli.fake_manifest` never writes this key (it defaults to SCRATCH), and roadmap
    step 5.0c's own hard rule denies every leaving unconditionally at SCRATCH -- mirrors
    `tests.e2e.kernel_helpers.set_budget_fraction`'s own after-the-fact text patch, for the same
    reason: no builder knob for it exists yet to extend instead.
    """
    text = manifest_path.read_text(encoding="utf-8")
    manifest_path.write_text(
        text.replace("[hive_stand]\n", '[hive_stand]\naccess_level = "FULL"\n', 1),
        encoding="utf-8",
    )


def _hive(manifest_path: Path) -> Hive:
    """Build a Hive scripted for both the QUEEN-slot plan call and a real Drone's own tool calls."""
    manifest = load_manifest(manifest_path, {})
    script = HaikuScript(_worker_turn, plan=_leaving_plan())
    return build_hive(
        manifest, environ={}, clock=SystemClock(), responders={"fake": script.responder}
    )


async def _task_is_blocked(hive: Hive) -> bool:
    """Return whether the goal's own (only) task is currently BLOCKED on a question."""
    tasks = await hive.stores.chamber.list(TaskFilter())
    return bool(tasks) and tasks[0].status is TaskStatus.BLOCKED


async def _question_is_posted(hive: Hive) -> bool:
    """Return whether the Queen has posted the blocked task's question to the chat.

    Posting is the last thing she does for a question, after the task turns BLOCKED: she appends
    the chat line, then tells the human channel, whose unbound relay writes a debug line to
    stdout before the chat store lets any reader see that line. `hive inbox` runs under
    `CliRunner`, which swaps the process-wide stdout while it runs, so invoking it before this
    holds let that debug line land at the head of its JSON output.
    """
    lines = await hive.stores.chat.read(ChatQuery())
    return any(line.kind is ChatKind.QUESTION for line in lines)


def test_a_declared_leaving_marked_ask_blocks_until_hive_inbox_answer_resumes_it(
    tmp_path: Path,
) -> None:
    """A leave-policy ASK verdict raises a Question that only "keep" resumes.

    `hive inbox answer ... --option 0` (keep) resumes the task, and the file is left in place
    with `approved_by=HUMAN` on the trail -- driven end to end by a real Drone calling the real
    `keep` tool, never a stand-in.
    """
    manifest_path = fake_manifest(tmp_path, capabilities="full")
    _set_full_access(manifest_path)
    hive = _hive(manifest_path)
    target = Path(_TARGET)
    try:
        asyncio.run(_run_leaving_ask(hive, manifest_path))
    finally:
        target.unlink(missing_ok=True)


async def _run_leaving_ask(hive: Hive, manifest_path: Path) -> None:
    """Run the goal, answer its leave Question with "keep" through the CLI, and assert it landed."""
    async with run_hive(hive):
        goal_task = asyncio.ensure_future(
            run_goal(hive, _GOAL, clearance=HoneyClearance.C1, timeout_s=_TIMEOUT_S)
        )
        await wait_until(lambda: _task_is_blocked(hive), timeout_s=_TIMEOUT_S)
        # Only once the question has reached the chat can nothing the Queen writes for it land
        # inside the CLI's own captured stdout (`_question_is_posted`'s own docstring).
        await wait_until(lambda: _question_is_posted(hive), timeout_s=_TIMEOUT_S)
        listed = await asyncio.to_thread(
            runner.invoke, app, ["inbox", "--manifest", str(manifest_path), "--json"]
        )
        assert listed.exit_code == 0, listed.output
        question = printed_object(listed.output)["questions"][0]
        assert question["options"] == ["keep", "keep for this whole goal", "discard"]
        answered = await asyncio.to_thread(
            runner.invoke,
            app,
            [
                "inbox",
                "answer",
                question["id"],
                "keep",
                "--option",
                "0",
                "--manifest",
                str(manifest_path),
            ],
        )
        assert answered.exit_code == 0, answered.output
        report = await goal_task
    assert report.succeeded, report
    # A short, already-flushed file: reading it directly (not through a thread) is fine here,
    # the same way scenario (a) reads scratch files back synchronously after the goal completes.
    assert await asyncio.to_thread(Path(_TARGET).read_text, encoding="utf-8") == "checker"
    payloads = [
        event.payload
        for event in await hive.stores.trail.query(TrailQuery())
        if event.kind == "capping.leave_decided"
    ]
    assert payloads, "capping.leave_decided never appeared on the trail"
    assert payloads[0]["verdict"] == "ASK"
    assert payloads[0]["persisted"] is True
    assert payloads[0]["human_answer"] == "KEEP"
