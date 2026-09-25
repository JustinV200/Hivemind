"""End-to-end: a Drone lured and refused on the Hive Stand; the Queen falls back on the request.

Roadmap steps 10.6 and 10.6a on a real run, the whole chain with nothing stubbed. The Hive is
composed by `build_hive` from a real manifest: real SQLite, the Hive Stand's real lease and
scratch, the composed Guard Bee on the Queen's tick and her real Guard request door. One real
Drone, scripted over a FakeLLMProvider, lands outside text carrying seed payloads in its scratch
with a real command, as a download would, and reads it back: the Worker's tool registry flags it
before the model reads a word (`guard.injection_suspected`). Steered, it reaches for a host it holds
no `net` capability for (`guard.denied`), then starts a long command.

The Guard Bee correlates the flag and the refusal in that one episode and files one request
through the Queen's door, durable in her table. Its rule is a shipped dire pattern, so she decides
it by rule on the tick it wakes, reaching to isolate the Cell. The Cell is the Hive Stand's own
lease, which only the human may isolate: the `isolation` point refuses her (`guard.denied`,
`guard.scope.hive_stand`), nothing is isolated, and the Hive Stand fallback fires instead. The
bee's task is quarantined through its Warden in the middle of its command (checkpointed, paused),
its goal is held off the Hive Stand on her decision's own row, and the human gets one CRITICAL
SECURITY Alarm naming the report.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests.e2e.test_guard_bee_on_virtual_cell for the same lure isolating a Virtual Cell.
    - tests.unit.queen.guard_requests.test_hive_stand for the fallback over fakes.
    - docs/guard/guard-bee.md and docs/guard/isolation.md.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.cli import ManifestTuning, fake_manifest
from builders.guard_bee import lure_call, quick_rounds
from e2e.kernel_helpers import (
    HaikuScript,
    single_task_plan,
    text_response,
    tool_response,
    tool_round_count,
    wait_until,
)

from hivemind.brood_chamber import TaskFilter, TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.cli.compose import Hive, build_hive, run_hive
from hivemind.guard import GuardReport
from hivemind.llm import LLMRequest, LLMResponse
from hivemind.manifest import load_manifest
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.queen.guard_requests import GuardRequest, report_alarm_id
from hivemind.queen.guard_requests.decision import FALLBACK_OUTCOME
from hivemind.supervision import AlarmKind, AlarmSeverity
from waggle.clock import SystemClock
from waggle.ids import TaskId

pytestmark = pytest.mark.e2e

_WAIT_S = 20.0  # Bound on each step's wait; each lands within a second or two.
# The quarantine runs in one Warden tick, its command's kill grace included, so the Queen's
# missed-beats rule needs a cadence that tick fits inside (as the quarantine e2e found).
_HEARTBEAT_S = 0.5


def _lured_drone(request: LLMRequest) -> LLMResponse:
    """The WORKER script: the lure's calls in order, keyed on the tool results so far."""
    call = lure_call(tool_round_count(request))
    if call is None:
        return text_response("Never reached: the quarantine ends this attempt mid-command.")
    return tool_response(request, (call,))


def _hive(tmp_path: Path) -> Hive:
    """Compose the Hive exactly as `hive run` does, the Guard Bee reading every half second."""
    tuning = ManifestTuning(heartbeat_interval_s=_HEARTBEAT_S)
    manifest_path = quick_rounds(fake_manifest(tmp_path, tuning=tuning), interval_s=_HEARTBEAT_S)
    script = HaikuScript(_lured_drone, plan=single_task_plan("haiku_1.txt"))
    return build_hive(
        load_manifest(manifest_path, {}),
        environ={},
        clock=SystemClock(),
        responders={"fake": script.responder},
    )


async def _events(hive: Hive, kind: str) -> list[PheromoneEvent]:
    """Every `kind` event on the Queen's trail, oldest first."""
    return list(await hive.stores.trail.query(TrailQuery(kind=kind)))


async def _filed(hive: Hive) -> GuardRequest | None:
    """The one request the Guard Bee filed, once its alert says so."""
    alerts = [a for a in await _events(hive, "guard.alert") if a.payload["disposition"] == "filed"]
    if not alerts:
        return None
    return await hive.queen_deps.guard.requests.get(str(alerts[0].payload["report_id"]))


async def _decided(hive: Hive) -> bool:
    """Whether the Queen has stamped her decision on the filed request."""
    row = await _filed(hive)
    return row is not None and row.decision is not None


async def _task_paused(hive: Hive, task_id: TaskId) -> bool:
    """Whether the quarantine has left the task PAUSED in the Brood Chamber."""
    return (await hive.stores.chamber.get(task_id)).status is TaskStatus.PAUSED


async def _scenario(hive: Hive) -> tuple[GuardRequest, TaskId]:
    """Run the lured goal until the Queen's fallback has quarantined its task; its goal id."""
    async with run_hive(hive):
        goal_id = await hive.queen.submit_goal("write one haiku", clearance=HoneyClearance.C1)
        await wait_until(lambda: _decided(hive), timeout_s=_WAIT_S)
        [task] = await hive.stores.chamber.list(TaskFilter(goal_id=goal_id))
        await wait_until(lambda: _task_paused(hive, task.id), timeout_s=_WAIT_S)
        row = await _filed(hive)
        assert row is not None
        await _assert_refused_and_fallen_back(hive, row.report, task.id)
    return row, goal_id


async def _assert_refused_and_fallen_back(hive: Hive, report: GuardReport, task_id: TaskId) -> None:
    """The isolation point refused her on the Hive Stand, and the fallback fired instead."""
    stand = hive.warden_link.cell.id
    filed = [a for a in await _events(hive, "guard.alert") if a.payload["disposition"] == "filed"]
    assert len(filed) == 1  # Exactly one request, however many rounds the Guard Bee ran.
    refusals = {
        (e.payload["point"], e.payload["rule"]) for e in await _events(hive, "guard.denied")
    }
    assert ("isolation", "guard.scope.hive_stand") in refusals
    assert await _events(hive, "cell.isolated") == []
    [intervened] = await _events(hive, "warden.intervened")
    assert (intervened.payload["action"], intervened.payload["task_id"]) == ("QUARANTINE", task_id)
    # One CRITICAL SECURITY Alarm names the report (the quarantine raises its own beside it).
    [alarm] = [a for a in hive.queen.human_inbox.alarms if a.id == report_alarm_id(report.id)]
    assert (alarm.kind, alarm.severity) == (AlarmKind.SECURITY, AlarmSeverity.CRITICAL)
    assert report.id in alarm.detail
    assert report.cell_id == stand


def test_a_lured_drone_on_the_hive_stand_is_quarantined_on_the_guard_bees_request(
    tmp_path: Path,
) -> None:
    hive = _hive(tmp_path)

    row, goal_id = asyncio.run(_scenario(hive))

    report, decision, hold = row.report, row.decision, row.hold
    assert (report.rule, report.recommended.value) == ("injection_then_denial", "quarantine_bee")
    assert decision is not None and decision.outcome == FALLBACK_OUTCOME
    # Her goal is held off the Hive Stand, on the decision's own row.
    assert hold is not None and (hold.cell_id, hold.goal_ids) == (report.cell_id, (goal_id,))
