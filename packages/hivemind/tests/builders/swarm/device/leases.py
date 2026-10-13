"""Answer the lease, session-lifecycle, watch and update requests the way a Pollen Packet does.

This is the lease half of the scripted device: `cell.request` opens a lease (granting the lower of
the access asked for and the node's, with a scratch directory of its own), `cell.teardown_request`
releases one and reports how it was left, `session.open` and `session.close` open and end the
lease's session (a close ends its running programs and its background processes), `swarm.observe`
answers a read-only observation, and `swarm.node_update` is remembered so a test can read what the
Hive told the device. A request for a lease the device does not hold draws
`hive.session.lease_not_open`, as the contract says.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the scripted device.

Key invariants:
    - A lease is granted `min(requested, node_access)` and never more.
    - A teardown removes the lease, kills its processes and reports the scripted outcome with the
      cause echoed.
    - A session close ends every running exec of the lease with a KILLED exit.

See Also:
    - docs/waggle/gateway-contract.md sections 9.1 and 9.2 for the behaviour mimicked.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from builders.swarm.device.context import DeviceContext
from builders.swarm.device.state import DeviceLease

from waggle.envelope import Envelope
from waggle.ids import new_lease_id
from waggle.messages.base import WaggleMessage
from waggle.messages.cell.leases import (
    CellRequest,
    CellTeardownRequest,
    LeaseOpened,
    LeaseReleased,
)
from waggle.messages.session.commands import (
    SessionClose,
    SessionOpen,
    SessionOutcome,
    SessionRequestKind,
)
from waggle.messages.session.output import SessionExit
from waggle.messages.swarm import NodeUpdate, Observation, Observe

LEASE_NOT_OPEN = "hive.session.lease_not_open"
LEASE_REFUSED = "pollen.lease_refused"

__all__ = ["LEASE_NOT_OPEN", "LEASE_REFUSED", "LeaseService"]

Handler = Callable[[Envelope], Awaitable[None]]


class LeaseService:
    """The scripted device's lease, session and observation behaviour."""

    def __init__(self, ctx: DeviceContext) -> None:
        """Bind the service to a device context."""
        self._ctx = ctx

    def handlers(self) -> dict[type[WaggleMessage], Handler]:
        """The request types this service answers, each with its handler."""
        return {
            CellRequest: self._open_lease,
            CellTeardownRequest: self._teardown,
            SessionOpen: self._open_session,
            SessionClose: self._close_session,
            Observe: self._observe,
            NodeUpdate: self._remember_update,
        }

    async def _open_lease(self, request: Envelope) -> None:
        """Answer `cell.request` with a lease, or refuse another Cell or holder."""
        asked = request.payload
        assert isinstance(asked, CellRequest)
        state = self._ctx.state
        if asked.cell_id != state.party.cell_id:
            await self._ctx.error(request, LEASE_REFUSED, "That is not this device's Cell.")
            return
        if not state.script.accept_any_holder and asked.holder != state.party.warden_id:
            await self._ctx.error(request, LEASE_REFUSED, "That is not this Cell's Warden.")
            return
        granted = min(asked.access_level, state.script.node_access, key=lambda level: level.rank)
        lease_id = new_lease_id(state.clock)
        root = f"{state.script.scratch_base}/{lease_id}"
        state.leases[lease_id] = DeviceLease(
            lease_id=lease_id,
            holder=str(asked.holder),
            scratch_root=root,
            access_level=granted,
            quota_bytes=asked.scratch_quota_bytes,
        )
        await self._ctx.reply(
            request,
            LeaseOpened(
                lease_id=lease_id,
                cell_id=state.party.cell_id,
                holder=state.party.warden_id if asked.holder is None else asked.holder,
                task_id=asked.task_id,
                access_level=granted,
                comb_shield=state.script.comb_shield,
                scratch_root=root,
                allowed_paths=state.script.allowed_scopes,
                dead_man_s=state.script.dead_man_s,
                reason="Granted by the scripted device.",
            ),
        )

    async def _teardown(self, request: Envelope) -> None:
        """Answer `cell.teardown_request` by releasing the lease and reporting the outcome."""
        asked = request.payload
        assert isinstance(asked, CellTeardownRequest)
        state = self._ctx.state
        if asked.lease_id is None:
            await self._ctx.error(request, LEASE_REFUSED, "A Real Cell is never retired.")
            return
        lease = state.leases.pop(asked.lease_id, None)
        if lease is None:
            await self._ctx.error(request, LEASE_NOT_OPEN, "That lease is not open.")
            return
        script = state.script.release
        released = LeaseReleased(
            lease_id=lease.lease_id,
            cell_id=state.party.cell_id,
            holder=state.party.warden_id,
            cause=asked.cause,
            is_restored=script.is_restored,
            killed_processes=state.kill_processes(lease.lease_id),
            residual_paths=script.residual_paths,
            reason="Released by the scripted device.",
        )
        state.released.append(released)
        await self._ctx.reply(request, released)

    async def _open_session(self, request: Envelope) -> None:
        """Answer `session.open` with the OPEN exit, or refuse a lease that is not open."""
        asked = request.payload
        assert isinstance(asked, SessionOpen)
        lease = self._ctx.state.leases.get(asked.lease_id)
        if lease is None:
            await self._ctx.error(request, LEASE_NOT_OPEN, "That lease is not open.")
            return
        lease.session_open = True
        await self._ctx.reply(
            request,
            SessionExit(
                lease_id=lease.lease_id,
                request_id=request.id,
                request_kind=SessionRequestKind.OPEN,
                outcome=SessionOutcome.COMPLETED,
                exit_code=None,
                signal_name=None,
                duration_s=0.0,
                size_bytes=None,
                sha256=None,
                reason="Session open.",
            ),
        )

    async def _close_session(self, request: Envelope) -> None:
        """End a lease's session: kill its execs and background processes, send no answer."""
        asked = request.payload
        assert isinstance(asked, SessionClose)
        state = self._ctx.state
        lease = state.leases.get(asked.lease_id)
        if lease is None:
            return
        lease.session_open = False
        state.kill_processes(lease.lease_id)
        for exec_id in tuple(state.execs):
            state.kill(exec_id)
        state.session_closed.set()

    async def _observe(self, request: Envelope) -> None:
        """Answer `swarm.observe` with the scripted observation (an empty one by default)."""
        asked = request.payload
        assert isinstance(asked, Observe)
        state = self._ctx.state
        if asked.device_id != state.party.device_id:
            await self._ctx.error(request, "waggle.codec.invalid_payload", "Another device.")
            return
        scripted = state.script.observation
        await self._ctx.reply(
            request,
            scripted
            if scripted is not None
            else Observation(
                device_id=state.party.device_id,
                observed_at=state.clock.now(),
                processes=(),
                resources=None,
                log_lines=(),
                file_changes=(),
                is_truncated=False,
                reason="Nothing scripted.",
            ),
        )

    async def _remember_update(self, request: Envelope) -> None:
        """Keep a `swarm.node_update` so a test can read what the Hive told the device."""
        update = request.payload
        assert isinstance(update, NodeUpdate)
        self._ctx.state.updates.append(update)
