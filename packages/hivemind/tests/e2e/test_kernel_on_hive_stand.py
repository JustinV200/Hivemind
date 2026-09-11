"""End-to-end tests for the Queen kernel on the Hive Stand: roadmap step 3.22's eight scenarios.

Every scenario builds a `hivemind.cli.compose.Hive` over a `builders.cli.fake_manifest` and one
`hivemind.llm.fake.FakeLLMProvider`, scripted through `tests.e2e.kernel_helpers.HaikuScript`, then
drives it with `hivemind.cli.compose.run_hive`/`run_goal` (or, for the CLI-shaped half of scenario
(a), `hive run` itself through `typer.testing.CliRunner`) -- the real Hive Stand, a real SQLite
file and, where a scenario scripts `run_command`, a real child process. Every scenario runs at
both `ProviderCapabilities.full()` and `.none()` (roadmap step 3.22's own "at full and at zero
capabilities"), parametrised through `_LEVELS`, and every scenario runs its Hive exactly once --
the checkpoint suite's own fresh-Hive retry loops (`_retry_goal` and scenario (d)'s own bespoke
one, plus scenario (d)'s own `heartbeat_interval_s` manifest tuning) are gone now that the two
kernel fix-forward commits (`git log`: "fix(kernel): make Alarms reach the trail and the rebind
chain work end to end", then "fix(kernel): record Worker-raised Alarms, never drop a pending
Alarm, forward Answers by one rule, stop cleanly") closed the races they were guarding against,
scenario (d)'s own cross-process Answer race included (see that scenario's own docstring, below,
for exactly which fix closed it).

Scenario (a)'s own trail order deserves its own note: the roadmap's prose lists "decompose ->
placed -> leased -> granted -> spawned -> ...", but the kernel leases *first*, because the Hive
Stand's own Warden leases its Cell at `Warden.start()` (roadmap step 3.19), before the Queen ever
plans a goal; `queen.assigned` is the placement event and `task.succeeded` is the acceptance one
(roadmap step 3.18). `tests.unit.cli.test_compose`'s own required-order unit test asserts one
single total order for all of `queen.assigned`/`forage.granted`/`worker.spawned`/the Capping
cycle, but that only holds under its own `FakeClock` pump, where every trail write is an in-memory,
synchronous append with no real suspension in between. On a real `SystemClock` over real SQLite
(this module's own way of running the same goal), `hivemind.queen.dispatcher._dispatch_one` sends
the `GrantIssued`/`TaskAssign` wire messages *before* it records its own `queen.assigned`/
`forage.granted` trail events, and the first real disk write in either the Queen's own remaining
bookkeeping or the Warden's own reaction to those messages (an `asyncio.to_thread`-bound SQLite
write, `hivemind.pheromone.trail.sqlite`) is a genuine suspension point -- so a fast-reacting
Warden can, and observably does, record `worker.spawned` before the Queen's own `queen.assigned`
lands. `_CAUSAL_TRAIL_ORDER` below asserts only the relationships the code's own structure
actually guarantees regardless of that race (each group is a strict sequence within one
coroutine, or otherwise causally impossible to invert); `forage.granted` and `worker.spawned` are
asserted present, not ordered against the rest.

Scenario (g)'s own Alarm chain is worth a note here too, since no single scenario file change
closed it -- two fixes from the same second fix-forward commit had to land together. Before that
commit, no component ever called `hivemind.supervision.alarm_trail.record_alarm_event(...,
"alarm.raised")` for a Worker-originated Alarm (a crash, or a Capping `ROLLED_BACK` outcome):
`hivemind.workers.runtime.reporter.Reporter.send_alarm` built and sent the wire `AlarmRaised` but
never recorded a trail event of its own, unlike a Warden's own *self*-raised Alarms. That commit's
own fix 1 closed the gap directly (`send_alarm` now calls `record_alarm_event` before the wire
send); its own fix 2 closed a second, independent gap that would otherwise have kept the Alarm
from ever being *sent* in time to matter here -- a Capping rollback noted mid-attempt now flushes
before every terminal `WorkerState` transition (`hivemind.workers.runtime.attempt.AttemptManager`'s
four `_finish_*` methods), so it always leaves on the Worker's own mailbox before the `TaskResult`
that closes the same attempt, on the same ordered link. Scenario (g) below asserts the whole
chain -- `capping.rolled_back`, `alarm.raised` (kind `POSTCONDITION_FAILED`) and `alarm.handled`
(the Warden's own policy dispatch, action `RETRY` per the shipped
`supervision/defaults/default-policy.toml`'s own `POSTCONDITION_FAILED`@1 row) -- with no
xfail and no retry.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md step 3.22 for the eight scenarios this module proves.
    - .claude/roadmap.md steps 3.18/3.19 for why leased/assigned/succeeded name what they do.
    - tests.unit.cli.test_compose for the three-haiku unit test scenario (a) mirrors end to end.
    - tests.e2e.kernel_helpers for HaikuScript, wait_until, snapshot_tree and this module's other
      scripting primitives.
    - tests.e2e.README.md for this suite's own budget and the one-line meaning of every trail
      event named above.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest
from builders.cli import ManifestTuning, fake_manifest
from e2e.kernel_helpers import (
    HaikuScript,
    WorkerTurn,
    assert_kinds_in_order,
    default_worker_turn,
    pid_alive,
    snapshot_tree,
    text_response,
    tool_response,
    tool_round_count,
    wait_until,
    write_call,
)
from typer.testing import CliRunner

import hivemind.cli.run as run_module
from hivemind.brood_chamber import TaskFilter, TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.cli.app import app
from hivemind.cli.compose import GoalReport, Hive, HiveStores, build_hive, run_goal, run_hive
from hivemind.cli.compose import build_hive as real_build_hive
from hivemind.llm import LLMRequest, LLMResponse, Responder
from hivemind.manifest import HiveManifest, load_manifest
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.supervision import Checkpoint
from waggle.clock import Clock, SystemClock

pytestmark = pytest.mark.e2e

_LEVELS = pytest.mark.parametrize("capabilities", ("full", "none"))
_GOAL = "write three haiku about bees to separate files"
_TIMEOUT_S = 10.0  # Generous: every scenario here actually finishes in well under a second.
_DEFAULT_FILES = ("haiku_1.txt", "haiku_2.txt", "haiku_3.txt")
# Three groups, each a strict sequence within one coroutine (or otherwise causally impossible to
# invert -- see the module docstring); never asserted against each other, since the Queen's own
# post-dispatch bookkeeping and the Warden's own reaction to the wire messages it just sent race
# under real SQLite I/O.
_CAUSAL_TRAIL_GROUPS = (
    ("cell.leased", "queen.planned", "queen.assigned"),
    ("capping.proposed", "capping.capped", "capping.applied", "capping.verified"),
    ("task.succeeded", "cell.released"),
)
_ALSO_EXPECTED_KINDS = ("forage.granted", "worker.spawned")

runner = CliRunner()


def _hive(manifest_path: Path, script: HaikuScript) -> Hive:
    """Build a Hive from an already-written manifest, scripted with `script`'s own responder."""
    manifest = load_manifest(manifest_path, {})
    return build_hive(
        manifest, environ={}, clock=SystemClock(), responders={"fake": script.responder}
    )


async def _run_goal_and_events(
    hive: Hive, *, timeout_s: float = _TIMEOUT_S
) -> tuple[GoalReport, tuple[PheromoneEvent, ...]]:
    """Run `_GOAL` inside `run_hive` to completion, and return the report plus every trail event."""
    async with run_hive(hive):
        report = await run_goal(hive, _GOAL, clearance=HoneyClearance.C1, timeout_s=timeout_s)
    events = await hive.stores.trail.query(TrailQuery())
    return report, tuple(events)


async def _run_goal_to_completion(
    hive: Hive, *, timeout_s: float = _TIMEOUT_S
) -> tuple[GoalReport, list[str]]:
    """Run `_GOAL` inside `run_hive` to completion, and return the report plus every trail kind."""
    report, events = await _run_goal_and_events(hive, timeout_s=timeout_s)
    return report, [event.kind for event in events]


# ──────────────────────────────────────────────────────────────────────────────
# (a) the three-haiku goal completes, in the required trail order
# ──────────────────────────────────────────────────────────────────────────────


@_LEVELS
def test_three_haiku_goal_completes_in_the_required_trail_order(
    tmp_path: Path, capabilities: str
) -> None:
    """(a) plan -> lease -> grant -> spawn -> the Capping QA cycle -> accept (module docstring).

    `_hive`/`build_hive` (its own default `stores=None` opens real SQLite via `asyncio.run`
    internally) must run outside any event loop, so this test's own body stays synchronous and
    hands the async scenario to `asyncio.run` itself -- exactly `hivemind.cli.run.run_command`'s
    own "build_hive must run before its own asyncio.run" rule, and `tests.unit.cli.test_compose`'s
    own reason for a plain, non-async `plain_hive`/`three_haiku_hive` fixture.
    """
    manifest_path = fake_manifest(tmp_path, capabilities=capabilities)
    hive = _hive(manifest_path, HaikuScript(default_worker_turn))
    asyncio.run(_run_three_haiku_goal(tmp_path, hive))


async def _run_three_haiku_goal(tmp_path: Path, hive: Hive) -> None:
    """The async body `test_three_haiku_goal_completes_in_the_required_trail_order` drives."""
    async with run_hive(hive):
        report = await run_goal(hive, _GOAL, clearance=HoneyClearance.C1, timeout_s=_TIMEOUT_S)
        lease = hive.warden.lease
        assert lease is not None
        during = snapshot_tree(lease.scratch_root)  # captured before release empties it
    assert report.succeeded, report
    task = report.tasks[0]
    assert task.status is TaskStatus.SUCCEEDED
    assert task.outcome is not None and task.outcome.verified_by is not None
    assert set(during) == set(_DEFAULT_FILES)
    kinds = [event.kind for event in await hive.stores.trail.query(TrailQuery())]
    for group in _CAUSAL_TRAIL_GROUPS:
        assert_kinds_in_order(kinds, group)
    for kind in _ALSO_EXPECTED_KINDS:
        assert kind in kinds, f"{kind!r} never appeared on the trail: {kinds}"
    assert snapshot_tree(tmp_path / "scratch") == {}


def _patch_build_hive(monkeypatch: pytest.MonkeyPatch, script: HaikuScript) -> None:
    """Monkeypatch `hivemind.cli.run.build_hive`, the one seam `hive run` itself exposes."""

    def patched(
        manifest: HiveManifest,
        *,
        environ: Mapping[str, str],
        clock: Clock,
        stores: HiveStores | None = None,
        responders: Mapping[str, Responder] | None = None,
    ) -> Hive:
        return real_build_hive(
            manifest,
            environ=environ,
            clock=clock,
            stores=stores,
            responders={"fake": script.responder},
        )

    monkeypatch.setattr(run_module, "build_hive", patched)


@_LEVELS
def test_three_haiku_goal_completes_through_hive_run_cli(
    tmp_path: Path, capabilities: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(a), the CLI-shaped half: `hive run` finishes the same goal.

    Mirrors `tests.unit.cli.test_run`'s own `build_hive` monkeypatch -- `hive run` takes no
    `responders` argument of its own.
    """
    manifest_path = fake_manifest(tmp_path, capabilities=capabilities)
    _patch_build_hive(monkeypatch, HaikuScript(default_worker_turn))
    result = runner.invoke(
        app, ["run", _GOAL, "--manifest", str(manifest_path), "--timeout", str(_TIMEOUT_S)]
    )
    assert result.exit_code == 0, result.output
    assert "task.succeeded" in result.output
    assert "succeeded" in result.output.splitlines()[-1]


# ──────────────────────────────────────────────────────────────────────────────
# (b) a hand-killed Drone is respawned by Warden autopilot, no Queen awake episode
# ──────────────────────────────────────────────────────────────────────────────


def _cancel_the_first_drone_mid_attempt(
    hive_box: list[Hive], cancelled: dict[str, bool]
) -> WorkerTurn:
    """Build a WORKER script whose first call cancels the Drone that is making it.

    The scenario needs the sub-bee to still be working when its task is cancelled. Polling for
    `warden.sub_bees` on the real clock and cancelling from the test body does not guarantee that:
    a Drone answered by a fake provider finishes the whole three-file attempt in about the same
    time as one 20ms poll, so roughly one run in thirty (more under load) delivered the cancel
    after `worker.done` had already been sent -- nothing left to respawn, one `worker.spawned`,
    and a goal that legitimately succeeded. Cancelling from inside the Drone's own first model
    call removes the race instead of widening a timeout: `Task.cancel()` on the running task
    raises at its next await, which is always inside the attempt, so the watchdog
    (`hivemind.wardens.ticks.heartbeat.raise_stalled_alarms`) always has a stalled sub-bee to
    find.

    Args:
        hive_box: A one-element list holding the Hive, because the script has to be built before
            `build_hive` can be called with it.
        cancelled: `{"done": False}`, flipped on the first call so the respawned Drone runs the
            ordinary script and the goal finishes.
    """

    def worker_turn(request: LLMRequest) -> LLMResponse:
        if not cancelled["done"]:
            cancelled["done"] = True
            hive_box[0].warden.sub_bees[0].runtime_task.cancel()
        return default_worker_turn(request)

    return worker_turn


@_LEVELS
def test_a_killed_drone_is_respawned_by_warden_autopilot_with_no_queen_awake_episode(
    tmp_path: Path, capabilities: str
) -> None:
    """(b) a hand-cancelled sub-bee is respawned without any Queen awake episode.

    A hand-cancelled sub-bee is only ever noticed by the Warden's own heartbeat watchdog
    (`hivemind.wardens.ticks.heartbeat.raise_stalled_alarms`): `WORKER_STALLED` respawns at one
    missed cycle, a Warden-autopilot decision that never reaches `hivemind.wardens.awake` or the
    Queen at all.
    """
    manifest_path = fake_manifest(tmp_path, capabilities=capabilities)
    hive_box: list[Hive] = []
    script = HaikuScript(_cancel_the_first_drone_mid_attempt(hive_box, {"done": False}))
    hive = _hive(manifest_path, script)
    hive_box.append(hive)

    asyncio.run(_run_kill_and_respawn(hive))


async def _run_kill_and_respawn(hive: Hive) -> None:
    """The async body `test_a_killed_drone_is_respawned_...` drives."""
    async with run_hive(hive):
        report = await run_goal(hive, _GOAL, clearance=HoneyClearance.C1, timeout_s=_TIMEOUT_S)
    assert report.succeeded, report
    events = await hive.stores.trail.query(TrailQuery())
    kinds = [event.kind for event in events]
    # Two spawns, not one: the cancelled attempt never reached `worker.done`, so the Warden's
    # watchdog had a stalled sub-bee to respawn.
    assert kinds.count("worker.spawned") >= 2, kinds
    assert "queen.awake" not in kinds, kinds


# ──────────────────────────────────────────────────────────────────────────────
# (c) a Drone that crashes twice escalates to the Queen, who rebinds to the fallback
# ──────────────────────────────────────────────────────────────────────────────


def _crashing_worker_turn(crash_budget: dict[str, int], used_models: set[str | None]) -> WorkerTurn:
    """Build a WORKER script that crashes `crash_budget["count"]` times, then writes the files.

    Args:
        crash_budget: `{"count": N}`, decremented once per crashed attempt; shared with the
            caller so it can assert exactly how many crashes actually happened.
        used_models: Every call's own `request.model` is recorded into it, so the caller can prove
            the goal actually finished on the Queen's own fallback binding (`test-model-strong`),
            not the original one.
    """

    def worker_turn(request: LLMRequest) -> LLMResponse:
        used_models.add(request.model)
        if tool_round_count(request) == 0 and crash_budget["count"] > 0:
            crash_budget["count"] -= 1
            raise RuntimeError("simulated crash: the bound provider vanished mid-attempt.")
        return default_worker_turn(request)

    return worker_turn


@_LEVELS
def test_a_drone_that_crashes_repeatedly_escalates_and_the_queen_rebinds_it_to_completion(
    tmp_path: Path, capabilities: str
) -> None:
    """(c) a Warden RESPAWN, two escalations, then the Queen's own REBIND finishes the goal.

    The kernel fix-forward commit's own `supervision/defaults/default-policy.toml` gives
    `WORKER_CRASHED` a three-row ladder (RESPAWN@1, REBIND@2, ESCALATE@3), keyed on the Warden's
    own per-sub-bee attempt count (`hivemind.wardens.autopilot.table._decide_alarm`), never the
    wire `AlarmRaised.attempts` field. With `worker_fallback=True` (a second `[llm.slots.
    local_worker]` row, `[llm.slots.worker] fallback = "local_worker"`), this scenario's own three
    scripted crashes walk the whole chain: attempt 1 crashes -> the Warden's own RESPAWN@1 row
    retries on the same binding (attempt 2); attempt 2 crashes -> REBIND@2 fires, but this
    Warden's own grant carries only one binding to offer (`builders.cli.fake_manifest`'s own
    module docstring: the fallback chain is a Queen-side concept a Warden's grant never carries),
    so `hivemind.wardens.ticks.alarms._rebind` finds no local target and escalates instead; the
    Queen's own first decision (her own attempt counter starts at 1) is RETRY_TASK, redispatching
    a fresh sub-bee at attempt 2 -- which also crashes (the crash budget's own third and last),
    escalating a second time; the Queen's own second decision (attempts now 2) is REBIND
    (`hivemind.queen.ticks.alarms._rebind`), and she already resolved the fallback key for
    herself, so her own `Intervene(REBIND)` names it (`binding="local_worker"`);
    `hivemind.wardens.ticks.control._handle_queen_rebind` turns that into a real respawn, one
    attempt higher, on `local_worker` -- which the script no longer crashes, so the goal finishes
    there. This exact chain (`worker.failed` exactly 3, `worker.spawned` exactly 4, one
    `queen.decided(REBIND)` naming `local_worker`, the goal finishing on `test-model-strong`) held
    on 20/20 manual runs at both capability levels in this dispatch's own soak test, so this
    scenario needs no retry loop.
    """
    used_models: set[str | None] = set()
    manifest_path = fake_manifest(
        tmp_path, capabilities=capabilities, tuning=ManifestTuning(worker_fallback=True)
    )
    script = HaikuScript(_crashing_worker_turn({"count": 3}, used_models))
    hive = _hive(manifest_path, script)
    report, events = asyncio.run(_run_goal_and_events(hive))
    kinds = [event.kind for event in events]

    assert report.succeeded, report
    assert kinds.count("worker.failed") == 3
    assert kinds.count("worker.spawned") >= 4
    assert "alarm.escalated" in kinds
    rebind_events = [
        event
        for event in events
        if event.kind == "queen.decided" and event.payload.get("action") == "REBIND"
    ]
    assert len(rebind_events) == 1, rebind_events
    # The Queen's own trail payload names the fallback binding her Intervene(REBIND) carried.
    assert rebind_events[0].payload.get("binding") == "local_worker"
    # The goal actually finished on the fallback binding, not a retry of the original one.
    assert "test-model-strong" in used_models


# ──────────────────────────────────────────────────────────────────────────────
# (d) a Drone's question blocks the task; `hive inbox answer` resumes it
# ──────────────────────────────────────────────────────────────────────────────


async def _task_is_blocked(hive: Hive) -> bool:
    """Return whether the goal's own (only) task is currently BLOCKED on a question."""
    tasks = await hive.stores.chamber.list(TaskFilter())
    return bool(tasks) and tasks[0].status is TaskStatus.BLOCKED


def _blocked_question_worker_turn(request: LLMRequest) -> LLMResponse:
    """The WORKER script this scenario scripts: ask, then write, then stop."""
    count = tool_round_count(request)
    if count == 0:
        return tool_response(request, (("ask_1", "ask", {"text": "Which season?"}),))
    if count == 1:  # the ask's own result is round 0; write the real files next.
        return tool_response(request, tuple(write_call(name) for name in _DEFAULT_FILES))
    return text_response("Three haiku written.")


@_LEVELS
def test_a_drones_question_blocks_the_task_until_hive_inbox_answer_resumes_it(
    tmp_path: Path, capabilities: str
) -> None:
    """(d) a question blocks the task until one `hive inbox answer` (CliRunner) resumes it.

    `ask` blocks the task; `hive inbox`/`hive inbox answer` (CliRunner, each in its own worker
    thread) resolve it, and `hivemind.queen.questions.sync_answers_from_chamber` forwards the
    answer to the blocked Drone. The first kernel fix-forward commit closed the two races the
    checkpoint suite's own retry loop was guarding against (`hivemind.queen.dispatcher.
    _dispatch_one` now records the chamber transition before either wire send, and
    `sync_answers_from_chamber` retries instead of dropping tracking when the answer Note has not
    landed yet); this dispatch's own earlier soak test (30 single-Hive runs at this suite's usual
    heartbeat cadence) still found a third, distinct race about 15% of the time: the Queen's own
    tick loop is purely event-driven (`hivemind.queen.queen._run_tick`'s own `asyncio.wait` on the
    next envelope from each Warden link, never a `clock.sleep`-paced tick of its own), and it used
    to call a separate `route_answers` sweep that dropped a question's tracking as soon as it saw
    the task off BLOCKED (`chamber.answer()`, the first of `hive inbox answer`'s own two separate
    writes, moves it off BLOCKED immediately), whether or not the second write (the answer Note)
    had landed yet -- so a Heartbeat waking the tick loop in that narrow window could lose the
    answer for good. The second kernel fix-forward commit's own fix 3 removed `route_answers`
    entirely: the Queen's own tick now calls `sync_answers_from_chamber` itself, the exact same
    retry-safe function `run_goal`'s own 50ms poll loop already called, so there is exactly one
    rule -- "keep tracking until the Note is present, then forward once" -- applied from both
    places (`hivemind.queen.questions`'s own module docstring has the full mechanics). That closes
    the race structurally rather than by timing, so this scenario needs no `heartbeat_interval_s`
    tuning any more: `builders.cli.fake_manifest`'s own default cadence held 20/20 single-Hive runs
    clean at both capability levels in this dispatch's own soak test (see this dispatch's own
    report for the 20-run result recorded against this exact test).
    """
    manifest_path = fake_manifest(tmp_path, capabilities=capabilities)
    hive = _hive(manifest_path, HaikuScript(_blocked_question_worker_turn))
    asyncio.run(_run_blocked_question(hive, manifest_path))


async def _run_blocked_question(hive: Hive, manifest_path: Path) -> None:
    """The async body `test_a_drones_question_blocks_the_task_...` drives; one answer, no retry.

    Both `runner.invoke` calls run in a worker thread (`asyncio.to_thread`): `hive inbox`/`hive
    inbox answer` each call their own `asyncio.run` internally (`hivemind.cli.readback.inbox`'s
    own module docstring), which would otherwise collide with the event loop this coroutine (and
    `goal_task`, running concurrently on it) is already inside.
    """
    async with run_hive(hive):
        goal_task = asyncio.ensure_future(
            run_goal(hive, _GOAL, clearance=HoneyClearance.C1, timeout_s=_TIMEOUT_S)
        )
        await wait_until(lambda: _task_is_blocked(hive), timeout_s=_TIMEOUT_S)
        listed = await asyncio.to_thread(
            runner.invoke, app, ["inbox", "--manifest", str(manifest_path), "--json"]
        )
        assert listed.exit_code == 0, listed.output
        question_id = json.loads(listed.output)["questions"][0]["id"]
        answered = await asyncio.to_thread(
            runner.invoke,
            app,
            ["inbox", "answer", question_id, "spring", "--manifest", str(manifest_path)],
        )
        assert answered.exit_code == 0, answered.output
        report = await goal_task
    assert report.succeeded, report


# ──────────────────────────────────────────────────────────────────────────────
# (e) a Drone that checkpoints resumes and finishes from its Handoff
# ──────────────────────────────────────────────────────────────────────────────


async def _send_checkpoint(hive: Hive) -> None:
    """Intervene(Checkpoint) on the goal's own (only) sub-bee, mid-attempt."""
    sub_bee = hive.warden.sub_bees[0]
    await hive.warden.intervene(sub_bee.worker_id, Checkpoint(reason="scripted mid-attempt"))


@_LEVELS
def test_a_drone_that_checkpoints_resumes_and_finishes_from_its_handoff(
    tmp_path: Path, capabilities: str
) -> None:
    """(e) a scripted checkpoint resumes and finishes from its own Handoff.

    An `Intervene(Checkpoint)`, scheduled the moment the very first WORKER-slot call is answered,
    is this suite's own closest true trigger: `hivemind.workers.telemetry.TelemetryTracker.
    should_hand_off` runs before every tool call, but the one call that ever records tokens
    (`hivemind.workers.roles.drone.role._record_usage`) only runs once an attempt's own tool loop
    has *already* finished, so a token-threshold crossing can never fire from *within* the very
    attempt whose own usage would cross it. `hive.warden.intervene` is the same lever a Queen- or
    Warden-driven REBIND/TAKEOVER pulls; see this dispatch's own report. Exactly which round the
    checkpoint actually lands on (before or after the first write commits) is not
    deterministic -- there is no synchronous signal this suite can await between "the model asked
    to write" and "the write landed" without reaching into the runtime's own internals -- so the
    script itself is idempotent to either outcome: a fresh attempt's own round 0 always writes
    haiku_1.txt again (a harmless re-write if it already exists), then 1 and 2 always write
    haiku_2.txt/haiku_3.txt, so the resumed attempt finishes with all three files whichever round
    the handoff actually happened on.
    """
    hive_holder: dict[str, Hive] = {}
    checkpoint_sent = {"done": False}
    pending_tasks: list[asyncio.Task[None]] = []

    def worker_turn(request: LLMRequest) -> LLMResponse:
        count = tool_round_count(request)
        if count == 0:
            if not checkpoint_sent["done"]:
                checkpoint_sent["done"] = True
                pending_tasks.append(asyncio.ensure_future(_send_checkpoint(hive_holder["hive"])))
            return tool_response(request, (write_call("haiku_1.txt"),))
        if count == 1:
            return tool_response(request, (write_call("haiku_2.txt"),))
        if count == 2:
            return tool_response(request, (write_call("haiku_3.txt"),))
        return text_response("Three haiku written.")

    manifest_path = fake_manifest(tmp_path, capabilities=capabilities)
    hive = _hive(manifest_path, HaikuScript(worker_turn))
    hive_holder["hive"] = hive
    asyncio.run(_run_checkpoint_and_resume(hive))


async def _run_checkpoint_and_resume(hive: Hive) -> None:
    """The async body `test_a_drone_that_checkpoints_resumes_...` drives."""
    report, kinds = await _run_goal_to_completion(hive)
    assert report.succeeded, report
    assert "worker.handing_off" in kinds
    assert "worker.resumed" in kinds
    checkpoint_event = next(
        e for e in await hive.stores.trail.query(TrailQuery()) if e.kind == "memory.checkpoint"
    )
    handoff, _clearance = await hive.stores.memory.get_handoff(checkpoint_event.id)
    assert handoff is not None


# ──────────────────────────────────────────────────────────────────────────────
# (f) a write outside scratch, with no capability for it, is rejected and never lands
# ──────────────────────────────────────────────────────────────────────────────


@_LEVELS
def test_a_write_outside_scratch_without_the_capability_is_rejected_and_never_appears(
    tmp_path: Path, capabilities: str
) -> None:
    """(f) a write outside scratch is rejected and never lands; the goal still finishes.

    `write_file` outside scratch fails `PathAllowlistCheck` (no `fs:write` capability reaches
    there); the goal still finishes, because the script's next call writes the real files. This
    scenario never needed a retry: 30/30 single-Hive runs held clean in this dispatch's own soak
    test (nothing here touches the CLI/cross-process path scenario (d)'s own race lives in).
    """
    outside = tmp_path / "outside" / "never.txt"

    def worker_turn(request: LLMRequest) -> LLMResponse:
        count = tool_round_count(request)
        if count == 0:
            sneaky = ("sneaky", "write_file", {"path": str(outside), "content": "sneaky"})
            return tool_response(request, (sneaky,))
        if count == 1:
            return tool_response(request, tuple(write_call(name) for name in _DEFAULT_FILES))
        return text_response("Three haiku written.")

    manifest_path = fake_manifest(tmp_path, capabilities=capabilities)
    hive = _hive(manifest_path, HaikuScript(worker_turn))
    report, kinds = asyncio.run(_run_goal_to_completion(hive))

    # The kernel keeps going after the rejection (see the docstring above).
    assert report.succeeded, report
    assert "capping.rejected" in kinds
    assert not outside.exists()


# ──────────────────────────────────────────────────────────────────────────────
# (g) a failed proposal is rolled back
# ──────────────────────────────────────────────────────────────────────────────


def _failing_command_worker_turn() -> WorkerTurn:
    """Build a WORKER script: one `run_command` that exits non-zero, then the real writes.

    See the module docstring for why a non-zero COMMAND exit, not a mismatched `write_file`
    postcondition, is this suite's own closest real `ROLLED_BACK` trigger. The failure budget is
    spent at most once, regardless of which attempt spends it: `hivemind.workers.tools.proposals.
    cap` queues an Alarm on every ROLLED_BACK outcome (`ctx.telemetry.note_alarm`), flushed and
    sent no later than this attempt's own terminal transition (`hivemind.workers.runtime.attempt.
    AttemptManager`'s `_finish_*` methods, the kernel fix-forward commit's own fix 2), and the
    Warden's own policy (`supervision/defaults/default-policy.toml`'s
    POSTCONDITION_FAILED@1 -> RETRY row) may retire and respawn the sub-bee before or after it
    finishes on its own -- both are
    correct outcomes, but a script that could fail on a fresh, respawned attempt's own round 0 too
    would cascade into a second rollback (attempts=2 -> ESCALATE) and race the Queen's own
    concurrent retry against the original sub-bee's own natural completion. A budget of one is
    what keeps this scenario deterministic regardless of which of those two equally-correct
    outcomes actually happens.
    """
    budget = {"count": 1}

    def worker_turn(request: LLMRequest) -> LLMResponse:
        count = tool_round_count(request)
        if count == 0 and budget["count"] > 0:
            budget["count"] -= 1
            argv = [sys.executable, "-c", "import sys; sys.exit(1)"]
            return tool_response(request, (("fail", "run_command", {"argv": argv}),))
        if count in (0, 1):
            # count == 0: a fresh attempt after an earlier one already spent the budget (the
            # Warden's own RETRY killed and respawned it before this attempt could fail again).
            # count == 1: the failing round's own single result is round 0 of the SAME attempt (a
            # rollback never interrupts the current attempt); either way, nothing left to fail.
            return tool_response(request, tuple(write_call(name) for name in _DEFAULT_FILES))
        return text_response("Three haiku written.")

    return worker_turn


@_LEVELS
def test_a_failing_command_proposal_is_rolled_back(tmp_path: Path, capabilities: str) -> None:
    """(g) a failed command proposal is rolled back, its Alarm chain lands, and the goal finishes.

    A `run_command` whose own exit is non-zero is the real, reachable `ROLLED_BACK` path
    (`hivemind.supervision.capping.gate._apply_and_verify`'s own "a COMMAND's own non-zero exit
    is itself the failure"), since no shipped tool can ever fail a *declared postcondition* after
    a successful apply (module docstring).

    Before the second kernel fix-forward commit, this scenario's own Alarm was a genuine
    real-timing race: `hivemind.workers.runtime.loop.WorkerRuntime._drain_pending_alarms` only
    ever ran at the very top of a tick, so a rollback noted mid-attempt sat queued until this
    Worker's own `asyncio.wait` woke again -- a fresh envelope, or the next heartbeat deadline --
    racing the sub-bee's own natural completion (the real writes, round 1) with no manifest tuning
    able to close it cleanly (module's own git history). That commit's own fix 2 closed it
    structurally instead of by timing: every terminal `WorkerState` transition
    (`hivemind.workers.runtime.attempt.AttemptManager`'s four `_finish_*` methods) now flushes the
    pending-Alarm queue first, so the queued `ROLLED_BACK` Alarm always leaves this Worker's own
    mailbox before the `TaskResult` that closes the very same attempt -- on the same ordered link,
    so the Warden always processes the Alarm first. Combined with fix 1 (`Reporter.send_alarm` now
    records `alarm.raised` before the wire send), the whole chain -- `capping.rolled_back`,
    `alarm.raised` (kind `POSTCONDITION_FAILED`) and `alarm.handled` (the Warden's own policy
    dispatch, action `RETRY` per `supervision/defaults/default-policy.toml`'s own
    `POSTCONDITION_FAILED`@1 row) -- is now asserted deterministically below.
    """
    manifest_path = fake_manifest(tmp_path, capabilities=capabilities)
    hive = _hive(manifest_path, HaikuScript(_failing_command_worker_turn()))
    asyncio.run(_run_rolled_back_proposal(tmp_path, hive))


async def _run_rolled_back_proposal(tmp_path: Path, hive: Hive) -> None:
    """The async body `test_a_failing_command_proposal_is_rolled_back` drives."""
    async with run_hive(hive):
        report = await run_goal(hive, _GOAL, clearance=HoneyClearance.C1, timeout_s=_TIMEOUT_S)
        lease = hive.warden.lease
        assert lease is not None
        during = snapshot_tree(lease.scratch_root)  # captured before release empties it
    assert report.succeeded, report
    events = await hive.stores.trail.query(TrailQuery())
    kinds = [event.kind for event in events]
    assert "capping.rolled_back" in kinds
    # Restored prior state: nothing the rolled-back command touched lingers; scratch holds exactly
    # the three real haiku files the script's own next round wrote (never a stray/partial file).
    assert set(during) == set(_DEFAULT_FILES)
    # The rollback's own Alarm chain: raised by the Worker (kind POSTCONDITION_FAILED, module
    # docstring), then handled by the Warden's own policy (action RETRY, one hop later).
    raised = [event for event in events if event.kind == "alarm.raised"]
    assert len(raised) == 1, raised
    assert raised[0].payload.get("kind") == "POSTCONDITION_FAILED", raised[0].payload
    handled = [event for event in events if event.kind == "alarm.handled"]
    assert len(handled) == 1, handled
    assert handled[0].payload.get("action") == "RETRY", handled[0].payload


# ──────────────────────────────────────────────────────────────────────────────
# (h) the left-as-found snapshot holds
# ──────────────────────────────────────────────────────────────────────────────


def _pid_is_dead(pid: int) -> Callable[[], bool]:
    """Build a zero-argument `wait_until` condition for one pid, binding it by value."""
    return lambda: not pid_alive(pid)


@_LEVELS
def test_the_hive_stand_is_left_exactly_as_found_after_the_goal(
    tmp_path: Path, capabilities: str
) -> None:
    """(h) the left-as-found snapshot holds: unchanged tree, empty scratch, every pid dead.

    Every path and size outside the SQLite data dir is unchanged, scratch is empty, and every pid
    the lease started (a real `run_command` child, `hivemind.cell.local.LocalProcessSession`) is
    dead once `run_hive` exits.
    """

    def worker_turn(request: LLMRequest) -> LLMResponse:
        count = tool_round_count(request)
        if count == 0:
            spawn = ("spawn", "run_command", {"argv": [sys.executable, "-c", "pass"]})
            return tool_response(request, (spawn,))
        if count == 1:
            return tool_response(request, tuple(write_call(name) for name in _DEFAULT_FILES))
        return text_response("Three haiku written.")

    manifest_path = fake_manifest(tmp_path, capabilities=capabilities)
    hive = _hive(manifest_path, HaikuScript(worker_turn))
    data_dir = tmp_path / "data"
    before = snapshot_tree(tmp_path, exclude=(data_dir,))
    asyncio.run(_run_left_as_found(hive))
    assert snapshot_tree(tmp_path / "scratch") == {}
    assert snapshot_tree(tmp_path, exclude=(data_dir,)) == before


async def _run_left_as_found(hive: Hive) -> None:
    """The async body `test_the_hive_stand_is_left_exactly_as_found_...` drives."""
    async with run_hive(hive):
        report = await run_goal(hive, _GOAL, clearance=HoneyClearance.C1, timeout_s=_TIMEOUT_S)
        lease = hive.warden.lease
        assert lease is not None
        started_pids = list(lease.started_pids)
    assert report.succeeded, report
    assert started_pids  # the run really did start at least one real child process.
    for pid in started_pids:
        await wait_until(_pid_is_dead(pid), timeout_s=5.0)
