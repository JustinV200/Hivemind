"""Tests for hivemind.queen.swarm_gate.colonize.nuc.desk: the terms a Warden says it runs under.

A Warden holds its access level and Comb Shield tier in its environment, so one the operator has
since changed is still running under the old ones until it is restarted; the desk keeps what each
Warden's `cell.ready` said so the Queen can tell. The same frame is a new connection, and a Warden
that has just connected has not shown its model server is running (a Warden that restarted starts
none), so the desk calls the device unproven until a flow of the Queen's has seen the server
answer.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/colonize/nuc/desk.py (codingrules section 3), split by
    feature (14.2) from test_desk.py, which covers the declared servers and the orders.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.colonize.nuc.desk for the module under test.
    - hivemind.queen.swarm_gate.colonize.renew for what the reported terms are compared with.
"""

from __future__ import annotations

from builders.swarm import (
    make_capacity_report,
    make_cell_ready,
    make_device_party,
    make_swarm_node,
)
from builders.swarm.parties import DeviceParty

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.queen.swarm_gate.colonize.nuc.desk import NucDesk, Reported
from hivemind.swarm import SwarmNode
from waggle.clock import FakeClock
from waggle.envelope import Envelope, Hop, wrap
from waggle.ids import new_cell_id
from waggle.messages.cell.status import AttestationCheck

_CLOCK = FakeClock()


def _scene() -> tuple[NucDesk, SwarmNode, DeviceParty]:
    """A desk and one device, whose Warden has said nothing yet."""
    party = make_device_party(_CLOCK)
    return NucDesk(), make_swarm_node(party, _CLOCK), party


def _ready(node: SwarmNode, party: DeviceParty, **fields: object) -> Envelope:
    """The `cell.ready` the device's Warden connects with, as it arrives at the gate's door."""
    hop = Hop(sender=node.warden_id, recipient=node.device_id, node_id=node.node_id)
    return wrap(make_cell_ready(party, **fields), hop, clock=_CLOCK)


def test_a_device_whose_warden_has_not_connected_has_reported_no_terms() -> None:
    desk, node, _party = _scene()

    assert desk.reported(node.device_id) is None
    assert not desk.is_proven(node.device_id)


def test_the_terms_a_warden_connects_with_are_noted_and_the_frame_goes_on_to_the_queen() -> None:
    desk, node, party = _scene()
    frame = _ready(node, party, access_level=AccessLevel.FULL.to_wire())

    consumed = desk.heard(node, frame)

    assert consumed is False  # The Queen's attach path reads the same frame.
    assert desk.reported(node.device_id) == Reported(AccessLevel.FULL, CombShieldLevel.MEADOW)


def test_the_directories_a_warden_was_started_to_watch_are_noted_with_its_terms() -> None:
    desk, node, party = _scene()
    roots = ("/var/log/app", "/srv/data")

    desk.heard(node, _ready(node, party, watch_roots=roots))

    reported = desk.reported(node.device_id)
    assert reported is not None and reported.watch_roots == roots


def test_a_warden_that_names_no_directories_has_reported_none() -> None:
    desk, node, party = _scene()

    desk.heard(node, _ready(node, party))  # The default `cell.ready`, as an older Warden sends.

    reported = desk.reported(node.device_id)
    assert reported is not None and reported.watch_roots == ()


def test_a_tier_above_the_meadow_is_noted_too() -> None:
    desk, node, party = _scene()
    check = AttestationCheck(name="tunnel", has_passed=True, detail="The tunnel carries the route.")
    frame = _ready(
        node, party, comb_shield=CombShieldLevel.PROPOLIS.to_wire(), attestation=(check,)
    )

    desk.heard(node, frame)

    reported = desk.reported(node.device_id)
    assert reported is not None and reported.comb_shield is CombShieldLevel.PROPOLIS


def test_the_latest_connection_replaces_the_terms_of_the_one_before() -> None:
    desk, node, party = _scene()
    desk.heard(node, _ready(node, party, access_level=AccessLevel.FULL.to_wire()))

    desk.heard(node, _ready(node, party, access_level=AccessLevel.READ_ONLY.to_wire()))

    reported = desk.reported(node.device_id)
    assert reported is not None and reported.access_level is AccessLevel.READ_ONLY


def test_a_cell_ready_naming_another_cell_is_not_noted() -> None:
    desk, node, party = _scene()

    desk.heard(node, _ready(node, party, cell_id=new_cell_id(_CLOCK)))

    assert desk.reported(node.device_id) is None  # A Warden speaks only for its own Cell.


def test_a_flow_that_proves_a_device_proves_it_until_its_warden_connects_again() -> None:
    desk, node, party = _scene()
    desk.heard(node, _ready(node, party))

    desk.prove(node.device_id)

    assert desk.is_proven(node.device_id)
    desk.heard(node, wrap(make_capacity_report(party), _hop(node), clock=_CLOCK))
    assert desk.is_proven(node.device_id)  # Other frames prove nothing and undo nothing.
    desk.heard(node, _ready(node, party))
    assert not desk.is_proven(node.device_id)  # The Warden restarted, or the link was remade.


def test_proof_is_the_devices_own() -> None:
    desk, node, party = _scene()
    other_party = make_device_party(_CLOCK)
    other = make_swarm_node(other_party, _CLOCK)
    desk.heard(node, _ready(node, party))
    desk.heard(other, _ready(other, other_party))

    desk.prove(node.device_id)

    assert desk.is_proven(node.device_id) and not desk.is_proven(other.device_id)


def test_forgetting_a_device_forgets_its_terms_and_its_proof() -> None:
    desk, node, party = _scene()
    desk.heard(node, _ready(node, party))
    desk.prove(node.device_id)

    desk.forget(node.device_id)

    assert desk.reported(node.device_id) is None
    assert not desk.is_proven(node.device_id)


def _hop(node: SwarmNode) -> Hop:
    return Hop(sender=node.warden_id, recipient=node.device_id, node_id=node.node_id)
