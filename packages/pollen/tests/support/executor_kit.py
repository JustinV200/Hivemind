"""Build the collaborators an executor needs, without a packet: state, lease table, reporter, scope.

An executor (the part of the Pollen Packet that runs a program, moves a file or starts a process)
is built from a handful of shared collaborators: the state store, the lease table, a reporter that
sends frames, a clock, the platform shim, the data layout and the packet's task scope. Most of an
executor's rules can be tested without a connection by handing it a reporter that records what it
is asked to send. This kit builds that bundle over a temporary directory, with an enrolled-looking
state, and opens a lease in the table (scratch directory included) the way the lease service does.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Used by the executor unit tests; depends on pollen's
    public executor seam and on waggle.

Key invariants:
    - The reporter records every frame in order and reports every send as delivered.
    - A lease opened here has a real scratch directory and a row in a real, persisted table.

See Also:
    - states.py for the state and frames the kit builds on.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from states import Sent, sample_state

from pollen.executors import ExecutorDeps, TaskScope
from pollen.executors.run import ExecPlan
from pollen.lease import DeviceLease, LeaseTable
from pollen.platform import select_shim
from pollen.state import DataLayout, PacketState, StateStore
from waggle.clock import Clock
from waggle.ids import new_lease_id, new_message_id
from waggle.messages.base import WaggleMessage
from waggle.messages.labels import AccessLevel

__all__ = ["Kit", "Limits", "RecordingReporter", "make_kit", "make_plan", "open_lease_for"]


class RecordingReporter:
    """A ``Reporter`` that records every frame instead of sending it."""

    def __init__(self) -> None:
        """Start with nothing recorded."""
        self.sent: list[Sent] = []
        self._changed = asyncio.Event()

    async def send(
        self, payload: WaggleMessage, *, to: str, correlation_id: str | None = None
    ) -> bool:
        """Record a live frame."""
        self.sent.append(Sent(payload, to, correlation_id, is_durable=False))
        self._changed.set()
        return True

    async def send_durable(
        self, payload: WaggleMessage, *, to: str, correlation_id: str | None = None
    ) -> None:
        """Record a durable frame."""
        self.sent.append(Sent(payload, to, correlation_id, is_durable=True))
        self._changed.set()

    def of[PayloadT: WaggleMessage](self, kind: type[PayloadT]) -> list[PayloadT]:
        """Return the payloads recorded so far that are a ``kind``, in order."""
        return [s.payload for s in self.sent if isinstance(s.payload, kind)]

    async def wait_for[PayloadT: WaggleMessage](
        self,
        kind: type[PayloadT],
        where: Callable[[PayloadT], bool] = lambda _payload: True,
        wait_s: float = 10.0,
    ) -> PayloadT:
        """Wait until a ``kind`` passing ``where`` has been recorded, and return it."""
        async with asyncio.timeout(wait_s):
            while True:
                for payload in self.of(kind):
                    if where(payload):
                        return payload
                self._changed.clear()
                await self._changed.wait()


@dataclass(frozen=True, slots=True)
class Kit:
    """The bundle an executor is built from, over a temporary directory."""

    deps: ExecutorDeps
    reporter: RecordingReporter
    state: PacketState
    table: LeaseTable
    layout: DataLayout


def make_kit(tmp_path: Path, clock: Clock, **state_changes: object) -> Kit:
    """Build the collaborators for an enrolled-looking device under ``tmp_path``.

    Args:
        tmp_path: The directory the data layout lives in.
        clock: The clock executors read.
        **state_changes: Fields of the state to set instead of the defaults.

    Returns:
        The kit; its task scope has not been entered (a test that spawns enters it).
    """
    layout = DataLayout(tmp_path / "data")
    layout.ensure()
    state = sample_state(clock, **state_changes)
    store = StateStore(layout.state, state)
    table = LeaseTable(layout.lease_table)
    reporter = RecordingReporter()
    deps = ExecutorDeps(store, table, reporter, clock, select_shim(), layout, TaskScope())
    return Kit(deps, reporter, state, table, layout)


async def open_lease_for(
    kit: Kit, clock: Clock, *, quota: int = 1_000_000, access: AccessLevel = AccessLevel.FULL
) -> DeviceLease:
    """Open a lease the way the lease service does: scratch on disk, a row in the table."""
    lease_id = new_lease_id(clock)
    scratch = kit.layout.scratch(lease_id)
    scratch.mkdir(parents=True)
    lease = DeviceLease(
        lease_id=lease_id,
        cell_id=kit.state.cell_id,
        holder=kit.state.warden_id,
        access_level=access,
        scratch_root=str(scratch.resolve()),
        quota_bytes=quota,
        opened_at=clock.now(),
    )
    await kit.table.put(lease)
    return lease


@dataclass(frozen=True, slots=True)
class Limits:
    """The limits an exec runs under; the defaults let an ordinary test program finish."""

    timeout_s: float = 30.0  # Wall-clock seconds before TIMED_OUT.
    has_stdin: bool = False  # Whether the Warden will stream stdin.
    max_output_bytes: int = 1_000_000  # Stdout plus stderr cap before the group is killed.
    quota_bytes: int | None = None  # The scratch quota; the lease's own when None.


def make_plan(
    kit: Kit,
    clock: Clock,
    lease: DeviceLease,
    argv: tuple[str, ...],
    limits: Limits | None = None,
) -> ExecPlan:
    """Plan an exec of ``argv`` in ``lease``.

    Args:
        kit: The device the exec runs on; its Warden is the requester.
        clock: Mints the request id.
        lease: The lease the exec belongs to; its scratch is the working directory.
        argv: The program and arguments.
        limits: The limits to run under; the defaults when None.

    Returns:
        The plan.
    """
    limits = limits or Limits()
    scratch = Path(lease.scratch_root)
    return ExecPlan(
        request_id=new_message_id(clock),
        lease_id=lease.lease_id,
        requester=kit.state.warden_id,
        argv=argv,
        cwd=scratch,
        env={},
        timeout_s=limits.timeout_s,
        has_stdin=limits.has_stdin,
        max_output_bytes=limits.max_output_bytes,
        scratch_root=scratch,
        quota_bytes=lease.quota_bytes if limits.quota_bytes is None else limits.quota_bytes,
    )
