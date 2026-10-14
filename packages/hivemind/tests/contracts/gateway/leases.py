"""Open, release and re-term the leases of a gateway under test, and open or close its sessions.

A lease (a scoped, time-limited right to use the device) is how the Queen hands a device to a
Warden: `cell.request` opens one, `cell.teardown` releases it, and a `swarm.node_update` changes
the terms the device holds it on. These are the requests the Queen sends (sections 8 and 10 of the
gateway contract); the Warden then opens the lease's session (`session.open`) and closes it
(`session.close`, which draws no answer, so `close_session` waits on a ping). `leased` holds one
lease for a block of a test and releases it after, whatever the block did.

Fits into the Hive:
    Test infrastructure (codingrules section 14.3), not shipped. A part of
    `contracts.gateway.Driver`; builds on `contracts.gateway.calls`.

Key invariants:
    - A request is the one the contract names, from the address the contract says sends it; what
      the gateway does with it is the gateway's.
    - `leased` releases with `IMMEDIATE` urgency and ignores a release that cannot be made (the
      test may have released, or broken the link, on purpose).

See Also:
    - docs/waggle/gateway-contract.md sections 8 and 10 for the requests sent here.
    - hivemind.swarm.enrolment.node_update_for for how the new terms are built.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from contracts.gateway.calls import Calls, exit_of

from hivemind.cell import AccessLevel as HiveAccess
from hivemind.swarm.enrolment import node_update_for
from hivemind.swarm.enrolment.capabilities import granted_capabilities
from hivemind.swarm.errors import DeviceRequestError, DeviceTimeoutError, GatewayUnavailableError
from waggle.ids import CellId, LeaseId, WardenId
from waggle.messages.cell import CellRequest, CellTeardownRequest, LeaseOpened, LeaseReleased
from waggle.messages.cell.status import ReleaseCause
from waggle.messages.labels import AccessLevel, Urgency
from waggle.messages.session import SessionClose, SessionExit, SessionOpen
from waggle.messages.swarm import NodeUpdate

__all__ = ["LeaseCalls"]


class LeaseCalls(Calls):
    """The requests that open, release and re-term leases, and open or close their sessions."""

    async def open_lease(
        self,
        access: AccessLevel = AccessLevel.FULL,
        *,
        quota: int | None = None,
        holder: WardenId | None = None,
        cell_id: CellId | None = None,
    ) -> LeaseOpened:
        """Ask the gateway for a lease on a Cell (its own by default); return `lease_opened`."""
        request = CellRequest(
            cell_id=cell_id or self.node.cell_id,
            needs=None,
            holder=holder or self.node.warden_id,
            task_id=None,
            access_level=access,
            lifetime_s=None,
            scratch_quota_bytes=quota,
            reason="The conformance suite leases the device.",
        )
        answer = (await self.ask(request, as_queen=True)).payload
        assert isinstance(answer, LeaseOpened)
        return answer

    def teardown(
        self,
        lease_id: LeaseId | None,
        *,
        cause: ReleaseCause = ReleaseCause.COMPLETED,
        urgency: Urgency = Urgency.GRACEFUL,
    ) -> CellTeardownRequest:
        """Build a teardown request for a lease (None asks to retire the whole Cell)."""
        return CellTeardownRequest(
            cell_id=self.node.cell_id,
            lease_id=lease_id,
            urgency=urgency,
            cause=cause,
            reason="The conformance suite is done with the device.",
        )

    async def release(
        self,
        lease: LeaseOpened,
        *,
        cause: ReleaseCause = ReleaseCause.COMPLETED,
        urgency: Urgency = Urgency.GRACEFUL,
    ) -> LeaseReleased:
        """Ask the gateway to release a lease and return its `cell.lease_released`."""
        request = self.teardown(lease.lease_id, cause=cause, urgency=urgency)
        answer = (await self.ask(request, as_queen=True)).payload
        assert isinstance(answer, LeaseReleased)
        return answer

    @asynccontextmanager
    async def leased(
        self, access: AccessLevel = AccessLevel.FULL, *, quota: int | None = None
    ) -> AsyncIterator[LeaseOpened]:
        """Hold a lease for a block and release it after, whatever the block did."""
        lease = await self.open_lease(access, quota=quota)
        try:
            yield lease
        finally:
            with suppress(DeviceRequestError, GatewayUnavailableError, DeviceTimeoutError):
                await self.release(lease, urgency=Urgency.IMMEDIATE)

    async def update_terms(
        self,
        access: HiveAccess | None = None,
        offline_limit_s: float | None = None,
        capabilities: tuple[str, ...] | None = None,
    ) -> None:
        """Send the gateway new terms as the Queen and wait until it has applied them.

        Args:
            access: A new access level; its capability strings come with it.
            offline_limit_s: A new dead-man limit.
            capabilities: Capability strings to hold instead of the access level's own.
        """
        changes: dict[str, object] = {}
        if access is not None:
            changes["access_level"] = access
            changes["granted_capabilities"] = granted_capabilities(access, self.node.cell_id)
        if capabilities is not None:
            changes["granted_capabilities"] = capabilities
        if offline_limit_s is not None:
            changes["offline_limit_s"] = offline_limit_s
        update = node_update_for(self.node.model_copy(update=changes), "conformance")
        await self.send_update(update)

    async def send_update(self, update: NodeUpdate) -> None:
        """Send a `swarm.node_update` as the Queen and wait until the gateway has handled it."""
        await self.link.send(update, sender=self.queen)
        await self.sync()

    async def open_session(self, lease: LeaseOpened) -> SessionExit:
        """Open the lease's session and return the gateway's exit."""
        request = SessionOpen(lease_id=lease.lease_id, reason="The conformance suite opens it.")
        return exit_of(await self.ask(request))

    async def close_session(self, lease: LeaseOpened) -> None:
        """Close the lease's session and wait until the gateway has handled the close."""
        close = SessionClose(lease_id=lease.lease_id, reason="The conformance suite closes it.")
        await self.link.send(close, sender=self.warden)
        await self.sync()
