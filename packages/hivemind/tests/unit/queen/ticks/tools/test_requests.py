"""Tests for hivemind.queen.ticks.tools.requests: a Warden's `tool.request`, door and job.

Fits into the Hive:
    Mirrors src/hivemind/queen/ticks/tools/requests.py (codingrules section 3). The first test
    runs the whole flow for real: the echo tool through the real Quarantine Comb in the
    subprocess sandbox, promoted into the registry, shipped to the Warden and announced, a Nectar
    deposited. The rest drive the door and the job with a scripted scaffolder.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.ticks.tools.requests for the module under test.
    - builders.queen_tools for the rig, the fake Warden and the fake sandbox.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.queen_tools import ToolRig, make_tool_rig
from builders.royal_jelly import make_malicious_files, make_scaffold_result
from builders.tasks import make_graph_draft

from hivemind.brood_chamber import Task
from hivemind.pheromone.trail import TrailQuery
from hivemind.queen.ticks.tools import replies
from hivemind.royal_jelly.scaffold import ScaffoldFailedError, ScaffoldResult, ToolRequestBrief
from hivemind.royal_jelly.spec import pack, sha256_of
from waggle.ids import new_task_id, new_warden_id, new_worker_id
from waggle.messages.control.protocol import ErrorMessage
from waggle.messages.tool import ToolPromoted, ToolScope


async def _kinds(rig: ToolRig, family: str) -> list[str]:
    events = await rig.deps.trail.query(TrailQuery(family=family))
    return [event.kind for event in events]


async def _error(rig: ToolRig) -> ErrorMessage:
    reply = await rig.reply()
    assert isinstance(reply.payload, ErrorMessage)
    return reply.payload


def _failing(brief: ToolRequestBrief) -> ScaffoldResult:
    raise ScaffoldFailedError(brief.name, "scripted failure")


def _socket_tool(brief: ToolRequestBrief) -> ScaffoldResult:
    """Scaffold a tool whose source imports `socket`: the static gate refuses it."""
    good = make_scaffold_result(brief, required_capabilities=())
    files = make_malicious_files("socket_import", good.spec)
    package = pack(files)
    return ScaffoldResult(good.spec, files, package, sha256_of(package))


async def test_a_request_is_scaffolded_proved_promoted_shipped_and_announced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch, honey=True)
    request = rig.request()

    item = await rig.send(request)
    await rig.settle()

    # The Warden is announced first (blob, then tool.promoted), then answered, correlated.
    announced = await rig.reply()
    answer = await rig.reply()
    assert isinstance(announced.payload, ToolPromoted) and announced.correlation_id is None
    assert isinstance(answer.payload, ToolPromoted) and answer.correlation_id == item.id
    promoted = answer.payload
    assert (promoted.name, promoted.scope, promoted.cell_id) == ("echo_tool", ToolScope.HIVE, None)
    assert promoted.promoted_by_warden is None and promoted.quarantined_on is not None
    assert promoted.package_sha256 in rig.peer.blobs
    assert sha256_of(rig.peer.blobs[promoted.package_sha256]) == promoted.package_sha256
    assert rig.peer.seen[0] == "basket.offer"
    # The registry holds the row, the Basket the package, the trail the whole story in order.
    (row,) = await rig.registry.list_tools()
    assert row.package_sha256 == promoted.package_sha256 and row.promoted_by == "queen"
    assert (await rig.kit.store.get(row.package_sha256)).size_bytes == row.package_size
    assert await _kinds(rig, "tool") == [
        "tool.requested",
        "tool.scaffolded",
        "tool.quarantine_requested",
        "tool.quarantined",
        "tool.promoted",
        "tool.announced",
    ]
    # Honey describes the tool, so a bee can find it (roadmap 9.8).
    assert rig.honey_store is not None
    titles = [nectar.title for nectar in await rig.honey_store.pending_nectar(20)]
    assert any(title.startswith("Tool echo_tool 1.0.0") for title in titles)
    await rig.aclose()


async def test_a_tool_the_comb_fails_is_rejected_with_the_gate_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch, scaffolder=_socket_tool)

    await rig.send(rig.request())
    await rig.settle()

    error = await _error(rig)
    assert error.code == replies.REJECTED and error.failed_kind == "tool.request"
    assert "static" in error.message
    # One retry carrying the failing report, then the rejection; nothing is promoted.
    assert len(rig.scaffolder.briefs) == 2
    assert rig.scaffolder.briefs[1].previous_report is not None
    assert await rig.registry.list_tools() == ()
    assert (await _kinds(rig, "tool"))[-1] == "tool.rejected"
    await rig.aclose()


async def test_a_scaffold_that_cannot_be_made_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch, scaffolder=_failing)

    await rig.send(rig.request())
    await rig.settle()

    error = await _error(rig)
    assert error.code == replies.REJECTED and "scaffold" in error.message
    await rig.aclose()


async def test_a_hive_with_no_scaffolder_model_rejects_at_the_scaffold_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch, bound=False)

    await rig.send(rig.request())
    await rig.settle()

    error = await _error(rig)
    assert error.code == replies.REJECTED and "scaffold" in error.message
    await rig.aclose()


async def test_a_request_for_a_hive_with_no_lab_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch, lab=False)

    await rig.send(rig.request())

    assert (await _error(rig)).code == replies.SANDBOX_UNAVAILABLE
    assert rig.deps.tool_lane.jobs == {}
    await rig.aclose()


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"required_capabilities": ("net:example.org",)}, "does not hold"),
        ({"required_capabilities": ("not a capability",)}, "does not parse"),
        ({"scope": ToolScope.CELL}, "cell-scope"),
    ],
)
async def test_a_request_the_requester_may_not_make_is_refused_at_the_door(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, overrides: dict[str, object], fragment: str
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)

    await rig.send(rig.request(**overrides))

    error = await _error(rig)
    assert error.code == replies.REQUEST_REFUSED and fragment in error.message
    assert rig.deps.tool_lane.jobs == {} and rig.scaffolder.briefs == []
    await rig.aclose()


async def test_a_request_naming_another_warden_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)
    forged = rig.request().model_copy(update={"warden_id": new_warden_id(rig.deps.clock)})

    await rig.send(forged)

    assert (await _error(rig)).code == replies.REQUEST_REFUSED
    await rig.aclose()


async def test_a_second_request_for_a_tool_in_flight_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = asyncio.Event()
    rig = await make_tool_rig(tmp_path, monkeypatch)

    class _Waiting:
        async def scaffold(self, brief: ToolRequestBrief) -> ScaffoldResult:
            await gate.wait()
            raise ScaffoldFailedError(brief.name, "released")

    monkeypatch.setattr(
        "hivemind.queen.ticks.tools.requests.ModelScaffolder", lambda *_: _Waiting()
    )
    request = rig.request()
    await rig.send(request)
    await rig.send(request)

    error = await _error(rig)
    assert error.code == replies.REQUEST_REFUSED and "already in flight" in error.message
    gate.set()
    await rig.settle()
    assert (await _error(rig)).code == replies.REJECTED  # The first request still gets its answer.
    await rig.aclose()


async def test_a_full_lane_refuses_a_new_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch, scaffolder=_failing)
    rig.deps.tool_lane.limit = 0

    await rig.send(rig.request())

    assert (await _error(rig)).code == replies.REQUEST_REFUSED
    await rig.aclose()


async def _place_worker_task(rig: ToolRig, capabilities: tuple[str, ...] | None) -> Task:
    """Submit one task with the goal ceiling `capabilities` and place it on the rig's Warden."""
    graph = make_graph_draft({"a": ()})
    draft = graph.tasks[0].model_copy(update={"capabilities": capabilities})
    (task,) = await rig.deps.chamber.submit(graph.model_copy(update={"tasks": (draft,)}))
    cell = rig.link.cell
    return await rig.deps.chamber.assign(
        task.id, rig.link.warden_id, cell.id, "a test", bound_tier=cell.comb_shield
    )


async def test_a_workers_request_is_held_to_its_goals_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch, scaffolder=_failing)
    task = await _place_worker_task(rig, ("fs:read:**",))
    worker = new_worker_id(rig.deps.clock)
    inside = rig.request(worker_id=worker, task_id=task.id, required_capabilities=("fs:read:**",))
    outside = rig.request(worker_id=worker, task_id=task.id, required_capabilities=("exec:*",))

    await rig.send(outside)
    refused = await _error(rig)
    await rig.send(inside)
    await rig.settle()
    started = await _error(rig)

    assert refused.code == replies.REQUEST_REFUSED and "exec:*" in refused.message
    # The request inside the ceiling got as far as the scaffolder (scripted to fail).
    assert started.code == replies.REJECTED and len(rig.scaffolder.briefs) == 1
    await rig.aclose()


async def test_a_workers_request_for_a_task_that_is_not_its_wardens_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch, scaffolder=_failing)
    worker = new_worker_id(rig.deps.clock)

    await rig.send(rig.request(worker_id=worker, task_id=None))
    no_task = await _error(rig)
    await rig.send(rig.request(worker_id=worker, task_id=new_task_id(rig.deps.clock)))

    assert no_task.code == replies.REQUEST_REFUSED and "task" in no_task.message
    assert (await _error(rig)).code == replies.REQUEST_REFUSED
    await rig.aclose()
