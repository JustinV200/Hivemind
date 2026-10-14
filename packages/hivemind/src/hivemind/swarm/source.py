"""Provide SwarmSource and SwarmLeaseReleaser: an enrolled device as a source of one Real Cell.

A `RealCellSource` hands out leases on Real Cells (existing devices the Hive borrows and leaves
exactly as found), and `SwarmSource` is the one for a single enrolled device. Its one Cell is the
device itself (`SwarmNode.as_cell`, source `"swarm"`). Leasing asks the device's Pollen Packet over
its link: a `cell.request` naming the Cell, the Warden that will hold the lease, the access level
needed and the scratch quota the lease is held to; the packet answers with a
`cell.lease_opened` carrying the lease id, the access it actually granted (never more than the node
holds), the scratch root and the dead-man limit. The quota is the Queen's: it is the scratch the
holder's ceilings allow one lease (`LeaseRequest.scratch_quota_bytes`), and where she has set none
it is the Hive's configured default for device leases (`[swarm] scratch_quota_mb`), so the packet's
own built-in figure only ever applies to a lease made by something that names neither. The source
builds a `RealCellLease` around the answer, and the lease's releaser, `SwarmLeaseReleaser`, is how
it ends: a `cell.teardown_request` and the packet's `cell.lease_released`, which says whether the
device was left exactly as found.
Opening a session sends `session.open` and returns a `PollenSession`. The device may end a lease
without being asked (its dead-man switch fires after the link has been down too long); the source
is a `HubListener` so that, subscribed to the hub, it hears that unprompted `cell.lease_released`
and releases its lease object with the device's own report, rather than later sending a teardown
for a lease the device no longer holds.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm`. Built by the
    composition root for each device Warden (wrapped in `ShieldedSource` for a Propolis device) and
    called by `queen.placement` (to list Cells) and by the Warden (to lease and open a session).
    Calls into hivemind.cell, the gateway hub and link, the session package and waggle.

Key invariants:
    - `lease()` either returns an OPEN `RealCellLease` or raises `LeaseRefusedError`; a device that
      is away, slow or unwilling is a refusal, never a half-opened lease.
    - One lease at a time per device, as the Hive Stand's own source: a second request while one
      is not RELEASED is refused.
    - The lease's access level is what the device granted, and its tier is the registry's, so the
      Guard and the floors read the operator's intent.
    - A lease is released only by a confirmed `cell.lease_released`; a device that cannot confirm
      leaves the lease ORPHANED for a retry, and `is_restored` is the device's own claim.

See Also:
    - docs/waggle/gateway-contract.md sections 9.1 and 13 for the lease messages and dead-man.
    - hivemind.cell.local.source for the Hive Stand's source this mirrors.
    - hivemind.swarm.session for the PollenSession a lease's session is.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.cell.errors import LeaseRefusedError
from hivemind.cell.lease import LeaseFacts, LeaseReleaseReport, LeaseRequest, RealCellLease
from hivemind.cell.lease_state import LeaseState
from hivemind.cell.models import Cell
from hivemind.cell.session import CellSession
from hivemind.common.logging import get_logger
from hivemind.swarm.errors import (
    DeviceRequestError,
    DeviceTimeoutError,
    GatewayUnavailableError,
)
from hivemind.swarm.events import SwarmRecorder
from hivemind.swarm.gateway.hub import GatewayHub
from hivemind.swarm.gateway.link import DeviceLink
from hivemind.swarm.models import SWARM_SOURCE, SwarmNode
from hivemind.swarm.node_state import NodeStatus
from hivemind.swarm.session import CommandGuard, DevicePathRule, PollenSession
from hivemind.swarm.session.codes import LEASE_NOT_OPEN
from waggle.envelope import Envelope
from waggle.messages.cell.leases import (
    CellRequest,
    CellTeardownRequest,
    LeaseOpened,
    LeaseReleased,
)
from waggle.messages.cell.status import ReleaseCause
from waggle.messages.labels import Urgency

# Seconds to wait for a device to open a lease: it creates a directory and writes its table.
LEASE_REPLY_TIMEOUT_S = 30.0
# Seconds to wait for a release: a graceful one asks processes to end, forces them, restores the
# files it recorded and deletes scratch, any of which can take a while on a slow disk.
RELEASE_REPLY_TIMEOUT_S = 120.0

log = get_logger(__name__)

__all__ = ["LEASE_REPLY_TIMEOUT_S", "RELEASE_REPLY_TIMEOUT_S", "SwarmLeaseReleaser", "SwarmSource"]


def _report_of(released: LeaseReleased) -> LeaseReleaseReport:
    """Turn a device's `cell.lease_released` into the report a `RealCellLease` records."""
    return LeaseReleaseReport(
        killed_processes=released.killed_processes,
        residual_paths=tuple(Path(path) for path in released.residual_paths),
        left_paths=(),
        is_restored=released.is_restored,
    )


class SwarmLeaseReleaser:
    """End a lease on a device: ask the packet to release it and report how it was left."""

    def __init__(self, hub: GatewayHub, node: SwarmNode) -> None:
        """Build the releaser for one device's leases.

        Args:
            hub: Where the device's link is found when the release is made.
            node: The device; its id finds the link.
        """
        self._hub = hub
        self._device_id = node.device_id
        self._reported: LeaseReleaseReport | None = None

    def note_device_release(self, released: LeaseReleased) -> None:
        """Remember that the device released the lease itself, so `release` need not ask.

        Args:
            released: The unprompted `cell.lease_released` (a dead-man release) the device sent.
        """
        self._reported = _report_of(released)

    async def release(self, lease: RealCellLease) -> LeaseReleaseReport:
        """Release `lease` on the device and report whether it was left exactly as found.

        Args:
            lease: The lease being released, already RELEASING.

        Returns:
            The device's own report of what it killed and restored; the one it sent on its own
            when its dead-man switch already released the lease.

        Raises:
            GatewayUnavailableError: The device is away, so nothing can confirm the release; the
                lease is left ORPHANED and may be released again.
            DeviceTimeoutError: The device did not confirm in time.
        """
        if self._reported is not None:
            return self._reported
        link = self._hub.link(self._device_id)
        if link is None:
            raise GatewayUnavailableError(
                f"Device {self._device_id} is not connected, so lease {lease.id} cannot be "
                "confirmed released; it stays orphaned until the device returns."
            )
        request = CellTeardownRequest(
            cell_id=lease.cell_id,
            lease_id=lease.id,
            urgency=Urgency.GRACEFUL,
            cause=ReleaseCause.COMPLETED,
            reason="The Warden finished with the device.",
        )
        try:
            reply = await link.request(
                request, sender=link.hive_id, timeout_s=RELEASE_REPLY_TIMEOUT_S
            )
        except DeviceRequestError as error:
            if error.device_code != LEASE_NOT_OPEN:
                raise
            # The device no longer holds it (its dead-man switch, or a restart, got there first)
            # and its own report did not reach us: say the restoration is unconfirmed.
            log.warning("swarm.lease_already_released", lease_id=lease.id)
            return LeaseReleaseReport(killed_processes=0, residual_paths=(), is_restored=False)
        return _report_of(_expect_released(reply, lease))


def _expect_released(reply: Envelope, lease: RealCellLease) -> LeaseReleased:
    """Return the `cell.lease_released` that answers a teardown, or raise if it is anything else."""
    released = reply.payload
    if not isinstance(released, LeaseReleased) or released.lease_id != lease.id:
        raise DeviceRequestError(
            "hive.swarm.unexpected_reply", f"the device did not confirm releasing {lease.id}."
        )
    return released


@dataclass(slots=True)
class _Tenancy:
    """The lease a source last handed out and the releaser that ends it."""

    lease: RealCellLease
    releaser: SwarmLeaseReleaser


class SwarmSource:
    """One enrolled device as a `RealCellSource`: one Cell, leased and released, never retired."""

    def __init__(
        self,
        node: SwarmNode,
        hub: GatewayHub,
        recorder: SwarmRecorder,
        guard: CommandGuard,
        default_scratch_quota_bytes: int | None = None,
    ) -> None:
        """Build the source for one device.

        Args:
            node: The enrolled device; the hub's latest record of it is read on every call.
            hub: Where the device's live link is found.
            recorder: The trail, clock and identity its leases record `cell.leased` and
                `cell.released` with.
            guard: Checks and records each command of every session it opens.
            default_scratch_quota_bytes: The scratch quota a lease asks the device to enforce when
                its request names none (`[swarm] scratch_quota_mb`, the Hive's figure for a lease
                the Queen's ceilings do not size); None leaves the device's own default.
        """
        self._node = node
        self._hub = hub
        self._recorder = recorder
        self._guard = guard
        self._default_quota = default_scratch_quota_bytes
        self._tenancy: _Tenancy | None = None

    @property
    def name(self) -> str:
        """This source's name, `"swarm"`, the value its Cell carries as `source`."""
        return SWARM_SOURCE

    async def cells(self) -> tuple[Cell, ...]:
        """Return the device's Cell, built from the freshest record, or none once revoked."""
        node = self._latest()
        return () if node.status is NodeStatus.REVOKED else (node.as_cell(),)

    async def lease(self, request: LeaseRequest) -> RealCellLease:
        """Open a lease on the device's Cell by asking its Pollen Packet.

        Args:
            request: Which Cell, held by which Warden, at which access level.

        Returns:
            An OPEN `RealCellLease` whose scratch directory is on the device.

        Raises:
            LeaseRefusedError: The request names another Cell, the device is already leased or
                away or revoked, or the packet refused or did not answer.
        """
        node = self._latest()
        link = self._check(request, node)
        opened = await self._open_on_device(link, request, node)
        releaser = SwarmLeaseReleaser(self._hub, node)
        lease = RealCellLease(
            _facts(opened, request, node),
            trail=self._recorder.trail,
            clock=self._recorder.clock,
            identity=self._recorder.identity,
            releaser=releaser,
        )
        await lease.open()
        self._tenancy = _Tenancy(lease, releaser)
        return lease

    async def open_session(self, lease: RealCellLease) -> CellSession:
        """Open a `PollenSession` on the device for `lease`.

        Args:
            lease: An OPEN lease this source issued.

        Returns:
            A session the device has agreed to.

        Raises:
            GatewayUnavailableError: The device is not connected.
        """
        link = self._hub.link(self._latest().device_id)
        if link is None:
            raise GatewayUnavailableError(
                f"Device {self._latest().device_id} is not connected, so no session can open."
            )
        return await PollenSession.open(link, lease, self._guard)

    async def on_online(self, node: SwarmNode, link: DeviceLink) -> None:
        """Ignore a device coming online: a source reads the hub when it is asked."""

    async def on_offline(self, node: SwarmNode, reason: str) -> None:
        """Ignore a device going offline: a lease outlives the link until the dead-man limit."""

    async def on_frame(self, node: SwarmNode, envelope: Envelope) -> None:
        """Release the lease when its device reports, unprompted, that it already did.

        Subscribe the source to the hub to hear this. A device with no Warden on it releases its
        leases itself when its link has been down past its dead-man limit and sends
        `cell.lease_released` (cause DEAD_MAN) when the link returns.

        Args:
            node: The device the frame came from.
            envelope: A frame no request was waiting for.
        """
        released = envelope.payload
        tenancy = self._tenancy
        if (
            not isinstance(released, LeaseReleased)
            or tenancy is None
            or node.device_id != self._node.device_id
            or released.lease_id != tenancy.lease.id
            or tenancy.lease.state not in (LeaseState.OPEN, LeaseState.ORPHANED)
        ):
            return
        tenancy.releaser.note_device_release(released)
        await tenancy.lease.release()

    def _latest(self) -> SwarmNode:
        """Return the hub's freshest record of the device, else the one the source holds."""
        return self._hub.node(self._node.device_id) or self._node

    def _check(self, request: LeaseRequest, node: SwarmNode) -> DeviceLink:
        """Return the device's link, or raise `LeaseRefusedError` for a request that cannot go."""
        if request.cell_id != node.cell_id:
            raise LeaseRefusedError(request.cell_id, "no such Cell on this device")
        tenancy = self._tenancy
        if tenancy is not None and tenancy.lease.state is not LeaseState.RELEASED:
            raise LeaseRefusedError(request.cell_id, "the device is already leased")
        link = self._hub.link(node.device_id)
        if node.status is NodeStatus.REVOKED or link is None:
            raise LeaseRefusedError(request.cell_id, "the device is not connected")
        return link

    def _quota_for(self, request: LeaseRequest) -> int | None:
        """Return the scratch quota to ask the device for: the Queen's figure, else the default."""
        # The holder's ceilings are the Queen's decision and win; the configured default stands in
        # only while she has set none, so the device's own built-in figure is never what decides.
        if request.scratch_quota_bytes is not None:
            return request.scratch_quota_bytes
        return self._default_quota

    async def _open_on_device(
        self, link: DeviceLink, request: LeaseRequest, node: SwarmNode
    ) -> LeaseOpened:
        """Ask the packet for a lease and return its `cell.lease_opened`, or refuse."""
        message = CellRequest(
            cell_id=request.cell_id,
            needs=None,
            holder=request.holder,
            task_id=request.task_id,
            access_level=request.access_level.to_wire(),
            lifetime_s=None,
            scratch_quota_bytes=self._quota_for(request),
            reason=f"Warden {request.holder} leases device {node.device_id}.",
        )
        try:
            reply = await link.request(
                message, sender=link.hive_id, timeout_s=LEASE_REPLY_TIMEOUT_S
            )
        except (DeviceRequestError, GatewayUnavailableError, DeviceTimeoutError) as exc:
            # A lease is a yes or a refusal: a device that says no, goes away or stays silent is
            # all the same to a Warden, which waits (WATCH) and tries again.
            raise LeaseRefusedError(
                request.cell_id, f"the device did not open one ({exc})"
            ) from exc
        opened = reply.payload
        if (
            not isinstance(opened, LeaseOpened)
            or opened.cell_id != node.cell_id
            or opened.holder != request.holder
        ):
            raise LeaseRefusedError(request.cell_id, "the device answered about another lease")
        return opened


def _facts(opened: LeaseOpened, request: LeaseRequest, node: SwarmNode) -> LeaseFacts:
    """Build a lease's frozen facts from the device's answer and the registry's record."""
    return LeaseFacts(
        id=opened.lease_id,
        cell_id=opened.cell_id,
        holder=opened.holder,
        task_id=request.task_id,
        scratch_root=Path(opened.scratch_root),
        access_level=AccessLevel.from_wire(opened.access_level),
        # The tier is the operator's, held in the registry; the device only echoes what it was
        # last told, so the registry's value is the one the Guard's floors must read.
        comb_shield=CombShieldLevel(node.comb_shield.value),
        allowed_paths=request.allowed_paths,
        # The device's paths are the device's: judged by their text, never by the Hive Stand's disk.
        paths=DevicePathRule(opened.scratch_root),
    )
