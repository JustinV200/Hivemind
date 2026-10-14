"""Define the swarm family's watch messages: ask a device what it looks like, and its answer.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance), and the
swarm family is about devices joining and living in the Swarm (the mesh of enrolled Real Cells,
existing devices borrowed for tasks and left exactly as found). When a Real Cell's Warden (the
always-on supervisor of its Cell, a unit of compute) has no bees working it enters WATCH: it
observes the device read-only and, on a schedule, reviews what it saw (the Patrol). Where the
Warden runs on the Hive Stand (the machine the Queen, the central orchestrator, runs on) and only
the device's Pollen Packet (the thin gateway installed on it) is on the device, the Warden asks
the packet with ``Observe`` and the packet answers with ``Observation``, built from a fixed set of
collectors it runs itself: no lease, no command a request could shape, and nothing a request can
widen, since it may name only the kinds of observation below and the roots to look inside. The
kinds are exactly the Guard's watch observations: the process list, resource use (network counters
included), logs and file changes inside the named roots; never the screen, never input. Everything
an Observation holds describes the operator's own machine, so it is ``C2`` (the most sensitive
label) on arrival. Added in PROTOCOL_MINOR 13 (roadmap phase 11, ADR-0054). Every bound is a named
constant here, chosen so that a maximal Observation of ordinary text encodes to well under the
frame ceiling (section 5 of the spec); the number, not the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Registered by waggle.messages.registry, which maps
    each class to its kind; built by a Warden (the request) or a Pollen Packet (the answer) and
    read by the other; calls into waggle.messages.base only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - Every message and value model is frozen and forbids extras through WaggleMessage's config.
    - Every rule the spec marks (validator) is a pydantic validator on the class; every rule it
      marks (receiver rule) is deliberately absent, because the receiver enforces it.
    - An Observe carries no command, no pattern and no unbounded path list: the packet cannot be
      made to run or read anything beyond its fixed collectors and the named roots.
    - Every count and every string in an Observation is bounded, so even a device with a million
      processes and a log full of long lines answers in one frame, with ``is_truncated`` set.

See Also:
    - docs/waggle/spec.md section 8.10 for the normative fields, bounds and validators.
    - docs/adr/0054-access-levels-watch-mode-and-the-patrol.md for the watch mode and the Patrol.
    - waggle.messages.base for the id aliases and the shared reason bound.
    - waggle.messages.registry for the kinds these classes are registered under.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field, field_validator, model_validator

from waggle.messages.base import (
    MAX_REASON_CHARS,
    VALUE_MODEL_CONFIG,
    DeviceIdField,
    UtcDatetime,
    WaggleMessage,
)

MAX_OBSERVE_KINDS = 4  # One entry per ObservationKind; uniqueness makes a longer list impossible.
MAX_OBSERVE_ROOTS = 16  # Directories to watch: a home, a project, a log directory, a handful more.
MIN_OBSERVE_ITEMS = 1  # A request for no items asks for nothing.
MAX_OBSERVE_ITEMS = 500  # The most items per list one Observe may ask for; a busy desktop's few
# hundred processes fit, and a page of file changes is never read in one sitting.
DEFAULT_OBSERVE_ITEMS = 200  # A Patrol's usual page: enough to see a pattern, small enough to read.
MAX_PROCESSES = MAX_OBSERVE_ITEMS  # An answer holds as many processes as a request may ask for.
MAX_FILE_CHANGES = MAX_OBSERVE_ITEMS  # Likewise for one page of file changes.
MAX_LOG_LINES = 200  # Log lines are the bulky part (up to 1,000 characters each), so fewer.
MAX_LOG_LINE_CHARS = 1_000  # One log line; a longer one is cut by the packet, not refused.
MAX_PROCESS_NAME_CHARS = 256  # An executable's name or a short command line; no process needs more.
MAX_INTERFACE_CHARS = 64  # A network interface name (eth0, wlp3s0, a Windows adapter alias).
MAX_NETWORK_INTERFACES = 32  # Interfaces on one device, virtual ones included.
MAX_OBSERVED_PATH_CHARS = 256  # A watched path; Windows' own MAX_PATH. A longer path is skipped
# (a cut path would name another file), which keeps the maximal answer well under one frame.

__all__ = [
    "DEFAULT_OBSERVE_ITEMS",
    "MAX_FILE_CHANGES",
    "MAX_INTERFACE_CHARS",
    "MAX_LOG_LINES",
    "MAX_LOG_LINE_CHARS",
    "MAX_NETWORK_INTERFACES",
    "MAX_OBSERVED_PATH_CHARS",
    "MAX_OBSERVE_ITEMS",
    "MAX_OBSERVE_KINDS",
    "MAX_OBSERVE_ROOTS",
    "MAX_PROCESSES",
    "MAX_PROCESS_NAME_CHARS",
    "MIN_OBSERVE_ITEMS",
    "FileChange",
    "FileChangeKind",
    "LogLine",
    "NetworkCounter",
    "Observation",
    "ObservationKind",
    "Observe",
    "ProcessSample",
    "ResourceSample",
    "WatchRoots",
]


# ──────────────────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────────────────


class ObservationKind(Enum):
    """One thing a Warden may ask a device to report; the closed set the Guard allows."""

    PROCESS_LIST = "PROCESS_LIST"  # Which processes run, with their CPU share and memory.
    RESOURCE_USE = "RESOURCE_USE"  # Load, memory, disk and network counters.
    LOGS = "LOGS"  # The tail of log files inside the named roots.
    FILE_CHANGES = "FILE_CHANGES"  # Files created, changed or deleted inside the named roots.


class FileChangeKind(Enum):
    """How a watched file changed since the cursor."""

    CREATED = "CREATED"
    MODIFIED = "MODIFIED"
    DELETED = "DELETED"


# The kinds that read files, and so need roots to read inside; the others read no path.
_ROOTED_KINDS = frozenset({ObservationKind.LOGS, ObservationKind.FILE_CHANGES})

# A reason field, as the catalogue conventions fix it: always named `reason`, always bounded by
# the shared MAX_REASON_CHARS, so the Pheromone Trail (the append-only audit log) records why.
_Reason = Annotated[str, Field(max_length=MAX_REASON_CHARS)]
# A watched path in an answer: non-empty, and short enough that a maximal answer fits a frame.
_ObservedPath = Annotated[str, Field(min_length=1, max_length=MAX_OBSERVED_PATH_CHARS)]
# The directories a device's Warden reads logs and file changes from: a bounded list of paths, as
# `Observe.roots` carries them. `NodeUpdate` sends it to the packet and `CellReady` reports it back,
# so both ends speak of exactly the list the Warden runs with.
WatchRoots = Annotated[tuple[_ObservedPath, ...], Field(max_length=MAX_OBSERVE_ROOTS)]


# ──────────────────────────────────────────────────────────────────────────────
# Value models of an Observation
# ──────────────────────────────────────────────────────────────────────────────


class ProcessSample(BaseModel):
    """One running process, as the packet's process collector saw it."""

    model_config = VALUE_MODEL_CONFIG

    pid: int = Field(ge=0, description="The process id on the device (0 only for an idle task).")
    name: str = Field(
        max_length=MAX_PROCESS_NAME_CHARS,
        description="The executable's name; never its arguments, which can carry secrets.",
    )
    cpu_share: float = Field(
        ge=0,
        le=1,
        description="The share of the device's whole CPU capacity the process used since the "
        "previous sample, between 0 and 1 (one core of four busy is 0.25).",
    )
    rss_bytes: int = Field(ge=0, description="Resident memory in bytes.")
    started_at: UtcDatetime | None = Field(
        description="When the process started; None where the platform does not say."
    )


class NetworkCounter(BaseModel):
    """Cumulative traffic of one network interface since the device booted."""

    model_config = VALUE_MODEL_CONFIG

    interface: str = Field(
        min_length=1, max_length=MAX_INTERFACE_CHARS, description="The interface's name."
    )
    rx_bytes: int = Field(ge=0, description="Bytes received since boot.")
    tx_bytes: int = Field(ge=0, description="Bytes sent since boot.")


class ResourceSample(BaseModel):
    """The device's resource use at one instant, always a full snapshot."""

    model_config = VALUE_MODEL_CONFIG

    cpu_load: float = Field(ge=0, description="One-minute load average divided by logical cores.")
    memory_bytes: int = Field(ge=0, description="Total memory.")
    memory_free_bytes: int = Field(
        ge=0, description="Memory available to start work; at most memory_bytes (validator)."
    )
    disk_bytes: int = Field(
        ge=0, description="Total size of the filesystem the packet works on (its data directory's)."
    )
    disk_free_bytes: int = Field(
        ge=0, description="Free bytes on that filesystem; at most disk_bytes (validator)."
    )
    uptime_s: float = Field(ge=0, description="Seconds since the device booted.")
    network: tuple[NetworkCounter, ...] = Field(
        max_length=MAX_NETWORK_INTERFACES,
        description="One counter per interface; names unique (validator).",
    )

    @model_validator(mode="after")
    def _free_within_total(self) -> ResourceSample:
        """Reject a free figure above its total, for memory and for disk."""
        # The Patrol's pre-filter subtracts free from total to get a fill ratio without a guard,
        # so the sample itself must be consistent (HostCapacityReport holds the same line).
        if self.memory_free_bytes > self.memory_bytes:
            raise ValueError(
                f"ResourceSample memory_free_bytes {self.memory_free_bytes} is more than "
                f"memory_bytes {self.memory_bytes}."
            )
        if self.disk_free_bytes > self.disk_bytes:
            raise ValueError(
                f"ResourceSample disk_free_bytes {self.disk_free_bytes} is more than "
                f"disk_bytes {self.disk_bytes}."
            )
        return self

    @field_validator("network")
    @classmethod
    def _interfaces_unique(cls, value: tuple[NetworkCounter, ...]) -> tuple[NetworkCounter, ...]:
        """Reject an interface counted twice."""
        # Two rows for one interface would double its traffic in any total a reader sums.
        names = [counter.interface for counter in value]
        if len(set(names)) != len(names):
            raise ValueError("ResourceSample network interface names must be unique.")
        return value


class LogLine(BaseModel):
    """One line from the tail of a log file inside a watched root."""

    model_config = VALUE_MODEL_CONFIG

    path: _ObservedPath = Field(description="The log file the line came from.")
    line: str = Field(
        max_length=MAX_LOG_LINE_CHARS,
        description="The line without its newline, cut at the bound if it was longer.",
    )


class FileChange(BaseModel):
    """One file created, changed or deleted inside a watched root since the cursor."""

    model_config = VALUE_MODEL_CONFIG

    path: _ObservedPath = Field(description="The file that changed.")
    change: FileChangeKind = Field(description="How it changed.")
    at: UtcDatetime = Field(description="When the change was seen (a modification time).")
    size_bytes: Annotated[int, Field(ge=0)] | None = Field(
        description="The file's size after the change; None for a deleted file (validator) and "
        "where the size could not be read."
    )

    @model_validator(mode="after")
    def _deleted_file_has_no_size(self) -> FileChange:
        """Reject a size on a deleted file."""
        # A file that is gone has no size to report; a figure here would be a stale one, and a
        # reader summing sizes would count bytes that no longer exist.
        if self.change is FileChangeKind.DELETED and self.size_bytes is not None:
            raise ValueError("FileChange size_bytes must be None for a DELETED file.")
        return self


# ──────────────────────────────────────────────────────────────────────────────
# Messages
# ──────────────────────────────────────────────────────────────────────────────


class Observe(WaggleMessage):
    """Ask a device's packet what the device looks like, read-only (swarm.observe, a request).

    From the Warden of a Level 0 device to its Pollen Packet, which answers with one correlated
    Observation, or with a control.error pollen.capability_denied when the Warden's Cell has no
    watch capability. The packet runs only its own collectors; the request chooses among them.
    """

    device_id: DeviceIdField = Field(description="The device asked; the packet's own.")
    kinds: tuple[ObservationKind, ...] = Field(
        min_length=1,
        max_length=MAX_OBSERVE_KINDS,
        description="What to report; at least one, each kind at most once (validator).",
    )
    roots: tuple[_ObservedPath, ...] = Field(
        max_length=MAX_OBSERVE_ROOTS,
        description="Directories to read logs from and watch for file changes, each inside what "
        "the Cell's watch capability and access level allow; non-empty exactly when kinds "
        "includes LOGS or FILE_CHANGES (validator).",
    )
    since_at: UtcDatetime | None = Field(
        description="The file-change cursor: report only changes after this instant. Only with "
        "FILE_CHANGES (validator); None asks for the packet's own recent window."
    )
    max_items: int = Field(
        default=DEFAULT_OBSERVE_ITEMS,
        ge=MIN_OBSERVE_ITEMS,
        le=MAX_OBSERVE_ITEMS,
        description="The most items to return in each of the process list, the file changes "
        "and the log lines (the last further capped at MAX_LOG_LINES).",
    )
    reason: _Reason = Field(description="Why the Warden looks now (a scheduled sample, a Patrol).")

    @field_validator("kinds")
    @classmethod
    def _kinds_unique(cls, value: tuple[ObservationKind, ...]) -> tuple[ObservationKind, ...]:
        """Reject a kind asked for twice."""
        # Two entries for one kind would make the packet decide whether to collect it once or
        # twice; refusing the request keeps the answer exactly what was asked.
        if len(set(value)) != len(value):
            raise ValueError("Observe kinds must be unique.")
        return value

    @model_validator(mode="after")
    def _roots_and_cursor_match_kinds(self) -> Observe:
        """Require roots exactly for the kinds that read files, and a cursor only for changes."""
        # LOGS and FILE_CHANGES read inside a root, so without one they would name nothing; roots
        # given to a request that reads no file would invite a reader to think they were checked.
        # The cursor belongs to the change feed alone: logs are a tail, not a feed.
        reads_files = bool(_ROOTED_KINDS.intersection(self.kinds))
        if reads_files != bool(self.roots):
            raise ValueError(
                f"Observe roots are required exactly when kinds includes LOGS or FILE_CHANGES, "
                f"got {len(self.roots)} root(s) for kinds "
                f"{[kind.value for kind in self.kinds]}."
            )
        if self.since_at is not None and ObservationKind.FILE_CHANGES not in self.kinds:
            raise ValueError("Observe since_at is allowed only when kinds includes FILE_CHANGES.")
        return self


class Observation(WaggleMessage):
    """Report what a device looks like (swarm.observation, a reply).

    The packet's answer to an Observe: only the parts that were asked for are filled, and the
    rest are empty or None. Everything in it describes the operator's machine and is C2 on
    arrival: the Warden stores it as Bee Bread (the warm memory tier) at that label and never
    forwards it as it is.
    """

    device_id: DeviceIdField = Field(description="The device observed.")
    observed_at: UtcDatetime = Field(description="When the packet took the sample.")
    processes: tuple[ProcessSample, ...] = Field(
        max_length=MAX_PROCESSES,
        description="The process list when PROCESS_LIST was asked for, busiest first, so a cut "
        "at max_items loses the least busy.",
    )
    resources: ResourceSample | None = Field(
        description="Resource use when RESOURCE_USE was asked for; None otherwise."
    )
    log_lines: tuple[LogLine, ...] = Field(
        max_length=MAX_LOG_LINES,
        description="The newest log lines when LOGS was asked for, oldest of them first.",
    )
    file_changes: tuple[FileChange, ...] = Field(
        max_length=MAX_FILE_CHANGES,
        description="Changes after the cursor when FILE_CHANGES was asked for, oldest first, so "
        "the last one's time is the next request's since_at with nothing missed.",
    )
    is_truncated: bool = Field(
        description="True when the packet left something out to stay within a bound: more items "
        "than max_items or the caps allow, a path over its bound, or a frame that would be too "
        "large; the Warden then knows the sample is a lower bound."
    )
    reason: _Reason = Field(description="What the packet did, especially why a part is missing.")
