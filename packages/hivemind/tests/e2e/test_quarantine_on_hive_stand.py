"""End-to-end: a real Drone quarantined mid-command on the Hive Stand, let out only once cleared.

Roadmap step 10.6c on a real run. The Hive is composed by `build_hive` from a real manifest: real
SQLite, the Hive Stand's real lease and scratch, and one real Drone scripted over a
FakeLLMProvider. The Drone writes its first haiku, then starts a real command that would run for a
minute. While that command runs, the Queen orders a quarantine through `Queen.intervene`, the
lever a Guard request pulls, naming the bee, its task and the command's `capping.proposed` event
as the episode its memory is suspect from. The Hive Stand's Warden writes the checkpoint first,
then stops the Drone, and the command's process dies with it while the lease is still open; it
records `warden.intervened`, taints the checkpoint (`memory.tainted`, which the Handoff loader then
refuses) and tells the Queen: the task is PAUSED in the Brood Chamber and a SECURITY Alarm reaches
the human's inbox.

Then the only way out, on the same run. The Queen's resume from the checkpoint while it is still
tainted is refused at the `quarantine` point (`guard.denied`), nothing spawns, and the task is held
again. Once a judge has cleared the checkpoint (`clear_taint`, CLEARED), the same resume is
admitted: a fresh Drone resumes from the cleared checkpoint and the goal finishes with its three
real files.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests.unit.wardens.quarantine for the same path over fakes, refusal by refusal.
    - tests.e2e.test_kernel_on_hive_stand for the scripting this reuses.
    - docs/adr/0035-guard-bee-requests-queen-only-isolation-and-tainted-memory.md.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from builders.cli import ManifestTuning, fake_manifest
from builders.taint import make_taint_judge
from e2e.kernel_helpers import (
    HaikuScript,
    assert_kinds_in_order,
    pid_alive,
    single_task_plan,
    text_response,
    tool_response,
    tool_round_count,
    wait_until,
    write_call,
)

from hivemind.brood_chamber import TaskFilter, TaskStatus
from hivemind.cell import HoneyClearance, LeaseState
from hivemind.cli.compose import GoalReport, Hive, build_hive, run_goal, run_hive
from hivemind.guard import QUEEN_ROLE, queen_principal, role_set
from hivemind.llm import LLMRequest, LLMResponse, TextPart
from hivemind.manifest import load_manifest
from hivemind.memory import MemoryContext, TaintedMemoryError, read_handoff
from hivemind.memory.taint import (
    ClearOutcome,
    TaintClearDeps,
    TaintClearRequest,
    TaintedKind,
    TaintSource,
    TaintTarget,
    clear_taint,
)
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.queen import resume_paused
from hivemind.supervision import AlarmKind, Quarantine
from waggle.clock import SystemClock
from waggle.ids import EventId, TaskId, WorkerId
from waggle.messages import HandoffRef
from waggle.messages.labels import HoneyClearance as WireHoneyClearance

pytestmark = pytest.mark.e2e

_GOAL = "write three haiku about bees to separate files"
_GOAL_TIMEOUT_S = 30.0  # The whole scenario, quarantine and way out included; a few seconds.
_WAIT_S = 10.0  # Bound on each step's wait; each lands well inside a second.
_FILES = ("haiku_1.txt", "haiku_2.txt", "haiku_3.txt")
_LINGER_S = 60  # The command outlives the scenario: only the quarantine can end it.
# The quarantine runs in one Warden tick, its command's kill grace included (0.2 s), so the
# Queen's three-missed-beats offline rule needs a cadence that tick fits inside, as the shipped
# 5 s one does; the builder's own 0.05 s would read that one tick as an unreachable Cell.
_HEARTBEAT_S = 0.5


class _SuspectDrone:
    """The WORKER script: one haiku, then a long command; once let out, the rest of the work."""

    def __init__(self) -> None:
        """Script a Drone whose first attempt is still running its command when quarantined."""
        self.let_out = False  # Set by the test just before the resume a judge's verdict allows.
        self.resumed_on = ""  # What the let-out attempt's first call showed the model.

    def turn(self, request: LLMRequest) -> LLMResponse:
        """Answer one WORKER-slot call, keyed on the attempt and its tool results so far."""
        count = tool_round_count(request)
        if self.let_out:
            if count == 0:  # Resumed from the cleared checkpoint: the rest of the work.
                self.resumed_on = _shown(request)
                return tool_response(request, tuple(write_call(name) for name in _FILES))
            return text_response("Three haiku written.")
        if count == 0:
            return tool_response(request, (write_call(_FILES[0]),))
        if count == 1:  # The command a Guard request flags: still running when the order lands.
            linger = [sys.executable, "-c", f"import time; time.sleep({_LINGER_S})"]
            return tool_response(request, (("linger", "run_command", {"argv": linger}),))
        return text_response("Never reached: the quarantine ends this attempt mid-command.")


def _shown(request: LLMRequest) -> str:
    """Every word one request shows the model: its system prompt and its turns."""
    parts = [part for message in request.messages for part in message.parts]
    return "\n".join([request.system or "", *(p.text for p in parts if isinstance(p, TextPart))])


@dataclass(frozen=True, slots=True)
class _Held:
    """What the quarantine left: the task, its bee, the killed command and the checkpoint."""

    task_id: TaskId
    bee: WorkerId
    pid: int  # The command's process, alive when the order was given.
    suspect: EventId  # The command's capping.proposed: the bee's memory is suspect from here.
    intervened: PheromoneEvent  # The Warden's own warden.intervened row.
    checkpoint: HandoffRef  # The Handoff the quarantine wrote first, and tainted.


async def _events(hive: Hive, kind: str | None = None) -> list[PheromoneEvent]:
    """Every trail event of `kind` (every event when None), oldest first."""
    return list(await hive.stores.trail.query(TrailQuery(kind=kind)))


async def _status_is(hive: Hive, task_id: TaskId, status: TaskStatus) -> bool:
    """Whether `task_id` is at `status` in the Brood Chamber (a `wait_until` condition)."""
    return (await hive.stores.chamber.get(task_id)).status is status


async def _denied(hive: Hive) -> bool:
    """Whether the Warden's quarantine gate has refused a respawn (a `wait_until` condition)."""
    return bool(await _events(hive, "guard.denied"))


async def _quarantine_mid_command(hive: Hive) -> _Held:
    """Wait for the Drone's command to run, then order its quarantine as a Guard request would."""
    lease = hive.warden.lease
    assert lease is not None
    # The command is the one process this run starts; its pid is recorded the moment it spawns.
    await wait_until(lambda: bool(lease.started_pids), timeout_s=_WAIT_S)
    pid = lease.started_pids[-1]
    assert pid_alive(pid)
    [task] = await hive.stores.chamber.list(TaskFilter())
    [bee] = hive.warden.sub_bees
    suspect = (await _events(hive, "capping.proposed"))[-1].id
    lever = Quarantine(
        reason="Guard request: a dire pattern on this bee's command.",
        bee=bee.worker_id,
        task_id=task.id,
        suspect_episode_id=suspect,
    )

    await hive.queen.intervene(hive.queen.wardens[0].warden_id, lever)
    await wait_until(lambda: _status_is(hive, task.id, TaskStatus.PAUSED), timeout_s=_WAIT_S)
    await wait_until(lambda: bool(hive.queen.human_inbox.alarms), timeout_s=_WAIT_S)

    [intervened] = await _events(hive, "warden.intervened")
    event_id = EventId(str(intervened.payload["handoff_event_id"]))
    [written] = [
        event for event in await _events(hive, "memory.checkpoint") if event.id == event_id
    ]
    checkpoint = HandoffRef(
        event_id=event_id, written_at=written.at, clearance=WireHoneyClearance.C1
    )
    return _Held(task.id, bee.worker_id, pid, suspect, intervened, checkpoint)


async def _assert_quarantined(hive: Hive, held: _Held) -> None:
    """The bee is gone and its command dead, the lease still open, the checkpoint refused."""
    # Killed by the quarantine, not by a release: the lease is still open while it dies.
    await wait_until(lambda: not pid_alive(held.pid), timeout_s=_WAIT_S)
    lease = hive.warden.lease
    assert lease is not None and lease.state is LeaseState.OPEN
    assert hive.warden.sub_bees == ()
    payload = held.intervened.payload
    assert (payload["action"], payload["ordered_by"]) == ("QUARANTINE", "queen")
    assert (payload["task_id"], payload["worker_id"]) == (held.task_id, held.bee)
    assert payload["suspect_episode_id"] == held.suspect
    tainted = await _events(hive, "memory.tainted")
    assert held.checkpoint.event_id in {event.subject_id for event in tainted}
    assert {(e.payload["source"], e.payload["cause_event_id"]) for e in tainted} == {
        (TaintSource.QUARANTINE.value, held.intervened.id)
    }
    with pytest.raises(TaintedMemoryError):
        await read_handoff(hive.stores.memory, held.checkpoint, HoneyClearance.C1)
    assert [alarm.kind for alarm in hive.queen.human_inbox.alarms] == [AlarmKind.SECURITY]


async def _resume(hive: Hive, held: _Held) -> None:
    """The Queen's resume from the quarantine checkpoint: a fresh grant and a TaskAssign."""
    await resume_paused(
        hive.queen._deps,
        hive.queen.wardens,
        held.task_id,
        held.checkpoint,
        "Resume from the quarantine checkpoint.",
    )


async def _clear(hive: Hive, held: _Held) -> ClearOutcome:
    """Have a judge (scripted CLEAR) review the checkpoint, the Queen as the clearer."""
    deps = hive.queen._deps
    judge, _provider = make_taint_judge("CLEAR")
    request = TaintClearRequest(
        target=TaintTarget(kind=TaintedKind.HANDOFF, item_id=held.checkpoint.event_id),
        clearer=queen_principal(deps.identity.hive_id),
        held=role_set(deps.enforcer.policy, QUEEN_ROLE),
    )
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    result = await clear_taint(
        request, TaintClearDeps(judge=judge, enforcer=deps.enforcer, ctx=ctx)
    )
    return result.outcome


async def _way_out(hive: Hive, drone: _SuspectDrone, held: _Held) -> None:
    """Refused while tainted and held again; cleared by the judge, then let out."""
    await _resume(hive, held)
    await wait_until(lambda: _denied(hive), timeout_s=_WAIT_S)
    await wait_until(lambda: _status_is(hive, held.task_id, TaskStatus.PAUSED), timeout_s=_WAIT_S)
    [denied] = await _events(hive, "guard.denied")
    assert (denied.payload["point"], denied.payload["rule"]) == (
        "quarantine",
        "guard.scope.quarantine_checkpoint",
    )
    assert hive.warden.sub_bees == ()  # Nothing spawned from the tainted checkpoint.

    assert await _clear(hive, held) is ClearOutcome.CLEARED
    drone.let_out = True
    await _resume(hive, held)


async def _run(hive: Hive, drone: _SuspectDrone) -> tuple[GoalReport, list[PheromoneEvent]]:
    """Run the goal through its quarantine and its way out, to the end."""
    async with run_hive(hive):
        goal = asyncio.ensure_future(
            run_goal(hive, _GOAL, clearance=HoneyClearance.C1, timeout_s=_GOAL_TIMEOUT_S)
        )
        held = await _quarantine_mid_command(hive)
        await _assert_quarantined(hive, held)
        await _way_out(hive, drone, held)
        report = await goal
    return report, await _events(hive)


def test_a_drone_quarantined_mid_command_is_killed_held_and_let_out_only_once_cleared(
    tmp_path: Path,
) -> None:
    tuning = ManifestTuning(heartbeat_interval_s=_HEARTBEAT_S)
    manifest = load_manifest(fake_manifest(tmp_path, tuning=tuning), {})
    drone = _SuspectDrone()
    script = HaikuScript(drone.turn, plan=single_task_plan(*_FILES))
    hive = build_hive(
        manifest, environ={}, clock=SystemClock(), responders={"fake": script.responder}
    )

    report, events = asyncio.run(_run(hive, drone))

    # Let out from the cleared checkpoint, the goal finished with its real files.
    assert report.succeeded, report
    assert "Quarantine checkpoint written by" in drone.resumed_on
    kinds = [event.kind for event in events]
    assert_kinds_in_order(
        kinds,
        (
            "memory.checkpoint",
            "warden.intervened",
            "memory.tainted",
            "task.paused",
            "guard.denied",
            "memory.taint_cleared",
            "task.succeeded",
        ),
    )
    # Two Drones: the quarantined one, and the one let out after the verdict, never before it.
    spawned = [index for index, kind in enumerate(kinds) if kind == "worker.spawned"]
    assert len(spawned) == 2 and spawned[1] > kinds.index("memory.taint_cleared")
