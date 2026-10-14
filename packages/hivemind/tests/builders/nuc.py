"""Build a Warden the Queen can promote to a Nuc, and the orders she sends it.

A device's Warden declares a model server and starts none until the Queen promotes the device
(roadmap step 11.8), so `make_nuc_rig` builds a started Warden over a hosting-capable Cell whose
one declared server does not load at start (`builders.hosting.make_hosting_warden` with
`load_on_start=False`): its server `box` offers `small` (4 GiB), `mid` (6 GiB) and `large`
(8 GiB), each a Forage map source with two seats. `allow` stands in for the `CeilingsSet` the Queen
sends before she orders a promotion; `promote_order` and `demote_order` build the
`swarm.nuc_promote` she sends; `reply_of` reads the answer off the Queen's end of the wire.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tests under
    packages/hivemind/tests/unit/wardens/ticks/nuc and the Queen's swarm gate tests.

Key invariants:
    - The rig's Warden is started (it holds a lease on its Cell); the test stops it.
    - Nothing here sends a `CeilingsSet`; `allow` sets what that message would have set.

See Also:
    - builders.hosting for the hosting rig this builds on.
    - hivemind.wardens.ticks.nuc for the handler under test.
"""

from __future__ import annotations

from dataclasses import dataclass

from builders.hosting import RegistryDoor, make_hosting_warden, vram_source
from builders.wardens import QueenEnd

from hivemind.forage import Ceilings, ForageMap
from hivemind.wardens import DeviceDeps, Warden, WardenDeps
from waggle.clock import FakeClock
from waggle.envelope import Envelope
from waggle.ids import new_device_id
from waggle.messages.swarm import NucAction, NucPromote, NucPromoted

GIB = 1024**3
SERVER_ID = "box"  # The one server the rig's Warden declares.
MODELS = (("small", 4 * GIB), ("mid", 6 * GIB), ("large", 8 * GIB))  # What `box` may serve.

__all__ = [
    "GIB",
    "MODELS",
    "SERVER_ID",
    "NucRig",
    "allow",
    "demote_order",
    "make_nuc_rig",
    "promote_order",
    "reply_of",
]


@dataclass
class NucRig:
    """A started Warden a test can promote, with the Queen's end of its wire."""

    warden: Warden
    deps: WardenDeps
    queen: QueenEnd
    forage_map: ForageMap
    door: RegistryDoor
    clock: FakeClock


async def make_nuc_rig(*, can_host_model: bool = True, device: DeviceDeps | None = None) -> NucRig:
    """Build and start a Warden whose server `box` loads only when it is promoted."""
    # Three models of different sizes to choose between, none loaded until the Queen asks.
    sources = tuple(vram_source(name, need) for name, need in MODELS)
    warden, deps, queen, forage_map, door = make_hosting_warden(
        can_host_model=can_host_model, load_on_start=False, sources=sources, device=device
    )
    await warden.start()
    assert isinstance(deps.clock, FakeClock)
    return NucRig(warden, deps, queen, forage_map, door, deps.clock)


def allow(warden: Warden, *source_ids: str, vram: int = 16 * GIB) -> None:
    """Set the ceilings the Queen's `CeilingsSet` would: these sources, this much VRAM."""
    warden._ceilings = Ceilings(
        max_sub_bees=2,
        model_vram_bytes=vram,
        model_disk_bytes=0,
        loadable_sources=source_ids,
        exportable_seats=0,
    )


def promote_order(
    warden: Warden, *models: str, server_id: str = SERVER_ID, deadline_s: float = 30.0
) -> NucPromote:
    """Build the `swarm.nuc_promote(PROMOTE)` the Queen sends for this Warden's Cell."""
    cell = warden._cell
    assert cell is not None, "start the Warden first"
    return NucPromote(
        cell_id=cell.id,
        device_id=new_device_id(warden._deps.clock),
        action=NucAction.PROMOTE,
        server_id=server_id,
        initial_models=models,
        deadline_s=deadline_s,
        reason="A test promotes this device.",
    )


def demote_order(warden: Warden) -> NucPromote:
    """Build the `swarm.nuc_promote(DEMOTE)` the Queen sends for this Warden's Cell."""
    cell = warden._cell
    assert cell is not None, "start the Warden first"
    return NucPromote(
        cell_id=cell.id,
        device_id=new_device_id(warden._deps.clock),
        action=NucAction.DEMOTE,
        server_id=None,
        initial_models=(),
        deadline_s=30.0,
        reason="A test demotes this device.",
    )


async def reply_of(rig: NucRig, nth: int = 1) -> tuple[Envelope, NucPromoted]:
    """Wait for the Warden's `nth` `swarm.nuc_promoted` and return its envelope and payload."""
    await rig.queen.pump_until(lambda: len(rig.queen.nuc_replies) >= nth)
    envelope = rig.queen.nuc_replies[nth - 1]
    assert isinstance(envelope.payload, NucPromoted)
    return envelope, envelope.payload
