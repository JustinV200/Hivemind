"""Define ExecPlan, one resolved and checked exec: everything a run needs and nothing to look up.

A Warden (the per-Cell supervisor) asks the Pollen Packet (the thin gateway program on a device
the Hive borrows) to run an argument list inside a lease (the Hive's temporary hold on the
device). By the time the request reaches the run machinery it has been read, its working
directory resolved against the lease's scratch directory and its capabilities checked, so what
is left is a plain value: the program, where and with what environment, the limits it runs under
and who is waiting for its output. Nothing in it is a path the caller still has to resolve or a
permission it still has to ask for.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Built by
    pollen.executors.session from a ``session.exec`` request; read by the other modules of
    pollen.executors.run; calls into waggle id types and the standard library only.

Key invariants:
    - Frozen: a run never changes its plan.
    - ``cwd`` and ``scratch_root`` are resolved absolute paths.
    - ``env`` holds the request's variables only; the packet's own environment is added at spawn.

See Also:
    - docs/waggle/gateway-contract.md section 11 for the exec semantics a plan carries out.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from waggle.ids import LeaseId, MessageId

__all__ = ["ExecPlan"]


@dataclass(frozen=True, slots=True)
class ExecPlan:
    """One resolved, checked exec."""

    request_id: MessageId  # The exec's identity: also the correlation of every frame it sends.
    lease_id: LeaseId  # The lease the exec belongs to.
    requester: str  # Who asked: the recipient of its output and exit frames.
    argv: tuple[str, ...]  # The program and arguments; never a shell string.
    cwd: Path  # The resolved working directory.
    env: Mapping[str, str]  # The request's variables, overlaid on the packet's environment.
    timeout_s: float  # Wall-clock seconds before TIMED_OUT.
    has_stdin: bool  # Whether the Warden will stream stdin (false closes it at start).
    max_output_bytes: int  # Stdout plus stderr cap before the group is killed.
    scratch_root: Path  # The lease's scratch directory, measured against the quota.
    quota_bytes: int  # The scratch quota.
