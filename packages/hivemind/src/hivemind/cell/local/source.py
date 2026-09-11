"""Define HiveStandSource: the machine the Queen runs on, as a RealCellSource with one Cell.

`HiveStandSource` is `hivemind.cell.local`'s implementation of `hivemind.cell.source.
RealCellSource`: it hands out exactly one Cell, named `"hive-stand"`, whose id is minted once at
construction and never changes. `cells()` reports that Cell's capabilities and capacity from
`hivemind.cell.local.probe` (static facts probed once, live figures refreshed on every call).
`lease()` refuses when the Hive Stand is disabled, when it is already leased (v0 allows one lease
at a time), when the requested access exceeds what `HiveStandConfig.access_level` allows, or when
the host's free disk is under `HiveStandConfig.disk_reserve_mb`; otherwise it creates a fresh
subdirectory of `scratch_root` for the new lease and opens a `RealCellLease` backed by
`hivemind.cell.local.releaser.HiveStandLeaseReleaser`. `open_session()` hands back a
`hivemind.cell.local.session.LocalProcessSession` sized from `HiveStandConfig.scratch_quota_mb`.

Fits into the Hive:
    Layer 2 (the Cell abstraction), inside `hivemind.cell.local` (the Hive Stand). Implements
    `hivemind.cell.source.RealCellSource`; constructed once by the composition root (`cli/
    stores.py`, a later step) from a loaded `HiveStandConfig`. Calls into `hivemind.cell.lease`,
    `.lease_state`, `.models`, `.source`, `hivemind.cell.local.config`, `.probe`, `.releaser`,
    `.session`, `hivemind.pheromone` and the standard library (`shutil.disk_usage`) only.

Key invariants:
    - `cells()` always returns exactly one Cell, whose id never changes across calls (minted once
      in `__init__`).
    - `lease()` either returns an OPEN `RealCellLease` or raises `LeaseRefusedError`; it never
      leaves a half-created scratch directory or a half-opened lease behind.
    - At most one lease is OPEN on the Hive Stand at a time (v0); a released lease frees the Cell
      for a fresh one, exactly like `hivemind.cell.fake.FakeCellSource`.

See Also:
    - .claude/roadmap.md step 3.11 for the refusal conditions and per-lease scratch directories.
    - docs/adr/0010-cells-are-real-or-virtual-terminal-first.md for "HiveStandSource hands out
      exactly one Cell... refuses a lease while disabled or already leased."
    - hivemind.cell.source for RealCellSource, the Protocol this class implements.
    - hivemind.cell.local.probe for probe_host/refresh_live, this source's own Cell description.
"""

from __future__ import annotations

import shutil

from hivemind.cell.errors import LeaseRefusedError
from hivemind.cell.lease import LeaseFacts, LeaseRequest, RealCellLease
from hivemind.cell.lease_state import LeaseState
from hivemind.cell.local.config import HiveStandConfig
from hivemind.cell.local.probe import ProbeResult, probe_host, refresh_live
from hivemind.cell.local.quota import ScratchQuota
from hivemind.cell.local.releaser import HiveStandLeaseReleaser
from hivemind.cell.local.session import LocalProcessSession
from hivemind.cell.models import Cell, CellKind
from hivemind.cell.session import CellSession
from hivemind.cell.source import CellIdentity
from hivemind.pheromone import PheromoneTrail
from waggle.clock import Clock
from waggle.ids import CellId, new_cell_id, new_lease_id

_SOURCE_NAME = "hive_stand"  # The `source` field every Cell this source hands out carries.
_CELL_NAME = "hive-stand"  # The human-readable label; codingrules 6.1's own example.
_BYTES_PER_MB = 1024 * 1024  # Manifest quota/reserve figures are MB; this package works in bytes.

__all__ = ["HiveStandSource"]


class HiveStandSource:
    """The Hive Stand as a RealCellSource: one Cell, leased and released, never provisioned."""

    def __init__(
        self,
        config: HiveStandConfig,
        identity: CellIdentity,
        trail: PheromoneTrail,
        clock: Clock,
    ) -> None:
        """Build a HiveStandSource over this machine, minting its one Cell's id.

        Args:
            config: The Hive Stand's own settings.
            identity: The Hive, node and actor this source stamps on every trail event.
            trail: Where cell.leased/cell.released events land.
            clock: Source of every minted id, timestamp and grace-period wait.
        """
        self._config = config
        self._identity = identity
        self._trail = trail
        self._clock = clock
        self._cell_id: CellId = new_cell_id(clock)
        self._static: ProbeResult = probe_host(config)
        self._active_lease: RealCellLease | None = None

    @property
    def name(self) -> str:
        """This source's name, `"hive_stand"`, the value its one Cell carries as `source`."""
        return _SOURCE_NAME

    async def cells(self) -> tuple[Cell, ...]:
        """Return the Hive Stand's one Cell, with live capacity figures refreshed.

        Returns:
            A one-element tuple holding the Hive Stand's Cell.
        """
        capacity = refresh_live(self._config, self._static.capacity)
        cell = Cell(
            id=self._cell_id,
            kind=CellKind.REAL,
            name=_CELL_NAME,
            source=_SOURCE_NAME,
            capabilities=self._static.capabilities,
            capacity=capacity,
            access_level=self._config.access_level,
            comb_shield=self._config.comb_shield,
        )
        return (cell,)

    async def lease(self, request: LeaseRequest) -> RealCellLease:
        """Open a lease on the Hive Stand's one Cell.

        Args:
            request: Which Cell, held by which Warden, at which access level.

        Returns:
            An OPEN RealCellLease with its own scratch subdirectory.

        Raises:
            LeaseRefusedError: The Hive Stand is disabled, `request.cell_id` names a different
                Cell, a lease is already OPEN, the requested access exceeds
                `HiveStandConfig.access_level`, the scratch root cannot be created, or free disk
                is under `disk_reserve_mb`.
        """
        self._check_refusal(request)
        self._prepare_scratch_root(request)
        lease_id = new_lease_id(self._clock)
        scratch_root = self._config.scratch_root / lease_id
        scratch_root.mkdir(parents=True, exist_ok=True)
        facts = LeaseFacts(
            id=lease_id,
            cell_id=self._cell_id,
            holder=request.holder,
            task_id=request.task_id,
            scratch_root=scratch_root,
            access_level=request.access_level,
            comb_shield=self._config.comb_shield,
            allowed_paths=request.allowed_paths,
        )
        lease = RealCellLease(
            facts,
            trail=self._trail,
            clock=self._clock,
            identity=self._identity,
            releaser=HiveStandLeaseReleaser(self._clock),
        )
        await lease.open()
        self._active_lease = lease
        return lease

    async def open_session(self, lease: RealCellLease) -> CellSession:
        """Open a LocalProcessSession scoped to `lease`, watchdogged at the configured quota.

        Args:
            lease: An OPEN lease this source itself issued.

        Returns:
            A new LocalProcessSession over `lease`'s scratch directory.
        """
        quota = ScratchQuota(quota_bytes=self._config.scratch_quota_mb * _BYTES_PER_MB)
        return LocalProcessSession(lease, quota, self._clock)

    def _prepare_scratch_root(self, request: LeaseRequest) -> None:
        """Make this Hive's scratch root if it is not there yet, then enforce the disk reserve.

        Args:
            request: The lease being considered; names the Cell any refusal reports.

        Raises:
            LeaseRefusedError: The scratch root cannot be created, or free disk is under
                `disk_reserve_mb`.
        """
        # The scratch root is this Hive's own directory to make, and on a brand-new Hive nothing
        # has made it yet: both steps below assume it exists, and measuring free space against a
        # missing path raises instead of reporting the host's real headroom (FileNotFoundError;
        # WinError 3 on Windows), so no fresh Hive could ever take its first lease.
        try:
            self._config.scratch_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise LeaseRefusedError(
                request.cell_id,
                f"could not create the scratch root {self._config.scratch_root}: {exc}",
            ) from exc
        # SAFETY: shutil.disk_usage touches the filesystem; it is fine to call directly here (a
        # single fast syscall), unlike the repeated sampling LocalProcessSession's watchdog does.
        free_bytes = shutil.disk_usage(self._config.scratch_root).free
        reserve_bytes = self._config.disk_reserve_mb * _BYTES_PER_MB
        if free_bytes < reserve_bytes:
            raise LeaseRefusedError(
                request.cell_id,
                f"free disk ({free_bytes} bytes) is under the configured reserve "
                f"({reserve_bytes} bytes)",
            )

    def _check_refusal(self, request: LeaseRequest) -> None:
        """Raise LeaseRefusedError for every condition that does not need a filesystem read."""
        if not self._config.enabled:
            raise LeaseRefusedError(request.cell_id, "the Hive Stand is disabled")
        if request.cell_id != self._cell_id:
            raise LeaseRefusedError(request.cell_id, "no such Cell on the Hive Stand")
        if self._active_lease is not None and self._active_lease.state is not LeaseState.RELEASED:
            raise LeaseRefusedError(request.cell_id, "the Hive Stand is already leased")
        if request.access_level.rank > self._config.access_level.rank:
            raise LeaseRefusedError(
                request.cell_id,
                f"requested access {request.access_level.name} exceeds the Hive Stand's own "
                f"{self._config.access_level.name}",
            )
