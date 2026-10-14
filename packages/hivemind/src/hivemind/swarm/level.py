"""Define the runtime ladder's transition table: which rung a device may move to next.

A device's Pollen Packet (the thin gateway on it) is the floor of a three-rung ladder
(ADR-0052). `GATEWAY` (Level 0) is the packet alone, with the device's Warden (the always-on
supervisor of one Cell, a unit of compute) running on the Hive Stand (the machine the Queen, the
central orchestrator, runs on). `COLONIZED` (Level 1) puts the Warden and its sub-bees on the
device. `NUC` (Level 2) adds a model server there too, so the device keeps working with no link
to the Hive Stand. The Warden is always the first bee to move, so the ladder is climbed and
descended one rung at a time and never skipped: `GATEWAY` to `NUC` would put a model server on a
device with no Warden to supervise it. This module reuses the wire enum
`waggle.messages.swarm.RuntimeLevel`, member for member, and holds the one table that decides
whether a rung change is legal, and `decide_colonize`, the pure decision to climb the first rung
(roadmap 11.6a): is there a reason to move a device's Warden onto it (the operator asked, or the
Hive Stand needs relief, or a task must survive short outages), and can this device carry one (it
reports Python 3.12 or newer, its access level lets the Hive work on it, and its Forage covers a
Warden and one sub-bee). It only decides; the Queen's swarm gate acts on the answer, and the
decision to climb the second rung is `decide_nuc`, in `hivemind.swarm.nuc`.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down). Read by the swarm registry, which
    refuses an illegal rung change before writing it, and by the Queen's swarm gate when it decides
    to migrate a Warden. Calls into hivemind.cell, hivemind.forage, hivemind.swarm.errors and
    waggle.messages.swarm only.

Key invariants:
    - The edges are exactly `GATEWAY <-> COLONIZED <-> NUC`; `GATEWAY <-> NUC` is never an edge.
    - A rung up needs a decision and the Guard's `NUC_PROMOTION` point; `COLONIZED -> GATEWAY`
      happens only when the on-device Warden is idle in `WATCH`; `NUC -> COLONIZED` is local to
      the device's Warden. This module decides which edges exist, not who may take them.
    - Every edge, up or down, is a `swarm.promoted` or `swarm.demoted` trail event with
      `from_level` and `to_level`, written in the same transaction as the change.
    - `decide_colonize` is pure: the same facts give the same decision, nothing is read or written,
      and a refusal is one stable word (never an address, a path or a figure).

See Also:
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for the ladder and why one rung at a time.
    - .claude/codingrules.md Appendix C, "Runtime ladder" row.
    - hivemind.swarm.node_state for the other machine on a node, its reachability.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from hivemind.cell import AccessLevel
from hivemind.forage import ForageCapacity, RoleFootprint
from hivemind.swarm.errors import LadderError
from waggle.messages.swarm import RuntimeLevel

__all__ = [
    "MIN_PYTHON",
    "TRANSITIONS",
    "ColonizeDecision",
    "ColonizeFacts",
    "ColonizeReason",
    "RuntimeLevel",
    "assert_move",
    "can_move",
    "colonize_reason",
    "decide_colonize",
    "is_promotion",
]

MIN_PYTHON = (3, 12)  # The oldest Python the on-device Warden's runtime installs under (ADR-0052).
# The access levels the Hive may run a Warden under: READ_ONLY lets it observe but never run its
# sub-bees' work, so a Warden there has nothing to supervise.
_COLONIZABLE_ACCESS = frozenset({AccessLevel.SCRATCH, AccessLevel.FULL})
_PYTHON_PREFIX = re.compile(r"^(\d+)\.(\d+)")  # "3.12.4", "3.13.0rc1": the leading major.minor.
_BEES_ON_DEVICE = 2  # A Warden and one sub-bee: what the device's Forage must cover.

# The single transition table (codingrules section 9): one entry per rung, each edge commented
# with what causes it. GATEWAY and NUC are absent from each other's sets on purpose.
TRANSITIONS: Mapping[RuntimeLevel, frozenset[RuntimeLevel]] = {
    RuntimeLevel.GATEWAY: frozenset(
        {
            RuntimeLevel.COLONIZED,  # colonization: the Warden moves onto the device first
        }
    ),
    RuntimeLevel.COLONIZED: frozenset(
        {
            RuntimeLevel.NUC,  # a model server starts on the device under its Warden's ceilings
            RuntimeLevel.GATEWAY,  # withdrawal: the on-device Warden is idle in WATCH and stops
        }
    ),
    RuntimeLevel.NUC: frozenset(
        {
            RuntimeLevel.COLONIZED,  # demotion: the device's Warden stops its model server
        }
    ),
}

# Position on the ladder, lowest first: how a move is told to be a rung up or a rung down.
_RANK: Mapping[RuntimeLevel, int] = {
    RuntimeLevel.GATEWAY: 0,
    RuntimeLevel.COLONIZED: 1,
    RuntimeLevel.NUC: 2,
}


def can_move(from_level: RuntimeLevel, to_level: RuntimeLevel) -> bool:
    """Return whether TRANSITIONS allows moving from `from_level` to `to_level`.

    Args:
        from_level: The rung the device is on.
        to_level: The rung a caller wants it on.

    Returns:
        True if `to_level` is one of the edges TRANSITIONS lists for `from_level`.
    """
    return to_level in TRANSITIONS[from_level]


def is_promotion(from_level: RuntimeLevel, to_level: RuntimeLevel) -> bool:
    """Return whether moving from `from_level` to `to_level` climbs the ladder.

    Used to choose between the two trail kinds an edge is recorded as (`swarm.promoted` for a
    rung up, `swarm.demoted` for a rung down); it does not check the edge is legal.

    Args:
        from_level: The rung the device is on.
        to_level: The rung it is moving to.

    Returns:
        True when `to_level` is higher than `from_level`.
    """
    return _RANK[to_level] > _RANK[from_level]


def assert_move(
    from_level: RuntimeLevel, to_level: RuntimeLevel, device_id: str | None = None
) -> None:
    """Raise unless TRANSITIONS allows moving from `from_level` to `to_level`.

    Args:
        from_level: The rung the device is on.
        to_level: The rung a caller wants it on.
        device_id: The device, when the caller has it, folded into the error message.

    Raises:
        LadderError: The move skips a rung (GATEWAY to NUC, or back) or does not move at all.
    """
    # Every writer of a node's level goes through this single check, so a rung is never skipped
    # anywhere the table itself does not list the edge.
    if not can_move(from_level, to_level):
        raise LadderError(from_level, to_level, device_id=device_id)


class ColonizeReason(Enum):
    """Why a device's Warden should move onto it: the first reason that holds, in this order."""

    OPERATOR = "operator"  # The operator asked for it (`desired_level`, `hive swarm promote`).
    RELIEF = "relief"  # The Hive Stand's pressure is past `[swarm.ladder] relief_pressure`.
    OUTAGE = "outage"  # A task needs to survive short outages, which a Warden on the device does.


@dataclass(frozen=True, slots=True)
class ColonizeFacts:
    """Everything `decide_colonize` weighs about one device and the Hive Stand.

    Attributes:
        level: The rung the device is on.
        python_version: The Python the device reported, e.g. "3.12.4"; None when it has none.
        access_level: The access level the operator granted the device.
        capacity: The device's latest Forage capacity report.
        footprint: What one bee costs a Cell (the Drone's `[forage.roles]` footprint); a Warden is
            counted as one more of them.
        min_free_memory_bytes: `[swarm.ladder] min_free_memory_bytes`: free memory the device must
            keep beyond the bees' footprints; 0 leaves the footprints as the only test.
        desired_level: The rung the operator asked for, or None.
        hive_pressure: The Hive Stand's relief pressure, 0 to 1: the larger share of its cores or
            memory that the running sub-bees of Level 0 Wardens take (defined once, in
            `hivemind.queen.swarm_gate.rungs.pressure`).
        relief_pressure: `[swarm.ladder] relief_pressure`: the pressure past which relief is wanted.
        needs_outage_survival: Whether a task placed on this device must survive short outages.
    """

    level: RuntimeLevel
    python_version: str | None
    access_level: AccessLevel
    capacity: ForageCapacity
    footprint: RoleFootprint
    min_free_memory_bytes: int
    desired_level: RuntimeLevel | None
    hive_pressure: float
    relief_pressure: float
    needs_outage_survival: bool = False


@dataclass(frozen=True, slots=True)
class ColonizeDecision:
    """The answer of `decide_colonize`: why to climb, or one word for why not.

    Attributes:
        reason: Why the device's Warden should move onto it; None when it should not.
        refusal: A stable word saying why not; None exactly when `reason` is set.
    """

    reason: ColonizeReason | None
    refusal: str | None = None

    @property
    def should_colonize(self) -> bool:
        """Whether the device's Warden should move onto it."""
        return self.reason is not None


def decide_colonize(facts: ColonizeFacts) -> ColonizeDecision:
    """Decide whether a GATEWAY device's Warden should move onto it (pure; ADR-0052).

    The reason is weighed first, so a device nobody wants colonized is refused as `not_asked` and
    not for whatever else it lacks. Then the device must be able to carry a Warden: on the first
    rung, running Python 3.12 or newer, under an access level the Hive may work at (SCRATCH or
    FULL), with Forage that covers a Warden plus one sub-bee.

    Args:
        facts: The device, the Hive Stand's pressure and the operator's thresholds.

    Returns:
        The reason to climb, or the first refusal: `not_asked`, `not_gateway`, `python_unknown`,
        `python_too_old`, `access_too_low` or `forage_too_small`.
    """
    reason = colonize_reason(facts)
    if reason is None:
        return ColonizeDecision(None, "not_asked")
    refusal = _cannot_carry_a_warden(facts)
    return ColonizeDecision(reason) if refusal is None else ColonizeDecision(None, refusal)


def colonize_reason(facts: ColonizeFacts) -> ColonizeReason | None:
    """Return the first reason to put a Warden on the device that holds, or None.

    Public because the Queen asks it two ways: before she climbs (through `decide_colonize`) and,
    for a device she climbed herself, whether the reason still holds before she takes it back, so
    the two questions can never be answered by two different rules.

    Args:
        facts: The device, the Hive Stand's pressure and the operator's thresholds.

    Returns:
        `OPERATOR` when the wish is set, else `RELIEF` when the pressure is at or past the
        threshold, else `OUTAGE` when a task needs it; None when nothing asks for it.
    """
    # NUC at GATEWAY is the same first step: the Warden always moves first (ADR-0052).
    if facts.desired_level in (RuntimeLevel.COLONIZED, RuntimeLevel.NUC):
        return ColonizeReason.OPERATOR
    if facts.hive_pressure >= facts.relief_pressure:
        return ColonizeReason.RELIEF
    return ColonizeReason.OUTAGE if facts.needs_outage_survival else None


def _cannot_carry_a_warden(facts: ColonizeFacts) -> str | None:
    """Return the word for the first thing the device lacks, or None when it lacks nothing."""
    if facts.level is not RuntimeLevel.GATEWAY:
        return "not_gateway"  # Only the first rung climbs here; a colonized device is done.
    python = _python_ok(facts.python_version)
    if python is not None:
        return python
    if facts.access_level not in _COLONIZABLE_ACCESS:
        return "access_too_low"
    return None if _forage_covers_a_warden(facts) else "forage_too_small"


def _python_ok(version: str | None) -> str | None:
    """Return a refusal word when `version` is missing or older than `MIN_PYTHON`, else None."""
    match = _PYTHON_PREFIX.match(version) if version is not None else None
    if match is None:
        return "python_unknown"
    return None if (int(match[1]), int(match[2])) >= MIN_PYTHON else "python_too_old"


def _forage_covers_a_warden(facts: ColonizeFacts) -> bool:
    """Whether the device's free cores and memory cover a Warden plus one sub-bee."""
    host, footprint = facts.capacity.host, facts.footprint
    free_cores = host.cores * max(0.0, 1.0 - host.cpu_load)  # `cpu_load` is load per core.
    memory_needed = _BEES_ON_DEVICE * footprint.memory_bytes + facts.min_free_memory_bytes
    return (
        facts.capacity.max_sub_bees >= 1
        and free_cores >= _BEES_ON_DEVICE * footprint.cpu_cores
        and host.memory_free_bytes >= memory_needed
    )
