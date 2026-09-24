"""Define HandoffRequestedError, _RecordingExecutor and collect_artifacts.

`HandoffRequestedError` is the control exception `_RecordingExecutor` raises between tool calls
once `ctx.telemetry` says this attempt should checkpoint (codingrules section 8.9: "a bee whose
telemetry crosses the manifest threshold... checkpoints and resets itself"); `Drone.run` catches
only this exception, never a bare `Exception` (codingrules section 10). `_RecordingExecutor`
implements `hivemind.llm.ToolExecutor` over a `hivemind.workers.tools.ToolRegistry`: it is the one
place a Drone's turn loop cooperates with pausing and cancellation
(`hivemind.workers.telemetry.TelemetryTracker.wait_if_paused`) and the one place that remembers
every call and its result as a bounded `hivemind.workers.roles.drone.outcome.records.
ToolCallRecord` list, since `hivemind.llm.ToolLoopResult` itself keeps only the calls, not what
each one returned. `collect_artifacts` reads that record back to find every `write_file` call
whose target still exists in scratch. A tool's media (roadmap step 6.5: a screenshot, a recording)
passes straight through to the model as `ToolResultPart.media` and is never recorded.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.drone.outcome`. Used by
    `hivemind.workers.roles.drone.Drone.run`. Calls into `hivemind.cell`, `hivemind.llm`,
    `hivemind.workers.base`, `hivemind.workers.context`, `hivemind.workers.errors`,
    `hivemind.workers.telemetry`, `hivemind.workers.tools` and waggle only.

Key invariants:
    - `_RecordingExecutor.execute` calls `wait_if_paused()` before every tool run, exactly once,
      so a role that pauses mid-loop is only ever blocked between calls, never mid-call.
    - `_RecordingExecutor.records` never grows past `MAX_RECORDED_CALLS`: the oldest entry is
      dropped once a new one would exceed it, so this stays a bounded per-attempt record, never a
      transcript (`scripts/check_no_transcripts.py`).
    - `_RecordingExecutor.pending_call` is set to the one call refused for a checkpoint, if any,
      so a Handoff's `open_threads` (`hivemind.workers.roles.drone.outcome.fields.
      open_thread_lines`) can say what was in progress when the attempt stopped.
    - `collect_artifacts` only ever reports a path whose resolved location is under
      `ctx.session.scratch_dir`: an outside-scratch write, whatever the gate decided, is never
      reported as one of this attempt's artifacts (roadmap step 3.16's own artifact bullet).
    - A record keeps a call's text only: media never enters `records`, so a Handoff built from
      them can never carry a frame or a recording (codingrules section 12).

See Also:
    - .claude/codingrules.md section 8.9 for the checkpoint-and-reset rule `HandoffRequestedError`
      answers to.
    - .claude/codingrules.md section 10 for "never `except Exception`" outside the three allowed
      sites -- `Drone.run` catches `HandoffRequestedError` by name, never broadly.
    - hivemind.workers.roles.drone.outcome.records for ToolCallRecord and classify_error.
    - hivemind.workers.roles.drone.outcome.build for how records become a Handoff.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import ClassVar

from hivemind.cell import PathNotAllowedError
from hivemind.llm import ToolCall, ToolResultPart
from hivemind.workers.context import WorkerContext
from hivemind.workers.errors import WorkerError
from hivemind.workers.roles.drone.outcome.records import ToolCallRecord, classify_error
from hivemind.workers.telemetry import TelemetryTracker
from hivemind.workers.tools import ToolInvocation, ToolRegistry
from waggle.messages.task import ArtifactRef

MAX_RECORDED_CALLS = 64  # DRONE_MAX_ROUNDS (12) means real usage never nears this; bounding the
# storage itself (not just what a later render caps) keeps this a fixed-size per-attempt record
# regardless of any future round-cap change, per the module docstring's own invariant.

__all__ = ["MAX_RECORDED_CALLS", "HandoffRequestedError", "collect_artifacts"]


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
    per tool call this attempt makes, read back by `collect_artifacts` and
    `hivemind.workers.roles.drone.outcome.build.build_handoff_outcome` once the loop ends.
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
        self.records: list[ToolCallRecord] = []
        self.pending_call: ToolCall | None = None

    async def execute(self, call: ToolCall) -> ToolResultPart:
        """Run `call` through the wrapped registry, after pause/cancel/handoff cooperation.

        Args:
            call: The already-schema-validated call `hivemind.llm.run_tool_loop` wants run.

        Returns:
            A ToolResultPart carrying the registry's own result text and any media beside it;
            an error when the text reads as one or the tool flagged it (`ToolOutput.is_error`).

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
            # Remember the call this attempt was about to make but never ran, so a Handoff's own
            # open_threads can name what was in progress when it checkpointed.
            self.pending_call = call
            raise HandoffRequestedError()
        output = await self._registry.execute(self._invocation, call)
        is_error = output.is_error or classify_error(call.name, output.text)
        # The record keeps the text alone; media goes to the model and nowhere else.
        self._remember(ToolCallRecord(call=call, result_text=output.text, is_error=is_error))
        return ToolResultPart(
            call_id=call.id, content=output.text, is_error=is_error, media=output.media
        )

    def _remember(self, record: ToolCallRecord) -> None:
        """Append `record`, dropping the oldest once past `MAX_RECORDED_CALLS` (class docstring)."""
        self.records.append(record)
        if len(self.records) > MAX_RECORDED_CALLS:
            self.records.pop(0)


async def collect_artifacts(
    ctx: WorkerContext, records: list[ToolCallRecord]
) -> tuple[ArtifactRef, ...]:
    """Return an ArtifactRef for every distinct `write_file` path still present under scratch.

    Args:
        ctx: This attempt's WorkerContext; supplies the session every path is re-read through.
        records: Every call `_RecordingExecutor` collected this attempt.

    Returns:
        One ArtifactRef per distinct path a `write_file` call named, in first-seen order, skipping
        any path outside scratch or that no longer exists (the gate rejected or rolled it back).
    """
    seen: set[str] = set()
    artifacts: list[ArtifactRef] = []
    for record in records:
        if record.call.name != "write_file":
            continue
        path = record.call.arguments.get("path")
        if not isinstance(path, str) or path in seen:
            continue
        seen.add(path)
        artifact = await _artifact_for(ctx, path)
        if artifact is not None:
            artifacts.append(artifact)
    return tuple(artifacts)


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
