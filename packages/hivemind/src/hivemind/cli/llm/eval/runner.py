"""Run one eval goal on a real Hive built from the manifest, and keep what it left behind.

`run_goal_evidence` is the only impure part of the harness (roadmap step 8.5). For one goal it
scopes the manifest to a private working directory (its own database, secrets and scratch root),
so the run never touches the operator's real Hive state and two goals never see each other's
tasks; builds a Hive exactly as `hive run` does (`hivemind.cli.compose.build_hive`, so the
providers, slots, routing and prompt overlays are the manifest's own); runs it with
`run_hive`/`run_goal`; and, beside the goal, runs an *attendant* that plays the human: it answers
the one question a goal is expected to provoke, and forces the one checkpoint a goal wants, so the
run needs nobody at the keyboard. When the goal ends it reads the scratch files before the lease is
released, then the whole trail, and returns them as a `GoalEvidence` for the graders.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.llm.eval`. Called by the
    evaluation pipeline once per goal. Calls into `hivemind.cli.compose` (build_hive, run_hive,
    run_goal), `hivemind.brood_chamber`, `hivemind.queen`, `hivemind.supervision` and
    `hivemind.pheromone` through the Hive it built, and this package's goals and evidence.

Key invariants:
    - One fresh Hive per goal: the trail a goal is graded on holds that goal's events and no
      other's, and the operator's own `[hive] db` is never opened.
    - `build_hive` runs outside any event loop (it opens its stores with its own `asyncio.run`),
      so `run_goal_evidence` is synchronous and owns the loop for the run itself.
    - A goal that cannot run at all (a Hive that will not build, a crash mid-run) comes back as
      evidence with `error` set; it never aborts the goals after it.
    - The attendant answers and checkpoints at most once each and is cancelled when the goal ends.

See Also:
    - hivemind.cli.run for the `hive run` path this mirrors.
    - hivemind.cli.llm.eval.graders for what is done with the evidence.
    - tests/e2e/test_kernel_on_hive_stand.py for the scenarios the attendant's two levers repeat.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from hivemind.brood_chamber import AnswerSource
from hivemind.cell import HoneyClearance
from hivemind.cli.compose import Hive, build_hive, run_goal, run_hive
from hivemind.cli.llm.eval.evidence import MAX_FILE_CHARS, GoalEvidence
from hivemind.cli.llm.eval.goals import EvalGoal
from hivemind.forage import ModelSlot
from hivemind.llm import Responder, Rung
from hivemind.manifest import HiveManifest
from hivemind.pheromone import MAX_QUERY_LIMIT, TrailQuery
from hivemind.queen import ChatKind, ChatQuery
from hivemind.supervision import Checkpoint
from waggle.clock import Clock

ATTEND_POLL_S = 0.05  # How often the attendant looks for a question or a worker: a fast poll.
MAX_SCRATCH_FILES = 200  # A goal here leaves a handful of files; more is a runaway, not evidence.

__all__ = ["ATTEND_POLL_S", "RunnerOptions", "run_goal_evidence"]


@dataclass(frozen=True, slots=True)
class RunnerOptions:
    """How a goal is run: the world it runs in, and the seams a test substitutes."""

    environ: Mapping[str, str]  # Read once for provider API keys (codingrules section 13).
    clock: Clock  # Injected time source shared by every collaborator of the Hive.
    work_dir: Path  # Parent of one private directory per goal; created on demand.
    responders: Mapping[str, Responder] | None = None  # Scripts every `fake` provider; test only.
    on_hive: Callable[[EvalGoal, Hive], None] | None = None  # Called with each freshly built Hive.
    force_checkpoints: bool = True  # False when the goal's own script triggers the checkpoint.


def run_goal_evidence(
    manifest: HiveManifest, goal: EvalGoal, options: RunnerOptions
) -> GoalEvidence:
    """Run `goal` on a fresh Hive built from `manifest` and return what happened.

    Args:
        manifest: The loaded manifest whose bindings are under evaluation.
        goal: The goal to run.
        options: The environment, clock, working directory and test seams.

    Returns:
        The run's evidence; `error` is set, and the rest is empty, when the run could not finish.
    """
    try:
        scoped = _scoped_manifest(manifest, options.work_dir / goal.name)
        # Outside any event loop: build_hive opens its stores with its own asyncio.run.
        hive = build_hive(
            scoped, environ=options.environ, clock=options.clock, responders=options.responders
        )
        if options.on_hive is not None:
            options.on_hive(goal, hive)
        return asyncio.run(_run(hive, goal, options))
    except Exception as exc:
        # SAFETY: the harness's one broad catch (codingrules section 10: an edge that must report
        # rather than die). A goal that crashes the Hive is a graded result, and the goals after
        # it still run; the error text is what the report shows for it.
        return GoalEvidence(
            goal=goal.name,
            succeeded=False,
            timed_out=False,
            elapsed_s=0.0,
            error=_describe(exc),
        )


def _scoped_manifest(manifest: HiveManifest, root: Path) -> HiveManifest:
    """Return `manifest` with its state paths moved under `root`, created and empty."""
    scratch = root / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    # model_copy keeps every other field, including the source path relative files resolve against.
    return manifest.model_copy(
        update={
            "hive": manifest.hive.model_copy(
                update={"db": root / "hive.db", "secrets_dir": root / "secrets"}
            ),
            "hive_stand": manifest.hive_stand.model_copy(update={"scratch_root": scratch}),
        }
    )


async def _run(hive: Hive, goal: EvalGoal, options: RunnerOptions) -> GoalEvidence:
    """Run the goal inside `run_hive` with its attendant, then collect the evidence."""
    async with run_hive(hive):
        attendant = asyncio.create_task(_attend(hive, goal, options))
        try:
            report = await run_goal(
                hive, goal.text, clearance=goal.clearance, timeout_s=goal.timeout_s
            )
            # Before the lease is released on leaving run_hive: scratch is emptied then.
            files = _read_scratch(hive)
        finally:
            attendant.cancel()
            await asyncio.wait({attendant})
    events = await hive.stores.trail.query(TrailQuery(limit=MAX_QUERY_LIMIT))
    return GoalEvidence(
        goal=goal.name,
        succeeded=report.succeeded,
        timed_out=report.timed_out,
        elapsed_s=report.elapsed_s,
        tasks=report.tasks,
        events=tuple(events),
        files=files,
        start_rung=_start_rung(hive),
    )


async def _attend(hive: Hive, goal: EvalGoal, options: RunnerOptions) -> None:
    """Play the human beside the goal: answer its question once, force its checkpoint once."""
    needs_answer = goal.answer is not None
    needs_checkpoint = goal.forces_checkpoint and options.force_checkpoints
    while needs_answer or needs_checkpoint:
        if needs_answer and goal.answer is not None:
            needs_answer = not await _answer_pending(hive, goal.answer)
        if needs_checkpoint:
            needs_checkpoint = not await _checkpoint_mid_task(hive)
        # External wait on the injected clock: a poll, never a fixed sleep for the run's length.
        await options.clock.sleep(ATTEND_POLL_S)


async def _answer_pending(hive: Hive, answer: str) -> bool:
    """Answer the one pending question once the Queen has put it to the human; True when sent."""
    pending = await hive.stores.chamber.pending_questions()
    lines = await hive.stores.chat.read(ChatQuery())
    # Only once the question is on the chat has the Queen finished routing it, the same condition
    # the kernel's own question scenario waits for before it answers.
    if not pending or not any(line.kind is ChatKind.QUESTION for line in lines):
        return False
    await hive.queen.answer_question(
        pending[0].id, answer, source=AnswerSource.HUMAN, clearance=HoneyClearance.C2
    )
    return True


async def _checkpoint_mid_task(hive: Hive) -> bool:
    """Checkpoint the first sub-bee once it has landed an action; True when one was sent."""
    sub_bees = hive.warden.sub_bees
    applied = await hive.stores.trail.query(TrailQuery(kind="capping.applied", limit=1))
    # "Mid-task" means the worker has already changed something, so the Handoff has real progress
    # to carry; a checkpoint before any action would prove nothing about resuming.
    if not sub_bees or not applied:
        return False
    await hive.warden.intervene(sub_bees[0].worker_id, Checkpoint(reason="eval: forced checkpoint"))
    return True


def _read_scratch(hive: Hive) -> dict[str, str]:
    """Read every regular file under the Hive Stand lease's scratch root, as cut text."""
    lease = hive.warden.lease
    if lease is None:
        return {}
    root = lease.scratch_root
    found: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if len(found) >= MAX_SCRATCH_FILES:
            break
        if path.is_file() and not path.is_symlink():
            with path.open(encoding="utf-8", errors="replace") as handle:
                found[path.relative_to(root).as_posix()] = handle.read(MAX_FILE_CHARS)
    return found


def _start_rung(hive: Hive) -> str:
    """Return the ladder rung the QUEEN slot's model starts on, from its declared capabilities."""
    capabilities = hive.registry.bound(ModelSlot.QUEEN).provider.capabilities
    if capabilities.schema_output:
        return Rung.NATIVE.value
    return Rung.JSON_MODE.value if capabilities.json_mode else Rung.PROMPTED.value


def _describe(exc: BaseException) -> str:
    """Flatten `exc`, unwrapping any ExceptionGroup a TaskGroup raised, into one report line."""
    if isinstance(exc, BaseExceptionGroup):
        return "; ".join(_describe(inner) for inner in exc.exceptions)
    return f"{type(exc).__name__}: {exc}"
