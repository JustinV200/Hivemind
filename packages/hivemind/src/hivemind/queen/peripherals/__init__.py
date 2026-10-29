"""Re-export the owner's approval of what watches them: watch requests, their book and the door.

ADR-0069: a Worker gets the operator's running screen (`exoskeleton:real_display`) or audio
(`exoskeleton:audio`) only after the owner approved that task on that Cell from the Hive Stand's
browser. `model` is one request (`WatchRequest`, `WatchStatus`), `book` the Queen's in-memory book
of them (`WatchBook`, owned by `hivemind.queen.deps.DispatchBook`), `door` her methods the Hive
Entrance answers through (`WatchDoor`) and `errors` the two refusals of an answer. `gate`, the
dispatcher's hold, is imported by the dispatcher directly: this face never imports it, because
`hivemind.queen.deps` imports this face and the gate needs `deps`.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package.
    Imported by `hivemind.queen.deps`, `hivemind.queen.queen` and the Hive Entrance's watch
    routes. Calls into this sub-package's modules only.

Key invariants:
    - This module contains no logic of its own: it is a face (codingrules 5.2).

See Also:
    - docs/adr/0069-the-owner-approves-the-running-screen-and-audio-at-the-hive-stand.md.

Public API:
    - WatchRequest, WatchStatus, WATCH_ID_PATTERN, new_watch_id: one request (model).
    - WatchBook, WATCH_REQUEST_TTL, MAX_WATCH_REQUESTS: the book of them (book).
    - WatchDoor: the Queen's list and answer methods (door).
    - UnknownWatchRequestError, WatchRequestSettledError: an answer's refusals (errors).
"""

from hivemind.queen.peripherals.book import MAX_WATCH_REQUESTS, WATCH_REQUEST_TTL, WatchBook
from hivemind.queen.peripherals.door import WatchDoor
from hivemind.queen.peripherals.errors import UnknownWatchRequestError, WatchRequestSettledError
from hivemind.queen.peripherals.model import (
    WATCH_ID_PATTERN,
    WatchRequest,
    WatchStatus,
    new_watch_id,
)

__all__ = [
    "MAX_WATCH_REQUESTS",
    "WATCH_ID_PATTERN",
    "WATCH_REQUEST_TTL",
    "UnknownWatchRequestError",
    "WatchBook",
    "WatchDoor",
    "WatchRequest",
    "WatchRequestSettledError",
    "WatchStatus",
    "new_watch_id",
]
