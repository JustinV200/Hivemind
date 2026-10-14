"""Define LeaseScopedGuard and LeaseScopedSource: a device Warden's command guard, per lease.

Every command a `PollenSession` sends is checked at the Guard's `device_command` point against the
set its Warden holds. That set is the `warden` role's default narrowed to the lease: the role names
`fs:write:{scratch}/**`, and which directory `{scratch}` is, on the device, is only known once the
device has opened the lease and said where its scratch directory is. A guard built when the Warden
is built cannot know it, and one built around a made-up root would hold the wrong write scope at
the SCRATCH access level (the very level that exists to confine a device's writes). So the guard
here is built for the lease instead. `LeaseScopedSource` wraps the device's source and, each time it
returns a lease, tells the guard about it (`LeaseScopedGuard.bind`); the guard then holds exactly
the set `hivemind.wardens.ticks.lease.open_lease` computes for the Warden's own ceiling from the
same lease, and checks each command with the swarm package's `EnforcedCommandGuard` over it.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose.swarm`. Built by the
    device Warden factory, one pair per device Warden; the guard is handed to the device's
    `SwarmSource`, which gives it to every `PollenSession`, and the source wraps that one. Calls
    into `hivemind.cell`, `hivemind.guard`, `hivemind.swarm` and waggle only.

Key invariants:
    - A command on a lease the guard was never told about is refused: it fails closed, and
      nothing is sent to the device.
    - A device holds one lease at a time (`SwarmSource` refuses a second), so the guard keeps the
      newest lease only; a command on an older one is refused like an unknown one.
    - The set is the Warden's own for that lease (role default, narrowed to the lease's access
      level and scratch root, less the deny list), never wider.
    - The wrapper changes nothing else about the source it wraps.

See Also:
    - hivemind.swarm.session.guard for EnforcedCommandGuard, which makes each check.
    - hivemind.guard.policy.roles for warden_set, the set built here.
    - hivemind.swarm.shield.shielded for the other wrapper of a device's source (Propolis).
"""

from __future__ import annotations

from hivemind.cell import Cell, RealCellLease, RealCellSource
from hivemind.cell.lease import LeaseRequest
from hivemind.cell.session import CellSession
from hivemind.guard import Enforcer, PolicyContext, PrincipalRef, warden_principal, warden_set
from hivemind.swarm import CommandDeniedError, DeviceCommand, EnforcedCommandGuard, SwarmRecorder
from waggle.ids import LeaseId, MessageId, WardenId

# What a refusal names when the command is on a lease the guard was never told about.
UNKNOWN_LEASE_SCOPE = "<a lease this Warden does not hold>"

__all__ = ["UNKNOWN_LEASE_SCOPE", "LeaseScopedGuard", "LeaseScopedSource"]


class LeaseScopedGuard:
    """Check a device Warden's commands against the set it holds on the lease they run in."""

    def __init__(
        self, enforcer: Enforcer, recorder: SwarmRecorder, warden_id: WardenId, cell: Cell
    ) -> None:
        """Build the guard for one device Warden; it holds nothing until a lease is bound.

        Args:
            enforcer: Decides each request and records a refusal as `guard.denied`.
            recorder: Writes `swarm.command_sent`.
            warden_id: The device's Warden, the principal of every command.
            cell: The device's Cell, whose operator opt-in for its running display narrows the set.
        """
        self._enforcer = enforcer
        self._recorder = recorder
        self._principal: PrincipalRef = warden_principal(warden_id)
        self._real_display = cell.capabilities.real_display_allowed
        self._lease_id: LeaseId | None = None
        self._guard: EnforcedCommandGuard | None = None

    def bind(self, lease: RealCellLease) -> None:
        """Hold, from now on, the set this Warden has on `lease`, forgetting any earlier lease's.

        Args:
            lease: The lease the device just opened; its access level, tier and scratch root are
                what the device granted.
        """
        held = warden_set(
            self._enforcer.policy,
            lease.access_level,
            lease.scratch_root,
            real_display=self._real_display,
        )
        context = PolicyContext(comb_shield=lease.comb_shield, access_level=lease.access_level)
        self._lease_id = lease.id
        self._guard = EnforcedCommandGuard(
            self._enforcer, self._recorder, self._principal, held, context
        )

    async def authorise(self, command: DeviceCommand) -> None:
        """Check `command` against the set held on its lease; see `CommandGuard.authorise`."""
        await self._for(command).authorise(command)

    async def record_sent(self, command: DeviceCommand, request_id: MessageId) -> None:
        """Record `swarm.command_sent` for `command`; see `CommandGuard.record_sent`."""
        await self._for(command).record_sent(command, request_id)

    def _for(self, command: DeviceCommand) -> EnforcedCommandGuard:
        """Return the guard for `command`'s lease, or refuse a command on any other."""
        if self._guard is None or command.lease_id != self._lease_id:
            raise CommandDeniedError(command.lease_id, UNKNOWN_LEASE_SCOPE)
        return self._guard


class LeaseScopedSource:
    """A `RealCellSource` that tells a `LeaseScopedGuard` about each lease it opens."""

    def __init__(self, inner: RealCellSource, guard: LeaseScopedGuard) -> None:
        """Wrap `inner`; every call but `lease` is passed straight through.

        Args:
            inner: The device's source, whose sessions use `guard`.
            guard: Told about each lease before the caller can send a command on it.
        """
        self._inner = inner
        self._guard = guard

    @property
    def name(self) -> str:
        """The wrapped source's name: the Cells it hands out keep their source."""
        return self._inner.name

    async def cells(self) -> tuple[Cell, ...]:
        """Return the wrapped source's Cells, unchanged."""
        return await self._inner.cells()

    async def lease(self, request: LeaseRequest) -> RealCellLease:
        """Open a lease on the wrapped source, then bind the guard to it.

        Args:
            request: Which Cell, held by which Warden, at which access level.

        Returns:
            The wrapped source's lease, with the guard already holding its set.

        Raises:
            LeaseRefusedError: The wrapped source refused the lease; the guard is left as it was.
        """
        lease = await self._inner.lease(request)
        self._guard.bind(lease)
        return lease

    async def open_session(self, lease: RealCellLease) -> CellSession:
        """Open a session through the wrapped source, unchanged."""
        return await self._inner.open_session(lease)
