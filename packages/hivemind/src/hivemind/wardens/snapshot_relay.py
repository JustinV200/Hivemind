"""Define RelaySnapshotter: a Virtual Cell Warden's Snapshotter, relayed to the Queen over Waggle.

A Virtual Cell's own Warden runs *inside* the Cell (ADR-0027) and so cannot reach the host's
Docker daemon or QEMU process to snapshot or roll back its own Cell -- only the Queen, on the Hive
Stand, holds that backend (`hivemind.hive.snapshot.snapshotter_for`). `RelaySnapshotter` is the
`hivemind.cell.Snapshotter` a Virtual Cell's Warden hands its `CappingGate`
(`hivemind.supervision.capping.gate.GateDeps.snapshotter`) instead: it sends a
`waggle.messages.cell.snapshot.CellSnapshotRequest`/`CellRollbackRequest` over the Warden's own
`queen_link` and awaits the matching reply, converting a timeout, a dropped link, or the Queen's
own `error` field into `hivemind.cell.SnapshotUnsupportedError`, the one exception
`hivemind.supervision.capping.gate.CappingGate` catches to fall back to REVERSE_DIFF (ADR-0018) --
exactly the same fallback a Real Cell's `NoopSnapshotter` already triggers, so a Virtual Cell whose
Queen link is down degrades the same way a Real Cell always has.

`waggle.transport.base.Transport`'s own concurrency model is "one task sends and one task receives
at a time" on a given transport, and the Warden's own tick loop is already that one receiving task
for `queen_link` (`hivemind.wardens.warden.Warden._queen_iter`); `snapshot`/`rollback` therefore
never call `.receive()` themselves. Instead each call appends a fresh `asyncio.Future` to a small
per-kind queue before sending its request, and `handle_reply` -- called by the Warden's own tick
(`hivemind.wardens.ticks.control.handle_snapshot_reply`) once it sees a `CellSnapshotReply`/
`CellRollbackReply` arrive on `queen_link` -- resolves the oldest pending future of the matching
kind. This is FIFO-per-kind, not per-request-id matching (documented simplification: neither reply
carries an id of its own beyond `cell_id`, and `hivemind.supervision.attendant.InboxItem` does not
carry an envelope's `correlation_id`; ordering within one connection, which `Transport`'s own
contract guarantees, is what makes FIFO correct for the ordinary case of one relay request in
flight per kind at a time). A snapshot or rollback freezes the whole Cell while the Queen's backend
works (`docker commit` pauses the container; a QMP `savevm` stops the VM), this Warden included,
so its Heartbeats stop for as long as that takes -- 27 s for a 1 GiB change on the development
host, more than half a Virtual Cell's 45 s window. Before each request the relay therefore calls
the announcer its Warden bound (`announce_freezes`), which sends one Heartbeat declaring the
relay's own timeout as its interval, and the Queen judges the frozen Warden by that longer window
rather than raising a false `CELL_UNREACHABLE` for silence the Hive caused itself.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers). Built by
    `hivemind.cli.in_cell.deps.build_in_cell_warden_deps` and set on `WardenDeps.snapshotter`, and
    called by `hivemind.wardens.spawn.spawn._build_capping_gate` through `GateDeps.snapshotter`.
    Calls into `hivemind.cell` (Cell, SnapshotId, SnapshotUnsupportedError, Snapshotter),
    `hivemind.common.logging`, `hivemind.wardens.deps` (send_guarded) and waggle only.

Key invariants:
    - `snapshot`/`rollback` never call `queen_link.receive()`: only the Warden's own tick loop
      does (module docstring's own concurrency-model note).
    - A timeout, a dropped/closed link, or a reply whose own `error`/`ok=False` field reports
      failure all raise `SnapshotUnsupportedError`, never anything else -- the one exception the
      Capping gate's REVERSE_DIFF fallback recognises (ADR-0018).
    - `handle_reply` is a no-op, not an error, when no matching future is pending (a reply for a
      request this instance's own timeout already gave up on): the Waggle spec's own "unmatched
      reply is dropped at debug level" rule (spec section 3).
    - With an announcer bound, every request is preceded on the link by the announcement of the
      freeze it may cause, never followed by it.

See Also:
    - docs/adr/0018-capping-gate-postconditions-and-risk-tiers.md for the Snapshotter contract and
      the REVERSE_DIFF fallback SnapshotUnsupportedError triggers.
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md for why a Virtual
      Cell's own Warden cannot reach its host backend directly.
    - hivemind.cell.snapshot for Snapshotter, SnapshotId and SnapshotUnsupportedError.
    - waggle.messages.cell.snapshot for the four relay messages this module builds and reads.
    - hivemind.wardens.ticks.control for handle_snapshot_reply, this module's one caller of
      `handle_reply`.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from typing import TypeVar

from hivemind.cell import Cell, SnapshotId, SnapshotUnsupportedError
from hivemind.common.logging import get_logger
from hivemind.wardens.links import send_guarded
from waggle.clock import Clock
from waggle.envelope import Hop, wrap
from waggle.ids import CellId
from waggle.messages.cell.snapshot import (
    CellRollbackReply,
    CellRollbackRequest,
    CellSnapshotReply,
    CellSnapshotRequest,
)
from waggle.transport.base import Transport

# Either reply this module's own two request kinds may draw; used only for handle_reply's own
# argument, which may be told apart by isinstance. _ask/_resolve are instead generic over `_R`
# (below), bound to exactly one of the two, so mypy can tell `snapshot()`'s own reply from
# `rollback()`'s without either call site needing its own isinstance narrowing.
_Reply = CellSnapshotReply | CellRollbackReply
_R = TypeVar("_R", CellSnapshotReply, CellRollbackReply)
# Told, before each request, the longest the Cell may stay frozen for it (this relay's timeout).
FreezeAnnouncer = Callable[[float], Awaitable[None]]

DEFAULT_TIMEOUT_S = 30.0  # Generous for a `docker commit`/QMP savevm plus one Waggle round trip.
# Why RelaySnapshotter asks for a snapshot: read by the Queen only as a human-readable purpose
# string (waggle.messages.cell.snapshot.CellSnapshotRequest.purpose); not a Capping tier of its
# own, since GateDeps.snapshotter carries no tier context to name one more precisely.
_SNAPSHOT_PURPOSE = (
    "hivemind.supervision.capping: pre-proposal snapshot before an irreversible tier"
)

__all__ = ["DEFAULT_TIMEOUT_S", "FreezeAnnouncer", "RelaySnapshotter"]

log = get_logger(__name__)


class RelaySnapshotter:
    """The Snapshotter a Virtual Cell's own Warden hands its CappingGate; relayed to the Queen.

    Owns its own small mutable state in place (codingrules section 8.5, documented): two FIFO
    queues of pending replies, one per kind, drained by `handle_reply`, and the announcer its
    Warden binds once (`announce_freezes`), None until then.
    """

    def __init__(
        self,
        cell_id: CellId,
        queen_link: Transport,
        hop: Hop,
        clock: Clock,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        """Build a RelaySnapshotter for `cell_id`, over the Warden's own queen_link.

        Args:
            cell_id: This Warden's own Cell; every request names it.
            queen_link: The Warden's own end of its Waggle link to the Queen
                (`hivemind.wardens.deps.WardenDeps.queen_link`); `send` only, never `.receive()`
                (module docstring).
            hop: The Warden's own address, the Queen's, and the sending node id
                (`hivemind.wardens.deps.WardenDeps.hop`), stamped on every request this sends.
            clock: Injected time source for the reply timeout.
            timeout_s: Seconds to wait for a reply before raising SnapshotUnsupportedError.
        """
        self._cell_id = cell_id
        self._queen_link = queen_link
        self._hop = hop
        self._clock = clock
        self._timeout_s = timeout_s
        self._pending_snapshot: deque[asyncio.Future[CellSnapshotReply]] = deque()
        self._pending_rollback: deque[asyncio.Future[CellRollbackReply]] = deque()
        self._announce: FreezeAnnouncer | None = None

    def announce_freezes(self, announce: FreezeAnnouncer) -> None:
        """Have every later request announce the freeze it may cause, before it is sent.

        Bound by the Warden once it exists (`hivemind.wardens.ticks.heartbeat.
        bind_freeze_announcer`), since this relay is built before it, into its `WardenDeps`.

        Args:
            announce: Awaited with this relay's own timeout before each request goes out.
        """
        self._announce = announce

    async def snapshot(self, cell: Cell) -> SnapshotId:
        """Ask the Queen to snapshot `cell` (always this instance's own Cell); see Snapshotter."""
        request = CellSnapshotRequest(cell_id=cell.id, purpose=_SNAPSHOT_PURPOSE)
        reply = await self._ask(request, self._pending_snapshot)
        if reply.error is not None or reply.snapshot_id is None:
            raise SnapshotUnsupportedError(cell.id)
        return SnapshotId(reply.snapshot_id)

    async def rollback(self, cell: Cell, snapshot: SnapshotId) -> None:
        """Ask the Queen to roll `cell` back to `snapshot`; see Snapshotter."""
        request = CellRollbackRequest(cell_id=cell.id, snapshot_id=str(snapshot))
        reply = await self._ask(request, self._pending_rollback)
        if not reply.ok:
            raise SnapshotUnsupportedError(cell.id)

    def handle_reply(self, payload: _Reply) -> bool:
        """Resolve the oldest pending ask of `payload`'s own kind, if one is waiting.

        Called by the Warden's own tick (`hivemind.wardens.ticks.control.handle_snapshot_reply`)
        for every `CellSnapshotReply`/`CellRollbackReply` it drains off `queen_link`.

        Args:
            payload: The reply just received.

        Returns:
            Whether a pending ask was resolved; False means a stale or unmatched reply, dropped
            at debug level (Waggle spec section 3's own "unmatched reply" rule).
        """
        if isinstance(payload, CellSnapshotReply):
            resolved = _resolve(self._pending_snapshot, payload)
        else:
            resolved = _resolve(self._pending_rollback, payload)
        if not resolved:
            log.debug("snapshot_relay.unmatched_reply", cell_id=self._cell_id)
        return resolved

    async def _ask(
        self,
        request: CellSnapshotRequest | CellRollbackRequest,
        queue: deque[asyncio.Future[_R]],
    ) -> _R:
        """Send `request` and await the reply `handle_reply` delivers, or time out."""
        future: asyncio.Future[_R] = asyncio.get_running_loop().create_future()
        queue.append(future)
        try:
            if self._announce is not None:
                # Ahead of the request on the same ordered link: the Cell may freeze right after.
                await self._announce(self._timeout_s)
            envelope = wrap(request, self._hop, clock=self._clock)
            if not await send_guarded(self._queen_link, envelope):
                # Phase-7 handoff item 8: a closed or dropped queen link is exactly what this
                # class already turns a timed-out or refused reply into, so a request that never
                # leaves at all takes the same SnapshotUnsupportedError fallback.
                raise SnapshotUnsupportedError(self._cell_id)
            async with asyncio.timeout(self._timeout_s):
                return await future
        except TimeoutError as exc:
            raise SnapshotUnsupportedError(self._cell_id) from exc
        finally:
            if not future.done() and future in queue:
                queue.remove(future)


def _resolve[T: (CellSnapshotReply, CellRollbackReply)](
    queue: deque[asyncio.Future[T]], payload: T
) -> bool:
    """Resolve the oldest not-yet-done future in `queue` with `payload`; False if none pending."""
    while queue:
        future = queue.popleft()
        if not future.done():
            future.set_result(payload)
            return True
    return False
