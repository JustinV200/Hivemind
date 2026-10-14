"""Provide ShieldedSource: hold a Propolis device's lease back until its tunnel has been verified.

A device enrolled in the Propolis tier is one the operator vouches for as running a VPN, and the
Hive does not take that on trust at lease time. `ShieldedSource` wraps any `RealCellSource` and
changes exactly one thing: when the inner source opens a lease on a Propolis Cell, it opens a
session on it, has a `ShieldVerifier` run the checks (for a Swarm device, `PropolisChecks`: fixed
read-only commands, pure judges), closes the session, and only then returns the lease. A lease
whose checks fail is released at once and refused with `ShieldUnverifiedError`, so the Warden
treats the device as unavailable and nothing is placed on it; the verifier has already recorded
`swarm.shield_refused` with each check's result. A Meadow lease passes straight through, and so
does everything else the source does. The decorator never installs, starts or reroutes anything:
on a borrowed device that would change the whole machine, not just the lease.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.shield`.
    Built by the composition root around a `SwarmSource` for each Propolis device Warden; called by
    the Warden exactly as the source it wraps. Calls into hivemind.cell, hivemind.cell.tunnel (the
    report a verifier returns) and the shield verifier.

Key invariants:
    - A lease on a Propolis Cell is returned only after every check passed; any other outcome
      releases it before the error is raised, so a refused device is left exactly as found.
    - Anything that stops the verification (a lost link, a denied command, a cancelled task) counts
      as unverified: the tier is granted only on a positive pass.
    - A lease that cannot be released is left ORPHANED for a sweep to retry, never forgotten.
    - The session opened for the checks is always closed.

See Also:
    - README.md "Comb Shield level" for the Propolis tier on a Real Cell.
    - hivemind.swarm.shield.propolis for the checks this runs.
    - hivemind.cell.source for the RealCellSource protocol this satisfies.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from hivemind.cell import CombShieldLevel
from hivemind.cell.errors import LeaseRefusedError
from hivemind.cell.lease import LeaseRequest, RealCellLease
from hivemind.cell.models import Cell
from hivemind.cell.session import CellSession
from hivemind.cell.source import RealCellSource
from hivemind.cell.tunnel import ShieldReport
from hivemind.common.errors import HiveMindError
from hivemind.common.logging import get_logger
from hivemind.swarm.errors import ShieldUnverifiedError

UNAVAILABLE_CHECK = "verification_unavailable"  # Named when the checks could not even be run.
NO_CHECKS_RAN = "no_checks_ran"  # Named when a verifier reported nothing at all.

log = get_logger(__name__)

__all__ = ["NO_CHECKS_RAN", "UNAVAILABLE_CHECK", "ShieldVerifier", "ShieldedSource"]


class ShieldVerifier(Protocol):
    """Check, over a leased Cell's session, that its Comb Shield tier holds."""

    async def verify(self, cell: Cell, lease: RealCellLease, session: CellSession) -> ShieldReport:
        """Run the tier's checks and say how they came out.

        Args:
            cell: The Cell whose tier is being verified.
            lease: The lease the checks run under.
            session: A session on that lease; every check runs through it.

        Returns:
            The report; the tier holds only when `is_verified` is True.
        """
        ...


class ShieldedSource:
    """A `RealCellSource` that verifies a Propolis Cell's tunnel before it lets a lease out."""

    def __init__(self, inner: RealCellSource, verifier: ShieldVerifier) -> None:
        """Wrap `inner`; every other call is passed straight through.

        Args:
            inner: The source that opens the leases, e.g. a `SwarmSource`.
            verifier: Runs the Propolis checks on a freshly opened lease.
        """
        self._inner = inner
        self._verifier = verifier

    @property
    def name(self) -> str:
        """The wrapped source's name: the Cells it hands out keep their source."""
        return self._inner.name

    async def cells(self) -> tuple[Cell, ...]:
        """Return the wrapped source's Cells, unchanged."""
        return await self._inner.cells()

    async def open_session(self, lease: RealCellLease) -> CellSession:
        """Open a session through the wrapped source, unchanged."""
        return await self._inner.open_session(lease)

    async def lease(self, request: LeaseRequest) -> RealCellLease:
        """Open a lease, and for a Propolis Cell return it only once its tunnel is verified.

        Args:
            request: Which Cell, held by which Warden, at which access level.

        Returns:
            An OPEN lease; on a Propolis Cell, one whose checks all passed.

        Raises:
            LeaseRefusedError: The wrapped source refused the lease.
            ShieldUnverifiedError: The Cell is Propolis and its checks did not all pass; the lease
                was released first.
        """
        lease = await self._inner.lease(request)
        if lease.comb_shield is not CombShieldLevel.PROPOLIS:
            return lease  # Meadow has nothing to verify.
        try:
            failed = await self._failed_checks(lease)
        except asyncio.CancelledError:
            # A cancelled task must not leave a lease it never used open on the device.
            await self._release_quietly(lease)
            raise
        if failed:
            await self._release_quietly(lease)
            raise ShieldUnverifiedError(lease.cell_id, failed)
        return lease

    async def _failed_checks(self, lease: RealCellLease) -> tuple[str, ...]:
        """Run the checks on a throwaway session; return the names of those that failed."""
        session: CellSession | None = None
        try:
            cell = await self._cell_for(lease)
            session = await self._inner.open_session(lease)
            report = await self._verifier.verify(cell, lease, session)
        except HiveMindError:
            return (UNAVAILABLE_CHECK,)
        finally:
            if session is not None:
                await session.close()
        if report.is_verified:
            return ()
        return report.failed or (NO_CHECKS_RAN,)

    async def _cell_for(self, lease: RealCellLease) -> Cell:
        """Return the Cell a lease is on, from the wrapped source's own inventory."""
        for cell in await self._inner.cells():
            if cell.id == lease.cell_id:
                return cell
        raise LeaseRefusedError(lease.cell_id, "the Cell left the source while it was leased")

    async def _release_quietly(self, lease: RealCellLease) -> None:
        """Release a refused lease; one that cannot be released stays ORPHANED for a retry."""
        try:
            await lease.release()
        except HiveMindError as exc:
            log.warning("swarm.shield_release_failed", lease_id=lease.id, error=type(exc).__name__)
