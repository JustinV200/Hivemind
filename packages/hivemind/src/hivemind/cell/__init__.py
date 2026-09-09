"""Define the Cell abstraction shared by every kind of machine the Hive runs work on.

Provides Cell, CellKind (REAL for a borrowed device or VIRTUAL for a provisioned one),
CellCapabilities (platform facts plus capability flags), CellSession (a terminal session on a
Cell), TaskNeeds (what a task requires from the Cell that runs it), the three security tier enums
(AccessLevel, CombShieldLevel, HoneyClearance) and, from phase 3 step 3.10 on, everything a Real
Cell's tenancy needs: LeaseState and its transition table, RealCellLease and the small models
around it, RealCellSource (how Real Cells are inventoried and leased), Snapshotter (usually a
no-op for a Real Cell), a fake implementation of both Protocols, and this package's own error
tree. It knows what a Cell is and how a Real Cell's lease works, never how a Virtual Cell is made
(that is `hive`'s job) nor how a Real Cell is produced beyond the Hive Stand (`hivemind.cell.local`
here; `swarm` for enrolled devices, a later phase).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by hive, swarm, exoskeleton
    and royal_jelly (Layer 3) and every layer above them. Calls into hivemind.common,
    hivemind.forage (ForageCapacity, Tempo), hivemind.pheromone (CellEvent, PheromoneTrail) and
    waggle only; it never imports hivemind.guard (codingrules section 4: nothing at Layer 2 or
    below imports guard for an enum) -- a Warden builds a CapabilitySet from a lease's
    access_level and scratch_root itself, once it holds both.

Key invariants:
    - AccessLevel, CombShieldLevel and HoneyClearance mirror waggle.messages.labels's enums of
      the same names, member for member (tests/unit/cell/test_tiers.py checks it).
    - A TaskNeeds with comb_shield == CombShieldLevel.NIGHT_VEIL always has
      isolation == Isolation.REQUIRED: Night Veil is virtual-only.
    - A REAL Cell is never CombShieldLevel.NIGHT_VEIL; a VIRTUAL Cell is always AccessLevel.FULL
      (codingrules section 8.7; enforced by Cell's own validator).
    - RealCellLease.state only ever moves along hivemind.cell.lease_state.TRANSITIONS, and every
      move writes its own trail event in the same call that changes it.
    - hivemind.cell.local (the Hive Stand, phase 3 step 3.11) is the one place under this package
      that may import subprocess/asyncio.subprocess (codingrules section 4; the root pyproject.toml
      import-linter contract enforces it).

See Also:
    - .claude/codingrules.md section 4 for the layer 2 row this package occupies.
    - .claude/codingrules.md section 8.7 for the Cell model this package implements.
    - .claude/codingrules.md Appendix C, "Lease (Real Cell)" row, for the lease state machine.
    - docs/adr/0010-cells-are-real-or-virtual-terminal-first.md for why one abstraction serves
      both kinds of Cell and why the Hive Stand is the first Real Cell source.
    - .claude/roadmap.md phase 2 step 2.3a for the security enums and TaskNeeds, and phase 3
      steps 3.10-3.11 for the rest of this package.

Public API:
    - AccessLevel, CombShieldLevel, HoneyClearance: the three Cell security dimensions
      (hivemind.cell.tiers).
    - Isolation, OsFamily, TaskNeeds: what a task requires from its Cell (hivemind.cell.needs).
    - CellKind, CellCapabilities, Cell: what a Cell is and can do (hivemind.cell.models).
    - OutputStream, OutputChunk, ExitStatus, ExecEvent, ExecSpec, CellSession, CompletedCommand,
      run, DEFAULT_EXEC_TIMEOUT_S, resolve_scratch_path: a terminal session on a Cell
      (hivemind.cell.session).
    - LeaseState, TRANSITIONS, can_transition, assert_transition: a lease's state machine
      (hivemind.cell.lease_state).
    - LeaseRequest, LeaseFacts, LeaseReleaseReport, LeaseReleaser, RealCellLease, RestoreRecord:
      a Real Cell's tenancy (hivemind.cell.lease).
    - RealCellSource, CellIdentity: how Real Cells are inventoried and leased
      (hivemind.cell.source).
    - SnapshotId, Snapshotter, NoopSnapshotter, NOOP_SNAPSHOT_ID: the (usually absent) ability to
      roll a Cell back (hivemind.cell.snapshot).
    - FakeSession, FakeCellSource, FakeLeaseReleaser, Responder: an in-memory implementation of
      both Protocols (hivemind.cell.fake).
    - CellError, LeaseRefusedError, SessionClosedError, CommandTimeoutError, PathNotAllowedError,
      SnapshotUnsupportedError, InvalidLeaseTransitionError, ProbeError,
      ScratchQuotaExceededError: this package's error tree (hivemind.cell.errors).
    - hivemind.cell.local: the Hive Stand, the first Real Cell source (roadmap step 3.11): see
      its own README for HiveStandConfig, HiveStandSource, LocalProcessSession and
      HiveStandLeaseReleaser, not re-exported here (codingrules section 4: `hivemind.cell.local`
      is the one place under this package allowed to import `subprocess`).
"""

from hivemind.cell.errors import (
    CellError,
    CommandTimeoutError,
    InvalidLeaseTransitionError,
    LeaseRefusedError,
    PathNotAllowedError,
    ProbeError,
    ScratchQuotaExceededError,
    SessionClosedError,
    SnapshotUnsupportedError,
)
from hivemind.cell.fake import FakeCellSource, FakeLeaseReleaser, FakeSession, Responder
from hivemind.cell.lease import (
    LeaseFacts,
    LeaseReleaser,
    LeaseReleaseReport,
    LeaseRequest,
    RealCellLease,
    RestoreRecord,
)
from hivemind.cell.lease_state import TRANSITIONS, LeaseState, assert_transition, can_transition
from hivemind.cell.models import Cell, CellCapabilities, CellKind
from hivemind.cell.needs import Isolation, OsFamily, TaskNeeds
from hivemind.cell.session import (
    DEFAULT_EXEC_TIMEOUT_S,
    CellSession,
    CompletedCommand,
    ExecEvent,
    ExecSpec,
    ExitStatus,
    OutputChunk,
    OutputStream,
    resolve_scratch_path,
    run,
)
from hivemind.cell.snapshot import NOOP_SNAPSHOT_ID, NoopSnapshotter, SnapshotId, Snapshotter
from hivemind.cell.source import CellIdentity, RealCellSource
from hivemind.cell.tiers import AccessLevel, CombShieldLevel, HoneyClearance

__all__ = [
    "DEFAULT_EXEC_TIMEOUT_S",
    "NOOP_SNAPSHOT_ID",
    "TRANSITIONS",
    "AccessLevel",
    "Cell",
    "CellCapabilities",
    "CellError",
    "CellIdentity",
    "CellKind",
    "CellSession",
    "CombShieldLevel",
    "CommandTimeoutError",
    "CompletedCommand",
    "ExecEvent",
    "ExecSpec",
    "ExitStatus",
    "FakeCellSource",
    "FakeLeaseReleaser",
    "FakeSession",
    "HoneyClearance",
    "InvalidLeaseTransitionError",
    "Isolation",
    "LeaseFacts",
    "LeaseRefusedError",
    "LeaseReleaseReport",
    "LeaseReleaser",
    "LeaseRequest",
    "LeaseState",
    "NoopSnapshotter",
    "OsFamily",
    "OutputChunk",
    "OutputStream",
    "PathNotAllowedError",
    "ProbeError",
    "RealCellLease",
    "RealCellSource",
    "Responder",
    "RestoreRecord",
    "ScratchQuotaExceededError",
    "SessionClosedError",
    "SnapshotId",
    "SnapshotUnsupportedError",
    "Snapshotter",
    "TaskNeeds",
    "assert_transition",
    "can_transition",
    "resolve_scratch_path",
    "run",
]
