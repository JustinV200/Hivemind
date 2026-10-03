"""Define make_scrub: have a Virtual Cell's own Warden scrub it before the pool pauses it.

ADR-0029: "Going dormant scrubs the Cell. Every sub-bee is stopped, scratch is removed ... before
the backend pauses the Cell." Until phase 5 open item 8 closed, the composition root handed the
Overwintering pool (paused Virtual Cells kept for reuse) a scrub that did nothing, so a Cell slept,
and woke for its next task, with whatever the last task left in it. The Queen has no `CellSession`
(terminal access) to a Virtual Cell, only her Waggle link to the Warden running inside it, so the
scrub is an order over that link: `Intervene(RELEASE_LEASE)`, the lever roadmap step 5.13 already
gave a Warden ("stop every sub-bee and release your lease"). The Warden retires every sub-bee,
releases its lease, which kills every process the lease started and removes the lease's scratch
directory (`hivemind.cell.in_cell.InCellLeaseReleaser`), ships its trail segment, and answers with
a `LeaseReleased` whose envelope names the order's in `correlation_id`. `make_scrub` builds the
`Scrubber` that sends the order and waits for that answer, bounded by `SCRUB_TIMEOUT_S` on the
injected clock. `ScrubAcks` holds each wait; the Cell's listener (`hivemind.queen.cell_gate.
listener`) hands the answer to it on the link's own pump, so the Queen's tick never reads it and no
awake episode is woken for it.

A scrub the Queen cannot confirm is a failed one: the Cell's Warden is not attached, its link has
closed, no answer comes in time, or the answer says the scratch directory could not be removed
(`is_restored` false). Each records `cell.scrub_failed` with the reason and raises
`hivemind.hive.CellScrubError`, so `OverwinterPool.admit` admits nothing and
`hivemind.queen.cell_gate.release.make_on_task_finished` tears the Cell down instead: a Cell is
never paused dirty. A Night Veil Cell never reaches a scrub at all, since it is never Overwintered
(`hivemind.hive.lifecycle`, and `OverwinterPool.admit` again).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `queen.cell_gate`. Built
    by the composition root (`hivemind.cli.compose.virtual_cells`), handed to
    `make_on_task_finished`, which passes it to `hivemind.hive.lifecycle.CellLifecycle.overwinter`;
    `ScrubAcks` is shared with the `CellListener`. Calls into `hivemind.cell` (Cell),
    `hivemind.common` (logging, tasks), `hivemind.hive` (CellScrubError, Scrubber),
    `hivemind.pheromone` (CellEvent, TrailRecorder), `hivemind.queen.deps` (WardenLink) and
    waggle only.

Key invariants:
    - A scrub returns only once the Cell's own Warden confirmed it with its scratch gone; on every
      other path it records `cell.scrub_failed` and raises `CellScrubError`.
    - The wait never outlasts `timeout_s` on the injected clock, and its entry in `ScrubAcks` is
      forgotten on every path. An answer that comes after its scrub gave up is still taken, and
      dropped: the Queen's tick never reads a scrub's answer, early or late.
    - `ScrubAcks.claim` takes an answer only from the link of the Cell its order went to.

See Also:
    - docs/adr/0029-overwintering-policy.md for the scrub this module carries out.
    - hivemind.wardens.ticks.control for handle_release_lease, the Warden's half of the order.
    - hivemind.queen.cell_gate.quiesce for make_quiesce, its counterpart before a teardown.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from hivemind.cell import Cell
from hivemind.common.logging import get_logger
from hivemind.common.tasks import reaping
from hivemind.hive import CellScrubError
from hivemind.hive.overwinter import Scrubber
from hivemind.pheromone import CellEvent, TrailRecorder
from hivemind.queen.deps import WardenLink
from waggle.clock import Clock
from waggle.envelope import Envelope, wrap
from waggle.ids import CellId, MessageId, new_event_id
from waggle.messages.cell import LeaseReleased
from waggle.messages.supervision import Intervene, InterventionAction

# How long a scrub waits for its Warden's answer: retiring the bees (already idle, their task
# done), killing what the lease left running (a fifth of a second each) and removing scratch take
# a second or two inside a Cell, plus one Waggle round trip. A Warden this slow is not trusted
# with a reused Cell: past it the scrub fails and the Cell is torn down.
SCRUB_TIMEOUT_S = 10.0
SCRUB_FAILED_KIND = "cell.scrub_failed"  # Why a Cell the pool would have kept was torn down.
# Orders whose scrub gave up unanswered, remembered so an answer that still comes is dropped here
# instead of reaching the Queen's tick, where an unrecognised kind wakes an awake episode for
# nothing. Only a handful of scrubs can fail at once; this covers far more than that.
_GAVE_UP_KEPT = 64
# The order's own reason, as its Warden records and logs it.
_SCRUB_REASON = (
    "Overwintering: stop every sub-bee and remove the finished task's scratch before this Cell "
    "is paused for reuse."
)

__all__ = ["SCRUB_FAILED_KIND", "SCRUB_TIMEOUT_S", "ScrubAcks", "make_scrub"]

log = get_logger(__name__)


class _WardensView(Protocol):
    """Structural stand-in for `hivemind.queen.queen.Queen`'s own `.wardens` property.

    A private copy of the Protocol `hivemind.queen.cell_gate.quiesce` keeps for the same reading
    (codingrules 5.4: a leading-underscore name is its own module's), so neither imports the other.
    """

    @property
    def wardens(self) -> tuple[WardenLink, ...]:
        """Every Warden currently attached, in attachment order."""
        ...


@dataclass(slots=True)
class _Wait:
    """One scrub's wait: the Cell its order went to, and the answer it resolves with."""

    cell_id: CellId
    answer: asyncio.Future[LeaseReleased]


class ScrubAcks:
    """Every scrub still waiting for its answer, by the id of the order's envelope.

    Owns its own mutable state in place (codingrules section 8.5): `_waits` gains an entry when a
    scrub sends its order (`expect`) and loses it when the answer is claimed or the scrub stops
    waiting (`forget`); `_gave_up` keeps the last orders forgotten unanswered, so their late
    answers are still taken. Touched only from the event loop's own thread, so no lock is needed.
    """

    def __init__(self) -> None:
        """Build a ScrubAcks with no scrub waiting."""
        self._waits: dict[MessageId, _Wait] = {}
        self._gave_up: deque[tuple[MessageId, CellId]] = deque(maxlen=_GAVE_UP_KEPT)

    def expect(self, order_id: MessageId, cell_id: CellId) -> asyncio.Future[LeaseReleased]:
        """Register a wait for the answer to the order `order_id`, sent to `cell_id`'s Warden.

        Args:
            order_id: The scrub order's own envelope id, which its answer names.
            cell_id: The Cell the order went to; only its link may answer.

        Returns:
            A future resolved with the Warden's `LeaseReleased` once `claim` takes it.
        """
        answer: asyncio.Future[LeaseReleased] = asyncio.get_running_loop().create_future()
        self._waits[order_id] = _Wait(cell_id=cell_id, answer=answer)
        return answer

    def forget(self, order_id: MessageId) -> None:
        """Stop waiting for `order_id`'s answer; one that still comes is taken and dropped.

        A no-op for a wait already claimed, or never registered.
        """
        wait = self._waits.pop(order_id, None)
        if wait is not None:
            # Given up unanswered: its answer may yet come, and is this scrub's, nobody else's.
            self._gave_up.append((order_id, wait.cell_id))

    def claim(self, cell_id: CellId, envelope: Envelope) -> bool:
        """Take `envelope` if it answers a scrub order sent to `cell_id`'s Warden.

        Args:
            cell_id: The Cell whose link `envelope` arrived on, as its handshake proved it.
            envelope: One frame the Cell sent.

        Returns:
            True when `envelope` was a scrub's answer: its wait now holds it, or (its scrub having
            given up) it is dropped, so the listener queues it for nobody else; False for anything
            else, which the Queen reads as before.
        """
        payload = envelope.payload
        order_id = envelope.correlation_id
        if not isinstance(payload, LeaseReleased) or order_id is None:
            return False  # Not an answer to anything a scrub could have sent.
        wait = self._waits.get(order_id)
        if wait is None:
            # Too late for its scrub, which already gave up: taken, so the Queen's tick never
            # reads it. Anything else names no scrub order of this Cell at all.
            return (order_id, cell_id) in self._gave_up
        if wait.cell_id != cell_id:
            return False  # Names an order sent to another Cell: never taken as its answer.
        del self._waits[order_id]
        if not wait.answer.done():
            wait.answer.set_result(payload)
        return True


def make_scrub(
    queen_getter: Callable[[], _WardensView | None],
    acks: ScrubAcks,
    clock: Clock,
    recorder: TrailRecorder,
    *,
    timeout_s: float = SCRUB_TIMEOUT_S,
) -> Scrubber:
    """Build the `Scrubber` the Overwintering pool runs before a Virtual Cell is paused.

    Args:
        queen_getter: Returns the live Queen (anything with a `.wardens` property), or None before
            one is bound; late-bound exactly as `make_quiesce`'s own (`provider.queen`).
        acks: Where each scrub registers its wait; the same one the `CellListener` claims into.
        clock: Stamps the order and bounds the wait, so a test drives it with a `FakeClock`.
        recorder: The Queen's trail and identity, for `cell.scrub_failed`.
        timeout_s: The longest a scrub waits for its answer. Must be > 0; `SCRUB_TIMEOUT_S`.

    Returns:
        An async callable taking the released Cell; it returns once the Cell is scrubbed.
    """

    async def _scrub(cell: Cell) -> None:
        """Order `cell`'s own Warden to scrub it and wait for its answer (module docstring).

        Raises:
            CellScrubError: The scrub could not be confirmed; `cell.scrub_failed` is recorded.
        """
        link = _find_link(queen_getter(), cell.id)
        if link is None:
            reason: str | None = "its Warden is not attached to the Queen"
        else:
            reason = await _order_and_wait(link, acks, clock, timeout_s)
        if reason is None:
            return  # Confirmed by its own Warden: every bee stopped and its scratch gone.
        await _record_failure(recorder, cell.id, reason)
        raise CellScrubError(cell.id, reason)

    return _scrub


def _find_link(queen: _WardensView | None, cell_id: CellId) -> WardenLink | None:
    """Return `cell_id`'s attached WardenLink, or None (no Queen bound yet, or not attached)."""
    if queen is None:
        return None
    return next((link for link in queen.wardens if link.cell.id == cell_id), None)


async def _order_and_wait(
    link: WardenLink, acks: ScrubAcks, clock: Clock, timeout_s: float
) -> str | None:
    """Send the scrub order over `link` and wait for its answer; return why it failed, or None."""
    order = wrap(_scrub_order(), link.hop, clock=clock)
    # Registered before the send, so an answer can never arrive ahead of its own wait.
    answer = acks.expect(order.id, link.cell.id)
    try:
        # Handed to the link's own send buffer, never awaited on the far side; a link already
        # closed fails the scrub at once rather than waiting out the bound for nothing.
        if not await link.send(order):
            return "its link closed before the scrub order could be sent"
        released = await _answer_within(answer, clock, timeout_s)
    finally:
        acks.forget(order.id)
    if released is None:
        return f"its Warden did not confirm the scrub within {timeout_s:g}s"
    if not released.is_restored:
        return f"its Warden could not remove {len(released.residual_paths)} path(s)"
    return None


async def _answer_within(
    answer: asyncio.Future[LeaseReleased], clock: Clock, timeout_s: float
) -> LeaseReleased | None:
    """Return the answer once it arrives, or None once `timeout_s` passes on `clock`."""
    timer = asyncio.ensure_future(clock.sleep(timeout_s))
    waiters: set[asyncio.Future[Any]] = {answer, timer}
    # Reaped however this ends, a cancellation of the scrub included (codingrules section 11).
    async with reaping(timer):
        # External await on the Warden: a second or two normally; the timer is its bound, and
        # running out is a failed scrub the caller reports, never an exception from here.
        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
    return answer.result() if answer.done() else None


def _scrub_order() -> Intervene:
    """Build the order: release your lease, stopping every sub-bee first (module docstring)."""
    return Intervene(
        action=InterventionAction.RELEASE_LEASE,
        subject=None,  # The recipient Warden itself, never one of its sub-bees (spec 8.3).
        task_id=None,
        slot=None,
        binding=None,
        alarm_id=None,
        reason=_SCRUB_REASON,
    )


async def _record_failure(recorder: TrailRecorder, cell_id: CellId, reason: str) -> None:
    """Record `cell.scrub_failed` about `cell_id` on the Queen's trail, and log it."""
    event = CellEvent(
        id=new_event_id(recorder.clock),
        hive_id=recorder.hive_id,
        node_id=recorder.node_id,  # The Queen's own record of what she could not confirm.
        at=recorder.clock.now(),
        actor="system",
        kind=SCRUB_FAILED_KIND,
        subject_id=cell_id,
        payload={"reason": reason},
    )
    await recorder.trail.record(event)
    log.warning("queen.scrub_failed", cell_id=cell_id, reason=reason)
