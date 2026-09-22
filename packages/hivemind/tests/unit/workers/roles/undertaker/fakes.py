"""Test doubles for hivemind.workers.roles.undertaker: a flaky backend, a flaky releaser, recorders.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5) local to this test package, not shipped: unlike
    `hivemind.hive.backends.fake.FakeCellBackend`, these fakes exist only to simulate a fixed
    number of failures before success, a scenario no production fake needs.

Key invariants:
    - None: this module holds test doubles only.

See Also:
    - hivemind.workers.roles.undertaker.role for the Protocols these fakes implement.
    - hivemind.hive.backends.fake for FakeCellBackend, wrapped (not reimplemented) by
      FlakyDestroyBackend.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from hivemind.cell import Cell, LeaseReleaseReport, RealCellLease, SessionClosedError
from hivemind.hive import BackendCapabilities, CellDestroyError, VirtualCellRecord
from hivemind.hive.backends.fake import FakeCellBackend
from hivemind.hive.models import VirtualCellSpec
from waggle.ids import CellId, HiveId

__all__ = [
    "FlakyDestroyBackend",
    "FlakyLeaseReleaser",
    "RecordingGrantRevoker",
    "RecordingLeavingsRemover",
    "RecordingWaxRetirer",
]


class FlakyDestroyBackend:
    """A CellBackend whose destroy() raises CellDestroyError a fixed number of times first.

    Wraps a real `FakeCellBackend` for every other call, so a test gets a fully Protocol-
    conforming backend without reimplementing provision/list_cells/pause/resume.
    """

    def __init__(self, inner: FakeCellBackend, fail_times: int) -> None:
        """Create a FlakyDestroyBackend over `inner` that fails destroy() `fail_times` times.

        Args:
            inner: The real FakeCellBackend every other call delegates to.
            fail_times: How many leading destroy() calls raise CellDestroyError before one
                succeeds; 0 means every call succeeds at once.
        """
        self._inner = inner
        self._fail_times = fail_times
        self.destroy_attempts = 0

    @property
    def name(self) -> str:
        """Delegates to the wrapped backend's own name."""
        return self._inner.name

    @property
    def capabilities(self) -> BackendCapabilities:
        """Delegates to the wrapped backend's own capabilities."""
        return self._inner.capabilities

    async def provision(self, spec: VirtualCellSpec) -> Cell:
        """Delegates to the wrapped backend."""
        return await self._inner.provision(spec)

    async def destroy(self, cell_id: CellId) -> None:
        """Raise CellDestroyError for the first `fail_times` calls, then delegate and succeed."""
        self.destroy_attempts += 1
        if self.destroy_attempts <= self._fail_times:
            raise CellDestroyError(self._inner.name, cell_id, "simulated flake")
        await self._inner.destroy(cell_id)

    async def list_cells(self, hive_id: HiveId) -> Sequence[VirtualCellRecord]:
        """Delegates to the wrapped backend."""
        return await self._inner.list_cells(hive_id)

    async def pause(self, cell_id: CellId) -> None:
        """Delegates to the wrapped backend."""
        await self._inner.pause(cell_id)

    async def resume(self, cell_id: CellId) -> None:
        """Delegates to the wrapped backend."""
        await self._inner.resume(cell_id)


class FlakyLeaseReleaser:
    """A LeaseReleaser whose release() raises SessionClosedError a fixed number of times first."""

    def __init__(self, fail_times: int) -> None:
        """Create a FlakyLeaseReleaser that fails `fail_times` times before succeeding.

        Args:
            fail_times: How many leading release() calls raise before one succeeds; 0 means every
                call succeeds at once.
        """
        self._fail_times = fail_times
        self.release_attempts = 0

    async def release(self, lease: RealCellLease) -> LeaseReleaseReport:
        """Raise for the first `fail_times` calls, then report a clean release."""
        self.release_attempts += 1
        if self.release_attempts <= self._fail_times:
            raise SessionClosedError(Path("scratch"))
        return LeaseReleaseReport(killed_processes=0, residual_paths=(), is_restored=True)


class RecordingGrantRevoker:
    """A GrantRevoker that records every call and reports a fixed revoked count."""

    def __init__(self, revoked_count: int = 1) -> None:
        """Create a RecordingGrantRevoker that reports `revoked_count` grants revoked per call."""
        self.calls: list[tuple[CellId, str]] = []
        self._revoked_count = revoked_count

    async def revoke_for_cell(self, cell_id: CellId, reason: str) -> int:
        """Record the call and report `revoked_count`."""
        self.calls.append((cell_id, reason))
        return self._revoked_count


class RecordingWaxRetirer:
    """A WaxRetirer that records every call and reports a fixed retired count."""

    def __init__(self, retired_count: int = 1) -> None:
        """Create a RecordingWaxRetirer that reports `retired_count` notes retired per call."""
        self.calls: list[tuple[CellId, datetime]] = []
        self._retired_count = retired_count

    async def retire_wax(self, cell_id: CellId, at: datetime) -> int:
        """Record the call and report `retired_count`."""
        self.calls.append((cell_id, at))
        return self._retired_count


class RecordingLeavingsRemover:
    """A LeavingsRemover that records every call and reports a fixed removed count."""

    def __init__(self, removed_count: int = 1) -> None:
        """Create a RecordingLeavingsRemover that reports `removed_count` rows removed per call."""
        self.calls: list[tuple[CellId, datetime]] = []
        self._removed_count = removed_count

    async def mark_cell_removed(self, cell_id: CellId, at: datetime) -> int:
        """Record the call and report `removed_count`."""
        self.calls.append((cell_id, at))
        return self._removed_count
