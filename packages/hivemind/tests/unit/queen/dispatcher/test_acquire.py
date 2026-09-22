"""Tests for hivemind.queen.dispatcher.acquire: resolve_link, with a fake VirtualCellProvider.

Fits into the Hive:
    Mirrors src/hivemind/queen/dispatcher/acquire.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.dispatcher.acquire for the module under test.
    - docs/adr/0028-placement-policy-real-versus-virtual.md for the retry-once Consequence these
      tests exercise.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from builders.cells import make_cell
from builders.forage import make_capacity
from builders.queen import make_queen_deps
from builders.tasks import make_task

from hivemind.brood_chamber import Task
from hivemind.cell import CellKind, CombShieldLevel
from hivemind.hive import BackendCapabilities, CellProvisionError, NetworkPolicy, VirtualCellSpec
from hivemind.queen.deps import WardenLink
from hivemind.queen.dispatcher.acquire import resolve_link
from hivemind.queen.placement import (
    DormantCandidate,
    Placement,
    PlacementError,
    PlacementPolicy,
    ProvisionVirtual,
    ReuseDormant,
    ReuseReal,
    VirtualBackendCandidate,
)
from waggle.clock import Clock
from waggle.ids import new_cell_id, new_hive_id, new_warden_id


@dataclass
class FakeVirtualCellProvider:
    """A scriptable VirtualCellProvider: fails `fail_times` calls, then returns `result`."""

    result: WardenLink
    fail_times: int = 0
    calls: list[Placement] = field(default_factory=list)

    async def acquire(self, placement: Placement, task: Task) -> WardenLink:
        """Record the attempt; fail `fail_times` times before returning `result`."""
        self.calls.append(placement)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise CellProvisionError("fake", "base-ubuntu", "boom")
        return self.result


def _spec(clock: Clock, image: str = "base-ubuntu") -> VirtualCellSpec:
    return VirtualCellSpec(
        image=image,
        cpu_cores=1.0,
        memory_bytes=1024**3,
        disk_bytes=8 * 1024**3,
        network_policy=NetworkPolicy.NONE,
        capacity=make_capacity(),
        comb_shield=CombShieldLevel.MEADOW,
        hive_id=new_hive_id(clock),
    )


def _backend(clock: Clock, name: str, headroom: int | None = None) -> VirtualBackendCandidate:
    capabilities = BackendCapabilities(can_snapshot=False, can_pause=True, headroom=headroom)
    return VirtualBackendCandidate(name=name, capabilities=capabilities, specs=(_spec(clock),))


async def test_reuse_real_looks_up_its_own_attached_warden() -> None:
    deps, link, warden_end = make_queen_deps()
    task = make_task()
    placement = ReuseReal(link.cell.id, link.warden_id, "test")

    resolved, effective = await resolve_link(deps, (link,), task, placement)

    assert resolved is link
    assert effective is placement
    await warden_end.close()


async def test_reuse_real_with_an_unattached_warden_raises_placement_error() -> None:
    deps, link, warden_end = make_queen_deps()
    task = make_task()
    placement = ReuseReal(new_cell_id(deps.clock), new_warden_id(deps.clock), "test")

    with pytest.raises(PlacementError):
        await resolve_link(deps, (link,), task, placement)
    await warden_end.close()


async def test_provision_virtual_with_no_provider_configured_raises_placement_error() -> None:
    deps, link, warden_end = make_queen_deps()
    task = make_task()
    placement = ProvisionVirtual(_spec(deps.clock), "docker", "test")

    with pytest.raises(PlacementError, match="VirtualCellProvider"):
        await resolve_link(deps, (link,), task, placement)
    await warden_end.close()


async def test_provision_virtual_calls_the_configured_provider() -> None:
    deps, link, warden_end = make_queen_deps()
    fresh_cell = make_cell(kind=CellKind.VIRTUAL, clock=deps.clock)
    fresh_link = WardenLink(
        warden_id=new_warden_id(deps.clock), cell=fresh_cell, transport=link.transport, hop=link.hop
    )
    provider = FakeVirtualCellProvider(result=fresh_link)
    deps2, link2, warden_end2 = make_queen_deps(virtual_provider=provider)
    task = make_task()
    placement = ProvisionVirtual(_spec(deps2.clock), "docker", "test")

    resolved, effective = await resolve_link(deps2, (link2,), task, placement)

    assert resolved is fresh_link
    assert effective is placement
    assert len(provider.calls) == 1
    await warden_end.close()
    await warden_end2.close()


async def test_a_failed_provision_retries_once_with_that_backends_headroom_zeroed() -> None:
    """ADR-0028 Consequences: re-enter placement once, with the failed backend's headroom zeroed."""
    deps, link, warden_end = make_queen_deps()
    fresh_cell = make_cell(kind=CellKind.VIRTUAL, clock=deps.clock)
    fresh_link = WardenLink(
        warden_id=new_warden_id(deps.clock), cell=fresh_cell, transport=link.transport, hop=link.hop
    )
    provider = FakeVirtualCellProvider(result=fresh_link, fail_times=1)
    backend_a = _backend(deps.clock, "docker")
    backend_b = _backend(deps.clock, "qemu")
    policy = PlacementPolicy(prefer="virtual")
    deps2, link2, warden_end2 = make_queen_deps(
        virtual_provider=provider,
        virtual_backends=(backend_a, backend_b),
        placement_policy=policy,
    )
    task = make_task()
    placement = ProvisionVirtual(_spec(deps2.clock), "docker", "test")

    resolved, effective = await resolve_link(deps2, (link2,), task, placement)

    assert resolved is fresh_link
    assert isinstance(effective, ProvisionVirtual)
    assert effective.backend == "qemu"  # docker's own headroom was zeroed on the retry.
    assert len(provider.calls) == 2
    await warden_end.close()
    await warden_end2.close()


async def test_a_failed_dormant_resume_retries_once_excluding_that_cell() -> None:
    """docs/adr/0029: a failed dormant resume falls through to a fresh provision."""
    deps, link, warden_end = make_queen_deps()
    fresh_cell = make_cell(kind=CellKind.VIRTUAL, clock=deps.clock)
    fresh_link = WardenLink(
        warden_id=new_warden_id(deps.clock), cell=fresh_cell, transport=link.transport, hop=link.hop
    )
    provider = FakeVirtualCellProvider(result=fresh_link, fail_times=1)
    backend = _backend(deps.clock, "docker")
    dormant_cell_id = new_cell_id(deps.clock)
    dormant = DormantCandidate(
        cell_id=dormant_cell_id,
        warden_id=new_warden_id(deps.clock),
        image="base-ubuntu",
        comb_shield=CombShieldLevel.MEADOW,
    )
    policy = PlacementPolicy(prefer="virtual")
    deps2, link2, warden_end2 = make_queen_deps(
        virtual_provider=provider,
        virtual_backends=(backend,),
        dormant_cells=(dormant,),
        placement_policy=policy,
    )
    task = make_task()
    placement = ReuseDormant(dormant_cell_id, dormant.warden_id, "test")

    resolved, effective = await resolve_link(deps2, (link2,), task, placement)

    # The dormant Cell's own resume failed, so the retry falls through to a fresh provision.
    assert resolved is fresh_link
    assert isinstance(effective, ProvisionVirtual)
    assert len(provider.calls) == 2
    await warden_end.close()
    await warden_end2.close()


async def test_the_retry_zeroes_the_live_backend_source_not_the_static_tuple() -> None:
    """The retry zeroes a copy of the live source, not the static tuple.

    A real Hive names its backends only through `virtual_backend_source`; zeroing the static
    tuple made the retry see no Virtual side at all (the first real Docker run).
    """
    deps, link, warden_end = make_queen_deps()
    fresh_cell = make_cell(kind=CellKind.VIRTUAL, clock=deps.clock)
    fresh_link = WardenLink(
        warden_id=new_warden_id(deps.clock), cell=fresh_cell, transport=link.transport, hop=link.hop
    )
    provider = FakeVirtualCellProvider(result=fresh_link, fail_times=1)
    live = (_backend(deps.clock, "docker"), _backend(deps.clock, "qemu"))

    async def source() -> tuple[VirtualBackendCandidate, ...]:
        return live

    deps2, link2, warden_end2 = make_queen_deps(
        virtual_provider=provider,
        virtual_backend_source=source,
        placement_policy=PlacementPolicy(prefer="virtual"),
    )
    task = make_task()
    placement = ProvisionVirtual(_spec(deps2.clock), "docker", "test")

    resolved, effective = await resolve_link(deps2, (link2,), task, placement)

    assert resolved is fresh_link
    assert isinstance(effective, ProvisionVirtual)
    assert effective.backend == "qemu"
    await warden_end.close()
    await warden_end2.close()
