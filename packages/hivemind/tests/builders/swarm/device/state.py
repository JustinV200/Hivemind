"""Hold the state and the script of a scripted Pollen Packet: what it knows, and what it will do.

The scripted device (`builders.swarm.device`) plays the packet side of the gateway contract so the
Hive's side can be tested against something that answers like one. This module is its memory and
its knobs. `DeviceScript` is what a test turns: how a command named `echo` behaves, which access the
device grants, which request kinds it refuses or never answers, how a release reports. `DeviceState`
is what the device accumulates as the Hive talks to it: the leases it holds open, its small virtual
file system, the background processes it started, the frames it received and the node updates it
was sent, all of which a test reads back to assert what really crossed the link. Nothing here
touches the real file system or starts a process.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the scripted device and its
    services, and read by the Swarm tests that assert on what the device saw.

Key invariants:
    - Paths in the virtual file system are POSIX text; nothing here resolves a path against the
      machine the tests run on.
    - A script is plain data: the device reads it on every request, so a test may change it
      between requests.
    - Every id is minted from the injected clock, so a run is deterministic.

See Also:
    - docs/waggle/gateway-contract.md for the behaviour the services built on this state mimic.
    - builders.swarm.device.core for the device that owns a DeviceState.
"""

from __future__ import annotations

import asyncio
import posixpath
from dataclasses import dataclass, field
from fnmatch import fnmatchcase

from builders.swarm.parties import DeviceParty

from waggle.clock import Clock
from waggle.envelope import Envelope
from waggle.ids import HiveId, LeaseId
from waggle.messages.base import WaggleMessage
from waggle.messages.cell.leases import LeaseReleased
from waggle.messages.labels import AccessLevel, CombShieldLevel
from waggle.messages.swarm import NodeUpdate, Observation

__all__ = [
    "DeviceLease",
    "DeviceProcess",
    "DeviceScript",
    "DeviceState",
    "ExecScript",
    "ReleaseScript",
]

FIRST_PID = 4000  # Where the device's fake process ids start counting.


@dataclass(frozen=True, slots=True)
class ExecScript:
    """How one program behaves when the device is asked to run it.

    Attributes:
        stdout: Bytes written to standard output.
        stderr: Bytes written to standard error.
        exit_code: The code the process exits with.
        hang: Run until the timeout (reported TIMED_OUT) or until killed (KILLED).
        unstartable: The program cannot be started: one stderr line and exit 127.
        signal_name: End as SIGNALED by this signal instead of exiting.
        output_chunks: Split each stream into this many chunks, to exercise ordering.
    """

    stdout: bytes = b""
    stderr: bytes = b""
    exit_code: int = 0
    hang: bool = False
    unstartable: bool = False
    signal_name: str | None = None
    output_chunks: int = 1


@dataclass(frozen=True, slots=True)
class ReleaseScript:
    """How the device reports a release.

    Attributes:
        is_restored: Whether it claims to have left the device exactly as found.
        residual_paths: Paths it could not restore; non-empty exactly when `is_restored` is False.
    """

    is_restored: bool = True
    residual_paths: tuple[str, ...] = ()


@dataclass
class DeviceScript:
    """The knobs a test turns on a scripted device; read afresh on every request."""

    # Keyed by the whole command line ("cat /proc/net/route") or, failing that, the program's name.
    execs: dict[str, ExecScript] = field(default_factory=dict)
    node_access: AccessLevel = AccessLevel.SCRATCH
    comb_shield: CombShieldLevel = CombShieldLevel.MEADOW
    accept_any_holder: bool = True
    scratch_base: str = "/pollen/scratch"
    allowed_scopes: tuple[str, ...] = ()
    dead_man_s: float = 120.0
    refusals: dict[type[WaggleMessage], str] = field(default_factory=dict)
    silent: set[type[WaggleMessage]] = field(default_factory=set)
    start_failures: set[str] = field(default_factory=set)
    start_logs: dict[str, bytes] = field(default_factory=dict)
    release: ReleaseScript = field(default_factory=ReleaseScript)
    observation: Observation | None = None
    lie_about_sha256: bool = False  # Report a wrong digest on a transfer's exit.
    reorder_file_chunks: bool = False  # Send a get-file's chunks out of order.


@dataclass
class DeviceLease:
    """One lease the device holds open."""

    lease_id: LeaseId
    holder: str
    scratch_root: str
    access_level: AccessLevel
    quota_bytes: int | None
    session_open: bool = False


@dataclass
class DeviceProcess:
    """One background process the device started for a lease."""

    pid: int
    lease_id: LeaseId
    argv: tuple[str, ...]
    log_path: str | None
    running: bool = True


@dataclass
class DeviceState:
    """Everything a scripted device has seen and holds."""

    party: DeviceParty
    hive_id: HiveId
    clock: Clock
    script: DeviceScript
    leases: dict[LeaseId, DeviceLease] = field(default_factory=dict)
    files: dict[str, bytes] = field(default_factory=dict)
    processes: dict[int, DeviceProcess] = field(default_factory=dict)
    received: list[Envelope] = field(default_factory=list)
    updates: list[NodeUpdate] = field(default_factory=list)
    released: list[LeaseReleased] = field(default_factory=list)
    stdin: dict[str, bytes] = field(default_factory=dict)
    killed: set[str] = field(default_factory=set)
    kill_events: dict[str, asyncio.Event] = field(default_factory=dict)
    exec_started: asyncio.Event = field(default_factory=asyncio.Event)
    kill_seen: asyncio.Event = field(default_factory=asyncio.Event)
    session_closed: asyncio.Event = field(default_factory=asyncio.Event)
    execs: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    next_pid: int = FIRST_PID

    def new_pid(self) -> int:
        """Hand out the next fake process id."""
        self.next_pid += 1
        return self.next_pid

    def resolve(self, lease: DeviceLease, path: str) -> str:
        """Resolve a path against the lease's scratch root the way the packet does, by text."""
        joined = path if posixpath.isabs(path) else posixpath.join(lease.scratch_root, path)
        return posixpath.normpath(joined)

    def is_reachable(self, lease: DeviceLease, resolved: str) -> bool:
        """Whether a resolved path is inside scratch or matches one of the node's allowed scopes."""
        root = lease.scratch_root
        if resolved == root or resolved.startswith(root + "/"):
            return True
        return any(fnmatchcase(resolved, scope) for scope in self.script.allowed_scopes)

    def kill(self, exec_id: str) -> None:
        """Record a kill of one exec and wake it if it is hanging."""
        self.killed.add(exec_id)
        self.kill_seen.set()
        event = self.kill_events.get(exec_id)
        if event is not None:
            event.set()

    def kill_processes(self, lease_id: LeaseId) -> int:
        """Stop every running process a lease started; return how many were running."""
        killed = 0
        for process in self.processes.values():
            if process.lease_id == lease_id and process.running:
                process.running = False
                killed += 1
        return killed
