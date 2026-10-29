"""Define WatchBook: every watch request this Queen filed, and the owner's answers to them.

The dispatcher asks the book, for a placed task, whether the owner approved the scopes that watch
them on that Cell (ADR-0069). The first ask files a PENDING request; later asks find it, expired
once its time has passed unanswered. The Queen's door settles a request on the owner's word. The
book is in memory, owned by `hivemind.queen.deps.DispatchBook`: a restarted Queen asks again
rather than trust an answer she did not hear in this life, and a request is forgotten once its
task has ended.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    peripherals sub-package. Held by `hivemind.queen.deps.DispatchBook`; asked by
    `hivemind.queen.peripherals.gate`, settled by `hivemind.queen.peripherals.door`. Calls into
    this sub-package's model and errors and waggle only.

Key invariants:
    - At most one request per (task, Cell, scopes); asking again returns the same one.
    - A request is settled once; settling a settled or expired one raises.
    - `approved_scopes` names scopes only for an APPROVED request on that very task and Cell.
    - Past `MAX_WATCH_REQUESTS` the oldest DENIED or EXPIRED ones are dropped; a PENDING or
      APPROVED one stays until its task ends (`forget`), so a live task's retry keeps its answer.

See Also:
    - hivemind.queen.peripherals.model for WatchRequest.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from hivemind.queen.peripherals.errors import UnknownWatchRequestError, WatchRequestSettledError
from hivemind.queen.peripherals.model import WatchRequest, WatchStatus
from waggle.ids import CellId, DeviceId, TaskId

WATCH_REQUEST_TTL = timedelta(minutes=15)  # How long a task waits for the owner before refusal.
MAX_WATCH_REQUESTS = 256  # Requests kept in memory; a Hive never has this many tasks waiting.

__all__ = ["MAX_WATCH_REQUESTS", "WATCH_REQUEST_TTL", "WatchBook"]


@dataclass(slots=True)
class WatchBook:
    """Every watch request this Queen filed, by id; owned in place (codingrules 8.5)."""

    requests: dict[str, WatchRequest] = field(default_factory=dict)

    def find(
        self, task_id: TaskId, cell_id: CellId, scopes: tuple[str, ...]
    ) -> WatchRequest | None:
        """Return the request for this task, Cell and scopes, or None if none was filed."""
        return next(
            (
                request
                for request in self.requests.values()
                if (request.task_id, request.cell_id, request.scopes) == (task_id, cell_id, scopes)
            ),
            None,
        )

    def file(self, request: WatchRequest) -> None:
        """Keep a freshly built PENDING request, dropping the oldest refused ones past the bound."""
        self.requests[request.id] = request
        # An APPROVED request is still in force for its live task's retries: never dropped here.
        refused = sorted(
            r.id
            for r in self.requests.values()
            if r.status in (WatchStatus.DENIED, WatchStatus.EXPIRED)
        )
        # Ids sort by filing time, so the first ones are the oldest answers.
        for old in refused[: max(0, len(self.requests) - MAX_WATCH_REQUESTS)]:
            del self.requests[old]

    def expire(self, now: datetime) -> None:
        """Settle every PENDING request whose time has passed as EXPIRED."""
        for request in tuple(self.requests.values()):
            if request.status is WatchStatus.PENDING and now >= request.expires_at:
                self.requests[request.id] = request.settled(WatchStatus.EXPIRED, None)

    def pending(self, now: datetime) -> tuple[WatchRequest, ...]:
        """Return every request still waiting for the owner, oldest first."""
        self.expire(now)
        waiting = (r for r in self.requests.values() if r.status is WatchStatus.PENDING)
        return tuple(sorted(waiting, key=lambda request: request.id))

    def decide(
        self, request_id: str, approved: bool, device_id: DeviceId, now: datetime
    ) -> WatchRequest:
        """Settle a PENDING request on the owner's word and return it.

        Args:
            request_id: The request the owner answered.
            approved: Yes (APPROVED) or no (DENIED).
            device_id: The Hive Stand device the owner answered from.
            now: The time of the answer; a request past its expiry is EXPIRED, not answered.

        Returns:
            The settled request.

        Raises:
            UnknownWatchRequestError: No request has that id (or it was forgotten).
            WatchRequestSettledError: It was already answered, or it expired.
        """
        self.expire(now)
        request = self.requests.get(request_id)
        if request is None:
            raise UnknownWatchRequestError(request_id)
        if request.status is not WatchStatus.PENDING:
            raise WatchRequestSettledError(request_id, request.status.value)
        status = WatchStatus.APPROVED if approved else WatchStatus.DENIED
        settled = request.settled(status, device_id)
        self.requests[request_id] = settled
        return settled

    def approved_scopes(self, task_id: TaskId, cell_id: CellId) -> tuple[str, ...]:
        """Return the scopes the owner approved for this task on this Cell; empty if none."""
        scopes = {
            scope
            for request in self.requests.values()
            if request.status is WatchStatus.APPROVED
            and (request.task_id, request.cell_id) == (task_id, cell_id)
            for scope in request.scopes
        }
        return tuple(sorted(scopes))

    def forget(self, task_ids: Iterable[TaskId]) -> None:
        """Drop every request of a task that has ended: nothing will be assigned for it again."""
        ended = set(task_ids)
        for request in tuple(self.requests.values()):
            if request.task_id in ended:
                del self.requests[request.id]
