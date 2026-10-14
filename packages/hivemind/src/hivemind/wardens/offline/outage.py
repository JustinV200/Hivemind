"""Remember an outage: the tasks a device Warden paused, when it was cut off, and who wakes it.

A device's Warden (the always-on supervisor of one Cell, a unit of compute) can be cut off in two
ways. Its own link to the Queen (the Hive's central orchestrator) can drop, if it runs on the
device (Level 1 of the runtime ladder, ADR-0052); or the link between the Hive Stand and the
device can drop, if it runs on the Hive Stand with only the device's hands out of reach (Level 0).
Either way the Warden pauses its sub-bees at a Handoff instead of letting their commands fail, and
resumes them when the link is back. `Outage` is what it must remember meanwhile, kept apart from
the Warden itself (whose class is at its size limit) and handed to it in `DeviceDeps`: the tasks it
paused, each with the Handoff its bee wrote (`HeldTask`); when the cut began; whether its lease
belongs to a link that has gone; and an event that wakes the Warden's tick the instant something
changes, because the Warden would otherwise notice only on its next heartbeat. `RemoteDeps` is the
other half, for a Warden on the device: the reconnecting link it asks "are you up", the limit past
which it gives up waiting and clusters fully, and, for a Warden that investigates why its link went
(ADR-0056), the budget it may spend on that and the host it is trying to reach. The `Outage` holds
that search too (`investigation`), because it is the one thing about a cut that outlives the cut by
a moment: a Drone that fixed the link is still writing up what it did.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    offline sub-package. Built by the composition root and carried in `hivemind.wardens.deps.
    DeviceDeps`; written by `hivemind.wardens.offline.freeze` and `.resume` and, through its few
    methods, by the Warden's tick handlers (which never import this package, so a tick handler
    only ever calls a method on the object `DeviceDeps` hands it). Calls into waggle only.

Key invariants:
    - Owned by exactly one Warden; the tick that owns it is the only task that changes the held
      tasks. The swarm gate and the reconnecting link only call `nudge`, from their own tasks.
    - A task is held under its id exactly once; holding it again replaces the earlier entry.
    - `wake_future` always returns something the tick can reap, so a Warden with no outage waits
      exactly as before.
    - `investigation` is cleared only by the search itself, never by `begin` or `end`, so ending
      the outage does not lose a Drone's finding; a new outage replaces a search left over.

See Also:
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for what an outage does.
    - docs/adr/0056-active-reconnection-diagnostic-drones.md for the budget and the search.
    - hivemind.wardens.offline.freeze for the pause, and hivemind.wardens.offline.resume for the
      way back.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from hivemind.wardens.deps import device_outage
from waggle.ids import TaskId
from waggle.messages import HandoffRef
from waggle.messages.task import TaskAssign

if TYPE_CHECKING:
    from hivemind.wardens.offline.diagnose.state import Investigation
    from hivemind.wardens.warden import Warden

__all__ = [
    "HeldTask",
    "Outage",
    "QueenLinkView",
    "ReconnectBudget",
    "RemoteDeps",
    "is_link_down",
    "outage_of",
    "remote_of",
    "wake_future",
]


@dataclass(frozen=True, slots=True)
class HeldTask:
    """One task a Warden paused, and what it needs to carry on.

    Attributes:
        assignment: The TaskAssign the paused sub-bee ran; the resume is this, with `handoff`.
        handoff: The Handoff the sub-bee wrote as it paused, or None if it had written none yet
            (the task then starts again from the assignment's own `resume_from`).
        held_at: When it was paused.
    """

    assignment: TaskAssign
    handoff: HandoffRef | None
    held_at: datetime


class QueenLinkView(Protocol):
    """The one question a Warden asks its reconnecting link."""

    @property
    def is_online(self) -> bool:
        """Whether the link to the Queen is forwarding messages right now."""
        ...


@dataclass(frozen=True, slots=True)
class ReconnectBudget:
    """What a Warden may spend finding out why its link went (`[supervision] reconnect_budget`).

    Attributes:
        attempts: How many diagnostic Drones it may send, one after another.
        wall_clock_s: Seconds, counted from the moment the link went, all of them may take
            together. It is carved out of `max_offline_s` and is below it, never added to it.
    """

    attempts: int
    wall_clock_s: float


@dataclass(frozen=True, slots=True)
class RemoteDeps:
    """What a Warden that dials the Queen from a device is built with, beyond an `Outage`.

    Attributes:
        link: The Warden's reconnecting link (`ReconnectingQueenLink`), read for its state.
        max_offline_s: How long the link may stay down before the Warden clusters fully, whatever
            it can still think with (`[supervision] max_offline_s`, codingrules 8.13).
        reconnect_budget: What it may spend on a diagnostic Drone while the link is down; None
            investigates nothing (roadmap 11.9a).
        peer: The Queen's host as the Drone's objective names it; never a credential or a path.
    """

    link: QueenLinkView
    max_offline_s: float
    reconnect_budget: ReconnectBudget | None = None
    peer: str = ""


class Outage:
    """The held tasks and the clock of one device Warden's outage.

    Owns mutable state (codingrules 8.5, documented): everything below changes in place, on the
    tick of the one Warden that holds this object.
    """

    def __init__(self) -> None:
        """Start with nothing held and no outage under way."""
        self.cut_since: datetime | None = None  # When the link to the Queen went; None when up.
        self.reason = ""  # Why, in one short sentence, for the trail and the log.
        self.is_clustered_fully = False  # The limit passed and every sub-bee was paused.
        self.is_cell_cut = False  # The Cell's link was lost and its sub-bees were paused for it.
        self.is_lease_stale = False  # The Cell is back, but the lease it had may not be.
        self.retry_after: datetime | None = None  # Not before this does a failed resume try again.
        self.frozen_grants: dict[str, int] = {}  # grant id -> revision held when the cut began.
        self.investigation: Investigation | None = None  # The search for why it went (11.9a).
        self._held: dict[TaskId, HeldTask] = {}
        self._wake = asyncio.Event()

    # ──────────────────────────────────────────────────────────────────────────
    # The held tasks
    # ──────────────────────────────────────────────────────────────────────────

    @property
    def has_held(self) -> bool:
        """Whether any task is held."""
        return bool(self._held)

    def hold(self, assignment: TaskAssign, handoff: HandoffRef | None, at: datetime) -> None:
        """Hold `assignment`'s task as paused, replacing any earlier entry for it.

        Args:
            assignment: The paused sub-bee's TaskAssign.
            handoff: Its last Handoff, if it has written one.
            at: When it was paused.
        """
        self._held[assignment.task_id] = HeldTask(assignment, handoff, at)

    def is_holding(self, task_id: TaskId) -> bool:
        """Whether a task is held under `task_id`."""
        return task_id in self._held

    def held(self) -> tuple[HeldTask, ...]:
        """Every held task, oldest first."""
        return tuple(self._held.values())

    def forget(self, task_id: TaskId) -> None:
        """Drop the task under `task_id`, if held: the Queen took it back, or it ended."""
        self._held.pop(task_id, None)

    def take(self) -> tuple[HeldTask, ...]:
        """Remove and return every held task, oldest first."""
        taken = tuple(self._held.values())
        self._held.clear()
        return taken

    def note_handoff(self, task_id: TaskId, handoff: HandoffRef | None) -> None:
        """Record the Handoff a paused sub-bee left, if the task is held and the bee left one.

        Args:
            task_id: The task whose sub-bee just stopped.
            handoff: The last Handoff that sub-bee reported (None if it reported none).
        """
        held = self._held.get(task_id)
        if held is not None and handoff is not None:
            self._held[task_id] = HeldTask(held.assignment, handoff, held.held_at)

    # ──────────────────────────────────────────────────────────────────────────
    # The cut, and the wake
    # ──────────────────────────────────────────────────────────────────────────

    def begin(self, at: datetime, reason: str, grants: dict[str, int]) -> None:
        """Note that the link to the Queen has been down since `at`, for `reason`.

        Args:
            at: When the Warden noticed.
            reason: Why, in one short sentence.
            grants: The revision of every shared grant the Warden holds now, which stay as they
                are until the Queen settles them on return.
        """
        self.cut_since = at
        self.reason = reason
        self.is_clustered_fully = False
        self.frozen_grants = grants

    def end(self) -> None:
        """Note that the link to the Queen is back."""
        self.cut_since = None
        self.reason = ""
        self.is_clustered_fully = False
        self.frozen_grants = {}

    def nudge(self) -> None:
        """Wake the Warden's tick: something it must look at has changed. Safe from any task."""
        self._wake.set()

    def waiter(self) -> asyncio.Future[bool]:
        """Return a throwaway future that resolves when `nudge` is called.

        The tick races it against its other waits and reaps it, as it does its stop future.
        """
        return asyncio.ensure_future(self._wake.wait())

    def acknowledge(self) -> None:
        """Clear the wake: everything it signalled is about to be looked at."""
        self._wake.clear()


def outage_of(warden: Warden) -> Outage | None:
    """Return `warden`'s `Outage`, or None for a Warden that is never cut off.

    Args:
        warden: Any Warden.

    Returns:
        The outage its `DeviceDeps` carries; None for the Hive Stand's and a Virtual Cell's.
    """
    return device_outage(warden._deps)


def remote_of(warden: Warden) -> RemoteDeps | None:
    """Return the link facts of a Warden that dials the Queen, or None for an in-process one.

    Args:
        warden: Any Warden.

    Returns:
        The reconnecting link and the offline limit its `DeviceDeps` carries, if it has them.
    """
    device = warden._deps.device
    return None if device is None else device.remote


def is_link_down(warden: Warden) -> bool:
    """Return whether `warden` dials the Queen and its link is down right now.

    Args:
        warden: Any Warden.

    Returns:
        False for a Warden with no reconnecting link (it never goes OFFLINE) and for one whose
        link is up.
    """
    remote = remote_of(warden)
    return remote is not None and not remote.link.is_online


def wake_future(warden: Warden) -> asyncio.Future[bool]:
    """Return what the Warden's tick races so an outage change ends its wait.

    Args:
        warden: The Warden whose tick is about to wait.

    Returns:
        The outage's waiter, or a future that never resolves for a Warden with no outage, so the
        tick can reap it the same way in both cases.
    """
    outage = outage_of(warden)
    if outage is None:
        return asyncio.get_running_loop().create_future()
    return outage.waiter()
