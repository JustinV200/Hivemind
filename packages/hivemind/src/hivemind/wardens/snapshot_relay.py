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
never call `.receive()` themselves. Instead each call registers a fresh `asyncio.Future` under its
request envelope's own id before sending it, and `handle_reply` -- called by the Warden's own tick
(`hivemind.wardens.ticks.control.handle_snapshot_reply`) once it sees a `CellSnapshotReply`/
`CellRollbackReply` arrive on `queen_link` -- resolves the one ask whose request that reply's
envelope names in `correlation_id` (Waggle spec section 3: a reply's `correlation_id` is its
request's id; the Queen's listener sets it, `hivemind.queen.cell_gate.listener`), carried to it
on `hivemind.supervision.attendant.InboxItem.correlation_id`. Matching by id, not by arrival
order (phase 5 open item 7), keeps two proposals in flight from one Warden at once (two sub-bees,
each with an irreversible proposal) from taking each other's snapshot when the Queen's backend
answers them out of order. A snapshot or rollback freezes the whole Cell while the Queen's backend
works (`docker commit` pauses the container; a QMP `savevm` stops the VM), this Warden included,
so its Heartbeats stop for as long as that takes -- 27 s for a 1 GiB change on the development
host, more than half a Virtual Cell's 45 s window. Before each request the relay therefore calls
the announcer its Warden bound (`announce_freezes`), which sends one Heartbeat declaring the
relay's own timeout as its interval, and the Queen judges the frozen Warden by that longer window
rather than raising a false `CELL_UNREACHABLE` for silence the Hive caused itself. The backend may
take longer than any interval declared beforehand (a large change, a slow disk), so the Queen, who
runs it, also holds this Warden's liveness for exactly as long as it takes (`hivemind.queen.attach.
frozen_cell`); the announcement covers the moments before her listener reads the request.

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
    - A reply resolves only the ask whose request envelope its `correlation_id` names, and only
      when it is that ask's own kind; `handle_reply` is a no-op, not an error, for any other (no
      or an unknown correlation id, the other kind, or an ask this instance's own timeout already
      gave up on): the Waggle spec's own "unmatched reply is dropped at debug level" rule
      (spec section 3).
    - Every ask's entry is registered before its request is sent and removed on every way out,
      so no reply can arrive ahead of its ask and no finished ask keeps an entry.
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
from collections.abc import Awaitable, Callable
from typing import TypeVar

from hivemind.cell import Cell, SnapshotId, SnapshotUnsupportedError
from hivemind.common.logging import get_logger
from hivemind.wardens.links import send_guarded
from waggle.clock import Clock
from waggle.envelope import Hop, wrap
from waggle.ids import CellId, MessageId
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

    Owns its own small mutable state in place (codingrules section 8.5, documented): the asks
    still waiting for a reply, one table per kind keyed by each request envelope's own id,
    resolved by `handle_reply`, and the announcer its Warden binds once (`announce_freezes`),
    None until then.
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
        self._pending_snapshot: dict[MessageId, asyncio.Future[CellSnapshotReply]] = {}
        self._pending_rollback: dict[MessageId, asyncio.Future[CellRollbackReply]] = {}
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

    def handle_reply(self, payload: _Reply, correlation_id: str | None) -> bool:
        """Resolve the ask whose request `correlation_id` names, if it waits for this kind.

        Called by the Warden's own tick (`hivemind.wardens.ticks.control.handle_snapshot_reply`)
        for every `CellSnapshotReply`/`CellRollbackReply` it drains off `queen_link`.

        Args:
            payload: The reply just received.
            correlation_id: Its envelope's own `correlation_id`: the id of the request it answers.

        Returns:
            Whether a pending ask was resolved; False means a stale or unmatched reply, dropped
            at debug level (Waggle spec section 3's own "unmatched reply" rule).
        """
        if correlation_id is None:
            resolved = False  # A reply that names no request answers none of them.
        elif isinstance(payload, CellSnapshotReply):
            resolved = _resolve(self._pending_snapshot, MessageId(correlation_id), payload)
        else:
            resolved = _resolve(self._pending_rollback, MessageId(correlation_id), payload)
        if not resolved:
            log.debug("snapshot_relay.unmatched_reply", cell_id=self._cell_id)
        return resolved

    async def _ask(
        self,
        request: CellSnapshotRequest | CellRollbackRequest,
        pending: dict[MessageId, asyncio.Future[_R]],
    ) -> _R:
        """Send `request` and await the reply `handle_reply` delivers for it, or time out."""
        future: asyncio.Future[_R] = asyncio.get_running_loop().create_future()
        request_id: MessageId | None = None
        try:
            if self._announce is not None:
                # Ahead of the request on the same ordered link: the Cell may freeze right after.
                await self._announce(self._timeout_s)
            envelope = wrap(request, self._hop, clock=self._clock)
            # Registered under the request's own id before it is sent, so its reply, which names
            # that id, can never arrive ahead of the ask waiting for it.
            request_id = envelope.id
            pending[request_id] = future
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
            if request_id is not None:
                pending.pop(request_id, None)


def _resolve[T: (CellSnapshotReply, CellRollbackReply)](
    pending: dict[MessageId, asyncio.Future[T]], request_id: MessageId, payload: T
) -> bool:
    """Resolve the ask registered under `request_id` with `payload`; False if none waits."""
    future = pending.pop(request_id, None)
    if future is None or future.done():
        return False
    future.set_result(payload)
    return True
