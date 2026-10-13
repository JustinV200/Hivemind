"""Answer swarm.observe: watch the device read-only, without a lease, from fixed collectors.

A Warden (the per-Cell supervisor) with no active bees watches its Real Cell (an existing device
the Hive borrows for a task) by asking the Pollen Packet (the thin gateway program on the device)
what the device looks like (ADR-0054). The packet answers from fixed collectors that read the
platform's own facilities and never run a command a request could shape: the process list,
resource use with network counters, log lines and file changes under named roots. It needs no
lease, but it checks that the node holds ``watch:<cell_id>`` and, for each root, ``fs:read``
before collecting anything, and it refuses a request that names another device. It never captures
the screen or the input and never writes. A kind the platform cannot collect comes back empty and
the reason says so; an answer that would not fit one frame is shortened and marked truncated.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Built by the composition root
    and consulted by pollen.agent.dispatch; sends through the Reporter from a task of the packet's
    scope; calls into pollen.executors.observe.system and .files, psutil and waggle only.

Key invariants:
    - Read-only: no write, no signal, no process started, whatever the request says.
    - The capability check (``watch`` and ``fs:read`` per root) happens before any collector runs.
    - The answer stays within the wire bounds, and a cut is reported in ``is_truncated``.

See Also:
    - docs/waggle/gateway-contract.md section 9.4 for the answer and its bounds.
    - docs/adr/0054-access-levels-watch-mode-and-the-patrol.md for why watching is read-only.
    - pollen.executors.observe.system and pollen.executors.observe.files for the collectors.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import ClassVar

import psutil

from pollen.errors import InvalidRequestError, PathRefusedError
from pollen.executors.base import (
    FS_READ,
    WATCH,
    ExecutorDeps,
    Need,
    Prepared,
    payload_of,
)
from pollen.executors.observe.files import ChangeWatcher, tail_logs
from pollen.executors.observe.system import collect_processes, collect_resources, prime_cpu
from pollen.executors.paths import resolve_path
from waggle.envelope import Envelope
from waggle.errors import FrameTooLargeError
from waggle.messages.base import WaggleMessage
from waggle.messages.swarm import (
    FileChange,
    LogLine,
    Observation,
    ObservationKind,
    Observe,
    ProcessSample,
    ResourceSample,
)

PRIME_WINDOW_S = 0.25  # How long the first process sample waits to measure a CPU share.
MAX_SHRINKS = 4  # Times an answer that does not fit one frame is halved before giving up.

__all__ = ["ObserveExecutor"]


class ObserveExecutor:
    """Answers ``swarm.observe`` from the fixed collectors.

    Owns mutable state (codingrules 8.5, documented): the change watcher's last listings and
    whether the CPU baseline has been taken.
    """

    handles: ClassVar[tuple[type[WaggleMessage], ...]] = (Observe,)

    def __init__(self, deps: ExecutorDeps) -> None:
        """Bind the executor to the shared collaborators.

        Args:
            deps: State, reporter, clock, layout and task scope.
        """
        self._deps = deps
        self._watcher = ChangeWatcher()
        self._is_primed = False

    def prepare(self, envelope: Envelope) -> Prepared:
        """Check the request names this device and name what it needs, changing nothing.

        Args:
            envelope: A ``swarm.observe``.

        Returns:
            The ``watch`` need for the Cell, an ``fs:read`` need per root, and the work.

        Raises:
            InvalidRequestError: The request names another device.
            PathRefusedError: A root is relative or cannot be resolved safely.
        """
        payload = payload_of(envelope, Observe)
        state = self._deps.state.current
        if payload.device_id != state.device_id:
            raise InvalidRequestError(f"Observe request {envelope.id} names another device.")
        roots = tuple(_resolve_root(text, envelope.id) for text in payload.roots)
        needs = (Need(WATCH, state.cell_id), *(Need(FS_READ, str(root)) for root in roots))

        async def run() -> None:
            self._deps.scope.spawn(self._observe(envelope, payload, roots), name="observe")

        return Prepared(run=run, needs=needs)

    async def _observe(self, request: Envelope, payload: Observe, roots: tuple[Path, ...]) -> None:
        """Collect, then send the one ``swarm.observation``, shortened if it will not fit."""
        observation = await self._collect(payload, roots)
        for _ in range(MAX_SHRINKS):
            try:
                await self._deps.reporter.send(
                    observation, to=request.sender, correlation_id=request.id
                )
                return
            except FrameTooLargeError:
                observation = _halved(observation)

    async def _collect(self, payload: Observe, roots: tuple[Path, ...]) -> Observation:
        """Run the collectors the request chose; one that fails is left empty and named."""
        kinds = set(payload.kinds)
        processes, resources, system_cut, missing = await self._system(kinds, payload.max_items)
        lines, changes, files_cut = await self._files(kinds, payload, roots)
        return Observation(
            device_id=payload.device_id,
            observed_at=self._deps.clock.now(),
            processes=processes,
            resources=resources,
            log_lines=lines,
            file_changes=changes,
            is_truncated=system_cut or files_cut,
            reason=f"Not collected: {', '.join(missing)}."
            if missing
            else "The device was observed.",
        )

    async def _system(
        self, kinds: set[ObservationKind], max_items: int
    ) -> tuple[tuple[ProcessSample, ...], ResourceSample | None, bool, list[str]]:
        """Collect the process list and the resources; name what the platform would not give."""
        processes: tuple[ProcessSample, ...] = ()
        resources: ResourceSample | None = None
        is_cut, missing = False, []
        if ObservationKind.PROCESS_LIST in kinds:
            try:
                processes, is_cut = await self._processes(max_items)
            except (OSError, psutil.Error):
                missing.append("PROCESS_LIST")
        if ObservationKind.RESOURCE_USE in kinds:
            try:
                resources = await asyncio.to_thread(collect_resources, self._deps.layout.root)
            except (OSError, psutil.Error):
                missing.append("RESOURCE_USE")
        return processes, resources, is_cut, missing

    async def _files(
        self, kinds: set[ObservationKind], payload: Observe, roots: tuple[Path, ...]
    ) -> tuple[tuple[LogLine, ...], tuple[FileChange, ...], bool]:
        """Collect the log tail and the file changes under the roots."""
        lines: tuple[LogLine, ...] = ()
        changes: tuple[FileChange, ...] = ()
        log_cut = change_cut = False
        if ObservationKind.LOGS in kinds:
            lines, log_cut = await asyncio.to_thread(tail_logs, roots, payload.max_items)
        if ObservationKind.FILE_CHANGES in kinds:
            changes, change_cut = await asyncio.to_thread(
                self._watcher.changes, roots, payload.since_at, payload.max_items
            )
        return lines, changes, log_cut or change_cut

    async def _processes(self, max_items: int) -> tuple[tuple[ProcessSample, ...], bool]:
        """Sample the process list, taking the CPU baseline first on the first call."""
        if not self._is_primed:
            await asyncio.to_thread(prime_cpu)
            await self._deps.clock.sleep(PRIME_WINDOW_S)  # Long enough to measure a share.
            self._is_primed = True
        return await asyncio.to_thread(collect_processes, max_items)


def _resolve_root(text: str, owner: str) -> Path:
    """Resolve a root, which must be absolute: there is no lease whose scratch it could join."""
    if not Path(text).is_absolute():
        raise PathRefusedError(f"A root in observe request {owner} is not an absolute path.")
    return resolve_path(Path("/"), text, owner)


def _halved(observation: Observation) -> Observation:
    """Return a copy with half the log lines and file changes, marked truncated."""
    return observation.model_copy(
        update={
            "log_lines": observation.log_lines[: len(observation.log_lines) // 2],
            "file_changes": observation.file_changes[: len(observation.file_changes) // 2],
            "is_truncated": True,
        }
    )
