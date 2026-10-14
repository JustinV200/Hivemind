"""Build the doubles watch-mode tests need: a scripted machine, a recording session, a stub probe.

`ScriptedMachine` answers the two fixed scripts `hivemind.wardens.watch.probe.commands` builds the
way a Linux box would: it reads the section names out of the script it was given, prints each
under the random mark the probe passed in, and fills them from plain Python state a test edits
between samples (processes with their CPU ticks, files with mtimes, log contents).
`RecordingSession` is a `hivemind.cell.fake.FakeSession` over that machine that remembers every
method called on it, so a test can prove a probe only ever ran `exec`. `StubProbe` is a
`WatchProbe` that returns prepared Observations (or raises prepared errors) and records what it was
asked. `make_observation`, `make_process` and `make_resources` build the wire values;
`make_watch_deps` builds a `WatchDeps` with small intervals; `ScriptedPatrolPort` is a
`PatrolPort` that decides what a test tells it to.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by every test under
    packages/hivemind/tests/unit/wardens/watch and the watch end-to-end test.

Key invariants:
    - The machine prints exactly what a shell would for the same script: markers with a leading
      newline, NUL-terminated file records, `tail`'s last `TAIL_BYTES` bytes.
    - `RecordingSession.calls` lists every method called on it, in order, `exec` included.
    - Every builder takes explicit values and mints nothing random except the ids waggle mints.

See Also:
    - hivemind.wardens.watch.probe.commands for the scripts the machine answers.
    - builders.wardens for make_warden_deps, which takes `watch=` as an override.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from hivemind.cell import CellSession, CompletedCommand, ExecEvent, ExecSpec
from hivemind.cell.fake import FakeCellSource, FakeSession, Responder
from hivemind.cell.lease import RealCellLease
from hivemind.memory import InMemoryMemoryStore, MemoryContext, MemoryIdentity
from hivemind.pheromone import MemoryPheromoneTrail, PheromoneTrail
from hivemind.wardens.watch import (
    PatrolDecision,
    PatrolOutcome,
    WatchDeps,
    WatchSchedule,
    WatchSettings,
)
from hivemind.wardens.watch.digest import PatrolWindow
from hivemind.wardens.watch.probe.commands import TAIL_BYTES
from waggle.clock import Clock, FakeClock
from waggle.ids import new_device_id, new_hive_id, new_node_id
from waggle.messages.swarm import (
    FileChange,
    LogLine,
    NetworkCounter,
    Observation,
    ObservationKind,
    ProcessSample,
    ResourceSample,
)

GIB = 1_073_741_824
_SECTION_NAME = re.compile(r'"\$mark" (\w+);')  # How the system script names a section.
DEVICE_ID = new_device_id(FakeClock())  # One valid device id for every observation a test builds.

__all__ = [
    "DEVICE_ID",
    "FakeProcess",
    "MachineCellSource",
    "RecordingSession",
    "ScriptedMachine",
    "ScriptedPatrolPort",
    "StubProbe",
    "make_memory_context",
    "make_observation",
    "make_process",
    "make_resources",
    "make_watch_deps",
    "stat_line",
]


def stat_line(
    pid: int, name: str, cpu_ticks: tuple[int, int] = (0, 0), started: int = 0, rss_pages: int = 0
) -> str:
    """Return a `/proc/<pid>/stat` line the way the Linux kernel prints it.

    Args:
        pid: The process id.
        name: Its name, put between parentheses (it may itself hold spaces and parentheses).
        cpu_ticks: User and system CPU ticks used so far.
        started: Start time, in ticks after boot.
        rss_pages: Resident pages.
    """
    utime, stime = cpu_ticks
    # Fields after the name, from the state: only utime, stime, starttime and rss are read.
    fields = ["S", "1", str(pid), str(pid), "0", "-1", "4194304", "0", "0", "0", "0"]
    fields += [str(utime), str(stime), "0", "0", "20", "0", "1", "0", str(started), "1000"]
    fields += [str(rss_pages), "18446744073709551615"]
    return f"{pid} ({name}) {' '.join(fields)}"


@dataclass
class FakeProcess:
    """One process of a ScriptedMachine."""

    pid: int
    name: str
    utime: int = 0  # User CPU ticks used so far; a test raises it between samples.
    stime: int = 0
    started_s: float = 10.0  # Seconds after boot it started.
    rss_pages: int = 100


@dataclass
class ScriptedMachine:
    """A Linux machine described in Python, answering the probe's two scripts."""

    cores: int = 4
    boot_epoch: float = 1_577_000_000.0
    uptime_s: float = 3_000_000.0
    page_size: int = 4_096
    load: float = 0.8
    mem_total_kib: int = 8_000_000
    mem_avail_kib: int = 4_000_000
    disk_total_kib: int = 100_000_000
    disk_avail_kib: int = 40_000_000
    net: dict[str, tuple[int, int]] = field(default_factory=lambda: {"eth0": (1_000, 2_000)})
    processes: list[FakeProcess] = field(default_factory=list)
    files: dict[str, list[tuple[str, float, int]]] = field(default_factory=dict)  # root -> files
    logs: dict[str, str] = field(default_factory=dict)  # path -> contents
    unreadable_sections: set[str] = field(default_factory=set)  # Printed empty, like a failed cat.

    def respond(self, spec: ExecSpec) -> CompletedCommand:
        """Answer one exec of either fixed script, or 127 for anything else."""
        argv = spec.argv
        if argv[:2] != ("sh", "-c") or len(argv) < 6:
            return CompletedCommand(exit_code=127, stdout=b"", stderr=b"?", duration_s=0.0)
        mark = argv[4]
        if argv[2].startswith("mark=$1; dir=$2"):
            text = self._system(argv[2], mark, argv[6:])
        else:
            text = self._tails(mark, argv[5:])
        return CompletedCommand(exit_code=0, stdout=text.encode(), stderr=b"", duration_s=0.0)

    def _system(self, script: str, mark: str, roots: Sequence[str]) -> str:
        """Print the sections the system script asks for, in its order."""
        out: list[str] = []
        for name in _SECTION_NAME.findall(script):
            out.append(f"\n{mark} {name}\n")
            if name == "roots":
                out.extend(self._root(mark, number, root) for number, root in enumerate(roots, 1))
            elif name not in self.unreadable_sections:
                out.append(self._section(name))
        return "".join(out)

    def _root(self, mark: str, number: int, root: str) -> str:
        """Print one root's marker and its NUL-terminated records."""
        records = "".join(
            f"{mtime}\t{size}\t{path}\0"
            for path, mtime, size in self.files.get(root, [])
            if path.startswith(root)
        )
        return f"\n{mark} root{number}\n{records}"

    def _tails(self, mark: str, paths: Sequence[str]) -> str:
        """Print each path's marker and the last TAIL_BYTES bytes of its log."""
        out = []
        for number, path in enumerate(paths, 1):
            content = self.logs.get(path, "").encode()[-TAIL_BYTES:].decode(errors="replace")
            out.append(f"\n{mark} tail{number}\n{content}")
        return "".join(out)

    def _section(self, name: str) -> str:
        """Return what one named section of the system script prints."""
        if name == "stat":
            cpus = "".join(f"cpu{n} 1 1 1 1\n" for n in range(self.cores))
            return f"cpu 1 1 1 1\n{cpus}btime {int(self.boot_epoch)}\n"
        if name == "uptime":
            return f"{self.uptime_s:.2f} 100.00\n"
        if name == "pagesize":
            return f"{self.page_size}\n"
        if name == "procs":
            return "".join(self._proc(p) + "\n" for p in self.processes)
        if name == "loadavg":
            return f"{self.load:.2f} 0.50 0.40 1/200 12345\n"
        if name == "meminfo":
            return f"MemTotal: {self.mem_total_kib} kB\nMemAvailable: {self.mem_avail_kib} kB\n"
        if name == "netdev":
            return _NETDEV_HEADER + "".join(
                self._net(i, rx, tx) for i, (rx, tx) in self.net.items()
            )
        return self._df()

    def _proc(self, process: FakeProcess) -> str:
        """Print one process's stat line."""
        started = int(process.started_s * 100)
        cpu = (process.utime, process.stime)
        return stat_line(process.pid, process.name, cpu, started, process.rss_pages)

    @staticmethod
    def _net(interface: str, rx: int, tx: int) -> str:
        """Print one interface row of /proc/net/dev."""
        return f"  {interface}: {rx} 1 0 0 0 0 0 0 {tx} 1 0 0 0 0 0 0\n"

    def _df(self) -> str:
        """Print `df -Pk`'s header and one row."""
        used = self.disk_total_kib - self.disk_avail_kib
        return (
            "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
            f"/dev/sda1 {self.disk_total_kib} {used} {self.disk_avail_kib} 60% /\n"
        )


_NETDEV_HEADER = (
    "Inter-|   Receive                                                |  Transmit\n"
    " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs\n"
)


class RecordingSession(FakeSession):
    """A FakeSession over a ScriptedMachine that remembers every method called on it."""

    def __init__(
        self,
        scratch: Path,
        clock: Clock,
        machine: ScriptedMachine,
        responder: Responder | None = None,
    ) -> None:
        """Build the session over `machine`; `responder` replaces its answers (a hang, a crash)."""
        super().__init__(scratch, clock, responder=responder or machine.respond)
        self.calls: list[str] = []
        self.execs: list[tuple[str, ...]] = []

    async def exec(self, spec: ExecSpec) -> AsyncIterator[ExecEvent]:
        """Record, then run the command on the machine."""
        self.calls.append("exec")
        self.execs.append(spec.argv)
        async for event in super().exec(spec):
            yield event

    async def put_file(self, path: Path, data: bytes) -> None:
        """Record, then write (a probe must never get here)."""
        self.calls.append("put_file")
        await super().put_file(path, data)

    async def delete_file(self, path: Path) -> None:
        """Record, then delete (a probe must never get here)."""
        self.calls.append("delete_file")
        await super().delete_file(path)


class MachineCellSource(FakeCellSource):
    """A FakeCellSource whose sessions are RecordingSessions over one ScriptedMachine."""

    def __init__(self, machine: ScriptedMachine, *args: object, **kwargs: object) -> None:
        """Build the source; the remaining arguments are FakeCellSource's own."""
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.machine = machine
        self.sessions: list[RecordingSession] = []

    async def open_session(self, lease: RealCellLease) -> CellSession:
        """Open a recording session on the machine."""
        session = RecordingSession(lease.scratch_root, self._clock, self.machine)
        self.sessions.append(session)
        return session


class StubProbe:
    """A WatchProbe that returns prepared answers in order, then the last one forever."""

    def __init__(self, *answers: Observation | Exception) -> None:
        """Prepare the answers; an Exception is raised instead of returned."""
        self._answers = list(answers)
        self.calls: list[tuple[tuple[ObservationKind, ...], tuple[str, ...], datetime | None]] = []

    async def observe(
        self, kinds: Sequence[ObservationKind], roots: Sequence[str], since_at: datetime | None
    ) -> Observation:
        """Record what was asked and answer."""
        self.calls.append((tuple(kinds), tuple(roots), since_at))
        answer = self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def make_process(
    pid: int = 100,
    name: str = "proc",
    cpu_share: float = 0.0,
    rss_bytes: int = 1_048_576,
    started_at: datetime | None = None,
) -> ProcessSample:
    """Build a ProcessSample."""
    return ProcessSample(
        pid=pid, name=name, cpu_share=cpu_share, rss_bytes=rss_bytes, started_at=started_at
    )


def make_resources(
    cpu_load: float = 0.2,
    memory_free_bytes: int = 4 * GIB,
    disk_free_bytes: int = 50 * GIB,
    network: Iterable[NetworkCounter] = (),
) -> ResourceSample:
    """Build a ResourceSample of an 8 GiB machine with a 100 GiB disk."""
    return ResourceSample(
        cpu_load=cpu_load,
        memory_bytes=8 * GIB,
        memory_free_bytes=memory_free_bytes,
        disk_bytes=100 * GIB,
        disk_free_bytes=disk_free_bytes,
        uptime_s=1_000.0,
        network=tuple(network),
    )


def make_observation(
    at: datetime,
    *,
    processes: Iterable[ProcessSample] = (),
    resources: ResourceSample | None = None,
    log_lines: Iterable[LogLine] = (),
    file_changes: Iterable[FileChange] = (),
) -> Observation:
    """Build an Observation of the test device taken at `at`."""
    return Observation(
        device_id=DEVICE_ID,
        observed_at=at,
        processes=tuple(processes),
        resources=resources,
        log_lines=tuple(log_lines),
        file_changes=tuple(file_changes),
        is_truncated=False,
        reason="Observed.",
    )


def make_memory_context(clock: FakeClock) -> tuple[MemoryContext, PheromoneTrail]:
    """Build a MemoryContext over an in-memory store, and the trail that store records on."""
    trail = MemoryPheromoneTrail(clock)
    identity = MemoryIdentity(
        hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system"
    )
    ctx = MemoryContext(store=InMemoryMemoryStore(trail), identity=identity, clock=clock)
    return ctx, trail


def make_watch_deps(
    probe: StubProbe | object,
    *,
    interval_s: float = 60.0,
    patrol_interval_s: float = 600.0,
    retention_s: float = 86_400.0,
    roots: tuple[str, ...] = (),
) -> WatchDeps:
    """Build WatchDeps over `probe` with the given cadence and the default thresholds."""
    settings = WatchSettings(
        interval_s=interval_s,
        retention_s=retention_s,
        patrol_interval_s=patrol_interval_s,
        cpu_share_alarm=0.5,
        disk_fill_alarm=0.95,
        roots=roots,
    )
    return WatchDeps(probe=probe, settings=settings, schedule=WatchSchedule())  # type: ignore[arg-type]


class ScriptedPatrolPort:
    """A PatrolPort that decides what a test says, and records everything it was asked."""

    def __init__(self, decision: PatrolDecision | Exception, delivers: bool = True) -> None:
        """Prepare the decision (an Exception is raised from `decide`) and whether it delivers."""
        self._decision = decision
        self._delivers = delivers
        self.windows: list[PatrolWindow] = []
        self.delivered: list[PatrolDecision] = []
        self.recorded: list[tuple[PatrolOutcome, PatrolWindow]] = []

    async def decide(self, window: PatrolWindow) -> PatrolDecision:
        """Return the prepared decision, or raise the prepared error."""
        self.windows.append(window)
        if isinstance(self._decision, Exception):
            raise self._decision
        return self._decision

    async def carry_out(self, decision: PatrolDecision, window: PatrolWindow) -> bool:
        """Remember the decision and say whether it was delivered."""
        del window
        self.delivered.append(decision)
        return self._delivers

    async def record(self, outcome: PatrolOutcome, window: PatrolWindow) -> None:
        """Remember how the Patrol ended."""
        self.recorded.append((outcome, window))
