"""Tests for hivemind.queen.swarm_gate.colonize.renew.drift: when a Warden runs under old terms.

A colonized device's Warden is started with its access level, Comb Shield tier and the directories
it watches, and keeps them until it is started again. The Queen tells that it has drifted from the
registry by what its `cell.ready` said, and `terms_drifted` is the whole comparison, so these tests
pin what counts as a difference: each of the three terms, and the one thing that must not: a
directory the device's capabilities do not let the Hive read, which the Queen never sends and the
Warden therefore can never report.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/colonize/renew/drift.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.colonize.renew.drift for the module under test.
    - hivemind.queen.swarm_gate.colonize.renew.flow for what is done about a difference.
"""

from __future__ import annotations

from builders.swarm import make_device_party, make_swarm_node

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.queen.swarm_gate.colonize.nuc.desk import Reported
from hivemind.queen.swarm_gate.colonize.renew.drift import terms_drifted
from hivemind.swarm import SwarmNode
from waggle.clock import FakeClock

_CLOCK = FakeClock()
_ROOTS = ("/var/log/app", "/srv/data")


def _node(**fields: object) -> SwarmNode:
    """A device on SCRATCH access and the Meadow tier, with `fields` replaced."""
    return make_swarm_node(make_device_party(_CLOCK), _CLOCK, **fields)


def _reported(node: SwarmNode, **fields: object) -> Reported:
    """The terms a Warden started under exactly `node`'s would report, with `fields` replaced."""
    terms: dict[str, object] = {
        "access_level": node.access_level,
        "comb_shield": node.comb_shield,
        "watch_roots": node.watch_roots,
    }
    return Reported(**{**terms, **fields})  # type: ignore[arg-type]


def test_a_warden_that_has_reported_nothing_is_not_drifted() -> None:
    assert not terms_drifted(None, _node(watch_roots=_ROOTS))


def test_a_warden_started_under_the_registrys_terms_is_not_drifted() -> None:
    node = _node(watch_roots=_ROOTS)

    assert not terms_drifted(_reported(node), node)


def test_a_warden_with_no_directories_to_watch_is_not_drifted_when_none_are_named() -> None:
    node = _node()

    assert not terms_drifted(Reported(node.access_level, node.comb_shield), node)


def test_a_different_access_level_is_a_drift_either_way() -> None:
    node = _node(access_level=AccessLevel.SCRATCH)

    assert terms_drifted(_reported(node, access_level=AccessLevel.FULL), node)
    assert terms_drifted(_reported(node, access_level=AccessLevel.READ_ONLY), node)


def test_a_different_tier_is_a_drift() -> None:
    node = _node()

    assert terms_drifted(_reported(node, comb_shield=CombShieldLevel.PROPOLIS), node)


def test_directories_named_since_the_warden_started_are_a_drift() -> None:
    node = _node(watch_roots=_ROOTS)

    assert terms_drifted(_reported(node, watch_roots=()), node)
    assert terms_drifted(_reported(node, watch_roots=_ROOTS[:1]), node)


def test_directories_withdrawn_since_the_warden_started_are_a_drift() -> None:
    node = _node()  # The operator cleared them (`hive swarm watch --clear`).

    assert terms_drifted(_reported(node, watch_roots=_ROOTS), node)


def test_other_directories_than_the_registrys_are_a_drift() -> None:
    node = _node(watch_roots=_ROOTS)

    assert terms_drifted(_reported(node, watch_roots=("/var/log/other",)), node)


def test_a_directory_the_capabilities_do_not_cover_is_never_sent_so_it_is_not_a_difference() -> (
    None
):
    # The Queen sends the packet only the directories the node's capabilities let the Hive read,
    # so a Warden can report no more than that. Comparing with the operator's whole list would
    # find a difference no restart could remove, and the device would be renewed for ever.
    node = _node(granted_capabilities=("fs:read:/srv/**",), watch_roots=("/srv/data", "/var/log"))

    assert not terms_drifted(_reported(node, watch_roots=("/srv/data",)), node)
    assert terms_drifted(_reported(node, watch_roots=("/srv/data", "/var/log")), node)
