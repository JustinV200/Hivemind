"""Provide FakeCellBackend: an in-memory CellBackend for tests, demos and hive doctor.

FakeCellBackend implements `hivemind.hive.backends.base.CellBackend` over a plain in-memory table:
`provision()` mints a fresh CellId, builds a Cell whose capabilities reflect `spec.exoskeleton` (a
desktop image reports has_display, has_audio and has_browser; a terminal-only one does not) and
records the Cell tagged with `spec.hive_id` and `spec.labels` merged; `destroy()`, `pause()` and
`resume()` mutate that same table. Deterministic via an injected Clock (`waggle.clock`), never a
real clock or a real backend of any kind, so a test controls exactly how long provisioning appears
to take. Three switches simulate failure without touching real infrastructure:
`set_provision_failure` makes every subsequent `provision()` raise `CellProvisionError`;
`set_destroy_failure` does the same for `destroy()`; `set_provision_delay` makes `provision()`
await `clock.sleep()` before returning, so a test can prove a slow-to-ready Cell still finishes
within `spec.ready_timeout_s` -- or times out when the delay exceeds it. Every call is recorded
(`provision_calls`, `destroy_calls`, `pause_calls`, `resume_calls`) so a test can assert on what
was actually asked for. Shipped code, not test-only (codingrules section 14.4: "fakes live in
src/ beside their Protocol"), because `hive doctor` and demo paths use it too.

Fits into the Hive:
    Layer 3 (sources of Cells). Implements `hivemind.hive.backends.base.CellBackend`; constructed
    directly by tests, demo scripts and `hive doctor`. Calls into hivemind.cell, hivemind.hive.
    backends.base, hivemind.hive.cell_state, hivemind.hive.errors, hivemind.hive.models and
    waggle only.

Key invariants:
    - A Cell this backend returns always has kind == CellKind.VIRTUAL and access_level ==
      AccessLevel.FULL (Cell's own validator enforces the latter).
    - A failed provision() (via set_provision_failure, a readiness-timeout simulation, or a
      headroom breach) never adds an entry to this backend's table: list_cells never sees it
      (CellBackend's own key invariant).
    - destroy() always removes the record on success, so a destroyed Cell disappears from
      list_cells, matching what a real backend's own infrastructure would report.
    - pause()/resume() raise BackendCapabilityError whenever capabilities.can_pause is False,
      before touching the table or the recorded call lists.

See Also:
    - .claude/codingrules.md section 14.4 for "fakes live in src/, are shipped code."
    - hivemind.hive.backends.base for CellBackend, BackendCapabilities and VirtualCellRecord, the
      Protocol and value types this class implements and returns.
    - hivemind.llm.fake for FakeLLMProvider, the switches-and-call-recording pattern this mirrors.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from hivemind.cell import AccessLevel, Cell, CellCapabilities, CellKind, OsFamily
from hivemind.hive.backends.base import BackendCapabilities, VirtualCellRecord
from hivemind.hive.cell_state import VirtualCellStatus
from hivemind.hive.errors import BackendCapabilityError, CellDestroyError, CellProvisionError
from hivemind.hive.models import NetworkPolicy, VirtualCellSpec
from waggle.clock import Clock
from waggle.ids import CellId, HiveId, new_cell_id

_FAKE_BACKEND_NAME = "fake"  # The `name` and every Cell's `source` this backend hands out.
_FAKE_ARCH = "x86_64"  # images/ is Ubuntu-based and this fake never runs on real hardware.
_FAKE_DISTRIBUTION = "Ubuntu 24.04 LTS"  # Matches codingrules 2's Virtual Cell image baseline.
_FAKE_SHELL = "/bin/bash"
_FAKE_PACKAGE_MANAGER = "apt"
_FAKE_PYTHON_VERSION = "3.12"

__all__ = ["FakeCellBackend"]


@dataclass(slots=True)
class _TrackedCell:
    """This backend's own bookkeeping for one provisioned Cell; never exposed outside this file."""

    cell: Cell
    status: VirtualCellStatus
    labels: dict[str, str]
    created_at: datetime


class FakeCellBackend:
    """An in-memory CellBackend: provisions, destroys, pauses and lists Cells with no real infra."""

    def __init__(self, clock: Clock, capabilities: BackendCapabilities | None = None) -> None:
        """Create a FakeCellBackend with nothing provisioned yet.

        Args:
            clock: Source of every minted CellId and every recorded timestamp.
            capabilities: What this fake declares it can do; defaults to can_snapshot=False,
                can_pause=True, headroom=None (unbounded) so most tests need not think about it.
        """
        self._clock = clock
        self._capabilities = (
            capabilities
            if capabilities is not None
            else BackendCapabilities(can_snapshot=False, can_pause=True, headroom=None)
        )
        self._cells: dict[CellId, _TrackedCell] = {}
        self._provision_failure_reason: str | None = None
        self._destroy_failure_reason: str | None = None
        self._provision_delay_s = 0.0
        self.provision_calls: list[VirtualCellSpec] = []
        self.destroy_calls: list[CellId] = []
        self.pause_calls: list[CellId] = []
        self.resume_calls: list[CellId] = []

    @property
    def name(self) -> str:
        """This backend's registry name, "fake"."""
        return _FAKE_BACKEND_NAME

    @property
    def capabilities(self) -> BackendCapabilities:
        """What this fake declares it can do; see `__init__`."""
        return self._capabilities

    def set_provision_failure(self, reason: str | None) -> None:
        """Make every subsequent provision() raise CellProvisionError, or stop doing so.

        Args:
            reason: The failure reason every subsequent provision() raises with; None turns the
                switch off and lets provision() succeed again.
        """
        self._provision_failure_reason = reason

    def set_destroy_failure(self, reason: str | None) -> None:
        """Make every subsequent destroy() raise CellDestroyError, or stop doing so.

        Args:
            reason: The failure reason every subsequent destroy() raises with; None turns the
                switch off.
        """
        self._destroy_failure_reason = reason

    def set_provision_delay(self, delay_s: float) -> None:
        """Make provision() await `clock.sleep(delay_s)` before returning; 0 for no delay.

        A test drives this with a FakeClock: a `delay_s` above the spec's own `ready_timeout_s`
        simulates a Cell that never became reachable in time, without a real backend or wait.

        Args:
            delay_s: Seconds to sleep before provision() finishes; must be >= 0.

        Raises:
            ValueError: `delay_s` is negative.
        """
        if delay_s < 0:
            raise ValueError(f"delay_s must be >= 0, got {delay_s}")
        self._provision_delay_s = delay_s

    async def provision(self, spec: VirtualCellSpec) -> Cell:
        """Build and record a Cell from `spec`; see `CellBackend.provision`."""
        self.provision_calls.append(spec)
        # A slow-readiness switch is a real await, driven by the injected clock, so a FakeClock
        # test controls exactly when provision() resumes instead of a real timer.
        if self._provision_delay_s > 0:
            await self._clock.sleep(self._provision_delay_s)
        if self._provision_delay_s > spec.ready_timeout_s:
            # The simulated Cell never became reachable within its own deadline: the same failure
            # shape a real backend's readiness poll would raise (Appendix A.1's own wording).
            raise CellProvisionError(
                self.name, spec.image, f"did not become reachable within {spec.ready_timeout_s}s"
            )
        if self._provision_failure_reason is not None:
            raise CellProvisionError(self.name, spec.image, self._provision_failure_reason)
        headroom = self._capabilities.headroom
        if headroom is not None and len(self._cells) >= headroom:
            raise CellProvisionError(self.name, spec.image, f"at its headroom of {headroom} cells")
        cell = _build_cell(spec, self.name, new_cell_id(self._clock))
        # hive_id first so a caller's own spec.labels can never shadow the id the sweep relies on.
        labels = {**spec.labels, "hive_id": spec.hive_id}
        self._cells[cell.id] = _TrackedCell(
            cell=cell, status=VirtualCellStatus.READY, labels=labels, created_at=self._clock.now()
        )
        return cell

    async def destroy(self, cell_id: CellId) -> None:
        """Remove `cell_id` from this backend's table; see `CellBackend.destroy` (idempotent)."""
        self.destroy_calls.append(cell_id)
        if cell_id not in self._cells:
            return  # Idempotent: nothing to destroy, matching a real backend's own contract.
        if self._destroy_failure_reason is not None:
            raise CellDestroyError(self.name, cell_id, self._destroy_failure_reason)
        del self._cells[cell_id]

    async def list_cells(self, hive_id: HiveId) -> Sequence[VirtualCellRecord]:
        """Return every tracked Cell whose hive_id label matches; see `CellBackend.list_cells`."""
        return tuple(
            VirtualCellRecord(
                cell_id=tracked.cell.id,
                status=tracked.status,
                image=tracked.cell.name,
                labels=dict(tracked.labels),
                created_at=tracked.created_at,
            )
            for tracked in self._cells.values()
            if tracked.labels.get("hive_id") == hive_id
        )

    async def pause(self, cell_id: CellId) -> None:
        """Mark `cell_id` DORMANT in this backend's table; see `CellBackend.pause`."""
        self.pause_calls.append(cell_id)
        self._require_pause_capability(cell_id)
        tracked = self._cells.get(cell_id)
        if tracked is not None:
            tracked.status = VirtualCellStatus.DORMANT

    async def resume(self, cell_id: CellId) -> None:
        """Mark `cell_id` READY in this backend's table; see `CellBackend.resume`."""
        self.resume_calls.append(cell_id)
        self._require_pause_capability(cell_id)
        tracked = self._cells.get(cell_id)
        if tracked is not None:
            tracked.status = VirtualCellStatus.READY

    def _require_pause_capability(self, cell_id: CellId) -> None:
        """Raise BackendCapabilityError unless this fake declares can_pause."""
        if not self._capabilities.can_pause:
            raise BackendCapabilityError(self.name, "pause", cell_id=cell_id)


def _build_cell(spec: VirtualCellSpec, source: str, cell_id: CellId) -> Cell:
    """Build the VIRTUAL, FULL-access Cell a provisioned `spec` reports.

    A desktop-capable image (spec.exoskeleton) reports has_display/has_audio/has_browser and the
    ability to start a display; a terminal-only image reports none of those, matching
    images/base-ubuntu versus images/desktop-ubuntu (codingrules section 3).
    """
    capabilities = CellCapabilities(
        os=OsFamily.LINUX,
        arch=_FAKE_ARCH,
        distribution=_FAKE_DISTRIBUTION,
        shell=_FAKE_SHELL,
        package_manager=_FAKE_PACKAGE_MANAGER,
        python_version=_FAKE_PYTHON_VERSION,
        has_display=spec.exoskeleton,
        has_audio=spec.exoskeleton,
        has_browser=spec.exoskeleton,
        can_start_display=spec.exoskeleton,
        can_host_model=True,
        network_scopes=(
            spec.network_allowlist if spec.network_policy is NetworkPolicy.ALLOWLIST else ()
        ),
    )
    return Cell(
        id=cell_id,
        kind=CellKind.VIRTUAL,
        name=spec.image,
        source=source,
        capabilities=capabilities,
        capacity=spec.capacity,
        access_level=AccessLevel.FULL,
        comb_shield=spec.comb_shield,
    )
