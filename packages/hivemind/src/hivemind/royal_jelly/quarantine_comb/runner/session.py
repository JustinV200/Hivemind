"""Give the tool its session: a real in-Cell session over the sandbox scratch, wrapped to record.

A generated tool acts only through the `CellSession` it is handed (codingrules section 4). Inside
the sandbox the runner builds that session as the Warden does inside a Virtual Cell: an
`InCellSession` over a lease on a scratch directory below the sandbox root, so `exec`, `put_file`
and `get_file` really run, with the same path checks. `RecordingSession` wraps it and logs every
call (which verb, and `argv[0]` or the scratch-relative path) so the capabilities gate can compare
what the tool did to what its spec declares. The recording is the whole point: the wrapper changes
no behaviour, so a tool that works here works against a real Cell session.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.quarantine_comb.
    Built by `runner.main`; handed to the tool and to its tests. Calls into `hivemind.cell`
    (InCellSession, the lease types), `hivemind.pheromone` (an in-memory trail for the lease) and
    waggle ids.

Key invariants:
    - `RecordingSession` delegates every call unchanged; it only counts them first.
    - A call is recorded before it runs, so a call the session refuses is still on the record.
    - At most `MAX_LIST_ITEMS` distinct calls are kept; a repeated call only raises its count.

See Also:
    - docs/adr/0047 (the Comb's gates and sandbox) for why the tool gets a recording session.
    - hivemind.cell.in_cell for the session this wraps.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

from hivemind.cell.in_cell import InCellLeaseReleaser, InCellSession
from hivemind.cell.lease import LeaseFacts, RealCellLease
from hivemind.cell.session import (
    BackgroundProcess,
    BackgroundSpec,
    CellSession,
    ExecEvent,
    ExecSpec,
)
from hivemind.cell.source import CellIdentity
from hivemind.cell.tiers import AccessLevel, CombShieldLevel
from hivemind.pheromone import MemoryPheromoneTrail
from hivemind.royal_jelly.quarantine_comb.runner.report import MAX_LIST_ITEMS, SessionCall
from waggle.clock import Clock
from waggle.ids import new_cell_id, new_hive_id, new_lease_id, new_node_id, new_warden_id

__all__ = ["RecordingSession", "open_scratch_session"]


async def open_scratch_session(scratch: Path, clock: Clock) -> InCellSession:
    """Open an `InCellSession` over a fresh lease whose scratch is `scratch`.

    Args:
        scratch: The directory the tool's relative paths resolve against; must exist.
        clock: Mints the lease's ids and times its commands.

    Returns:
        A session on an OPEN lease. Nothing is trailed anywhere that outlives the process.
    """
    facts = LeaseFacts(
        id=new_lease_id(clock),
        cell_id=new_cell_id(clock),
        holder=new_warden_id(clock),
        task_id=None,
        scratch_root=scratch,
        access_level=AccessLevel.FULL,
        comb_shield=CombShieldLevel.MEADOW,
        allowed_paths=(),
    )
    identity = CellIdentity(hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system")
    lease = RealCellLease(
        facts,
        trail=MemoryPheromoneTrail(clock),
        clock=clock,
        identity=identity,
        releaser=InCellLeaseReleaser(clock),
    )
    await lease.open()
    return InCellSession(lease, clock)


class RecordingSession:
    """A `CellSession` that counts every call it forwards to the session it wraps.

    Mutable state owned here alone (codingrules 8.5): the call table, keyed by verb and target.
    """

    def __init__(self, inner: CellSession) -> None:
        """Wrap `inner`; nothing is recorded until a call is made."""
        self._inner = inner
        self._counts: dict[tuple[str, str], int] = {}

    @property
    def scratch_dir(self) -> Path:
        """The wrapped session's scratch directory."""
        return self._inner.scratch_dir

    @property
    def is_open(self) -> bool:
        """Whether the wrapped session is open."""
        return self._inner.is_open

    def calls(self) -> tuple[SessionCall, ...]:
        """Return the distinct calls made so far, in first-call order, with counts."""
        return tuple(
            SessionCall(op=op, target=target, count=count)
            for (op, target), count in self._counts.items()
        )

    async def exec(self, spec: ExecSpec) -> AsyncIterator[ExecEvent]:
        """Record the command's program, then stream the wrapped session's events."""
        self._record("exec", spec.argv[0])
        async for event in self._inner.exec(spec):
            yield event

    async def put_file(self, path: Path, data: bytes) -> None:
        """Record a write of `path`, then forward it."""
        self._record("put_file", self._relative(path))
        await self._inner.put_file(path, data)

    async def get_file(self, path: Path) -> bytes:
        """Record a read of `path`, then forward it."""
        self._record("get_file", self._relative(path))
        return await self._inner.get_file(path)

    async def delete_file(self, path: Path) -> None:
        """Record a delete of `path`, then forward it."""
        self._record("delete_file", self._relative(path))
        await self._inner.delete_file(path)

    async def start(self, spec: BackgroundSpec) -> BackgroundProcess:
        """Record the background command's program, then forward it."""
        self._record("start", spec.argv[0])
        return await self._inner.start(spec)

    async def stop(self, process: BackgroundProcess) -> bool:
        """Forward a stop; stopping what the tool itself started needs no new capability."""
        return await self._inner.stop(process)

    async def is_running(self, process: BackgroundProcess) -> bool:
        """Forward the liveness query."""
        return await self._inner.is_running(process)

    async def close(self) -> None:
        """Close the wrapped session."""
        await self._inner.close()

    def _record(self, op: str, target: str) -> None:
        """Count one call, keeping the table within its bound."""
        key = (op, target)
        if key in self._counts or len(self._counts) < MAX_LIST_ITEMS:
            self._counts[key] = self._counts.get(key, 0) + 1

    def _relative(self, path: Path) -> str:
        """Express `path` the way a capability scope does: scratch-relative where it is inside."""
        scratch = os.path.normpath(self._inner.scratch_dir)
        text = os.path.normpath(path)
        if os.path.isabs(text) and (text == scratch or text.startswith(scratch + os.sep)):
            return Path(os.path.relpath(text, scratch)).as_posix()
        return Path(text).as_posix()
