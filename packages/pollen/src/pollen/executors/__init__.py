"""Do what an enrolled device can be asked to do: run programs, move files, start processes, look.

The Pollen Packet (the thin gateway program on a device the Hive borrows) never decides what to do;
a Warden (the per-Cell supervisor) or the Queen (the Hive's central orchestrator) asks, and an
executor carries it out on the device inside a lease (the Hive's temporary hold on it). Each
executor serves some payload types in two steps (``DeviceExecutor``): ``prepare`` reads the request
and resolves its paths and names what it needs, the dispatcher checks that against the node's
granted capabilities, and only then does the work run. ``session`` serves open, exec and stdin
(``run`` holds the exec itself), ``files`` the put, get and delete of a file (``transfers`` holds
the chunked upload), ``process`` the background processes a lease starts, and ``observe`` the
read-only ``swarm.observe``; ``paths`` resolves every path before it is judged and ``info`` probes
the device for the enrolment request and the capacity reports.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Consulted by
    pollen.agent.dispatch and told to stop a lease's work by pollen.agent.leases; calls into
    pollen.lease, the platform shim and waggle only.

Key invariants:
    - ``prepare`` has no side effect, and no executor runs before its needs are granted.
    - Every program runs in its own process group, recorded in the lease table before its first
      output frame; a run belongs to its lease and not to the connection.
    - Nothing outside scratch is changed that was not recorded first.

See Also:
    - docs/waggle/gateway-contract.md sections 9 to 12.
    - .claude/codingrules.md section 3 for where this sub-package sits under pollen.

Public API:
    - DeviceExecutor, Prepared, Need, ExecutorDeps, Reporter, LeaseStopper, TaskScope: the seam.
    - SessionExecutor, FileExecutor, ProcessExecutor, ObserveExecutor: the four executors.
    - error_payload, payload_of, request_id_of: helpers the dispatcher and services share.
"""

from pollen.executors.base import (
    DeviceExecutor,
    ExecutorDeps,
    LeaseStopper,
    Need,
    Prepared,
    Reporter,
    TaskScope,
    error_payload,
    payload_of,
    request_id_of,
)
from pollen.executors.files import FileExecutor
from pollen.executors.observe import ObserveExecutor
from pollen.executors.process import ProcessExecutor
from pollen.executors.session import SessionExecutor

__all__ = [
    "DeviceExecutor",
    "ExecutorDeps",
    "FileExecutor",
    "LeaseStopper",
    "Need",
    "ObserveExecutor",
    "Prepared",
    "ProcessExecutor",
    "Reporter",
    "SessionExecutor",
    "TaskScope",
    "error_payload",
    "payload_of",
    "request_id_of",
]
