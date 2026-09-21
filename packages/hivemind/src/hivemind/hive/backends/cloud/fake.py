"""Provide FakeCloudCellBackend: the in-memory reference CloudCellBackend (roadmap step 5.12).

ADR-0026 names no real cloud provider for Brood 1.0 ("cloud backends ... are optional"); this
class is the one implementation this phase ships, and the contract suite's fourth harness
(alongside `fake`, `docker` and `qemu`). It never talks to any cloud API: `provision`/`destroy`/
`list_cells`/`pause`/`resume` are delegated straight to an internally owned
`hivemind.hive.backends.fake.FakeCellBackend` (the exact same in-memory bookkeeping every other
`CellBackend` test already trusts, reused rather than re-implemented -- codingrules 8.3's "prefer
reuse" read for a fake instead of a pure function), and this class adds only what
`hivemind.hive.backends.cloud.base.CloudCellBackend` requires beyond that: `config` and
`accrued_cost_usd`, both driven by the injected `Clock` so a test can advance simulated time and
watch a Cell's own spend grow with no real wall-clock wait.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hivemind.hive.backends.cloud`. Implements
    `hivemind.hive.backends.cloud.base.CloudCellBackend`; constructed directly by tests, demo
    scripts and `hive doctor`. Calls into `hivemind.hive.backends.cloud.base`,
    `hivemind.hive.backends.base`, `hivemind.hive.backends.fake`, `hivemind.hive.errors` and
    `waggle` only.

Key invariants:
    - `accrued_cost_usd` only ever grows while a Cell is provisioned and not yet destroyed;
      `destroy()` freezes the elapsed time used for every accrual computed afterwards, mirroring a
      real cloud bill stopping the instant an instance terminates.
    - `accrued_cost_usd` raises `UnknownCellError` for a `cell_id` this instance never
      provisioned, never a silent 0.0 -- a caller asking about the wrong Cell should find out, not
      be told it cost nothing.

See Also:
    - .claude/codingrules.md section 14.4 for "fakes live in src/, are shipped code."
    - hivemind.hive.backends.cloud.base for CloudCellBackend, CloudBackendConfig and PricingTag,
      the protocol and value types this class implements and reads.
    - hivemind.hive.backends.fake for FakeCellBackend, the provisioning bookkeeping this class
      delegates to rather than re-implementing.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from hivemind.cell import Cell
from hivemind.hive.backends.base import BackendCapabilities, VirtualCellRecord
from hivemind.hive.backends.cloud.base import CloudBackendConfig
from hivemind.hive.backends.fake import FakeCellBackend
from hivemind.hive.errors import UnknownCellError
from hivemind.hive.models import VirtualCellSpec
from waggle.clock import Clock
from waggle.ids import CellId, HiveId

_FAKE_BACKEND_NAME = "cloud-fake"
_SECONDS_PER_HOUR = 3600.0

__all__ = ["FakeCloudCellBackend"]


class FakeCloudCellBackend:
    """An in-memory CloudCellBackend: delegates to FakeCellBackend, adds cost accrual."""

    def __init__(
        self,
        clock: Clock,
        config: CloudBackendConfig,
        capabilities: BackendCapabilities | None = None,
    ) -> None:
        """Create a FakeCloudCellBackend with nothing provisioned yet.

        Args:
            clock: Source of every minted CellId and of every cost-accrual computation.
            config: This backend's own region, credentials and pricing.
            capabilities: What this fake declares it can do; forwarded to the internal
                `FakeCellBackend` unchanged (see that class's own docstring for the default).
        """
        self._clock = clock
        self._config = config
        self._inner = FakeCellBackend(clock, capabilities)
        self._started_at: dict[CellId, datetime] = {}
        self._stopped_at: dict[CellId, datetime] = {}

    @property
    def name(self) -> str:
        """This backend's registry name, "cloud-fake"."""
        return _FAKE_BACKEND_NAME

    @property
    def capabilities(self) -> BackendCapabilities:
        """What this fake declares it can do; delegated to the internal FakeCellBackend."""
        return self._inner.capabilities

    @property
    def config(self) -> CloudBackendConfig:
        """This backend's own region, credentials and pricing; see `CloudCellBackend.config`."""
        return self._config

    def set_provision_failure(self, reason: str | None) -> None:
        """Arm (or disarm, with None) the internal FakeCellBackend's next provision() to raise.

        Exposed so a test (and the contract suite's own cloud harness) can simulate a failed
        provision the same way it does for every other backend, without reaching into this
        class's own private `_inner` attribute.
        """
        self._inner.set_provision_failure(reason)

    def set_destroy_failure(self, reason: str | None) -> None:
        """Arm (or disarm, with None) the internal FakeCellBackend's next destroy() to raise."""
        self._inner.set_destroy_failure(reason)

    async def provision(self, spec: VirtualCellSpec) -> Cell:
        """Provision through the internal FakeCellBackend and start this Cell's cost clock."""
        cell = await self._inner.provision(spec)
        self._started_at[cell.id] = self._clock.now()
        return cell

    async def destroy(self, cell_id: CellId) -> None:
        """Destroy through the internal FakeCellBackend and freeze this Cell's cost accrual."""
        await self._inner.destroy(cell_id)
        if cell_id in self._started_at and cell_id not in self._stopped_at:
            self._stopped_at[cell_id] = self._clock.now()

    async def list_cells(self, hive_id: HiveId) -> Sequence[VirtualCellRecord]:
        """See `CellBackend.list_cells`; delegated to the internal FakeCellBackend."""
        return await self._inner.list_cells(hive_id)

    async def pause(self, cell_id: CellId) -> None:
        """See `CellBackend.pause`; delegated to the internal FakeCellBackend."""
        await self._inner.pause(cell_id)

    async def resume(self, cell_id: CellId) -> None:
        """See `CellBackend.resume`; delegated to the internal FakeCellBackend."""
        await self._inner.resume(cell_id)

    async def accrued_cost_usd(self, cell_id: CellId) -> float:
        """See `CloudCellBackend.accrued_cost_usd`."""
        started = self._started_at.get(cell_id)
        if started is None:
            raise UnknownCellError(self.name, cell_id)
        ended = self._stopped_at.get(cell_id, self._clock.now())
        elapsed_hours = (ended - started).total_seconds() / _SECONDS_PER_HOUR
        return elapsed_hours * self._config.pricing.cost_per_hour_usd
