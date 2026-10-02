"""Tests for hivemind.queen.placement.decide's command fit (ADR-0060, rule 4d).

A task that runs commands takes a Real Cell only where the Cell's own ceiling holds `exec`: at
FULL, or on a Cell that contains its processes. Otherwise it lands on a Virtual Cell, and on a Hive
with no Virtual backend at all it fails for good, with the reason. Split out of test_decide.py by
feature (codingrules section 5.1), like test_decide_exoskeleton.py.

Fits into the Hive:
    Mirrors src/hivemind/queen/placement/decide.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.placement.decide for the module under test.
    - hivemind.queen.placement.rules for command_shortfall, the rule these tests drive.
    - docs/adr/0060-commands-run-only-where-they-are-contained-or-fully-granted.md.
"""

from __future__ import annotations

import dataclasses

import pytest
from builders.cells import make_capabilities
from builders.forage import make_capacity, make_footprint

from hivemind.cell import AccessLevel, CellCapabilities, CombShieldLevel, TaskNeeds
from hivemind.hive import BackendCapabilities, NetworkPolicy, VirtualCellSpec
from hivemind.queen.placement import (
    ForageView,
    Inventory,
    Placement,
    PlacementError,
    PlacementPolicy,
    ProvisionVirtual,
    RealCandidate,
    ReuseReal,
    VirtualBackendCandidate,
    decide,
)
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_hive_id, new_warden_id

_CLOCK = FakeClock()
_COMMANDS = TaskNeeds(commands=True)


def _real(access_level: AccessLevel, capabilities: CellCapabilities | None = None) -> RealCandidate:
    return RealCandidate(
        warden_id=new_warden_id(_CLOCK),
        cell_id=new_cell_id(_CLOCK),
        capabilities=capabilities if capabilities is not None else make_capabilities(),
        comb_shield=CombShieldLevel.MEADOW,
        is_hive_stand=True,
        has_free_capacity=True,
        access_level=access_level,
    )


def _backend() -> VirtualBackendCandidate:
    spec = VirtualCellSpec(
        image="base-ubuntu",
        cpu_cores=1.0,
        memory_bytes=1024**3,
        disk_bytes=8 * 1024**3,
        network_policy=NetworkPolicy.NONE,
        exoskeleton=False,
        capacity=make_capacity(),
        comb_shield=CombShieldLevel.MEADOW,
        hive_id=new_hive_id(_CLOCK),
    )
    capabilities = BackendCapabilities(can_snapshot=False, can_pause=True, headroom=None)
    return VirtualBackendCandidate(name="docker", capabilities=capabilities, specs=(spec,))


def _decide(needs: TaskNeeds, inventory: Inventory) -> Placement:
    forage = ForageView(footprint=make_footprint())
    return decide(needs, inventory, forage, PlacementPolicy(prefer="real"))


def test_a_task_that_runs_commands_skips_the_scratch_hive_stand_for_a_virtual_cell() -> None:
    real = _real(AccessLevel.SCRATCH)

    placement = _decide(_COMMANDS, Inventory(real=(real,), virtual_backends=(_backend(),)))

    assert isinstance(placement, ProvisionVirtual)
    assert "running commands needs FULL access" in placement.reason


def test_a_task_that_runs_commands_takes_a_full_real_cell_or_a_contained_one() -> None:
    full = _real(AccessLevel.FULL)
    contained = _real(AccessLevel.SCRATCH, make_capabilities(processes_contained=True))

    assert isinstance(_decide(_COMMANDS, Inventory(real=(full,))), ReuseReal)
    assert isinstance(_decide(_COMMANDS, Inventory(real=(contained,))), ReuseReal)


def test_a_task_that_runs_no_commands_still_takes_the_scratch_hive_stand() -> None:
    real = _real(AccessLevel.SCRATCH)

    assert isinstance(_decide(TaskNeeds(), Inventory(real=(real,))), ReuseReal)


def test_with_no_virtual_backend_a_task_that_runs_commands_fails_for_good() -> None:
    # Nothing could place it later: the dispatcher cancels a final error with its message.
    real = _real(AccessLevel.SCRATCH)

    with pytest.raises(PlacementError, match="no Virtual backend is configured") as raised:
        _decide(_COMMANDS, Inventory(real=(real,)))

    assert raised.value.final is True
    assert "processes_contained=false" in str(raised.value)


def test_with_no_cell_attached_yet_a_task_that_runs_commands_waits() -> None:
    with pytest.raises(PlacementError) as raised:
        _decide(_COMMANDS, Inventory())

    assert raised.value.final is False


def test_a_busy_full_real_cell_leaves_a_task_that_runs_commands_waiting() -> None:
    busy = dataclasses.replace(_real(AccessLevel.FULL), has_free_capacity=False)

    with pytest.raises(PlacementError) as raised:
        _decide(_COMMANDS, Inventory(real=(busy,)))

    assert raised.value.final is False
