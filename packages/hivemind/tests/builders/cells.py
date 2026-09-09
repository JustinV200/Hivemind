"""Build valid hivemind.cell test data without repeating pydantic boilerplate per test.

Every builder here returns a real, validated model (codingrules 14.5), with sensible defaults for
every field a test does not care about: `make_capabilities` for a terminal-only Linux Cell,
`make_cell` for a Cell whose `access_level` already matches the `kind` it is given (SCRATCH for a
REAL Cell, FULL for a VIRTUAL one, so `Cell`'s own kind/access validator never needs a caller to
remember the constraint), `make_identity` for a `CellIdentity`, `make_lease_request` for a
`LeaseRequest`, `make_hive_stand_config` for a `hivemind.cell.local.HiveStandConfig` pointed at a
test's own `tmp_path`, and `make_real_cell_lease` for a REQUESTED (not yet opened)
`RealCellLease` a test can `await .open()` itself.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by every test under
    packages/hivemind/tests/unit/cell (including tests/unit/cell/local) and the two Cell contract
    suites.

Key invariants:
    - Every builder that mints an id or a timestamp takes an optional `clock: Clock` (default a
      fresh FakeClock) so a test run is deterministic.
    - make_cell's default access_level always satisfies Cell's kind/access validator for the
      `kind` it is building, so `make_cell(kind=CellKind.VIRTUAL)` needs no further overrides.
    - make_real_cell_lease returns a lease in LeaseState.REQUESTED; a test calls `await
      lease.open()` itself, matching how a RealCellSource's own `lease()` behaves.

See Also:
    - .claude/codingrules.md section 14.5 for the builders-over-fixtures rule this module follows.
    - hivemind.cell.models for Cell, CellCapabilities and CellKind.
    - hivemind.cell.source for CellIdentity.
    - hivemind.cell.lease for LeaseRequest and RealCellLease.
    - hivemind.cell.local.config for HiveStandConfig, the Hive Stand's own resolved settings.
    - packages/hivemind/tests/builders/forage.py for make_capacity, reused here for Cell.capacity.
"""

from __future__ import annotations

from pathlib import Path

from builders.forage import make_capacity

from hivemind.cell.fake import FakeLeaseReleaser
from hivemind.cell.lease import LeaseFacts, LeaseReleaser, LeaseRequest, RealCellLease
from hivemind.cell.local.config import HiveStandConfig
from hivemind.cell.models import Cell, CellCapabilities, CellKind
from hivemind.cell.needs import OsFamily
from hivemind.cell.source import CellIdentity
from hivemind.cell.tiers import AccessLevel, CombShieldLevel
from hivemind.pheromone import PheromoneTrail
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from waggle.clock import Clock, FakeClock
from waggle.ids import (
    HiveId,
    NodeId,
    new_cell_id,
    new_hive_id,
    new_lease_id,
    new_node_id,
    new_warden_id,
)

__all__ = [
    "make_capabilities",
    "make_cell",
    "make_hive_stand_config",
    "make_identity",
    "make_lease_request",
    "make_real_cell_lease",
]


def make_capabilities(**overrides: object) -> CellCapabilities:
    """Build a valid CellCapabilities: a terminal-only Linux machine with nothing attached.

    Args:
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated CellCapabilities.
    """
    fields: dict[str, object] = {
        "os": OsFamily.LINUX,
        "arch": "x86_64",
        "distribution": "Ubuntu 24.04 LTS",
        "shell": "/bin/bash",
        "package_manager": "apt",
        "python_version": "3.12.7",
        "has_display": False,
        "has_audio": False,
        "has_browser": False,
        "can_start_display": False,
        "can_host_model": False,
        "network_scopes": (),
    }
    fields.update(overrides)
    return CellCapabilities(**fields)


def make_cell(
    kind: CellKind = CellKind.REAL, clock: Clock | None = None, **overrides: object
) -> Cell:
    """Build a valid Cell of `kind`, with an access_level that already satisfies its validator.

    Args:
        kind: REAL or VIRTUAL; REAL by default.
        clock: Source of the minted id; a fresh FakeClock when omitted.
        **overrides: Field values that replace the defaults below, including `kind` itself.

    Returns:
        A validated Cell.
    """
    active_clock = clock if clock is not None else FakeClock()
    # A VIRTUAL Cell must be FULL access (Cell's own validator); SCRATCH is a plausible default
    # for a REAL one, matching a device enrolled at less than FULL.
    default_access = AccessLevel.FULL if kind is CellKind.VIRTUAL else AccessLevel.SCRATCH
    fields: dict[str, object] = {
        "id": new_cell_id(active_clock),
        "kind": kind,
        "name": "test-cell",
        "source": "test-source",
        "capabilities": make_capabilities(),
        "capacity": make_capacity(),
        "access_level": default_access,
        "comb_shield": CombShieldLevel.MEADOW,
    }
    fields.update(overrides)
    return Cell(**fields)


def make_identity(
    clock: Clock | None = None,
    *,
    hive_id: HiveId | None = None,
    node_id: NodeId | None = None,
    actor: str = "system",
) -> CellIdentity:
    """Build a valid CellIdentity: a fresh Hive and node, acting as "system" by default.

    A plain dataclass rather than a pydantic model, so its fields are named parameters here
    instead of a `**overrides` dict (mypy cannot check a dataclass constructor against an
    unpacked `dict[str, object]` the way the pydantic plugin lets it for the other builders).

    Args:
        clock: Source of every minted id; a fresh FakeClock when omitted.
        hive_id: The Hive id; a freshly minted one when omitted.
        node_id: The node id; a freshly minted one when omitted.
        actor: Who this identity acts as; "system" by default.

    Returns:
        A CellIdentity.
    """
    active_clock = clock if clock is not None else FakeClock()
    return CellIdentity(
        hive_id=hive_id if hive_id is not None else new_hive_id(active_clock),
        node_id=node_id if node_id is not None else new_node_id(active_clock),
        actor=actor,
    )


def make_lease_request(clock: Clock | None = None, **overrides: object) -> LeaseRequest:
    """Build a valid LeaseRequest for a fresh Cell and Warden, at SCRATCH access.

    Args:
        clock: Source of every minted id; a fresh FakeClock when omitted.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated LeaseRequest.
    """
    active_clock = clock if clock is not None else FakeClock()
    fields: dict[str, object] = {
        "cell_id": new_cell_id(active_clock),
        "holder": new_warden_id(active_clock),
        "task_id": None,
        "access_level": AccessLevel.SCRATCH,
        "allowed_paths": (),
    }
    fields.update(overrides)
    return LeaseRequest(**fields)


def make_hive_stand_config(scratch_root: Path, **overrides: object) -> HiveStandConfig:
    """Build a valid HiveStandConfig rooted at `scratch_root`, enabled, at SCRATCH access.

    Args:
        scratch_root: An absolute directory (typically a test's own `tmp_path`) to hold every
            lease's own scratch subdirectory.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated HiveStandConfig.
    """
    fields: dict[str, object] = {
        "enabled": True,
        "scratch_root": scratch_root,
        "scratch_quota_mb": 4096,
        # 0 by default: a test's own tmp_path should never fail the reserve check regardless of
        # how little free disk the test machine happens to have.
        "disk_reserve_mb": 0,
        "max_sub_bees": None,
        "cores": None,
        "memory_bytes": None,
        "access_level": AccessLevel.SCRATCH,
    }
    fields.update(overrides)
    return HiveStandConfig(**fields)


def make_real_cell_lease(
    scratch_root: Path,
    clock: Clock | None = None,
    releaser: LeaseReleaser | None = None,
    trail: PheromoneTrail | None = None,
    allowed_paths: tuple[Path, ...] = (),
) -> RealCellLease:
    """Build a REQUESTED RealCellLease over `scratch_root`; the caller awaits `.open()` itself.

    Mirrors `RealCellSource.lease()`'s own construction (a fresh `LeaseFacts` plus an injected
    `LeaseReleaser`) without going through a source, for a test that only needs the lease itself
    (a `CellSession` implementation's own unit tests, for instance). `LeaseFacts` is a plain
    dataclass, not a pydantic model, so mypy checks a keyword splat against its exact field types
    field by field; this builder fixes SCRATCH access and MEADOW shield rather than accepting a
    `**overrides` dict, since neither has had a test that needs to vary them yet.

    Args:
        scratch_root: This lease's own scratch directory; a test's own `tmp_path` typically.
        clock: Source of every minted id; a fresh FakeClock when omitted.
        releaser: What `release()` delegates to; a fresh `FakeLeaseReleaser` when omitted.
        trail: Where cell.leased/cell.released events land; a fresh in-memory trail when omitted.
        allowed_paths: Paths outside scratch this lease may touch; none by default.

    Returns:
        A RealCellLease in LeaseState.REQUESTED.
    """
    active_clock = clock if clock is not None else FakeClock()
    facts = LeaseFacts(
        id=new_lease_id(active_clock),
        cell_id=new_cell_id(active_clock),
        holder=new_warden_id(active_clock),
        task_id=None,
        scratch_root=scratch_root,
        access_level=AccessLevel.SCRATCH,
        comb_shield=CombShieldLevel.MEADOW,
        allowed_paths=allowed_paths,
    )
    return RealCellLease(
        facts,
        trail=trail if trail is not None else MemoryPheromoneTrail(active_clock),
        clock=active_clock,
        identity=make_identity(clock=active_clock),
        releaser=releaser if releaser is not None else FakeLeaseReleaser(),
    )
