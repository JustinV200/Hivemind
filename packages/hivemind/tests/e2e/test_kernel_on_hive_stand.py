"""End-to-end tests for the Queen kernel on the Hive Stand: roadmap step 3.22's eight scenarios.

Every scenario builds a `hivemind.cli.compose.Hive` over a `builders.cli.fake_manifest` and one
`hivemind.llm.fake.FakeLLMProvider`, scripted through `tests.e2e.kernel_helpers.HaikuScript`, then
drives it with `hivemind.cli.compose.run_hive`/`run_goal` (or, for the CLI-shaped half of scenario
(a), `hive run` itself through `typer.testing.CliRunner`) -- the real Hive Stand, a real SQLite
file and, where a scenario scripts `run_command`, a real child process. Every scenario runs at
both `ProviderCapabilities.full()` and `.none()` (roadmap step 3.22's own "at full and at zero
capabilities"), parametrised through `_LEVELS`.

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

Three gaps this module documents rather than works around (full detail in this dispatch's own
report; each is also named at its own xfail site below):
    - No shipped `AlarmKind` any live component ever raises has a REBIND row in `docs/
      supervision/default-policy.toml` (only `WORKER_FAILED`/`PROVIDER_UNAVAILABLE` do, and
      neither kind is ever constructed anywhere in `hivemind/workers/runtime/attempt.py`,
      `hivemind/wardens/ticks/heartbeat.py`, `hivemind/wardens/ticks/results.py` or `hivemind/
      queen/ticks/liveness.py`); scenario (c)'s own "rebinds to a stronger slot" is xfailed.
    - No shipped Drone tool can ever produce a Proposal whose declared postcondition fails after a
      successful apply (`write_file`'s own `FILE_EXISTS` always names the exact path it just
      wrote; `run_command` declares none at all -- `hivemind/workers/tools/session.py`); scenario
      (g) exercises the other real `ROLLED_BACK` path instead (a `COMMAND` action whose own exit
      is non-zero) and says so.
    - No component ever raises an Alarm from a Capping `ROLLED_BACK` outcome; scenario (g)'s own
      alarm assertion is xfailed.

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
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest
from builders.cli import fake_manifest
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
from hivemind.pheromone import TrailQuery
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


async def _run_goal_to_completion(
    hive: Hive, *, timeout_s: float = _TIMEOUT_S
) -> tuple[GoalReport, list[str]]:
    """Run `_GOAL` inside `run_hive` to completion, and return the report plus every trail kind."""
    async with run_hive(hive):
        report = await run_goal(hive, _GOAL, clearance=HoneyClearance.C1, timeout_s=timeout_s)
    events = await hive.stores.trail.query(TrailQuery())
    return report, [event.kind for event in events]


def _retry_goal(
    tmp_dir: Path,
    capabilities: str,
    worker_turn_factory: Callable[[], WorkerTurn],
    check: Callable[[GoalReport, list[str]], bool],
    *,
    worker_fallback: bool = False,
) -> None:
    """Run a scripted goal to completion, retrying with a fresh Hive on a known-rare race.

    See `test_a_drones_question_blocks_the_task_...`'s own docstring for the class of real,
    low-probability races this guards against: real SQLite I/O lets the Queen's own post-dispatch
    bookkeeping and a fast Warden/Drone reaction interleave in ways `tests.unit.cli.test_compose`'s
    own FakeClock pump never exercises, since every trail write there is an in-memory, synchronous
    append with no real suspension in between for a race to land in.

    Args:
        tmp_dir: This test's own `tmp_path`; each attempt gets its own subdirectory.
        capabilities: `"full"` or `"none"`, passed straight through to `fake_manifest`.
        worker_turn_factory: Builds this scenario's own WORKER script fresh for each attempt, so a
            script with its own mutable state (a crash budget, say) never carries a spent state
            into a retry.
        check: Makes this scenario's own assertions and returns whether this attempt actually
            satisfied them; a raised exception (a crashed Hive) also just retries.
        worker_fallback: Passed straight through to `fake_manifest`.
    """
    for attempt in range(_RETRY_ATTEMPTS):
        manifest_path = fake_manifest(
            tmp_dir / f"try_{attempt}", capabilities=capabilities, worker_fallback=worker_fallback
        )
        hive = _hive(manifest_path, HaikuScript(worker_turn_factory()))
        try:
            report, kinds = asyncio.run(_run_goal_to_completion(hive, timeout_s=_RETRY_TIMEOUT_S))
            if check(report, kinds):
                return
        except Exception:  # noqa: S112 -- SAFETY: a crashed/hung attempt just retries, fresh.
            continue
    pytest.fail(f"never completed cleanly after {_RETRY_ATTEMPTS} attempts.")


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
    hive = _hive(manifest_path, HaikuScript(default_worker_turn))
    asyncio.run(_run_kill_and_respawn(hive))


async def _run_kill_and_respawn(hive: Hive) -> None:
    """The async body `test_a_killed_drone_is_respawned_...` drives."""
    async with run_hive(hive):
        goal_task = asyncio.ensure_future(
            run_goal(hive, _GOAL, clearance=HoneyClearance.C1, timeout_s=_TIMEOUT_S)
        )
        await wait_until(lambda: bool(hive.warden.sub_bees), timeout_s=_TIMEOUT_S)
        hive.warden.sub_bees[0].runtime_task.cancel()
        report = await goal_task
    assert report.succeeded, report
    events = await hive.stores.trail.query(TrailQuery())
    kinds = [event.kind for event in events]
    assert kinds.count("worker.spawned") >= 2
    assert "queen.awake" not in kinds


# ──────────────────────────────────────────────────────────────────────────────
# (c) a Drone that crashes repeatedly escalates; the task eventually completes
# ──────────────────────────────────────────────────────────────────────────────


def _crashing_worker_turn(
    crash_budget: dict[str, int], used_models: set[str | None] | None = None
) -> WorkerTurn:
    """Build a WORKER script that crashes `crash_budget["count"]` times, then writes the files.

    Args:
        crash_budget: `{"count": N}`, decremented once per crashed attempt; shared with the
            caller so it can assert exactly how many crashes actually happened.
        used_models: When given, every call's own `request.model` is recorded into it (scenario
            (c)'s own xfail test reads this back to prove no REBIND to a stronger model ever
            happens).
    """

    def worker_turn(request: LLMRequest) -> LLMResponse:
        if used_models is not None:
            used_models.add(request.model)
        if tool_round_count(request) == 0 and crash_budget["count"] > 0:
            crash_budget["count"] -= 1
            raise RuntimeError("simulated crash: the bound provider vanished mid-attempt.")
        return default_worker_turn(request)

    return worker_turn


@_LEVELS
def test_a_drone_that_crashes_repeatedly_escalates_and_the_task_eventually_completes(
    tmp_path: Path, capabilities: str
) -> None:
    """(c) three crashes escalate to the Queen, whose own retry finishes the task.

    Three `WORKER_CRASHED` alarms (`docs/supervision/default-policy.toml`'s own RESPAWN@1/
    ESCALATE@3 rows) exhaust the Warden's own local retries and reach the Queen, whose own
    RESPAWN@1 row (`QueenAction.RETRY_TASK`) redispatches it: the real, reachable path. Wrapped in
    `_retry_goal` like scenarios (d)/(f): with four separate dispatch-and-spawn cycles of its own,
    this scenario has even more chances than most to hit the same rare, real SQLite-timing race.
    """

    def check(report: GoalReport, kinds: list[str]) -> bool:
        return (
            report.succeeded
            and kinds.count("worker.failed") == 3
            and kinds.count("worker.spawned") >= 4
        )

    _retry_goal(
        tmp_path,
        capabilities,
        lambda: _crashing_worker_turn({"count": 3}),
        check,
        worker_fallback=True,
    )


@_LEVELS
@pytest.mark.xfail(
    strict=True,
    reason=(
        "no shipped AlarmKind a live component ever raises has a REBIND row in docs/supervision/"
        "default-policy.toml, and hivemind/queen/ticks/alarms.py's own REBIND path forwards an "
        "Intervene naming no target binding to hivemind/wardens/ticks/control.py's "
        "forward_control, which only relays it to the CURRENT sub-bee (checkpoint-and-stop, "
        "never a fresh spawn on a stronger slot): the goal always finishes on the original "
        "'worker' binding, never 'local_worker'."
    ),
)
def test_a_queen_rebind_to_a_stronger_slot_never_actually_happens(
    tmp_path: Path, capabilities: str
) -> None:
    """(c), the documented gap: the Queen never rebinds to a stronger slot.

    The roadmap's own "Queen rebinds it to a stronger slot" never happens under the shipped
    policy and wire protocol; see the xfail reason above.
    """
    used_models: set[str | None] = set()
    manifest_path = fake_manifest(tmp_path, capabilities=capabilities, worker_fallback=True)
    script = HaikuScript(_crashing_worker_turn({"count": 3}, used_models))
    hive = _hive(manifest_path, script)
    report, _kinds = asyncio.run(_run_goal_to_completion(hive))
    assert report.succeeded, report
    assert "test-model-strong" in used_models  # never true today: see the xfail reason.


# ──────────────────────────────────────────────────────────────────────────────
# (d) a Drone's question blocks the task; `hive inbox answer` resumes it
# ──────────────────────────────────────────────────────────────────────────────


async def _task_is_blocked(hive: Hive) -> bool:
    """Return whether the goal's own (only) task is currently BLOCKED on a question."""
    tasks = await hive.stores.chamber.list(TaskFilter())
    return bool(tasks) and tasks[0].status is TaskStatus.BLOCKED


_RETRY_ATTEMPTS = 10  # See the docstring below for the two real races this bounds a retry around.
_RETRY_TIMEOUT_S = 0.5  # An unraced run finishes in well under this; a raced one gives up fast.


def _blocked_question_worker_turn(request: LLMRequest) -> LLMResponse:
    """The WORKER script every retry attempt below scripts: ask, then write, then stop."""
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
    """(d) a question blocks the task until `hive inbox answer` resumes it.

    `ask` blocks the task; `hive inbox`/`hive inbox answer` (CliRunner) resolve it, and
    `hivemind.queen.sync_answers_from_chamber` -- polled every `run_goal` iteration -- forwards
    the answer to the blocked Drone. This dispatch's own report names two real races this test
    discovered under real SQLite I/O, retried around here rather than worked around in src (both
    are per-attempt, low-probability, and never reproduce under `tests.unit.cli.test_compose`'s
    own FakeClock pump, where every trail write is an in-memory, synchronous append with no real
    suspension in between for either race to land in):
        - `hivemind.queen.dispatcher._dispatch_one` sends the wire `GrantIssued`/`TaskAssign`
          *before* it records `queen.assigned`/calls `chamber.start()`; a fast sub-bee whose very
          first action is `ask` can have its own forwarded Question reach `Queen._act`'s
          `BLOCK_ON_QUESTION` handling while the chamber still reads ASSIGNED, raising an unhandled
          `InvalidTransitionError` that crashes the whole Queen task (and, via its own TaskGroup,
          the whole Hive).
        - `hive inbox answer`'s own two writes (`chamber.answer()`, then the `Note` `sync_answers_
          from_chamber` reads back) are against two separate store connections and are not atomic;
          a poll landing between them finds no Note yet and -- by `sync_answers_from_chamber`'s own
          design -- drops its tracking for that question unconditionally, so the answer is never
          forwarded and the goal hangs for good.
    Retrying with a fresh Hive and a fresh goal is what an operator would do too, and is safe here
    specifically because each attempt's own Hive, Queen and chamber are new: an earlier attempt's
    stuck state (or crashed Queen) never carries into the next one.
    """
    script = HaikuScript(_blocked_question_worker_turn)
    for attempt in range(_RETRY_ATTEMPTS):
        manifest_path = fake_manifest(tmp_path / f"try_{attempt}", capabilities=capabilities)
        hive = _hive(manifest_path, script)
        try:
            succeeded = asyncio.run(_run_blocked_question(hive, manifest_path))
        except Exception:
            # A crashed or hung attempt (the docstring's own two races) just retries, fresh.
            succeeded = False
        if succeeded:
            return
    pytest.fail(f"never completed after {_RETRY_ATTEMPTS} attempts (see the docstring above).")


async def _run_blocked_question(hive: Hive, manifest_path: Path) -> bool:
    """Run one attempt of the scenario `test_a_drones_question_blocks_the_task_...` drives.

    Both `runner.invoke` calls run in a worker thread (`asyncio.to_thread`): `hive inbox`/`hive
    inbox answer` each call their own `asyncio.run` internally (`hivemind.cli.readback.inbox`'s
    own module docstring), which would otherwise collide with the event loop this coroutine (and
    `goal_task`, running concurrently on it) is already inside.

    Returns:
        Whether the goal succeeded; never raises for the goal itself failing or timing out (only
        for a genuinely unexpected CLI exit code), so the caller's own retry loop decides.
    """
    async with run_hive(hive):
        goal_task = asyncio.ensure_future(
            run_goal(hive, _GOAL, clearance=HoneyClearance.C1, timeout_s=_RETRY_TIMEOUT_S)
        )
        await wait_until(lambda: _task_is_blocked(hive), timeout_s=_RETRY_TIMEOUT_S)
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
    return report.succeeded


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
    there); the goal still finishes, because the script's next call writes the real files. Wrapped
    in `_retry_goal` like scenario (d): an isolated repeated-run check (this dispatch's own report)
    caught this scenario hitting the same class of real, rare, SQLite-timing race once in several
    dozen runs.
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

    def check(report: GoalReport, kinds: list[str]) -> bool:
        # The kernel keeps going after the rejection (see the docstring above).
        return report.succeeded and "capping.rejected" in kinds and not outside.exists()

    _retry_goal(tmp_path, capabilities, lambda: worker_turn, check)


# ──────────────────────────────────────────────────────────────────────────────
# (g) a failed proposal is rolled back
# ──────────────────────────────────────────────────────────────────────────────


def _failing_command_worker_turn() -> WorkerTurn:
    """Build a WORKER script: a `run_command` that exits non-zero, then the real writes.

    See the module docstring for why a non-zero COMMAND exit, not a mismatched `write_file`
    postcondition, is this suite's own closest real `ROLLED_BACK` trigger.
    """

    def worker_turn(request: LLMRequest) -> LLMResponse:
        count = tool_round_count(request)
        if count == 0:
            argv = [sys.executable, "-c", "import sys; sys.exit(1)"]
            return tool_response(request, (("fail", "run_command", {"argv": argv}),))
        if count == 1:
            return tool_response(request, tuple(write_call(name) for name in _DEFAULT_FILES))
        return text_response("Three haiku written.")

    return worker_turn


@_LEVELS
def test_a_failing_command_proposal_is_rolled_back(tmp_path: Path, capabilities: str) -> None:
    """(g) a failed command proposal is rolled back.

    A `run_command` whose own exit is non-zero is the real, reachable `ROLLED_BACK` path
    (`hivemind.supervision.capping.gate._apply_and_verify`'s own "a COMMAND's own non-zero exit
    is itself the failure"), since no shipped tool can ever fail a *declared postcondition* after
    a successful apply (module docstring). Wrapped in `_retry_goal` like scenarios (d)/(f)/(c).
    """

    def check(report: GoalReport, kinds: list[str]) -> bool:
        return report.succeeded and "capping.rolled_back" in kinds

    _retry_goal(tmp_path, capabilities, _failing_command_worker_turn, check)


@_LEVELS
@pytest.mark.xfail(
    strict=True,
    reason=(
        "no component raises an Alarm from a Capping ROLLED_BACK outcome anywhere in "
        "hivemind/supervision/capping/gate.py or its callers (hivemind/workers/tools/proposals.py "
        "returns the outcome as tool-result text only); the trail never carries an alarm.* event "
        "for it."
    ),
)
def test_a_rolled_back_proposal_raises_an_alarm(tmp_path: Path, capabilities: str) -> None:
    """(g), the documented gap: see the xfail reason above."""
    manifest_path = fake_manifest(tmp_path, capabilities=capabilities)
    hive = _hive(manifest_path, HaikuScript(_failing_command_worker_turn()))
    _report, kinds = asyncio.run(_run_goal_to_completion(hive))
    assert any(kind.startswith("alarm.") for kind in kinds)  # never true today.


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
