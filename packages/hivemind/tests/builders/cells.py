"""Build valid hivemind.cell test data without repeating pydantic boilerplate per test.

Every builder here returns a real, validated model (codingrules 14.5), with sensible defaults for
every field a test does not care about: `make_capabilities` for a terminal-only Linux Cell,
`make_cell` for a Cell whose `access_level` already matches the `kind` it is given (SCRATCH for a
REAL Cell, FULL for a VIRTUAL one, so `Cell`'s own kind/access validator never needs a caller to
remember the constraint), `make_identity` for a `CellIdentity`, and `make_lease_request` for a
`LeaseRequest`.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by every test under
    packages/hivemind/tests/unit/cell and the two Cell contract suites.

Key invariants:
    - Every builder that mints an id or a timestamp takes an optional `clock: Clock` (default a
      fresh FakeClock) so a test run is deterministic.
    - make_cell's default access_level always satisfies Cell's kind/access validator for the
      `kind` it is building, so `make_cell(kind=CellKind.VIRTUAL)` needs no further overrides.

See Also:
    - .claude/codingrules.md section 14.5 for the builders-over-fixtures rule this module follows.
    - hivemind.cell.models for Cell, CellCapabilities and CellKind.
    - hivemind.cell.source for CellIdentity.
    - hivemind.cell.lease for LeaseRequest.
    - packages/hivemind/tests/builders/forage.py for make_capacity, reused here for Cell.capacity.
"""

from __future__ import annotations

from builders.forage import make_capacity

from hivemind.cell.lease import LeaseRequest
from hivemind.cell.models import Cell, CellCapabilities, CellKind
from hivemind.cell.needs import OsFamily
from hivemind.cell.source import CellIdentity
from hivemind.cell.tiers import AccessLevel, CombShieldLevel
from waggle.clock import Clock, FakeClock
from waggle.ids import HiveId, NodeId, new_cell_id, new_hive_id, new_node_id, new_warden_id

__all__ = ["make_capabilities", "make_cell", "make_identity", "make_lease_request"]


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
