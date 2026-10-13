"""Run one exec for a lease: the plan, the process, its streams and its limits.

A Warden (the per-Cell supervisor) asks the Pollen Packet (the thin gateway program on a device
the Hive borrows) to run an argument list inside a lease (the Hive's temporary hold on the
device). ``plan`` is the request after it was read, resolved and checked, ``runner`` spawns the
program as the leader of a process group of its own and supervises it to the one
``session.exit``, ``streams`` sends its output and writes its stdin, and ``control`` holds the
verdict (why the gateway ended it), the signals and the watchdog that enforces the timeout and the
scratch quota.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Used by
    pollen.executors.session, which builds a plan per ``session.exec`` and tracks the runs; calls
    into waggle, pollen.lease and the platform shim only.

Key invariants:
    - A run's pid is recorded in the lease table before its first output frame.
    - Every kill goes to the whole process group, and the first reason a run is ended wins.

See Also:
    - docs/waggle/gateway-contract.md section 11 (exec semantics).

Public API:
    - ExecPlan: one resolved, checked exec.
    - ExecRun: spawn, stream, watch and report one exec.
    - EXIT_COMMAND_NOT_STARTED: the exit code of a program that could not start.
"""

from pollen.executors.run.plan import ExecPlan
from pollen.executors.run.runner import EXIT_COMMAND_NOT_STARTED, ExecRun

__all__ = ["EXIT_COMMAND_NOT_STARTED", "ExecPlan", "ExecRun"]
