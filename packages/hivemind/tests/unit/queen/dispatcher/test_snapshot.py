"""Tests for hivemind.queen.dispatcher.snapshot: build_inventory and build_forage_view.

Fits into the Hive:
    Mirrors src/hivemind/queen/dispatcher/snapshot.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.dispatcher.snapshot for the module under test.
"""

from __future__ import annotations

from builders.queen import make_queen_deps

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.hive import BackendCapabilities
from hivemind.memory import MemoryContext, WaxSeverity
from hivemind.memory.cell_wax.writes import WaxProposalInput, propose_wax, write_wax
from hivemind.queen.deps import QueenDeps
from hivemind.queen.dispatcher.snapshot import build_forage_view, build_inventory
from hivemind.queen.placement import DormantCandidate, VirtualBackendCandidate
from waggle.ids import CellId
from waggle.messages.cell.wax import WaxDecision, WaxOrigin


async def _write_wax(deps: QueenDeps, cell_id: CellId, severity: WaxSeverity, text: str) -> None:
    """Propose then write one CellWax note directly, bypassing autopilot for this test's setup."""
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    proposal = WaxProposalInput(
        cell_id=cell_id,
        severity=severity,
        text=text,
        reason="test setup",
        clearance=HoneyClearance.C1,
        origin=WaxOrigin.HUMAN,
    )
    proposed = await propose_wax(proposal, text_cap_chars=4_000, ctx=ctx)
    await write_wax(proposed, WaxDecision.AUTOPILOT, "test setup", ctx)


async def test_build_inventory_carries_one_real_candidate_per_attached_warden() -> None:
    deps, link, warden_end = make_queen_deps()

    inventory = await build_inventory(deps, (link,))

    assert len(inventory.real) == 1
    assert inventory.real[0].cell_id == link.cell.id
    assert inventory.real[0].warden_id == link.warden_id
    assert inventory.real[0].has_free_capacity is True
    await warden_end.close()


async def test_build_inventory_reads_blocked_and_cautioned_wax() -> None:
    deps, link, warden_end = make_queen_deps()
    await _write_wax(deps, link.cell.id, WaxSeverity.BLOCK, "disk nearly full")

    inventory = await build_inventory(deps, (link,))

    assert link.cell.id in inventory.blocked
    assert inventory.blocked[link.cell.id].text == "disk nearly full"
    assert inventory.cautioned == {}
    await warden_end.close()


async def test_build_inventory_reads_deps_own_virtual_backends_and_dormant_cells() -> None:
    backend = VirtualBackendCandidate(
        name="docker", capabilities=BackendCapabilities(can_snapshot=False, can_pause=True)
    )
    deps, link, warden_end = make_queen_deps(virtual_backends=(backend,))

    inventory = await build_inventory(deps, (link,))

    assert inventory.virtual_backends == (backend,)
    await warden_end.close()


async def test_build_inventory_virtual_backends_override_replaces_deps_own_backends() -> None:
    original = VirtualBackendCandidate(
        name="docker", capabilities=BackendCapabilities(can_snapshot=False, can_pause=True)
    )
    deps, link, warden_end = make_queen_deps(virtual_backends=(original,))
    zeroed = VirtualBackendCandidate(
        name="docker",
        capabilities=BackendCapabilities(can_snapshot=False, can_pause=True, headroom=0),
    )

    inventory = await build_inventory(deps, (link,), virtual_backends=(zeroed,))

    assert inventory.virtual_backends == (zeroed,)
    await warden_end.close()


async def test_build_inventory_exclude_dormant_drops_the_named_cell() -> None:
    dormant = DormantCandidate(
        cell_id=CellId("cell_dormant"),
        warden_id=None,
        image="base-ubuntu",
        comb_shield=CombShieldLevel.MEADOW,
    )
    deps, link, warden_end = make_queen_deps(dormant_cells=(dormant,))

    inventory = await build_inventory(deps, (link,), exclude_dormant=frozenset({dormant.cell_id}))

    assert inventory.dormant == ()
    await warden_end.close()


async def test_build_forage_view_carries_the_drone_footprint() -> None:
    deps, _link, warden_end = make_queen_deps()

    forage_view = build_forage_view(deps)

    assert forage_view.footprint is not None
    await warden_end.close()
