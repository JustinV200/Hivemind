"""Hold the device-side half of a Real Cell lease: scratch, started processes, restore and silence.

A Real Cell is an existing device the Hive borrows for a task and leaves exactly as found. A lease
(the Hive's temporary hold on it) has a scratch directory of its own under a byte quota, a list of
the processes it started, and a record of every path it changed outside scratch with what that
path held before. The Pollen Packet (the thin gateway program on the device) keeps all of it in a
persisted lease table, written before anything that depends on it happens, so a crash leaks
neither scratch nor a process. ``lease`` is the record and its state machine, ``table`` the
persisted set, ``restore`` the before-change records and the release-time undo, ``quota`` the
scratch measurement and ``deadman`` the clock of how long the Queen (the Hive's central
orchestrator) has been silent.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Used by the executors, which
    record processes and changed paths, and by pollen.agent, which opens and releases leases and
    runs the dead-man switch; calls into waggle, pollen.state and the standard library only.

Key invariants:
    - Nothing is changed outside scratch that was not recorded first, and nothing a lease started
      survives its release.
    - The lease table is what the disk holds: a change that did not reach the disk is invisible.

See Also:
    - .claude/codingrules.md Appendix C (Device lease) for the state machine.
    - docs/waggle/gateway-contract.md sections 9.1, 12 and 13.

Public API:
    - DeviceLease, LeaseState, ProcessRecord, ProcessKind, RestoreRecord, RestoreKind: the record.
    - transition, TRANSITIONS: the lease state machine's single table.
    - LeaseTable: the persisted set of leases.
    - record_before_change, replay_restores, remove_scratch: record and undo changes.
    - directory_size_bytes, check_room, DEFAULT_SCRATCH_QUOTA_BYTES: the scratch quota.
    - DeadManSwitch: how long the Queen has been silent.
"""

from pollen.lease.deadman import DeadManSwitch
from pollen.lease.lease import (
    TRANSITIONS,
    DeviceLease,
    LeaseState,
    ProcessKind,
    ProcessRecord,
    RestoreKind,
    RestoreRecord,
    transition,
)
from pollen.lease.quota import (
    DEFAULT_SCRATCH_QUOTA_BYTES,
    QUOTA_SAMPLE_INTERVAL_S,
    check_room,
    directory_size_bytes,
)
from pollen.lease.restore import record_before_change, remove_scratch, replay_restores
from pollen.lease.table import LeaseTable

__all__ = [
    "DEFAULT_SCRATCH_QUOTA_BYTES",
    "QUOTA_SAMPLE_INTERVAL_S",
    "TRANSITIONS",
    "DeadManSwitch",
    "DeviceLease",
    "LeaseState",
    "LeaseTable",
    "ProcessKind",
    "ProcessRecord",
    "RestoreKind",
    "RestoreRecord",
    "check_room",
    "directory_size_bytes",
    "record_before_change",
    "remove_scratch",
    "replay_restores",
    "transition",
]
