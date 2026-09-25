"""End-to-end: roadmap phase 10's first exit criterion, the enforcement points of step 10.3.

`.claude/roadmap.md` phase 10 exit criteria, first bullet, one test per clause: "A Drone without
`net:*` is denied `http_get` with the reason on the trail; a task without `cell:hive_stand` lands
on a Virtual Cell even under `prefer = "real"`; a Warden without `forage:request` cannot ask for
more." Each runs the real code on the path it names: a real Drone's tool loop over a scripted
`FakeLLMProvider`; a real Hive (Queen, Hive Stand Warden, `CellListener`, a real in-Cell Warden per
Virtual Cell over the fake backend the phase 5 suite uses); the Queen's real Forage-request handler
under a Guard policy whose `warden` role omits `forage:request`.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md phase 10 exit criteria, verbatim, for the bullet this module proves.
    - docs/adr/0039-capability-model-attenuation-and-enforcement-points.md for the points.
    - tests.e2e.test_virtual_cells for the Virtual Cell wiring criterion 1b reuses.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.cells import make_cell
from builders.forage import make_grant
from builders.llm import make_bound, make_tool_call
from builders.queen import make_queen_deps, with_guard_policy
from builders.virtual_cells import (
    ContainerSpawningFakeCellBackend,
    VirtualCellsTuning,
    single_haiku_plan,
    virtual_cells_manifest,
)
from builders.workers import make_assignment, make_context
from e2e.kernel_helpers import HaikuScript, default_worker_turn, wait_until

from hivemind.brood_chamber import TaskFilter, TaskStatus, is_terminal
from hivemind.cell import AccessLevel, CellKind, HoneyClearance
from hivemind.cli.compose import Hive, build_hive, run_hive
from hivemind.forage.grant_state import GrantState
from hivemind.guard import load_guard_policy
from hivemind.llm import FakeLLMProvider, text_response, tool_call_response
from hivemind.manifest import GuardRoleSection, GuardSection, load_manifest
from hivemind.pheromone import TrailQuery
from hivemind.queen.ticks.forage import handle_forage_request
from hivemind.workers.roles.drone import Drone
from waggle.clock import SystemClock
from waggle.ids import TaskId, new_message_id
from waggle.messages.forage import ForageDelta, ForageOutcome, ForageRequest
from waggle.messages.forage.values import ForageRequestKind
from waggle.messages.labels import AccuracyBar, Tempo

pytestmark = pytest.mark.e2e

_TIMEOUT_S = 15.0  # Generous: the Virtual Cell scenario finishes in well under this.
# A device-shaped goal ceiling: everything a Drone's haiku task needs on a Virtual Cell, but not
# `cell:hive_stand` -- so the Hive Stand is no candidate for any task of this goal.
_GOAL_WITHOUT_HIVE_STAND = (
    "cell:virtual",
    "cell:comb_shield:*",
    "llm:*",
    "tool:*",
    "fs:read:**",
    "fs:write:**",
    "exec:*",
    "question:human",
)


async def test_1a_a_drone_without_net_is_denied_http_request_with_the_reason_on_the_trail() -> None:
    """Criterion 1a, through a real Drone's own executor, with a scripted FakeLLMProvider.

    The roadmap names the tool `http_get`; this codebase's one HTTP tool is `http_request`
    (`hivemind.workers.tools.http`), a GET or a POST. The model asks for it, the Drone's
    `_RecordingExecutor` runs it through the registry, the Guard refuses `net:example.com` at the
    `tool_invocation` point, and the model reads the refusal as its tool result.
    """
    provider = FakeLLMProvider()
    # A FULL Cell and the builder's default set (no `net` at all): the set refuses, not the level.
    cell = make_cell(kind=CellKind.REAL, access_level=AccessLevel.FULL)
    ctx = make_context(cell=cell, bound=make_bound(provider=provider))
    call = make_tool_call(
        name="http_request", arguments={"method": "GET", "url": "https://example.com/data"}
    )
    provider.script(tool_call_response(call), text_response("The fetch was refused."))

    await Drone().run(ctx, make_assignment(), resume_from=None)

    [denial] = await ctx.trail.query(TrailQuery(kind="guard.denied"))
    assert denial.subject_id == ctx.worker_id
    assert denial.payload["point"] == "tool_invocation"
    assert denial.payload["capability"] == "net:example.com"
    assert denial.payload["rule"] == "guard.not_held"
    assert "net:example.com" in str(denial.payload["reason"])
    # The model's next turn carried the Guard's refusal as the tool's own result.
    assert "refused by the Guard" in provider.calls[-1].model_dump_json()


def test_1b_a_task_without_cell_hive_stand_lands_on_a_virtual_cell_under_prefer_real(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterion 1b: `prefer = "real"`, a goal lacking `cell:hive_stand`, and a Virtual landing.

    Built exactly like `tests.e2e.test_virtual_cells`' own hives (the container-spawning fake
    backend runs a real in-Cell Warden per Cell); only the goal differs, submitted with a ceiling
    the Hive Stand's own `cell:hive_stand` is missing from.
    """
    monkeypatch.setattr(
        "hivemind.cli.compose.virtual_cell_backends.FakeCellBackend",
        ContainerSpawningFakeCellBackend,
    )
    manifest_path = virtual_cells_manifest(tmp_path, tuning=VirtualCellsTuning(prefer="real"))
    script = HaikuScript(default_worker_turn, plan=single_haiku_plan("haiku_1.txt"))
    hive = build_hive(
        load_manifest(manifest_path, {}),
        environ={},
        clock=SystemClock(),
        responders={"fake": script.responder},
    )
    asyncio.run(_run_scenario_1b(hive))


async def _run_scenario_1b(hive: Hive) -> None:
    """Submit the ceilinged goal, wait for its task, and read where and why it was placed."""
    assert hive.virtual_cells is not None
    backend = hive.virtual_cells.registry.get("fake")
    assert isinstance(backend, ContainerSpawningFakeCellBackend)
    try:
        async with run_hive(hive):
            goal_id = await hive.queen.submit_goal(
                "write one haiku about bees",
                clearance=HoneyClearance.C1,
                capabilities=_GOAL_WITHOUT_HIVE_STAND,
            )
            await wait_until(lambda: _goal_finished(hive, goal_id), timeout_s=_TIMEOUT_S)
        [task] = await hive.stores.chamber.list(TaskFilter(goal_id=goal_id))
        # The ceiling travelled with the task, and the work still got done beneath it.
        assert task.spec.capabilities == tuple(sorted(_GOAL_WITHOUT_HIVE_STAND))
        assert task.status is TaskStatus.SUCCEEDED
        placed = await hive.stores.trail.query(TrailQuery(kind="queen.placed", subject_id=task.id))
        reason = str(placed[0].payload["reason"])
        assert "prefer=real found no Real Cell" in reason
        assert "Hive Stand: goal lacks cell:hive_stand" in reason
        [assigned] = await hive.stores.trail.query(
            TrailQuery(kind="queen.assigned", subject_id=task.id)
        )
        assert assigned.payload["cell_id"] != hive.warden_link.cell.id
        assert len(backend.provision_calls) >= 1
    finally:
        await backend.aclose()


async def _goal_finished(hive: Hive, goal_id: TaskId) -> bool:
    """True once every task under `goal_id` has reached a terminal status."""
    tasks = await hive.stores.chamber.list(TaskFilter(goal_id=goal_id))
    return bool(tasks) and all(is_terminal(task.status) for task in tasks)


async def test_1c_a_warden_without_forage_request_cannot_ask_for_more() -> None:
    """Criterion 1c: the Queen denies a ForageRequest from a Warden whose set lacks the family."""
    section = GuardSection(
        roles={"warden": GuardRoleSection(allow=("cell:hive_stand", "llm:*", "tool:*"))}
    )
    base, link, warden_end = make_queen_deps()
    deps = with_guard_policy(base, load_guard_policy(None, section))
    grant = make_grant(clock=deps.clock, holder=link.warden_id, state=GrantState.ACTIVE)
    await deps.ledger.record_grant(grant)

    await handle_forage_request(
        deps, {link.warden_id: link}, link, _more_sub_bees(grant.id), new_message_id(deps.clock)
    )

    reply = await warden_end.wait_for_forage_reply()
    assert reply.outcome is ForageOutcome.DENIED
    assert warden_end.grants == []  # Nothing was granted: the grant is exactly as it was.
    assert deps.ledger.grant(grant.id) == grant
    [denial] = await deps.trail.query(TrailQuery(kind="guard.denied"))
    assert denial.subject_id == link.warden_id
    assert denial.payload["point"] == "forage_request"
    assert denial.payload["capability"] == "forage:request"
    kinds = [event.kind for event in await deps.trail.query(TrailQuery(family="forage"))]
    assert kinds == ["forage.requested", "forage.denied"]


def _more_sub_bees(grant_id: str) -> ForageRequest:
    """A Warden's own request for one more sub-bee on `grant_id`."""
    return ForageRequest(
        grant_id=grant_id,
        kind=ForageRequestKind.SUB_BEES,
        wanted=ForageDelta(
            sub_bees=1, seats=0, source_id=None, spend=0.0, tokens=0, slot=None, minimum_grade=None
        ),
        task_id=None,
        tempo=Tempo(latency_budget_s=None, accuracy=AccuracyBar.NORMAL),
        reason="a Warden wants one more sub-bee",
    )
