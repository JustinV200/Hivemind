"""Define decide_release: the pure Overwintering rule from a released Cell to keep-or-destroy.

ADR-0029: a released Virtual Cell may go dormant (Overwintered) instead of being torn down, so the
next task with the same image starts in seconds instead of minutes. `decide_release` is that
decision, and only that decision -- no backend call, no pool mutation (codingrules section 8.3,
"pure core, effectful edges"): `hivemind.hive.overwinter.pool.OverwinterPool` is the effectful half
that acts on what this function returns. The function takes a `VirtualCellSpec` (not just a `Cell`)
on purpose: only a Virtual Cell is ever provisioned from a spec, so requiring one is what keeps this
module "Virtual only" (ADR-0029's own first rule) without ever comparing `cell.kind` -- this module
is not on `scripts/check_no_kind_branches.py`'s allowlist, and does not need to be, because the type
signature already excludes a Real Cell (`hivemind.cell.lease.RealCellLease` release never has a
`VirtualCellSpec` to pass).

Every other ADR-0029 rule below is its own small function (`_RULES`), each returning a veto reason
string or `None`; `decide_release` walks them in order and returns `TEARDOWN` with the first veto's
reason, or `OVERWINTER` once every rule clears. Keeping each rule its own function is what makes
`test_policy.py` give the Night Veil rule its own hypothesis test ("never OVERWINTER whatever else
holds") independent of every other rule's own inputs.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.hive.overwinter`.
    Called by `hivemind.hive.lifecycle` (roadmap step 5.6, a concurrent dispatch) once a task's
    Cell moves to RELEASED, before it decides whether to call `OverwinterPool.admit` or the
    Undertaker's `destroy_virtual`. Calls into `hivemind.cell` (Cell, CombShieldLevel),
    `hivemind.hive.models` (VirtualCellSpec) and `hivemind.manifest.schema.placement`
    (VirtualCellsOverwinterSection) only.

Key invariants:
    - `decide_release` performs no I/O and reads no clock: same five arguments in, same
      `ReleaseDecision` out, every time (codingrules section 8.3).
    - A `cell.comb_shield` of `CombShieldLevel.NIGHT_VEIL` always decides `TEARDOWN`, regardless of
      every other argument (`test_policy.py`'s own hypothesis test walks this).
    - `_RULES` is walked in a fixed order and `decide_release` returns on the first veto, so
      `ReleaseDecision.reason` always names the first rule that failed, never a later one.

See Also:
    - docs/adr/0029-overwintering-policy.md for the rule this module implements verbatim.
    - .claude/codingrules.md section 8.3 for "pure core, effectful edges".
    - hivemind.hive.overwinter.pool for OverwinterPool, the effectful half this decision feeds.
    - hivemind.manifest.schema.placement for VirtualCellsOverwinterSection, the manifest section
      `OverwinterConfig.from_section` reads.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum

from hivemind.cell import Cell, CombShieldLevel
from hivemind.hive.models import VirtualCellSpec
from hivemind.manifest.schema.placement import VirtualCellsOverwinterSection

__all__ = [
    "OverwinterConfig",
    "OverwinterDecision",
    "PoolView",
    "ReleaseDecision",
    "ReleaseOutcome",
    "decide_release",
]


class OverwinterDecision(Enum):
    """What `decide_release` decides for one released Virtual Cell."""

    OVERWINTER = "OVERWINTER"  # Pause it dormant, in the pool, for fast reuse.
    TEARDOWN = "TEARDOWN"  # Destroy it now; the Undertaker's own destroy_virtual.


@dataclass(frozen=True, slots=True)
class ReleaseOutcome:
    """The facts about one task's finished run on a Virtual Cell that `decide_release` needs.

    Attributes:
        rolled_back_whole_cell: True when the Capping gate rolled the whole Cell back (roadmap
            step 5.10) at some point during the task's run; a rolled-back Cell's own state is
            suspect, so it is never kept.
        has_block_wax: True when a WRITTEN BLOCK Cell Wax note names this Cell; a Cell the Queen
            has already flagged unsafe is never kept dormant for reuse.
        single_use: The task's own `hivemind.cell.TaskNeeds.disposable` (ADR-0029's own
            vocabulary calls this "disposability = single_use"); True means the Cell may be
            destroyed the moment the task ends and was never a candidate for reuse.
        backend_can_pause: The provisioning backend's own `hivemind.hive.backends.base.
            BackendCapabilities.can_pause`; a backend that cannot pause can never Overwinter.
    """

    rolled_back_whole_cell: bool
    has_block_wax: bool
    single_use: bool
    backend_can_pause: bool


@dataclass(frozen=True, slots=True)
class PoolView:
    """The Overwintering pool's own occupancy, as `decide_release` needs to see it.

    Attributes:
        total: How many Cells are dormant in the pool right now, across every image.
        per_image: Dormant count, keyed by `VirtualCellSpec.image`; an image absent here has zero.
        disk_used_mb: Disk, in megabytes, the pool's dormant Cells currently hold.
    """

    total: int
    per_image: Mapping[str, int]
    disk_used_mb: int


@dataclass(frozen=True, slots=True)
class OverwinterConfig:
    """The manifest's own `[virtual_cells.overwinter]` bounds, as this package reads them.

    A plain mirror of `hivemind.manifest.schema.placement.VirtualCellsOverwinterSection` rather
    than that pydantic model itself: `hive` is Layer 3 and would otherwise carry a pydantic
    boundary type into a pure-core signature for no benefit (codingrules section 8.5's "internal
    values are frozen dataclasses").
    """

    enabled: bool
    max_cells: int
    max_per_image: int
    max_dormant_s: float
    disk_budget_mb: int

    @classmethod
    def from_section(cls, section: VirtualCellsOverwinterSection) -> OverwinterConfig:
        """Build an OverwinterConfig from the manifest's own `[virtual_cells.overwinter]` section.

        Args:
            section: The validated manifest section.

        Returns:
            The equivalent OverwinterConfig.
        """
        return cls(
            enabled=section.enabled,
            max_cells=section.max_cells,
            max_per_image=section.max_per_image,
            max_dormant_s=section.max_dormant_s,
            disk_budget_mb=section.disk_budget_mb,
        )


@dataclass(frozen=True, slots=True)
class ReleaseDecision:
    """What `decide_release` decided, and why.

    Attributes:
        decision: OVERWINTER or TEARDOWN.
        reason: The first rule that vetoed OVERWINTER, or why it was granted; always non-empty,
            so a `queen.placed`-style trail event always has something to say.
    """

    decision: OverwinterDecision
    reason: str


# One argument bundle every rule function shares, so `_RULES` can be a plain tuple of functions
# with an identical signature rather than a dispatch table keyed by name.
_Rule = Callable[[Cell, VirtualCellSpec, ReleaseOutcome, PoolView, OverwinterConfig], "str | None"]


def decide_release(
    cell: Cell,
    spec: VirtualCellSpec,
    outcome: ReleaseOutcome,
    pool: PoolView,
    config: OverwinterConfig,
) -> ReleaseDecision:
    """Decide whether a released Virtual Cell should Overwinter or be torn down (ADR-0029).

    Args:
        cell: The released Cell; only `comb_shield` is read (module docstring: `spec` already
            proves this is a Virtual Cell).
        spec: The `VirtualCellSpec` this Cell was provisioned from; `image` and `disk_bytes` are
            checked against `pool`'s own occupancy.
        outcome: What happened on this Cell's last task.
        pool: The Overwintering pool's current occupancy.
        config: The manifest's own `[virtual_cells.overwinter]` bounds.

    Returns:
        OVERWINTER once every rule clears, or TEARDOWN with the first rule's own veto reason.
    """
    for rule in _RULES:
        veto = rule(cell, spec, outcome, pool, config)
        if veto is not None:
            return ReleaseDecision(OverwinterDecision.TEARDOWN, veto)
    return ReleaseDecision(
        OverwinterDecision.OVERWINTER,
        f"Eligible: image {spec.image!r} has room in the pool and every ADR-0029 rule cleared.",
    )


def _pool_enabled(
    cell: Cell,
    spec: VirtualCellSpec,
    outcome: ReleaseOutcome,
    pool: PoolView,
    config: OverwinterConfig,
) -> str | None:
    """Veto when the manifest turns the pool off outright."""
    del cell, spec, outcome, pool  # Unused: this rule reads only the manifest's own switch.
    if not config.enabled:
        return "Overwintering is disabled ([virtual_cells.overwinter] enabled = false)."
    return None


def _never_night_veil(
    cell: Cell,
    spec: VirtualCellSpec,
    outcome: ReleaseOutcome,
    pool: PoolView,
    config: OverwinterConfig,
) -> str | None:
    """Veto a NIGHT_VEIL Cell: teardown-only, never Overwintered (codingrules section 8.7)."""
    del spec, outcome, pool, config  # Unused: this rule reads only the Cell's own tier.
    if cell.comb_shield is CombShieldLevel.NIGHT_VEIL:
        return "Night Veil Cells are teardown-only and are never Overwintered."
    return None


def _never_single_use(
    cell: Cell,
    spec: VirtualCellSpec,
    outcome: ReleaseOutcome,
    pool: PoolView,
    config: OverwinterConfig,
) -> str | None:
    """Veto a task that asked for a single-use (disposable) Cell."""
    del cell, spec, pool, config  # Unused: this rule reads only the task's own outcome.
    if outcome.single_use:
        return "The task asked for a single-use Cell (TaskNeeds.disposable=True)."
    return None


def _never_after_whole_cell_rollback(
    cell: Cell,
    spec: VirtualCellSpec,
    outcome: ReleaseOutcome,
    pool: PoolView,
    config: OverwinterConfig,
) -> str | None:
    """Veto a Cell whose last task triggered a whole-Cell Capping rollback."""
    del cell, spec, pool, config  # Unused: this rule reads only the task's own outcome.
    if outcome.rolled_back_whole_cell:
        return "The last task's Capping gate rolled the whole Cell back; its state is suspect."
    return None


def _never_with_block_wax(
    cell: Cell,
    spec: VirtualCellSpec,
    outcome: ReleaseOutcome,
    pool: PoolView,
    config: OverwinterConfig,
) -> str | None:
    """Veto a Cell the Queen has already flagged with an open BLOCK Cell Wax note."""
    del cell, spec, pool, config  # Unused: this rule reads only the task's own outcome.
    if outcome.has_block_wax:
        return "An open BLOCK Cell Wax note names this Cell."
    return None


def _requires_backend_pause(
    cell: Cell,
    spec: VirtualCellSpec,
    outcome: ReleaseOutcome,
    pool: PoolView,
    config: OverwinterConfig,
) -> str | None:
    """Veto when the provisioning backend cannot pause a Cell at all."""
    del cell, spec, pool, config  # Unused: this rule reads only the backend's own capability.
    if not outcome.backend_can_pause:
        return "The provisioning backend cannot pause a Cell (BackendCapabilities.can_pause=False)."
    return None


def _requires_room_per_image(
    cell: Cell,
    spec: VirtualCellSpec,
    outcome: ReleaseOutcome,
    pool: PoolView,
    config: OverwinterConfig,
) -> str | None:
    """Veto when the pool is already at its own max_per_image cap for this Cell's own image."""
    del cell, outcome  # Unused: this rule reads only the pool's own occupancy for spec.image.
    if pool.per_image.get(spec.image, 0) >= config.max_per_image:
        return (
            f"The pool already holds {config.max_per_image} dormant Cells for image "
            f"{spec.image!r} (max_per_image)."
        )
    return None


def _requires_room_total(
    cell: Cell,
    spec: VirtualCellSpec,
    outcome: ReleaseOutcome,
    pool: PoolView,
    config: OverwinterConfig,
) -> str | None:
    """Veto when the pool is already at its own max_cells cap, across every image."""
    del cell, spec, outcome  # Unused: this rule reads only the pool's own total occupancy.
    if pool.total >= config.max_cells:
        return f"The pool already holds {config.max_cells} dormant Cells in total (max_cells)."
    return None


def _requires_disk_budget(
    cell: Cell,
    spec: VirtualCellSpec,
    outcome: ReleaseOutcome,
    pool: PoolView,
    config: OverwinterConfig,
) -> str | None:
    """Veto when adding this Cell's own disk would exceed the pool's disk_budget_mb."""
    del cell, outcome  # Unused: this rule reads only the pool's occupancy and this Cell's disk.
    spec_disk_mb = spec.disk_bytes // (1024**2)
    if pool.disk_used_mb + spec_disk_mb > config.disk_budget_mb:
        return (
            f"Adding {spec_disk_mb} MB would exceed the pool's {config.disk_budget_mb} MB "
            "disk_budget_mb."
        )
    return None


# Walked in this order by decide_release; the first veto wins (module docstring's own key
# invariant). Cheap, argument-only checks first; the pool-occupancy checks last, since they are
# the only ones that read anything beyond this one Cell's own facts.
_RULES: tuple[_Rule, ...] = (
    _pool_enabled,
    _never_night_veil,
    _never_single_use,
    _never_after_whole_cell_rollback,
    _never_with_block_wax,
    _requires_backend_pause,
    _requires_room_per_image,
    _requires_room_total,
    _requires_disk_budget,
)
