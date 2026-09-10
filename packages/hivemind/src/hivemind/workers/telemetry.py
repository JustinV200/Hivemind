"""Define TelemetryTracker: the mutable per-Worker telemetry a role writes and the runtime reports.

Every bee reports `ContextTelemetry` (tokens used against the window, the current goal, recent
actions, blockers and spend) on heartbeat (codingrules section 8.8). `TelemetryTracker` is the one
object, owned by `hivemind.workers.runtime.WorkerRuntime` and handed to a role through
`hivemind.workers.context.WorkerContext`, that holds those figures between heartbeats: the role
writes to it as it works (`record_tokens`, `record_action`, `set_blockers`, `add_spend`) and the
runtime reads a `snapshot()` to build each `Heartbeat` and to decide, via `should_hand_off`,
whether the manifest's handoff threshold (codingrules section 8.9: "a bee whose telemetry crosses
the manifest threshold... checkpoints and resets itself") has been crossed. It also carries the
two flags and the one event a role's own turn loop cooperates with: `cancel_requested` and
`handoff_requested`, set by the runtime on `TaskCancel`/`Intervene`, and `wait_if_paused`, which a
role awaits between turns so `TaskPause` takes effect without the runtime forcibly suspending the
role's coroutine. `note_alarm`/`take_pending_alarms` (this dispatch's own fix 2) are a second such
seam, a small queue: a tool's own side effect several calls deep in a role's tool loop
(`hivemind.workers.tools.proposals.cap`, on a Capping rollback) has no handle on the runtime that
alone may send an `AlarmRaised`, but it already shares this tracker with it.

Fits into the Hive:
    Layer 4 (roles that do the work). Constructed and owned by `hivemind.workers.runtime.
    WorkerRuntime` (roadmap step 3.15); written by whichever role `hivemind.workers.base.Worker.run`
    is running (roadmap step 3.16 for the Drone); read by the runtime to build every outgoing
    `Heartbeat`, to decide on a checkpoint, and to drain `take_pending_alarms` on every tick.
    Calls into `hivemind.workers.errors` and waggle only.

Key invariants:
    - This class owns its own mutable state in place (codingrules section 8.5): every `record_*`/
      `add_spend`/`set_blockers`/`note_alarm` call mutates the tracker itself rather than
      returning a new one, because it is meant to accumulate across a role's whole attempt, not to
      be replaced.
    - `snapshot()` never raises: every field it copies into a ContextTelemetry is already clamped
      to that model's own bounds by the `record_*`/`set_blockers` methods, so a role that writes
      an over-length action or too many blockers never breaks the next heartbeat.
    - `wait_if_paused` returns immediately while not paused (the pause event starts set), and
      raises WorkerCancelledError the moment `cancel_requested` is true, whether or not a pause is
      also in effect: a cancelled Worker's role must never be left blocked by a pause forever.
    - `take_pending_alarms` drains the queue it reads: the same `PendingAlarm` is never returned
      twice, so a caller that sends every drained entry on can never double-send one.

See Also:
    - .claude/codingrules.md section 8.8 for "every bee reports ContextTelemetry on heartbeat".
    - .claude/codingrules.md section 8.9 for the handoff-threshold rule `should_hand_off` checks.
    - hivemind.workers.context for WorkerContext.telemetry, this class's one home.
    - hivemind.workers.errors for WorkerCancelledError, the error `wait_if_paused` raises.
    - waggle.messages.supervision.telemetry for ContextTelemetry and its own field bounds, which
      this module's constants mirror so a snapshot always validates.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass

from hivemind.workers.errors import WorkerCancelledError
from waggle.messages.supervision import AlarmKind
from waggle.messages.supervision.alarms import MAX_DETAIL_CHARS
from waggle.messages.supervision.telemetry import (
    MAX_ACTION_CHARS,
    MAX_BLOCKER_CHARS,
    MAX_BLOCKERS,
    MAX_GOAL_CHARS,
    MAX_LAST_ACTIONS,
    ContextTelemetry,
)

__all__ = ["PendingAlarm", "TelemetryTracker"]


@dataclass(frozen=True, slots=True)
class PendingAlarm:
    """One Alarm a tool-level failure noted, waiting for the runtime to actually send it.

    `hivemind.workers.tools.proposals.cap` builds one of these when the Capping gate rolls a
    proposal back (this dispatch's own fix 2: a rollback becomes a real `AlarmRaised`, not only
    tool-result text); `hivemind.workers.runtime.loop.WorkerRuntime` drains the queue on its next
    tick and turns each entry into a real `AlarmRaised` to this Worker's Warden.
    """

    kind: AlarmKind
    detail: str


class TelemetryTracker:
    """Mutable per-Worker telemetry: written by a role between turns, read by the runtime.

    Owns its own mutable state in place (codingrules section 8.5), documented here: `_tokens_used`,
    `_context_window`, `_goal`, `_last_actions`, `_blockers`, `_spend` and `_pending_alarms` all
    change as the role runs, and `cancel_requested`/`handoff_requested` are plain public flags the
    runtime sets and a role's own turn loop reads.
    """

    def __init__(self, context_window: int, goal: str = "") -> None:
        """Create a tracker for one Worker's attempt, at zero tokens and zero spend.

        Args:
            context_window: The bound model's context window, in tokens (from `BoundModel.
                context_window`); `should_hand_off` and every snapshot divide against this until
                `record_tokens` reports a different one.
            goal: The Worker's current goal in one line; empty until the role sets one.
        """
        self._tokens_used = 0
        self._context_window = context_window
        self._goal = goal[:MAX_GOAL_CHARS]
        # A bounded deque: appending past MAX_LAST_ACTIONS silently drops the oldest, so a role
        # that never trims its own action log still produces a valid ContextTelemetry.
        self._last_actions: deque[str] = deque(maxlen=MAX_LAST_ACTIONS)
        self._blockers: tuple[str, ...] = ()
        self._spend = 0.0
        # Grows on note_alarm (a tool-level failure the role never sees as an exception, e.g. a
        # Capping rollback) and drains on take_pending_alarms, this dispatch's own fix 2.
        self._pending_alarms: list[PendingAlarm] = []
        # Starts "not paused": a role's first wait_if_paused() call returns at once unless a
        # TaskPause has already landed by then.
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        # Plain public flags: the runtime sets these from TaskCancel/Intervene, and a
        # cooperative role (hivemind.workers.roles, roadmap step 3.16) reads them between turns.
        self.cancel_requested = False
        self.handoff_requested = False

    def record_tokens(self, used: int, window: int) -> None:
        """Overwrite the tracked token usage and context window with the role's latest count.

        Args:
            used: Tokens consumed in the assembled prompt plus the response so far.
            window: The bound model's context window this usage is measured against; a role
                passes this every time in case a rebind changed the binding mid-attempt.
        """
        self._tokens_used = used
        self._context_window = window

    def record_action(self, text: str) -> None:
        """Append one short description of what the role just did.

        Args:
            text: The action, e.g. a tool call or a check run; truncated to MAX_ACTION_CHARS so a
                snapshot always validates regardless of what a role writes.
        """
        self._last_actions.append(text[:MAX_ACTION_CHARS])

    def set_blockers(self, *blockers: str) -> None:
        """Replace the tracked blockers with `blockers`.

        Args:
            *blockers: What the role is currently stuck on, if anything; each is truncated to
                MAX_BLOCKER_CHARS and the whole list to MAX_BLOCKERS entries.
        """
        self._blockers = tuple(blocker[:MAX_BLOCKER_CHARS] for blocker in blockers[:MAX_BLOCKERS])

    def add_spend(self, usd: float) -> None:
        """Add `usd` to the tracked spend for this attempt.

        Args:
            usd: The additional spend to record; never negative in practice (a call's own cost),
                but this method does not itself validate that -- `ContextTelemetry.spend`'s own
                `ge=0` bound is what a caller ultimately answers to via `snapshot()`.
        """
        self._spend += usd

    def snapshot(self) -> ContextTelemetry:
        """Build the ContextTelemetry a Heartbeat or an inspect reply carries right now.

        Returns:
            A validated ContextTelemetry over the tracker's current figures.
        """
        return ContextTelemetry(
            tokens_used=self._tokens_used,
            context_window=self._context_window,
            goal=self._goal,
            last_actions=tuple(self._last_actions),
            blockers=self._blockers,
            spend=self._spend,
        )

    def should_hand_off(self, threshold: float) -> bool:
        """Return whether tracked usage has reached or passed `threshold` of the context window.

        Args:
            threshold: The fraction (the manifest's `[memory] handoff_threshold`, carried on
                `WorkerContext.handoff_threshold`) a role or the runtime checks against.

        Returns:
            True if `tokens_used / context_window >= threshold`.
        """
        return self._tokens_used / self._context_window >= threshold

    async def wait_if_paused(self) -> None:
        """Block while the runtime holds this Worker paused; raise if it was cancelled instead.

        A role calls this between turns (codingrules section 8.8's "a pause event a role awaits
        between turns"), so `TaskPause`/`TaskResume` take effect without the runtime forcibly
        suspending the role's own coroutine. It is also where a cooperative role notices a pending
        cancellation promptly, rather than only when `hivemind.workers.runtime.WorkerRuntime`
        force-cancels the role's asyncio task after `TaskCancel`'s grace period.

        Raises:
            WorkerCancelledError: `cancel_requested` was already set, or became set while waiting
                for the pause to lift.
        """
        await self._pause_event.wait()
        if self.cancel_requested:
            raise WorkerCancelledError("cancellation was requested while the role was running")

    def pause(self) -> None:
        """Hold every future `wait_if_paused()` call until `resume()` is called.

        Called by the runtime on TaskPause; idempotent (pausing an already-paused tracker is a
        no-op).
        """
        self._pause_event.clear()

    def resume(self) -> None:
        """Release every `wait_if_paused()` call currently blocked, and every future one.

        Called by the runtime on TaskResume; idempotent (resuming an already-running tracker is a
        no-op).
        """
        self._pause_event.set()

    def note_alarm(self, kind: AlarmKind, detail: str) -> None:
        """Queue an Alarm a tool-level failure raised, for the runtime to send on its next tick.

        A tool's own side effect (`hivemind.workers.tools.proposals.cap`) runs deep inside a
        role's tool loop, several calls away from `hivemind.workers.runtime.WorkerRuntime`, which
        is the only thing that may actually send an `AlarmRaised` (this dispatch's own fix 2): the
        tracker is the one object both sides already share, so noting it here is how the failure
        crosses that gap without a tool needing a handle on the runtime itself.

        Args:
            kind: What went wrong, as the escalation policy keys it.
            detail: The failing assertion or observation; truncated to MAX_DETAIL_CHARS so this
                can never fail to build the eventual AlarmRaised.
        """
        self._pending_alarms.append(PendingAlarm(kind=kind, detail=detail[:MAX_DETAIL_CHARS]))

    def take_pending_alarms(self) -> tuple[PendingAlarm, ...]:
        """Drain and return every Alarm noted since the last drain, oldest first.

        Returns:
            Every `PendingAlarm` queued by `note_alarm` since the last call; empty when none are
            pending. Draining clears the queue, so a caller that does not send them on is the only
            way one could ever be sent twice.
        """
        drained = tuple(self._pending_alarms)
        self._pending_alarms.clear()
        return drained
