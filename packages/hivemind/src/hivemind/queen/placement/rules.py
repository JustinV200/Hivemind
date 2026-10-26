"""Define the small, individually-tested rule functions ADR-0028's ordered pipeline runs.

ADR-0028 lists six rules, hard ones first, preference last: (1) `isolation = "required"` is always
Virtual; (2) `comb_shield = NIGHT_VEIL` is always a fresh Virtual Cell; (3) a `BLOCK` Cell Wax
excludes a Cell and `allow_hive_stand = false` excludes the Hive Stand; (4) a candidate must fit --
OS, network scopes, the Comb Shield tier the task needs (a Real Cell's tier is the operator's and
never changes, so a task that needs PROPOLIS never lands on a MEADOW Cell), and the Exoskeleton
(the display, input, audio and browser attachment) the task asks for; (5) Forage must cover the
grant; (6) otherwise honour `prefer`, rank a `CAUTION`ed Cell behind a clean one, and prefer a
dormant Cell over a fresh provision. ADR-0039 adds one rule
ahead of all six (roadmap step 10.3): a goal's capability set is a ceiling, so a candidate the goal
does not allow -- the Hive Stand without `cell:hive_stand`, any other Real Cell without
`cell:real:<cell id>`, any Virtual Cell without `cell:virtual`, any Cell at a tier without
`cell:comb_shield:<tier>` -- is excluded first, whatever `prefer` says (`placement_needs`,
`virtual_placement_needs`, `goal_lacks`). Each rule here is one small, pure function with its own
unit test (roadmap step 5.7's own words), so `hivemind.queen.placement.decide` reads as an ordered
list of named checks rather than one large conditional. Every function takes plain data (a
`TaskNeeds`, a `CellCapabilities`, a `VirtualCellSpec`, ...) and returns a plain `bool` or ranking
key; none of them do I/O or read a store.

Roadmap step 6.12 makes rule 4c, for a Real Cell (an existing device the Hive borrows), ask what
attach will ask once the Cell is leased (ADR-0031, "Attach is a pure plan"): the Cell's own
capability report, and the widest set of Exoskeleton scopes its access level lets any bee on it
hold (`hivemind.guard.ceiling_for`, with the operator's running-display opt-in, exactly as the
Cell's own Warden builds it). A browser-only need needs a browser; a desktop need needs a display
the lease may start, or the running display its operator lets the Hive drive; an audio need also
needs a sound server. These return the unmet requirement in words, or `None`, rather than a bare
`bool`, so `decide`'s reason names the rule that excluded the Cell and the fact that failed it.
ADR-0060 adds rule 4d the same way: a task that runs commands needs a Cell whose ceiling holds
`exec`, which below FULL only a Cell that contains its processes does (`command_shortfall`). A
Virtual spec always meets it: its container contains what it runs, and it is always FULL.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the `queen.placement`
    sub-package. Called only by `hivemind.queen.placement.decide.decide`. Calls into `hivemind.cell`
    (AccessLevel, CellCapabilities, CellKind, CombShieldLevel, Isolation, TaskNeeds),
    `hivemind.guard` (Capability, CapabilityFamily, CapabilitySet, ceiling_for), `hivemind.hive`
    (NetworkPolicy, VirtualCellSpec), `hivemind.forage` (RoleFootprint) and this package's own
    `policy`/`inventory` modules only.

Key invariants:
    - Every function here is pure: same inputs, same output, every time (ADR-0028: "`decide` is
      pure"). None of them mutate an argument or read anything beyond it.
    - Every function name says which ADR-0028 rule it implements, in its own docstring's first
      sentence, so a reader can match a rule in the ADR to the function that enforces it.
    - Rule 4c reads capabilities and the access level only, never `cell.kind` (codingrules 8.7),
      and never widens what `hivemind.guard.ceiling_for` allows: a scope no ceiling grants, such as
      `exoskeleton:real_display` without the operator's opt-in, never qualifies a Cell.
    - `goal_lacks` never excludes anything for a goal with no ceiling (None): the operator's own
      local path is limited by the Hive's other rules only.

See Also:
    - docs/adr/0028-placement-policy-real-versus-virtual.md for the six rules this module
      implements, verbatim, one function per rule.
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md, "Needs travel with the
      task", for what rule 4c asks of a Real Cell and why a Virtual one must boot a desktop image.
    - hivemind.queen.placement.decide for decide, the one caller that runs these in order.
    - hivemind.queen.placement.inventory for RealCandidate and WaxMention, two of these functions'
      own argument types.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from hivemind.cell import (
    AccessLevel,
    CellCapabilities,
    CellKind,
    CombShieldLevel,
    Isolation,
    TaskNeeds,
)
from hivemind.forage import RoleFootprint
from hivemind.guard import Capability, CapabilityFamily, CapabilitySet, ceiling_for
from hivemind.hive import NetworkPolicy, VirtualCellSpec
from hivemind.queen.placement.inventory import Inventory, RealCandidate, WaxMention
from hivemind.queen.placement.policy import PlacementPolicy
from waggle.ids import CellId

# The four scopes of ADR-0031's `exoskeleton` capability family that rule 4c asks a ceiling about.
_DISPLAY = Capability.parse("exoskeleton:display")  # Drive a display the lease itself starts.
_REAL_DISPLAY = Capability.parse("exoskeleton:real_display")  # Drive the operator's own screen.
_AUDIO = Capability.parse("exoskeleton:audio")  # Hear and speak through the lease's sound server.
_BROWSER = Capability.parse("exoskeleton:browser")  # Drive the lease's own browser.
_EXEC = Capability.parse("exec:*")  # Rule 4d (ADR-0060): run any command on the Cell.
# WHY a stand-in: `ceiling_for` reads a lease's scratch root only to build its `fs:write` scope
# (its own docstring), and placement runs before any lease exists. No Exoskeleton scope depends on
# the root, so this value never reaches an answer rule 4c reads; reusing `ceiling_for` rather than
# restating its tables here keeps "what each access level permits" in `hivemind.guard` alone.
_NO_LEASE_SCRATCH_ROOT = Path("/")
# The two flag capabilities a Cell of each source asks of a goal (ADR-0039's placement families).
_CELL_VIRTUAL = Capability(family=CapabilityFamily.CELL_VIRTUAL)
_CELL_HIVE_STAND = Capability(family=CapabilityFamily.CELL_HIVE_STAND)

# The Comb Shield tiers from the weakest controls to the strongest. A Cell meets a task's minimum
# tier when it ranks at least as high; `CombShieldLevel` itself carries no order (unlike
# `AccessLevel`), because only placement ever has to compare tiers.
_TIER_ORDER = (CombShieldLevel.MEADOW, CombShieldLevel.PROPOLIS, CombShieldLevel.NIGHT_VEIL)

__all__ = [
    "audio_shortfall",
    "browser_shortfall",
    "caution_rank",
    "command_shortfall",
    "commands_need_a_virtual_cell",
    "desktop_shortfall",
    "excluded_by_block_wax",
    "excluded_by_hive_stand_policy",
    "exoskeleton_shortfall",
    "fits_comb_shield",
    "fits_network_scopes",
    "fits_os",
    "goal_lacks",
    "isolation_requires_virtual",
    "night_veil_requires_virtual",
    "placement_needs",
    "real_has_forage",
    "tier_capability",
    "virtual_fits_exoskeleton",
    "virtual_fits_network_scopes",
    "virtual_fits_os",
    "virtual_has_forage",
    "virtual_has_headroom",
    "virtual_placement_needs",
]


def tier_capability(tier: CombShieldLevel) -> Capability:
    """The goal ceiling's tier rule: the capability a Cell at `tier` asks of a goal.

    Returns `cell:comb_shield:<tier>`, the lowercase member name (`meadow`, `propolis`,
    `night_veil`), which is the enumerated scope the Guard's grammar reads.
    """
    return Capability(family=CapabilityFamily.CELL_COMB_SHIELD, scope=tier.name.lower())


def placement_needs(candidate: RealCandidate) -> tuple[Capability, ...]:
    """The goal ceiling for an attached Cell: its source's capability, then its tier's.

    An attached Cell a Virtual backend provisioned asks `cell:virtual`, the Hive Stand
    `cell:hive_stand`, and any other Real Cell `cell:real:<cell id>`; each then asks its tier.
    Placement is one of the two callers allowed to read a Cell's kind (codingrules 8.7).
    """
    if candidate.kind is CellKind.VIRTUAL:
        cell = _CELL_VIRTUAL
    elif candidate.is_hive_stand:
        cell = _CELL_HIVE_STAND
    else:
        cell = Capability(family=CapabilityFamily.CELL_REAL, scope=candidate.cell_id)
    return (cell, tier_capability(candidate.comb_shield))


def virtual_placement_needs(tier: CombShieldLevel) -> tuple[Capability, ...]:
    """The goal ceiling for a Virtual Cell to provision or resume at `tier`: both capabilities."""
    return (_CELL_VIRTUAL, tier_capability(tier))


def goal_lacks(goal: CapabilitySet | None, needed: tuple[Capability, ...]) -> Capability | None:
    """The goal ceiling itself: the first of `needed` the goal's set does not allow, or None.

    Always None for a goal with no ceiling (`goal` is None): the operator's own local path.
    """
    if goal is None:
        return None
    return next((capability for capability in needed if not goal.allows(capability)), None)


def isolation_requires_virtual(needs: TaskNeeds) -> bool:
    """Rule 1: `isolation = "required"` always excludes every Real Cell, whatever `prefer` says."""
    return needs.isolation is Isolation.REQUIRED


def night_veil_requires_virtual(needs: TaskNeeds) -> bool:
    """Rule 2: `comb_shield = NIGHT_VEIL` is always a fresh Virtual Cell, never Real or dormant."""
    return needs.comb_shield is CombShieldLevel.NIGHT_VEIL


def excluded_by_block_wax(cell_id: CellId, blocked: Mapping[CellId, WaxMention]) -> bool:
    """Rule 3a: a Cell carrying a WRITTEN `WaxSeverity.BLOCK` note is excluded outright."""
    return cell_id in blocked


def excluded_by_hive_stand_policy(is_hive_stand: bool, policy: PlacementPolicy) -> bool:
    """Rule 3b: `[placement] allow_hive_stand = false` excludes the Hive Stand outright."""
    return is_hive_stand and not policy.allow_hive_stand


def fits_os(needs: TaskNeeds, capabilities: CellCapabilities) -> bool:
    """Rule 4a: a task's `os` need, when set, must match a Real candidate's own OS."""
    return needs.os is None or needs.os is capabilities.os


def fits_network_scopes(needs: TaskNeeds, capabilities: CellCapabilities) -> bool:
    """Rule 4b: every network scope the task needs must already be reachable from the candidate."""
    return set(needs.network_scopes) <= set(capabilities.network_scopes)


def fits_comb_shield(needs: TaskNeeds, tier: CombShieldLevel) -> bool:
    """Rule 4d: an attached Cell must carry at least the Comb Shield tier the task needs.

    A Real Cell's tier is set by its operator and describes a VPN the device already runs
    (codingrules 8.7), so a task that needs PROPOLIS can only run where the operator vouched for
    one; placement never lifts a MEADOW Cell to it. A task never lands on a weaker tier than it
    asked for, while a stronger Cell is welcome to a plainer task.

    Args:
        needs: The task's needs; only `comb_shield` is read.
        tier: The candidate Cell's own tier.

    Returns:
        True when `tier` ranks at least as high as `needs.comb_shield`.
    """
    return _TIER_ORDER.index(tier) >= _TIER_ORDER.index(needs.comb_shield)


def exoskeleton_shortfall(
    needs: TaskNeeds, capabilities: CellCapabilities, access_level: AccessLevel
) -> str | None:
    """Rule 4c: say what a Real Cell lacks for the task's Exoskeleton need, or None if nothing.

    Asks what attach will ask once the Cell is leased (ADR-0031): a browser-only need is the
    browser alone and never a display; a desktop need is a display, plus a sound server when the
    task needs audio. The first unmet requirement is the one reported.

    Args:
        needs: The task's needs; only `exoskeleton`, `browser_only` and `audio` are read.
        capabilities: The candidate Real Cell's own capability report.
        access_level: The candidate's own level, which caps every Exoskeleton scope any bee on
            it could ever hold.

    Returns:
        None when the Cell can meet the need (always, for a terminal-only task); otherwise the
        unmet requirement in words, ready to become a placement reason.

    Example:
        A Windows Hive Stand at SCRATCH (a running display its operator never lent, and nothing
        to start one with) meets `TaskNeeds(exoskeleton=True, browser_only=True)`, so that gets
        None; a plain `TaskNeeds(exoskeleton=True)` gets "desktop Exoskeleton needs a display,
        but cannot start one (can_start_display=false) or drive the running one
        (real_display_allowed=false)".
    """
    if not needs.exoskeleton:
        return None  # A terminal-only task needs nothing beyond the Cell's own session.
    if needs.browser_only:
        # The browser fast path alone: attach starts no display and no sound server for it.
        return browser_shortfall(capabilities, access_level)
    desktop = desktop_shortfall(capabilities, access_level)
    # Audio plays through the desktop's own sound server, so it is checked only once a desktop
    # itself fits; the reason then names the first thing the Cell lacks, never a later one.
    if desktop is not None or not needs.audio:
        return desktop
    return audio_shortfall(capabilities, access_level)


def browser_shortfall(capabilities: CellCapabilities, access_level: AccessLevel) -> str | None:
    """Rule 4c, browser-only need: a browser the Cell has and its bees may drive; no display."""
    if not capabilities.has_browser:
        return "browser-only Exoskeleton needs a browser (has_browser=false)"
    if not _ceiling(capabilities, access_level).allows(_BROWSER):
        return f"browser-only Exoskeleton needs a browser ({_never_grants(access_level, _BROWSER)})"
    return None


def desktop_shortfall(capabilities: CellCapabilities, access_level: AccessLevel) -> str | None:
    """Rule 4c, desktop need: a display the lease may start, or the running one the operator lends.

    Either display will do, as it does for attach: the operator's own screen when its report says
    they allowed it and the level grants `exoskeleton:real_display`, else an Xvfb the lease starts
    when the Cell can start one and the level grants `exoskeleton:display`.
    """
    ceiling = _ceiling(capabilities, access_level)
    lease_gap = _lease_display_gap(capabilities, ceiling, access_level)
    running_gap = _running_display_gap(capabilities, ceiling, access_level)
    if lease_gap is None or running_gap is None:
        return None  # One way to a display is enough.
    return (
        f"desktop Exoskeleton needs a display, but cannot start one ({lease_gap}) "
        f"or drive the running one ({running_gap})"
    )


def audio_shortfall(capabilities: CellCapabilities, access_level: AccessLevel) -> str | None:
    """Rule 4c, audio need: a sound server the Cell has and its bees may use."""
    if not capabilities.has_audio:
        return "Exoskeleton audio needs a sound server (has_audio=false)"
    if not _ceiling(capabilities, access_level).allows(_AUDIO):
        return f"Exoskeleton audio needs a sound server ({_never_grants(access_level, _AUDIO)})"
    return None


def command_shortfall(
    needs: TaskNeeds, capabilities: CellCapabilities, access_level: AccessLevel
) -> str | None:
    """Rule 4d: say why a Real Cell cannot run the task's commands, or None if it can.

    Asks the ceiling the Cell's own Warden will build (ADR-0060): below FULL, only a Cell whose
    processes are contained holds `exec`, so a Real Cell at SCRATCH sends a task that runs
    commands to a Virtual Cell instead.

    Example:
        The Hive Stand at SCRATCH gets "running commands needs FULL access or a Cell that
        contains its processes (access level SCRATCH, processes_contained=false)".
    """
    if not needs.commands or _ceiling(capabilities, access_level).allows(_EXEC):
        return None
    contained = str(capabilities.processes_contained).lower()
    return (
        "running commands needs FULL access or a Cell that contains its processes "
        f"(access level {access_level.name}, processes_contained={contained})"
    )


def commands_need_a_virtual_cell(needs: TaskNeeds, inventory: Inventory) -> bool:
    """Say whether only a Virtual Cell could run this task's commands, and the Hive has none.

    True when the task runs commands, at least one Real Cell is attached and none of them may
    run one (rule 4d), and there is no Virtual backend or dormant Cell at all. With no Cell
    attached yet the wait is ordinary: a Warden may still attach.
    """
    if not needs.commands or inventory.virtual_backends or inventory.dormant:
        return False
    return bool(inventory.real) and all(
        command_shortfall(needs, candidate.capabilities, candidate.access_level) is not None
        for candidate in inventory.real
    )


def real_has_forage(candidate: RealCandidate) -> bool:
    """Rule 5a: a Real Cell needs the free-capacity flag the caller already measured."""
    return candidate.has_free_capacity


def virtual_has_headroom(headroom: int | None) -> bool:
    """Rule 5b: a Virtual backend needs headroom left (`None` means it declares no limit)."""
    return headroom is None or headroom > 0


def virtual_fits_os(needs: TaskNeeds, spec: VirtualCellSpec) -> bool:
    """Rule 4a, for a Virtual spec: compare against the OS its own promised capacity reports."""
    return needs.os is None or needs.os.value == spec.capacity.host.os.value


def virtual_fits_exoskeleton(needs: TaskNeeds, spec: VirtualCellSpec) -> bool:
    """Rule 4c, for a Virtual spec: an Exoskeleton need requires a spec that provisions one.

    Such a spec boots the desktop image (`[virtual_cells] exoskeleton_image`, `images/desktop-
    ubuntu` by default), which carries a display to start, a sound server and a browser, on a Cell
    that is always FULL access; so it meets every shape of need, browser-only and audio included.
    """
    return not needs.exoskeleton or spec.exoskeleton


def virtual_fits_network_scopes(needs: TaskNeeds, spec: VirtualCellSpec) -> bool:
    """Rule 4b, for a Virtual spec: a network need excludes NONE and a too-narrow allowlist."""
    if not needs.network_scopes:
        return True  # Nothing beyond the Cell's own grant is needed; every policy covers that.
    if spec.network_policy is NetworkPolicy.NONE:
        return False  # No outbound reach at all can never cover a named network scope.
    if spec.network_policy is NetworkPolicy.ALLOWLIST:
        return set(needs.network_scopes) <= set(spec.network_allowlist)
    return True  # EGRESS_ONLY/VPN_TOR: unrestricted outbound reach covers any named scope.


def virtual_has_forage(spec: VirtualCellSpec, footprint: RoleFootprint) -> bool:
    """Rule 5c: a Virtual spec's own promised capacity must cover the placed bee's footprint."""
    return (
        spec.capacity.max_sub_bees >= 1
        and spec.cpu_cores >= footprint.cpu_cores
        and spec.memory_bytes >= footprint.memory_bytes
    )


def caution_rank(cell_id: CellId, cautioned: Mapping[CellId, WaxMention]) -> int:
    """Rule 6's caution penalty: `1` (ranked behind) when CAUTIONed, `0` for a clean candidate."""
    return 1 if cell_id in cautioned else 0


# ──────────────────────────────────────────────────────────────────────────────
# Rule 4c's own helpers for a Real Cell: the ceiling, and why each display path fails.
# ──────────────────────────────────────────────────────────────────────────────


def _ceiling(capabilities: CellCapabilities, access_level: AccessLevel) -> CapabilitySet:
    """Return the widest set any bee on this Real Cell could hold, as its own Warden builds it."""
    # The operator's running-display opt-in rides on the Cell's own report, exactly as
    # hivemind.wardens.warden passes it once leased: never implied (codingrules section 15).
    return ceiling_for(
        access_level,
        _NO_LEASE_SCRATCH_ROOT,
        real_display=capabilities.real_display_allowed,
        processes_contained=capabilities.processes_contained,
    )


def _lease_display_gap(
    capabilities: CellCapabilities, ceiling: CapabilitySet, access_level: AccessLevel
) -> str | None:
    """Say why attach could not start a display for the lease on this Cell, or None if it could."""
    if not capabilities.can_start_display:
        return "can_start_display=false"
    if not ceiling.allows(_DISPLAY):
        return _never_grants(access_level, _DISPLAY)
    return None


def _running_display_gap(
    capabilities: CellCapabilities, ceiling: CapabilitySet, access_level: AccessLevel
) -> str | None:
    """Say why attach could not drive this Cell's running display, or None if it could."""
    if not capabilities.has_display:
        return "has_display=false"
    # Checked before the ceiling: without the operator's opt-in no ceiling ever holds the scope,
    # and naming the missing opt-in tells the operator which switch is theirs to flip.
    if not capabilities.real_display_allowed:
        return "real_display_allowed=false"
    if not ceiling.allows(_REAL_DISPLAY):
        return _never_grants(access_level, _REAL_DISPLAY)
    return None


def _never_grants(access_level: AccessLevel, scope: Capability) -> str:
    """Word the one fact that fails a requirement when the Cell's own access level is the cause."""
    return f"access level {access_level.name} never grants {scope}"
