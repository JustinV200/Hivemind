"""Define the session family's background-process control: start, stop and query one process.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance), and the
session family is terminal-over-Waggle: a Warden (the always-on supervisor of one Cell, a unit of
compute) drives a remote Real Cell (an existing device borrowed for a task and left exactly as
found) through its Pollen Packet (the thin gateway on an enrolled device). An exec runs a program
to its end and streams its output back; the two messages here are for the other kind of program,
one that must outlive the request that started it (a virtual display server, an audio daemon, a
browser: the Exoskeleton, the bundle of peripherals the Hive lends a Worker, needs them running
for the whole task). ``SessionProcess`` starts, stops or queries one such process, and
``SessionProcessState`` answers with its pid and whether it is running. The packet records every
process it starts with the lease before it answers, so a release (or the dead-man switch) kills
it and the device is left as found. A process that cannot start is a ``control.error``
``pollen.start_failed``, never a state. Added in PROTOCOL_MINOR 13 (roadmap phase 11, ADR-0051).
Every bound is a named constant in ``waggle.messages.session.commands`` or here; the number, not
the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Registered by waggle.messages.registry, which maps
    each class to its kind; built by a Warden (the request) or a Pollen Packet (the state) and
    read by the other; calls into waggle.messages.base and waggle.messages.session.commands
    (EnvVar, the command-line bounds and the two checks every command line shares) only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - Every message is frozen and forbids extras through WaggleMessage's config.
    - Every rule the spec marks (validator) is a pydantic validator on the class; every rule it
      marks (receiver rule) is deliberately absent, because the receiver enforces it.
    - A command line, a working directory, an environment and a log path travel only with START,
      and a pid only with STOP and QUERY, so one message does exactly one thing.
    - EnvVar.value never appears in a repr (a possible secret).

See Also:
    - docs/waggle/spec.md section 8.6 for the normative fields, bounds and validators.
    - waggle.messages.session.commands for ProcessSignal, EnvVar and the shared command checks.
    - waggle.messages.session.output for the SessionExit that ends the other session requests.
    - waggle.messages.registry for the kinds these classes are registered under.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import Field, field_validator, model_validator

from waggle.messages.base import (
    MAX_PATH_CHARS,
    MAX_REASON_CHARS,
    LeaseIdField,
    TaskIdField,
    WaggleMessage,
    WorkerIdField,
)
from waggle.messages.session.commands import (
    MAX_ARGV_ITEM_CHARS,
    MAX_ARGV_ITEMS,
    MAX_ENV_VARS,
    EnvVar,
    check_argv_total,
    check_env_names_unique,
)

MIN_PID = 1  # Pid 0 is the scheduler or the idle task on every platform, never a process we start.

__all__ = ["MIN_PID", "ProcessAction", "SessionProcess", "SessionProcessState"]


# ──────────────────────────────────────────────────────────────────────────────
# Enum and shared field types
# ──────────────────────────────────────────────────────────────────────────────


class ProcessAction(Enum):
    """What a SessionProcess asks of a background process; one message does exactly one."""

    START = "START"  # Launch a new process in the lease, detached from this request.
    STOP = "STOP"  # End the lease's process with this pid, politely and then by force.
    QUERY = "QUERY"  # Report whether the lease's process with this pid is still running.


# A reason field, as the catalogue conventions fix it: always named `reason`, always bounded by
# the shared MAX_REASON_CHARS, so the Pheromone Trail (the append-only audit log) records why.
_Reason = Annotated[str, Field(max_length=MAX_REASON_CHARS)]
# A command line as SessionExec bounds it, except that it may be empty: only START carries one,
# so the non-empty rule is the START validator's, not the field's.
_Argv = Annotated[
    tuple[Annotated[str, Field(max_length=MAX_ARGV_ITEM_CHARS)], ...],
    Field(max_length=MAX_ARGV_ITEMS),
]
# A path on the device (a working directory or a log file), bounded like every path on the wire.
_Path = Annotated[str, Field(max_length=MAX_PATH_CHARS)]
# A process id: positive, so a missing pid is None and never 0.
_Pid = Annotated[int, Field(ge=MIN_PID)]


# ──────────────────────────────────────────────────────────────────────────────
# Messages
# ──────────────────────────────────────────────────────────────────────────────


class SessionProcess(WaggleMessage):
    """Start, stop or query one background process in the lease (session.process, a request).

    The answer is one correlated SessionProcessState, or a control.error (pollen.start_failed
    for a START that could not launch, hive.session.lease_not_open for an unknown lease).
    """

    lease_id: LeaseIdField = Field(
        description="The open lease the process belongs to; its scratch directory is the "
        "default working directory and the lease's release kills the process."
    )
    action: ProcessAction = Field(description="Start a process, stop one, or ask about one.")
    argv: _Argv = Field(
        description="The program and its arguments, as SessionExec.argv (never a shell string, "
        "at most MAX_ARGV_TOTAL_CHARS characters in total). Non-empty exactly when action is "
        "START (validator)."
    )
    cwd: _Path | None = Field(
        description="Working directory for a START; None means the lease's scratch directory. "
        "The packet refuses a path outside scratch or the lease's allowed roots. None for STOP "
        "and QUERY (validator)."
    )
    env: tuple[EnvVar, ...] = Field(
        max_length=MAX_ENV_VARS,
        description="Variables overlaid on the new process only; names unique. Empty for STOP "
        "and QUERY (validator).",
    )
    log_path: _Path | None = Field(
        description="Where the packet redirects the process's stdout and stderr, absolute or "
        "relative to scratch; None discards them. None for STOP and QUERY (validator)."
    )
    pid: _Pid | None = Field(
        description="The process to stop or query, as a SessionProcessState reported it. "
        "Required for STOP and QUERY, None for START (validator). A pid the lease did not "
        "start draws a control.error pollen.not_found (receiver rule), so one lease cannot "
        "signal another's process or the device's own; one it started that has exited is "
        "answered normally."
    )
    task_id: TaskIdField | None = Field(description="The task this process serves.")
    worker_id: WorkerIdField | None = Field(
        description="The sub-bee on whose behalf it is started, stopped or queried."
    )

    @field_validator("argv")
    @classmethod
    def _argv_total_within_limit(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject a command line whose arguments together exceed MAX_ARGV_TOTAL_CHARS."""
        return check_argv_total("SessionProcess", value)

    @field_validator("env")
    @classmethod
    def _env_names_unique(cls, value: tuple[EnvVar, ...]) -> tuple[EnvVar, ...]:
        """Reject a variable overlaid twice."""
        return check_env_names_unique("SessionProcess", value)

    @model_validator(mode="after")
    def _start_fields_match_action(self) -> SessionProcess:
        """Require a command line for START, and nothing that describes a launch otherwise."""
        # A START with no program launches nothing, and a STOP or QUERY that carries a command
        # line, directory, environment or log path would read as a restart in disguise; both
        # directions are checked so one message does exactly one thing.
        starting = self.action is ProcessAction.START
        if starting != bool(self.argv):
            raise ValueError(
                f"SessionProcess argv is required exactly when action is START, got action "
                f"{self.action.value} with {len(self.argv)} argument(s)."
            )
        launch_fields = (self.cwd is not None, bool(self.env), self.log_path is not None)
        if not starting and any(launch_fields):
            raise ValueError(
                f"SessionProcess cwd, env and log_path are for START only, got action "
                f"{self.action.value}."
            )
        return self

    @model_validator(mode="after")
    def _pid_matches_action(self) -> SessionProcess:
        """Require a pid for STOP and QUERY, and none for START."""
        # START creates the process, so it has no pid yet; STOP and QUERY act on an existing
        # one and cannot work without it. The packet never searches by program name.
        starting = self.action is ProcessAction.START
        if (self.pid is None) != starting:
            raise ValueError(
                f"SessionProcess pid is required exactly when action is STOP or QUERY, got "
                f"action {self.action.value} with pid {self.pid}."
            )
        return self


class SessionProcessState(WaggleMessage):
    """Report one background process's state (session.process_state, a reply).

    The answer to a SessionProcess: the pid of what was started, stopped or queried, and
    whether it is running now. Correlated to the request it answers.
    """

    lease_id: LeaseIdField = Field(description="The lease the process belongs to.")
    action: ProcessAction = Field(description="Echo of the request's action.")
    pid: _Pid = Field(
        description="The process's id on the device: the new pid after START, the request's "
        "pid after STOP or QUERY."
    )
    argv: _Argv = Field(
        description="The command line as started, bounded like SessionProcess.argv; non-empty "
        "exactly when action is START, so a Warden sees what ran (validator)."
    )
    log_path: _Path | None = Field(
        description="The resolved absolute path of the redirected output; set only on START, "
        "and None there when the request named no log (validator)."
    )
    is_running: bool = Field(
        description="Whether the process is running now; after START, false means it exited "
        "before the packet could answer, and the log says why."
    )
    was_running: bool | None = Field(
        description="Whether the process was running when the STOP arrived, so a stop of an "
        "already-dead process is told apart from a kill; set exactly when action is STOP "
        "(validator)."
    )
    reason: _Reason = Field(description="What happened, in words, especially why a stop failed.")

    @field_validator("argv")
    @classmethod
    def _argv_total_within_limit(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject a command line whose arguments together exceed MAX_ARGV_TOTAL_CHARS."""
        return check_argv_total("SessionProcessState", value)

    @model_validator(mode="after")
    def _start_fields_match_action(self) -> SessionProcessState:
        """Allow the command line and the log path only after a START, and require the former."""
        # Only a START has something to echo: the argv it ran and the log it redirected to. A
        # STOP or QUERY reply that repeated them would invite a reader to trust a stale launch.
        starting = self.action is ProcessAction.START
        if starting != bool(self.argv):
            raise ValueError(
                f"SessionProcessState argv is set exactly when action is START, got action "
                f"{self.action.value} with {len(self.argv)} argument(s)."
            )
        if not starting and self.log_path is not None:
            raise ValueError(
                f"SessionProcessState log_path is for START only, got action {self.action.value}."
            )
        return self

    @model_validator(mode="after")
    def _was_running_exactly_for_stop(self) -> SessionProcessState:
        """Require was_running for STOP and forbid it otherwise."""
        # was_running describes the moment before the stop; for a START or a QUERY there is no
        # such moment, and is_running already says everything.
        stopping = self.action is ProcessAction.STOP
        if (self.was_running is not None) != stopping:
            raise ValueError(
                f"SessionProcessState was_running is set exactly when action is STOP, got "
                f"action {self.action.value} with was_running {self.was_running}."
            )
        return self
