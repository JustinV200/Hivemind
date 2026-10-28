"""Chaos: a Swarm node's Warden ships a forged trail segment; the merge must be refused.

Roadmap 13.6, on a whole Hive with the Swarm on (`e2e.swarm.hive`). A colonized device is enrolled
with its own key, and its Warden dials the Queen through the swarm gate's real door (the one the
listener's router hands a verified connection to). It then ships two forged segments over its
proved link: one signed with a key that is not the node's, one claiming to be another node's. Each
is refused and recorded (`guard.segment_refused`), nothing in either reaches the Queen's trail,
and the link stays up, an honest Warden's bad export being a fault to hear of, not to cut off for.
Nothing is left open once the Hive is down.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests/unit/queen/swarm_gate/test_attach.py for each refusal over the gate's fakes.
    - hivemind.queen.attach.refusals for what a refused segment records.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from builders.queen import WardenEnd
from builders.segments import SegmentRig, segment_chunks, signed
from builders.swarm.enrolment import enrol_node
from builders.swarm.parties import (
    DeviceParty,
    make_cell_ready,
    make_device_party,
    make_swarm_node,
)
from e2e.chaos.rig import assert_nothing_left_open, bounded
from e2e.kernel_helpers import wait_until
from e2e.scripted_openai import free_port
from e2e.swarm.hive import swarm_hive

from hivemind.cell import AccessLevel
from hivemind.cli.compose import Hive, run_hive
from hivemind.pheromone import TrailQuery
from hivemind.queen.swarm_gate import DeviceWardenDoor
from hivemind.swarm import RuntimeLevel, SwarmNode, WardenConnection, readable_roots
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop, wrap
from waggle.ids import new_node_id
from waggle.signing import Ed25519Signer
from waggle.transport.memory import MemoryTransport

pytestmark = [pytest.mark.e2e, pytest.mark.chaos]

_WAIT_S = 20.0
_REFUSED = "guard.segment_refused"


def test_a_forged_trail_segment_from_a_swarm_node_is_never_merged(tmp_path: Path) -> None:
    hive = swarm_hive(tmp_path, free_port())

    reasons, merged = asyncio.run(bounded("segment forged", _forged(hive)))

    assert reasons == {"bad_signature", "another_node"}
    assert merged == []  # Not one forged event reached the Queen's trail.
    asyncio.run(assert_nothing_left_open(hive))


async def _forged(hive: Hive) -> tuple[set[str], list[str]]:
    """Dial a colonized device's Warden in, ship two forged segments, and read what landed."""
    clock = FakeClock()  # Mints the device's and the segments' ids; the Hive keeps its own time.
    async with run_hive(hive):
        party, node = await _enrolled(hive, clock)
        party_signer = party.signer
        end, handler = await _dial(hive, party, node, clock)
        attached = len(hive.queen.wardens)
        segment = SegmentRig(
            clock, hive.manifest.hive.id, node.cell_id, node.node_id, node.warden_id, party_signer
        )
        # Signed, but with a key that is not this node's.
        for chunk in signed(await segment_chunks(segment), Ed25519Signer.generate()):
            await end.send(chunk)
        await wait_until(lambda: _refusals(hive, 1), timeout_s=_WAIT_S)
        # Signed with the node's own key, but claiming another node wrote it.
        other = SegmentRig(
            clock, segment.hive_id, node.cell_id, new_node_id(clock), node.warden_id, party_signer
        )
        for chunk in signed(await segment_chunks(other), party_signer):
            await end.send(chunk)
        await wait_until(lambda: _refusals(hive, 2), timeout_s=_WAIT_S)
        assert len(hive.queen.wardens) == attached  # Refused, not cut off.
        refused = await hive.stores.trail.query(TrailQuery(kind=_REFUSED))
        merged = await hive.stores.trail.query(TrailQuery(subject_id=node.cell_id))
        await end.close()
        await asyncio.wait_for(handler, _WAIT_S)
    reasons = {str(event.payload["reason"]) for event in refused}
    return reasons, [event.kind for event in merged if event.kind == "cell.provisioned"]


async def _enrolled(hive: Hive, clock: FakeClock) -> tuple[DeviceParty, SwarmNode]:
    """Enrol a colonized device with FULL access; the device (its key) and its row."""
    assert hive.swarm is not None
    party = make_device_party(clock)
    node = make_swarm_node(
        party, clock, level=RuntimeLevel.COLONIZED, access_level=AccessLevel.FULL
    )
    return party, await enrol_node(hive.swarm.registry, node, clock)


async def _dial(
    hive: Hive, party: DeviceParty, node: SwarmNode, clock: FakeClock
) -> tuple[WardenEnd, asyncio.Task[None]]:
    """Connect `node`'s Warden to the gate's door, as the listener's router does once verified."""
    assert hive.swarm is not None
    # The door the gate bound into the listener at start; the router's own handle on it.
    door = hive.swarm.listener._router._warden_handler
    assert isinstance(door, DeviceWardenDoor)
    queen_end, warden_end = MemoryTransport.pair(Codec(), Codec())
    hop = Hop(sender=node.warden_id, recipient=hive.manifest.hive.id, node_id=node.node_id)
    ready = make_cell_ready(
        party,
        access_level=node.access_level.to_wire(),
        comb_shield=node.comb_shield.to_wire(),
        watch_roots=readable_roots(node),
    )
    connection = WardenConnection(
        node, queen_end, wrap(ready, hop, clock=clock), _frames(queen_end)
    )
    handler = asyncio.ensure_future(door.handle(connection))
    await wait_until(
        lambda: any(link.warden_id == node.warden_id for link in hive.queen.wardens),
        timeout_s=_WAIT_S,
    )
    return WardenEnd(warden_end, hop, clock), handler


async def _frames(transport: MemoryTransport) -> AsyncGenerator[Envelope, None]:
    """Yield every frame the Queen's end receives, as the router's pinned connection does."""
    async for envelope in transport.receive():
        yield envelope


async def _refusals(hive: Hive, count: int) -> bool:
    """Whether `count` segment refusals are on the trail."""
    return len(await hive.stores.trail.query(TrailQuery(kind=_REFUSED))) >= count
