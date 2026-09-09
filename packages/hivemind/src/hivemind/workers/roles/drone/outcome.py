"""Define HandoffRequestedError, the recording tool executor, and how an attempt becomes an outcome.

`HandoffRequestedError` is the control exception `_RecordingExecutor` raises between tool calls once
`ctx.telemetry` says this attempt should checkpoint (codingrules section 8.9: "a bee whose
telemetry crosses the manifest threshold... checkpoints and resets itself"); `Drone.run` catches
only this exception, never a bare `Exception` (codingrules section 10). `_RecordingExecutor`
implements `hivemind.llm.ToolExecutor` over a `hivemind.workers.tools.ToolRegistry`: it is the one
place a Drone's turn loop cooperates with pausing and cancellation
(`hivemind.workers.telemetry.TelemetryTracker.wait_if_paused`) and the one place that remembers
every call and its result text, since `hivemind.llm.ToolLoopResult` itself keeps only the calls,
not what each one returned. `collect_artifacts` reads that record back to find every `write_file`
call whose target still exists in scratch, and `build_claimed_outcome`/`build_handoff_outcome` are
the two ways one attempt ends, matching `hivemind.workers.base.WorkerOutcome`'s own claimed-xor-
handoff shape.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.drone`. Used by
    `hivemind.workers.roles.drone.Drone.run`. Calls into `hivemind.cell`, `hivemind.llm`,
    `hivemind.memory`, `hivemind.workers.base`, `hivemind.workers.context`,
    `hivemind.workers.errors`, `hivemind.workers.telemetry`, `hivemind.workers.tools` and waggle
    only.

Key invariants:
    - `_RecordingExecutor.execute` calls `wait_if_paused()` before every tool run, exactly once,
      so a role that pauses mid-loop is only ever blocked between calls, never mid-call.
    - `collect_artifacts` only ever reports a path whose resolved location is under
      `ctx.session.scratch_dir`: an outside-scratch write, whatever the gate decided, is never
      reported as one of this attempt's artifacts (roadmap step 3.16's own artifact bullet: "every
      file write_file created under scratch").
    - Every free-text field a built Handoff carries is truncated to the same caps
      `hivemind.memory.handoff.Handoff` itself validates against, so construction never raises.

See Also:
    - .claude/codingrules.md section 8.9 for the checkpoint-and-reset rule `HandoffRequestedError`
      answers to.
    - .claude/codingrules.md section 10 for "never `except Exception`" outside the three allowed
      sites -- `Drone.run` catches `HandoffRequestedError` by name, never broadly.
    - hivemind.memory.handoff for Handoff and Decision, the shapes `build_handoff_outcome` builds.
    - hivemind.workers.roles.drone for Drone, this module's one caller.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import ClassVar

from hivemind.cell import HoneyClearance, PathNotAllowedError
from hivemind.llm import ToolCall, ToolLoopResult, ToolResultPart
from hivemind.memory import Decision, Handoff
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from hivemind.workers.errors import WorkerError
from hivemind.workers.telemetry import TelemetryTracker
from hivemind.workers.tools import ToolInvocation, ToolRegistry
from waggle.messages.task import ArtifactRef, TaskAssign

MAX_SUMMARY_CHARS = 4_000  # A paragraph or two; matches Handoff.progress's own scale.
# Mirror hivemind.memory.handoff's own per-field caps (a non-init submodule this package may not
# import from directly, codingrules section 5.4): truncating to these numbers keeps every
# Handoff this module builds within that model's own validated bounds.
_MAX_GOAL_CHARS = 2_000  # Mirrors MAX_GOAL_CHARS.
_MAX_PROGRESS_CHARS = 4_000  # Mirrors MAX_PROGRESS_CHARS.
_MAX_DECISION_TEXT_CHARS = 500  # Mirrors MAX_DECISION_WHAT_CHARS / MAX_DECISION_WHY_CHARS.
_MAX_DECISIONS_KEPT = 20  # Well under MAX_DECISIONS (64); a bounded tool loop rarely nears it.
_NEXT_STEPS_ON_HANDOFF = (
    "Resume the objective from this Handoff; the tool loop was interrupted to checkpoint.",
)

__all__ = [
    "MAX_SUMMARY_CHARS",
    "HandoffRequestedError",
    "build_claimed_outcome",
    "build_handoff_outcome",
    "collect_artifacts",
]


class HandoffRequestedError(WorkerError):
    """Raise between tool calls once this attempt should checkpoint instead of continuing.

    Raised by `_RecordingExecutor.execute`, never by a tool itself; `Drone.run` is this
    exception's one catcher.
    """

    code: ClassVar[str] = "hivemind.workers.roles.drone.handoff_requested"

    def __init__(self) -> None:
        """Build the error; carries no extra state beyond the fact that a handoff was due."""
        super().__init__(
            "This Drone attempt is handing off: telemetry crossed the handoff threshold, or a "
            "handoff was explicitly requested."
        )


class _RecordingExecutor:
    """A ToolExecutor over a ToolRegistry that cooperates with pause/cancel/handoff and remembers.

    Owns its own mutable state in place (codingrules section 8.5): `records` grows by one entry
    per tool call this attempt makes, read back by `collect_artifacts` and `build_handoff_outcome`
    once the loop ends.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        invocation: ToolInvocation,
        telemetry: TelemetryTracker,
        handoff_threshold: float,
    ) -> None:
        """Wrap `registry` as a ToolExecutor for one Drone attempt.

        Args:
            registry: Where a validated call actually runs.
            invocation: This attempt's context and assignment, passed to every `registry.execute`.
            telemetry: This attempt's own tracker; polled for pause/cancel/handoff between calls.
            handoff_threshold: The fraction of the context window past which this attempt hands
                off on its own (`ctx.handoff_threshold`).
        """
        self._registry = registry
        self._invocation = invocation
        self._telemetry = telemetry
        self._handoff_threshold = handoff_threshold
        self.records: list[tuple[ToolCall, str]] = []

    async def execute(self, call: ToolCall) -> ToolResultPart:
        """Run `call` through the wrapped registry, after pause/cancel/handoff cooperation.

        Args:
            call: The already-schema-validated call `hivemind.llm.run_tool_loop` wants run.

        Returns:
            A ToolResultPart carrying the registry's own result text.

        Raises:
            hivemind.workers.errors.WorkerCancelledError: Cancellation was noticed while paused.
            HandoffRequestedError: This attempt's telemetry says it should checkpoint now.
        """
        # External await: waits out a pause the runtime holds between tool calls; no fixed
        # timeout, since a paused attempt is meant to wait until TaskResume, not time out on its
        # own (hivemind.workers.telemetry.TelemetryTracker.wait_if_paused's own contract).
        await self._telemetry.wait_if_paused()
        if self._telemetry.handoff_requested or self._telemetry.should_hand_off(
            self._handoff_threshold
        ):
            raise HandoffRequestedError()
        content = await self._registry.execute(self._invocation, call)
        self.records.append((call, content))
        return ToolResultPart(call_id=call.id, content=content, is_error=False)


async def collect_artifacts(
    ctx: WorkerContext, records: list[tuple[ToolCall, str]]
) -> tuple[ArtifactRef, ...]:
    """Return an ArtifactRef for every distinct `write_file` path still present under scratch.

    Args:
        ctx: This attempt's WorkerContext; supplies the session every path is re-read through.
        records: Every `(call, result text)` `_RecordingExecutor` collected this attempt.

    Returns:
        One ArtifactRef per distinct path a `write_file` call named, in first-seen order, skipping
        any path outside scratch or that no longer exists (the gate rejected or rolled it back).
    """
    seen: set[str] = set()
    artifacts: list[ArtifactRef] = []
    for call, _ in records:
        if call.name != "write_file":
            continue
        path = call.arguments.get("path")
        if not isinstance(path, str) or path in seen:
            continue
        seen.add(path)
        artifact = await _artifact_for(ctx, path)
        if artifact is not None:
            artifacts.append(artifact)
    return tuple(artifacts)


def build_claimed_outcome(
    assignment: TaskAssign, result: ToolLoopResult, artifacts: tuple[ArtifactRef, ...]
) -> WorkerOutcome:
    """Build the WorkerOutcome for a Drone attempt that finished its tool loop normally.

    Args:
        assignment: The task this attempt worked; supplies the outcome's clearance.
        result: What `hivemind.llm.run_tool_loop` returned.
        artifacts: Every file `write_file` produced under scratch, from `collect_artifacts`.

    Returns:
        A `claimed=True` WorkerOutcome; the Drone never marks itself SUCCEEDED (codingrules
        section 8.7), only that it believes the work is done.
    """
    summary = result.final_text.strip() or "The Drone finished its tool loop with no closing text."
    return WorkerOutcome(
        summary=summary[:MAX_SUMMARY_CHARS],
        clearance=HoneyClearance.from_wire(assignment.clearance),
        artifacts=artifacts,
        claimed=True,
        handoff=None,
        spend_usd=result.usage.cost_usd or 0.0,
    )


def build_handoff_outcome(
    ctx: WorkerContext, assignment: TaskAssign, records: list[tuple[ToolCall, str]]
) -> WorkerOutcome:
    """Build the WorkerOutcome for a Drone attempt that stopped early to hand off.

    Args:
        ctx: This attempt's WorkerContext; supplies `worker_id` (`Handoff.written_by`) and the
            telemetry spend recorded so far.
        assignment: The task this attempt was working; supplies the Handoff's goal and clearance.
        records: Every `(call, result text)` made before the handoff, from `_RecordingExecutor`.

    Returns:
        A `claimed=False` WorkerOutcome carrying a fully populated Handoff.
    """
    clearance = HoneyClearance.from_wire(assignment.clearance)
    handoff = Handoff(
        goal=assignment.objective[:_MAX_GOAL_CHARS],
        progress=_summarise_progress(records)[:_MAX_PROGRESS_CHARS],
        decisions=_decisions_from_records(records),
        tried_and_failed=(),
        constraints=(),
        open_threads=(),
        next_steps=_NEXT_STEPS_ON_HANDOFF,
        do_not_redo=(),
        pinned_facts=(),
        notes="",
        clearance=clearance,
        written_by=str(ctx.worker_id),
        task_id=assignment.task_id,
    )
    return WorkerOutcome(
        summary="Checkpointing to hand off; see the attached Handoff for progress and next steps.",
        clearance=clearance,
        artifacts=(),
        claimed=False,
        handoff=handoff,
        spend_usd=ctx.telemetry.snapshot().spend,
    )


async def _artifact_for(ctx: WorkerContext, path: str) -> ArtifactRef | None:
    """Build an ArtifactRef for `path` if it exists under scratch, else return None."""
    resolved = _resolve(ctx.session.scratch_dir, Path(path))
    if not _within_scratch(resolved, ctx.session.scratch_dir):
        return None  # Only scratch artifacts are reported (module docstring).
    try:
        data = await ctx.session.get_file(Path(path))
    except (FileNotFoundError, PathNotAllowedError):
        return None  # Rejected or rolled back: nothing to report.
    return ArtifactRef(path=path, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


def _resolve(scratch_dir: Path, path: Path) -> Path:
    """Join `path` under `scratch_dir` when relative, then resolve it (collapsing ".." segments)."""
    joined = path if path.is_absolute() else scratch_dir / path
    return joined.resolve(strict=False)


def _within_scratch(resolved: Path, scratch_dir: Path) -> bool:
    """Return whether `resolved` equals `scratch_dir` or is somewhere underneath it."""
    scratch_resolved = scratch_dir.resolve(strict=False)
    return resolved == scratch_resolved or scratch_resolved in resolved.parents


def _summarise_progress(records: list[tuple[ToolCall, str]]) -> str:
    """Render every tool call made so far as one line of progress, oldest first."""
    if not records:
        return "No tool calls completed before handing off."
    return "; ".join(f"called {call.name}" for call, _ in records)


def _decisions_from_records(records: list[tuple[ToolCall, str]]) -> tuple[Decision, ...]:
    """Turn the most recent tool calls and their results into Handoff Decisions."""
    kept = records[-_MAX_DECISIONS_KEPT:]
    return tuple(
        Decision(
            what=f"Called {call.name}"[:_MAX_DECISION_TEXT_CHARS],
            why=(content[:_MAX_DECISION_TEXT_CHARS] if content else "no result text"),
        )
        for call, content in kept
    )
