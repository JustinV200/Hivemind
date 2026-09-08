"""Define the session family's Warden-sent messages: open, exec, stdin, close, and its enums.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance), and the
session family is terminal-over-Waggle: a Warden (the always-on supervisor of one Cell, a unit of
compute) on the Hive Stand (the machine the Queen, the central orchestrator, runs on) drives a
remote Real Cell (an existing device borrowed for a task and left exactly as found) through its
Pollen Packet (the thin gateway on an enrolled device) exactly as it would a local terminal. A
session is identified by its lease (the Hive's temporary hold on a Real Cell), one session per
lease; concurrency lives at the exec level, each exec keyed by its request's MessageId, and a
command is always an argument list, never a shell string. This file holds the family's enums,
its ``EnvVar`` value model and the four messages a Warden sends; what the packet sends back
(``SessionOutput``, ``SessionExit``) lives in ``waggle.messages.session_output`` and the file
transfers in ``waggle.messages.session_files``, split out by responsibility so each file stays
under the codingrules 5.1 size limit. Stdin bytes and environment values are possible secrets:
redacted from logs and the Pheromone Trail (the append-only audit log) and never in a repr.
Every bound is a named constant here; the number, not the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Registered by waggle.messages.registry, which maps
    each class to its kind; built by a Warden and read by a Pollen Packet; calls into
    waggle.messages.base only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - Every message is frozen and forbids extras through WaggleMessage's config.
    - Every rule the spec marks (validator) is a pydantic validator on the class; every rule it
      marks (receiver rule) is deliberately absent, because the receiver enforces it.
    - EnvVar.value and SessionStdin.chunk never appear in a repr (Field(repr=False)).

See Also:
    - docs/waggle/spec.md section 8.6 for the normative fields, bounds and validators.
    - docs/waggle/spec.md section 3 (the session rule) for which exit answers which request.
    - waggle.messages.session_output for SessionOutput and SessionExit.
    - waggle.messages.session_files for SessionPutFile and SessionGetFile.
    - waggle.messages.registry for the kinds these classes are registered under.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field, field_validator, model_validator

from waggle.messages.base import (
    DEFAULT_MAX_OUTPUT_BYTES,
    MAX_CHUNK_BYTES,
    MAX_PATH_CHARS,
    MAX_REASON_CHARS,
    VALUE_MODEL_CONFIG,
    LeaseIdField,
    MessageIdField,
    TaskIdField,
    WaggleMessage,
    WorkerIdField,
)

MAX_ENV_NAME_CHARS = 256  # Longer than any real variable name; bounds the overlay's keys.
ENV_NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"  # A portable identifier: nothing a shell parses.
MAX_ENV_VALUE_CHARS = 4_096  # A path list or a token, never a document; the same as a path.
MAX_ENV_VARS = 64  # An overlay is a handful of variables; the environment stays on the device.
MIN_ARGV_ITEMS = 1  # argv[0] is the executable; an empty command runs nothing.
MAX_ARGV_ITEMS = 128  # A program, its flags and a batch of files; more belongs in a script.
MAX_ARGV_ITEM_CHARS = 4_096  # One argument may be a whole path, so it gets a path's 4096.
MAX_ARGV_TOTAL_CHARS = 131_072  # 128 KiB: JSON-escaped it still leaves a frame room to spare.
MIN_OUTPUT_BYTES = 1  # A cap of 0 would kill every process on its first byte of output.

__all__ = [
    "ENV_NAME_PATTERN",
    "MAX_ARGV_ITEMS",
    "MAX_ARGV_ITEM_CHARS",
    "MAX_ARGV_TOTAL_CHARS",
    "MAX_ENV_NAME_CHARS",
    "MAX_ENV_VALUE_CHARS",
    "MAX_ENV_VARS",
    "MIN_ARGV_ITEMS",
    "MIN_OUTPUT_BYTES",
    "EnvVar",
    "ProcessSignal",
    "SessionClose",
    "SessionExec",
    "SessionOpen",
    "SessionOutcome",
    "SessionRequestKind",
    "SessionStdin",
    "SessionStream",
]


# ──────────────────────────────────────────────────────────────────────────────
# Enums and value models
# ──────────────────────────────────────────────────────────────────────────────


class SessionStream(Enum):
    """Which stream a SessionOutput chunk belongs to."""

    STDOUT = "STDOUT"
    STDERR = "STDERR"
    FILE = "FILE"  # A get-file transfer's content, never an exec's.


class SessionOutcome(Enum):
    """How a session request ended, as its SessionExit reports it."""

    EXITED = "EXITED"  # The process ended on its own; exit_code says how.
    SIGNALED = "SIGNALED"  # The process was ended by a signal; signal_name says which.
    TIMED_OUT = "TIMED_OUT"  # Killed by the packet at timeout_s.
    KILLED = "KILLED"  # Killed on a stdin signal, an output cap or a session close.
    COMPLETED = "COMPLETED"  # An open or a file transfer finished.


class ProcessSignal(Enum):
    """A platform-neutral signal a SessionStdin delivers; the packet maps it to the OS's own."""

    INTERRUPT = "INTERRUPT"
    TERMINATE = "TERMINATE"
    KILL = "KILL"


class SessionRequestKind(Enum):
    """Which session request a SessionExit ends, so its field rules are decidable on the model."""

    OPEN = "OPEN"
    EXEC = "EXEC"
    PUT_FILE = "PUT_FILE"
    GET_FILE = "GET_FILE"


# A reason field, as the catalogue conventions fix it: always named `reason`, always bounded by
# the shared MAX_REASON_CHARS, so the Pheromone Trail (the append-only audit log) records why.
_Reason = Annotated[str, Field(max_length=MAX_REASON_CHARS)]


class EnvVar(BaseModel):
    """One environment variable overlaid on the process of a single exec.

    The value is a possible secret (a token, a credential): logs and the Pheromone Trail record
    names only, and the field is left out of the model's repr.
    """

    model_config = VALUE_MODEL_CONFIG

    name: str = Field(
        max_length=MAX_ENV_NAME_CHARS,
        pattern=ENV_NAME_PATTERN,
        description="The variable's name: a portable identifier, so nothing a shell could parse.",
    )
    value: str = Field(
        max_length=MAX_ENV_VALUE_CHARS,
        repr=False,
        description="The value; a possible secret, redacted from logs and the trail, which "
        "record names only.",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Messages
# ──────────────────────────────────────────────────────────────────────────────


class SessionOpen(WaggleMessage):
    """Open the terminal session for one open lease on the device (session.open, a request).

    Exec, file and close traffic may follow; success is a SessionExit with outcome COMPLETED.
    """

    lease_id: LeaseIdField = Field(
        description="The open lease whose scratch directory and process list this session "
        "works inside; the session's identity from here on. A lease the packet does not hold "
        "open draws hive.session.lease_not_open."
    )
    reason: _Reason = Field(description="Why the Warden opens the session now.")


class SessionExec(WaggleMessage):
    """Run one program on the device inside the lease (session.exec, a request).

    Output streams back as SessionOutput events and the run ends with one SessionExit, both
    correlated to this request's envelope id, which is also the exec's identity for SessionStdin.
    """

    lease_id: LeaseIdField = Field(description="The session to run in.")
    argv: tuple[Annotated[str, Field(max_length=MAX_ARGV_ITEM_CHARS)], ...] = Field(
        min_length=MIN_ARGV_ITEMS,
        max_length=MAX_ARGV_ITEMS,
        description="The program and its arguments, argv[0] the executable; never a shell "
        "string. At most MAX_ARGV_TOTAL_CHARS characters in total.",
    )
    cwd: Annotated[str, Field(max_length=MAX_PATH_CHARS)] | None = Field(
        description="Working directory; None means the lease's scratch directory. The packet "
        "refuses a path outside scratch or the lease's allowed roots."
    )
    env: tuple[EnvVar, ...] = Field(
        max_length=MAX_ENV_VARS,
        description="Variables overlaid for this process only; names unique.",
    )
    timeout_s: float = Field(
        gt=0,
        description="Wall-clock seconds before the packet kills the process and reports TIMED_OUT.",
    )
    has_stdin: bool = Field(
        default=False,
        description="True when the Warden will stream SessionStdin and close it with final; "
        "false closes stdin at start.",
    )
    max_output_bytes: int = Field(
        default=DEFAULT_MAX_OUTPUT_BYTES,
        ge=MIN_OUTPUT_BYTES,
        description="Cap on total stdout plus stderr bytes; past it the packet kills the process "
        "(KILLED), so a looping device never streams unbounded output into a Warden.",
    )
    task_id: TaskIdField | None = Field(
        description="The task this command serves; None for the Warden's own diagnostics or "
        "Patrol probes (its scheduled read-only review of an idle Cell)."
    )
    worker_id: WorkerIdField | None = Field(description="The sub-bee the Warden runs it for.")

    @field_validator("argv")
    @classmethod
    def _argv_total_within_limit(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject a command line whose arguments together exceed MAX_ARGV_TOTAL_CHARS."""
        # The per-item and per-count bounds alone allow half a megabyte of command line; the
        # total keeps an exec request well inside one frame once JSON-escaped.
        total = sum(len(item) for item in value)
        if total > MAX_ARGV_TOTAL_CHARS:
            raise ValueError(
                f"SessionExec argv totals {total} characters, more than the "
                f"{MAX_ARGV_TOTAL_CHARS} allowed."
            )
        return value

    @field_validator("env")
    @classmethod
    def _env_names_unique(cls, value: tuple[EnvVar, ...]) -> tuple[EnvVar, ...]:
        """Reject a variable overlaid twice."""
        # Two values for one name would leave the packet to pick a winner; refusing the overlay
        # keeps the process's environment exactly what the Warden asked for.
        names = [variable.name for variable in value]
        if len(set(names)) != len(names):
            raise ValueError("SessionExec env names must be unique.")
        return value


class SessionStdin(WaggleMessage):
    """Feed bytes or a signal to a running exec's standard input (session.stdin, an event).

    Exactly as a terminal would; a signal is how one exec is cancelled without closing the
    session. The bytes are a possible secret: redacted from logs and the trail, never in a repr.
    """

    lease_id: LeaseIdField = Field(description="The session the exec belongs to.")
    exec_id: MessageIdField = Field(
        description="The envelope id of the SessionExec this input targets; also the envelope's "
        "correlation_id."
    )
    chunk: bytes = Field(
        max_length=MAX_CHUNK_BYTES,
        repr=False,
        description="Raw stdin bytes; a possible secret, redacted from logs and the trail. "
        "Empty when signal is set.",
    )
    offset: int = Field(ge=0, description="Byte offset within the stdin stream.")
    final: bool = Field(default=False, description="True closes stdin after this chunk.")
    signal: ProcessSignal | None = Field(
        description="Deliver a signal instead of bytes; when set, chunk must be empty."
    )

    @model_validator(mode="after")
    def _signal_carries_no_bytes(self) -> SessionStdin:
        """Reject a signal that also carries stdin bytes."""
        # Bytes and a signal in one message would leave their order undefined (write first, or
        # interrupt first?); one message does one thing, so the packet never has to guess.
        if self.signal is not None and self.chunk:
            raise ValueError(
                f"SessionStdin with signal {self.signal.value} must carry an empty chunk, got "
                f"{len(self.chunk)} bytes."
            )
        return self


class SessionClose(WaggleMessage):
    """Close the lease's session (session.close, an event).

    The packet kills every exec still running in it (each gets a SessionExit KILLED) and stops
    accepting session traffic for that lease. The lease itself is released by
    cell.lease_released, not here.
    """

    lease_id: LeaseIdField = Field(description="The session to close.")
    reason: _Reason = Field(description="Why the session closes.")
