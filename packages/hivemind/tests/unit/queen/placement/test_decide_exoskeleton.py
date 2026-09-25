"""Tests for hivemind.queen.placement.decide's Exoskeleton fit (roadmap step 6.12, rule 4c).

A task that needs an Exoskeleton (a display, input, audio or browser attachment) takes a Real Cell
only where attach could honour it there, judged from the Cell's capability report and its access
level; otherwise it lands on a Virtual Cell booted from the desktop image. Split out of
test_decide.py by feature (codingrules section 5.1), like test_decide_properties.py.

Fits into the Hive:
    Mirrors src/hivemind/queen/placement/decide.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.placement.decide for the module under test.
    - hivemind.queen.placement.rules for exoskeleton_shortfall, the rule these tests drive.
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md, "Needs travel with the task".
"""

from __future__ import annotations

import pytest
from builders.cells import (
    make_capabilities,
    make_desktop_capabilities,
    make_windows_hive_stand_capabilities,
)
from builders.forage import make_capacity, make_footprint

from hivemind.cell import AccessLevel, CellCapabilities, CombShieldLevel, TaskNeeds
from hivemind.hive import BackendCapabilities, NetworkPolicy, VirtualCellSpec
from hivemind.queen.placement import (
    DormantCandidate,
    ForageView,
    Inventory,
    Placement,
    PlacementError,
    PlacementPolicy,
    Prefer,
    ProvisionVirtual,
    RealCandidate,
    ReuseDormant,
    ReuseReal,
    VirtualBackendCandidate,
    decide,
)
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_hive_id, new_warden_id

_CLOCK = FakeClock()
_BASE_IMAGE = "base-ubuntu"  # [virtual_cells] default_image's own default: terminal-only.
_DESKTOP_IMAGE = "desktop-ubuntu"  # [virtual_cells] exoskeleton_image's own default.

_DESKTOP = TaskNeeds(exoskeleton=True)
_DESKTOP_WITH_AUDIO = TaskNeeds(exoskeleton=True, audio=True)
_BROWSER_ONLY = TaskNeeds(exoskeleton=True, browser_only=True)


def _real(capabilities: CellCapabilities, access_level: AccessLevel) -> RealCandidate:
    return RealCandidate(
        warden_id=new_warden_id(_CLOCK),
        cell_id=new_cell_id(_CLOCK),
        capabilities=capabilities,
        comb_shield=CombShieldLevel.MEADOW,
        is_hive_stand=True,
        has_free_capacity=True,
        access_level=access_level,
    )


def _spec(image: str, *, exoskeleton: bool) -> VirtualCellSpec:
    return VirtualCellSpec(
        image=image,
        cpu_cores=1.0,
        memory_bytes=1024**3,
        disk_bytes=8 * 1024**3,
        network_policy=NetworkPolicy.NONE,
        exoskeleton=exoskeleton,
        capacity=make_capacity(),
        comb_shield=CombShieldLevel.MEADOW,
        hive_id=new_hive_id(_CLOCK),
    )


def _backend(*specs: VirtualCellSpec) -> VirtualBackendCandidate:
    """A backend offering `specs`, or the composition root's own (terminal, desktop) pair."""
    offered = specs or (
        _spec(_BASE_IMAGE, exoskeleton=False),
        _spec(_DESKTOP_IMAGE, exoskeleton=True),
    )
    capabilities = BackendCapabilities(can_snapshot=False, can_pause=True, headroom=None)
    return VirtualBackendCandidate(name="docker", capabilities=capabilities, specs=offered)


def _dormant(image: str) -> DormantCandidate:
    return DormantCandidate(
        cell_id=new_cell_id(_CLOCK), warden_id=None, image=image, comb_shield=CombShieldLevel.MEADOW
    )


def _decide(needs: TaskNeeds, inventory: Inventory, prefer: Prefer = "real") -> Placement:
    forage = ForageView(footprint=make_footprint())
    return decide(needs, inventory, forage, PlacementPolicy(prefer=prefer))


# ──────────────────────────────────────────────────────────────────────────────
# A desktop need on a Real Cell: a display the lease may start, or the operator's lent one.
# ──────────────────────────────────────────────────────────────────────────────


def test_a_linux_real_cell_that_can_start_a_display_takes_a_desktop_need_at_full() -> None:
    real = _real(make_desktop_capabilities(), AccessLevel.FULL)

    placement = _decide(_DESKTOP, Inventory(real=(real,)))

    assert placement == ReuseReal(real.cell_id, real.warden_id, placement.reason)


def test_a_running_display_its_operator_never_allowed_does_not_take_a_desktop_need() -> None:
    real = _real(make_capabilities(has_display=True, real_display_allowed=False), AccessLevel.FULL)

    with pytest.raises(PlacementError, match="real_display_allowed=false"):
        _decide(_DESKTOP, Inventory(real=(real,)))


def test_a_running_display_its_operator_allowed_takes_a_desktop_need_at_full() -> None:
    real = _real(make_capabilities(has_display=True, real_display_allowed=True), AccessLevel.FULL)

    assert isinstance(_decide(_DESKTOP, Inventory(real=(real,))), ReuseReal)


def test_a_cell_with_no_display_and_nothing_to_start_one_does_not_take_a_desktop_need() -> None:
    real = _real(make_capabilities(), AccessLevel.FULL)

    with pytest.raises(PlacementError, match="can_start_display=false"):
        _decide(_DESKTOP, Inventory(real=(real,)))


def test_the_linux_cell_at_scratch_refuses_a_desktop_but_takes_a_browser_only_need() -> None:
    # SCRATCH's ceiling holds exoskeleton:browser but never exoskeleton:display (hivemind.guard).
    real = _real(make_desktop_capabilities(), AccessLevel.SCRATCH)
    inventory = Inventory(real=(real,))

    with pytest.raises(
        PlacementError, match="access level SCRATCH never grants exoskeleton:display"
    ):
        _decide(_DESKTOP, inventory)
    assert isinstance(_decide(_BROWSER_ONLY, inventory), ReuseReal)


# ──────────────────────────────────────────────────────────────────────────────
# Browser-only and audio needs.
# ──────────────────────────────────────────────────────────────────────────────


def test_a_windows_like_real_cell_takes_a_browser_only_need_but_not_a_desktop() -> None:
    real = _real(make_windows_hive_stand_capabilities(), AccessLevel.SCRATCH)
    inventory = Inventory(real=(real,))

    assert isinstance(_decide(_BROWSER_ONLY, inventory), ReuseReal)
    with pytest.raises(PlacementError, match="desktop Exoskeleton needs a display"):
        _decide(_DESKTOP, inventory)


def test_an_audio_need_needs_the_cells_sound_server() -> None:
    silent = _real(make_desktop_capabilities(has_audio=False), AccessLevel.FULL)
    heard = _real(make_desktop_capabilities(), AccessLevel.FULL)

    with pytest.raises(PlacementError, match="has_audio=false"):
        _decide(_DESKTOP_WITH_AUDIO, Inventory(real=(silent,)))
    placement = _decide(_DESKTOP_WITH_AUDIO, Inventory(real=(silent, heard)))

    assert isinstance(placement, ReuseReal)
    assert placement.cell_id == heard.cell_id  # The silent Cell is skipped, not merely ranked.


def test_an_unreported_access_level_fails_closed() -> None:
    real = RealCandidate(
        warden_id=new_warden_id(_CLOCK),
        cell_id=new_cell_id(_CLOCK),
        capabilities=make_desktop_capabilities(),
        comb_shield=CombShieldLevel.MEADOW,
        is_hive_stand=True,
        has_free_capacity=True,
    )  # No access_level: RealCandidate's own READ_ONLY default.

    with pytest.raises(PlacementError, match="READ_ONLY never grants exoskeleton:browser"):
        _decide(_BROWSER_ONLY, Inventory(real=(real,)))


# ──────────────────────────────────────────────────────────────────────────────
# The Virtual side: an Exoskeleton need boots the desktop image, and nothing else does.
# ──────────────────────────────────────────────────────────────────────────────


def test_no_qualifying_real_cell_provisions_a_virtual_cell_from_the_desktop_image() -> None:
    hive_stand = _real(make_desktop_capabilities(), AccessLevel.SCRATCH)
    inventory = Inventory(real=(hive_stand,), virtual_backends=(_backend(),))

    placement = _decide(_DESKTOP, inventory, prefer="real")

    assert isinstance(placement, ProvisionVirtual)
    assert placement.spec.image == _DESKTOP_IMAGE
    assert placement.spec.exoskeleton is True
    # The reason says why the preferred Real side lost: the Hive Stand's own shortfall.
    assert "prefer=real found no Real Cell" in placement.reason
    assert "Hive Stand: desktop Exoskeleton needs a display" in placement.reason


def test_a_terminal_only_task_still_boots_the_default_image() -> None:
    placement = _decide(TaskNeeds(), Inventory(virtual_backends=(_backend(),)), prefer="virtual")

    assert isinstance(placement, ProvisionVirtual)
    assert placement.spec.image == _BASE_IMAGE
    assert placement.spec.exoskeleton is False


def test_an_exoskeleton_need_resumes_a_dormant_desktop_cell_never_a_base_one() -> None:
    base = _dormant(_BASE_IMAGE)  # First in the pool, but its image carries no Exoskeleton tools.
    desktop = _dormant(_DESKTOP_IMAGE)
    inventory = Inventory(virtual_backends=(_backend(),), dormant=(base, desktop))

    placement = _decide(_BROWSER_ONLY, inventory, prefer="virtual")

    assert placement == ReuseDormant(desktop.cell_id, None, placement.reason)


def test_a_backend_offering_only_a_terminal_spec_cannot_take_an_exoskeleton_need() -> None:
    backend = _backend(_spec(_BASE_IMAGE, exoskeleton=False))

    with pytest.raises(PlacementError, match="provisions none"):
        _decide(_DESKTOP, Inventory(virtual_backends=(backend,)), prefer="virtual")
