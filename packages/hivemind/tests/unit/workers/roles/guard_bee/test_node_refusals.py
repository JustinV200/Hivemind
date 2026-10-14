"""Tests for the Guard Bee over a Waggle peer's refusals: repeated floods and bursts of replays.

The Hive Stand's listeners close a connection that floods past its frame ceiling and drop an
envelope id they already handled, recording `guard.frame_ceiling` and `guard.replay_refused` with
the node's id (roadmap steps 11.3a and 11.3b, ADR-0055), and the Guard Bee counts them: three
closes of one node in ten minutes, five replays in five. The events here are written by the real
`TransportRefusals` the listeners run, onto the rig's trail, so a change to what a listener records
that the rules no longer read fails here. What is counted is per node (the same count spread over
several nodes is nobody's pattern), a node the Queen bound to a Cell gets that Cell isolated by
request, one nothing binds is only observed, a refusal that named no node is counted against no
one, and a Cell's own node can never write its way into a count against another node.

Fits into the Hive:
    Mirrors the transport-refusal rules of src/hivemind/workers/roles/guard_bee/rules.toml
    (codingrules section 3), over whole Guard Bee rounds.

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests.unit.swarm.gateway.test_refusals for what the listener records, alone.
    - docs/guard/guard-bee.md for the two rules.
"""

from __future__ import annotations

from builders.cells import make_identity
from builders.guard_bee import GuardBeeRig, TrailSeeder, bind_cell, make_guard_bee

from hivemind.guard import GuardAction, GuardConfidence
from hivemind.swarm import SwarmRecorder
from hivemind.swarm.gateway import GatewayHub, TransportRefusals
from waggle.ids import NodeId, new_message_id, new_node_id
from waggle.transport.limits import ConnectionRefusal, RefusalKind

_WINDOW_S = 601.0  # Past the ten minutes a frame-ceiling count is made over.


async def _refuse(rig: GuardBeeRig, kind: RefusalKind, node: NodeId | None, times: int) -> None:
    """Have the Hive Stand's listener callback record `times` refusals of `kind` about `node`."""
    recorder = SwarmRecorder(rig.trail, rig.clock, rig.identity)
    refusals = TransportRefusals(recorder, GatewayHub(rig.clock), rig.identity.hive_id)
    for _ in range(times):
        # A flood concerns the connection and no envelope; a replay names the id it repeated.
        envelope = None if kind is RefusalKind.FRAME_CEILING else str(new_message_id(rig.clock))
        await refusals(ConnectionRefusal(node, kind, envelope, rig.clock.now()))


async def test_three_floods_from_one_node_are_one_finding_naming_that_node() -> None:
    rig = make_guard_bee()
    node = new_node_id(rig.clock)
    await _refuse(rig, RefusalKind.FRAME_CEILING, node, 3)

    await rig.bee.tick()

    [alert] = await rig.alerts()
    assert alert.payload["rule"] == "frame_ceiling_repeats"
    assert (alert.payload["group"], alert.payload["key"]) == ("node", node)
    assert alert.subject_id == node
    # Nothing binds this node to a Cell, so there is none to isolate: the finding is only seen.
    assert (alert.payload["action"], alert.payload["disposition"]) == ("observe", "observed")
    assert rig.door.filed == []


async def test_two_floods_are_not_a_repeat() -> None:
    rig = make_guard_bee()
    await _refuse(rig, RefusalKind.FRAME_CEILING, new_node_id(rig.clock), 2)

    await rig.bee.tick()

    assert await rig.alerts() == []


async def test_floods_spread_over_nodes_are_nobodys_repeat() -> None:
    rig = make_guard_bee()
    for _ in range(3):
        await _refuse(rig, RefusalKind.FRAME_CEILING, new_node_id(rig.clock), 1)

    await rig.bee.tick()

    assert await rig.alerts() == []


async def test_floods_ten_minutes_apart_are_not_a_repeat() -> None:
    rig = make_guard_bee()
    node = new_node_id(rig.clock)
    await _refuse(rig, RefusalKind.FRAME_CEILING, node, 2)
    rig.clock.advance(_WINDOW_S)
    await _refuse(rig, RefusalKind.FRAME_CEILING, node, 1)

    await rig.bee.tick()

    assert await rig.alerts() == []


async def test_a_node_the_queen_bound_to_a_cell_gets_that_cell_isolated_by_request() -> None:
    rig = make_guard_bee()
    node = new_node_id(rig.clock)
    cell = await bind_cell(rig.seed, node)  # The Queen's warden.spawned: this node speaks for it.
    await _refuse(rig, RefusalKind.FRAME_CEILING, node, 3)

    await rig.bee.tick()

    [report] = rig.door.filed
    assert report.rule == "frame_ceiling_repeats"
    assert (report.recommended, report.confidence) == (
        GuardAction.ISOLATE_CELL,
        GuardConfidence.HIGH,
    )
    assert report.cell_id == cell
    [alert] = await rig.alerts()
    assert (alert.payload["key"], alert.payload["disposition"]) == (node, "filed")


async def test_five_replays_from_one_node_are_a_burst_and_four_are_not() -> None:
    rig = make_guard_bee()
    burst, fewer = new_node_id(rig.clock), new_node_id(rig.clock)
    await _refuse(rig, RefusalKind.REPLAY, fewer, 4)
    await _refuse(rig, RefusalKind.REPLAY, burst, 5)

    await rig.bee.tick()

    [alert] = await rig.alerts()
    assert (alert.payload["rule"], alert.payload["key"]) == ("replay_refusal_burst", burst)


async def test_a_nodes_floods_and_replays_are_counted_by_their_own_rules_alone() -> None:
    rig = make_guard_bee()
    node = new_node_id(rig.clock)
    await _refuse(rig, RefusalKind.FRAME_CEILING, node, 3)
    await _refuse(rig, RefusalKind.REPLAY, node, 4)  # One short of a burst.

    await rig.bee.tick()

    assert [alert.payload["rule"] for alert in await rig.alerts()] == ["frame_ceiling_repeats"]


async def test_a_refusal_that_named_no_node_is_counted_against_no_one() -> None:
    rig = make_guard_bee()
    await _refuse(rig, RefusalKind.FRAME_CEILING, None, 10)  # Tripped before any frame decoded.

    await rig.bee.tick()

    assert await rig.alerts() == []


async def test_a_cells_own_records_cannot_write_a_count_against_another_node() -> None:
    rig = make_guard_bee()
    cell_node = new_node_id(rig.clock)
    await bind_cell(rig.seed, cell_node)
    identity = make_identity(rig.clock, hive_id=rig.identity.hive_id, node_id=cell_node)
    cell_seed = TrailSeeder(rig.trail, rig.clock, identity)
    victim = new_node_id(rig.clock)
    for _ in range(3):
        # What only the Hive Stand's listener may say, said from a Cell's shipped segment.
        await cell_seed.transport_refusal("guard.frame_ceiling", victim)

    await rig.bee.tick()

    assert await rig.alerts() == []
