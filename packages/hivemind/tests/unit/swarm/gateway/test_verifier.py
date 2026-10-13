"""Tests for hivemind.swarm.gateway.verifier: who may sign what on the swarm listener.

Fits into the Hive:
    Mirrors src/hivemind/swarm/gateway/verifier.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). Frames go through a real signing `Codec`, so the verifier is tested where it runs.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.gateway.verifier for the module under test.
    - docs/waggle/enrolment.md section 5.3 for the first-frame rule.
"""

from __future__ import annotations

import pytest
from builders.swarm import (
    DeviceParty,
    HiveParty,
    make_device_party,
    make_enrol_envelope,
    make_enrol_request,
    make_heartbeat,
    make_hive_party,
    make_swarm_node,
)

from hivemind.swarm.enrolment import new_invite_token
from hivemind.swarm.gateway import KeyRing, SwarmVerifier
from hivemind.swarm.gateway.verifier import ENROL_REQUEST_KIND
from hivemind.swarm.node_state import NodeStatus
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop, wrap
from waggle.errors import InvalidSignatureError, UnknownSignerError
from waggle.ids import new_device_id

_CLOCK = FakeClock()
_HIVE: HiveParty = make_hive_party(_CLOCK)
_CODE = new_invite_token()


def _frame(
    party: DeviceParty, payload: object | None = None, *, sender: str | None = None
) -> bytes:
    """Encode a signed heartbeat (or `payload`) the way `party` sends it."""
    hop = Hop(
        sender=sender if sender is not None else party.device_id,
        recipient=_HIVE.hive_id,
        node_id=party.node_id,
    )
    message = payload if payload is not None else make_heartbeat(party)
    return Codec(signer=party.signer).encode(wrap(message, hop, clock=_CLOCK))  # type: ignore[arg-type]


def _decode(ring: KeyRing, frame: bytes) -> Envelope:
    """Decode a frame the way the listener does: through a codec whose verifier is the ring's."""
    return Codec(verifier=SwarmVerifier(ring)).decode(frame)


def _ring(*parties: DeviceParty) -> KeyRing:
    """A ring that trusts each party's node."""
    ring = KeyRing()
    for party in parties:
        ring.add(make_swarm_node(party, _CLOCK))
    return ring


def test_a_known_node_signing_as_its_device_or_its_warden_is_accepted() -> None:
    party = make_device_party(_CLOCK)
    ring = _ring(party)

    assert _decode(ring, _frame(party)).sender == party.device_id
    assert _decode(ring, _frame(party, sender=party.warden_id)).sender == party.warden_id


def test_a_known_node_signing_for_an_address_it_is_not_bound_to_is_refused() -> None:
    party = make_device_party(_CLOCK)

    with pytest.raises(UnknownSignerError):
        _decode(_ring(party), _frame(party, sender=new_device_id(_CLOCK)))


def test_a_frame_signed_by_the_wrong_key_for_a_known_node_is_an_invalid_signature() -> None:
    real, impostor = make_device_party(_CLOCK), make_device_party(_CLOCK)
    forged = make_swarm_node(real, _CLOCK)
    ring = KeyRing()
    ring.add(forged)
    hop = Hop(sender=real.device_id, recipient=_HIVE.hive_id, node_id=real.node_id)
    frame = Codec(signer=impostor.signer).encode(wrap(make_heartbeat(real), hop, clock=_CLOCK))

    with pytest.raises(InvalidSignatureError):
        _decode(ring, frame)


def test_an_unknown_node_may_send_only_a_self_consistent_enrol_request() -> None:
    party = make_device_party(_CLOCK)
    request = make_enrol_request(party, _CODE)
    frame = Codec(signer=party.signer).encode(make_enrol_envelope(party, _HIVE, request, _CLOCK))

    decoded = _decode(KeyRing(), frame)

    assert decoded.kind == ENROL_REQUEST_KIND


def test_an_unknown_node_sending_anything_else_is_refused() -> None:
    party = make_device_party(_CLOCK)

    with pytest.raises(UnknownSignerError):
        _decode(KeyRing(), _frame(party))


def test_an_enrol_request_whose_payload_names_another_node_is_refused() -> None:
    party, other = make_device_party(_CLOCK), make_device_party(_CLOCK)
    request = make_enrol_request(party, _CODE, node_id=other.node_id)
    frame = Codec(signer=party.signer).encode(make_enrol_envelope(party, _HIVE, request, _CLOCK))

    with pytest.raises(UnknownSignerError):
        _decode(KeyRing(), frame)


def test_an_enrol_request_whose_payload_names_another_device_is_refused() -> None:
    party, other = make_device_party(_CLOCK), make_device_party(_CLOCK)
    request = make_enrol_request(party, _CODE, device_id=other.device_id)
    frame = Codec(signer=party.signer).encode(make_enrol_envelope(party, _HIVE, request, _CLOCK))

    with pytest.raises(UnknownSignerError):
        _decode(KeyRing(), frame)


def test_an_enrol_request_signed_by_a_key_other_than_the_one_it_carries_is_invalid() -> None:
    party, thief = make_device_party(_CLOCK), make_device_party(_CLOCK)
    request = make_enrol_request(party, _CODE)  # Carries party's public key.
    frame = Codec(signer=thief.signer).encode(make_enrol_envelope(party, _HIVE, request, _CLOCK))

    with pytest.raises(InvalidSignatureError):
        _decode(KeyRing(), frame)


def test_an_enrol_request_claiming_a_known_nodes_id_must_verify_against_the_known_key() -> None:
    victim, thief = make_device_party(_CLOCK), make_device_party(_CLOCK)
    request = make_enrol_request(victim, _CODE, public_key_hex=thief.public_hex)
    hop = Hop(sender=victim.device_id, recipient=_HIVE.hive_id, node_id=victim.node_id)
    frame = Codec(signer=thief.signer).encode(wrap(request, hop, clock=_CLOCK))

    with pytest.raises(InvalidSignatureError):
        _decode(_ring(victim), frame)


@pytest.mark.parametrize("garbage", [b"not json", b"[1, 2]", b"null"])
def test_canonical_bytes_that_are_not_a_json_object_are_refused(garbage: bytes) -> None:
    with pytest.raises(UnknownSignerError):
        SwarmVerifier(KeyRing()).verify("node_x", garbage, "sig")


def test_the_ring_adds_removes_and_counts_nodes() -> None:
    party = make_device_party(_CLOCK)
    ring = KeyRing()
    node = make_swarm_node(party, _CLOCK)

    ring.add(node)

    assert party.node_id in ring and len(ring) == 1
    binding = ring.binding(party.node_id)
    assert binding is not None
    assert binding.senders == {party.device_id, party.warden_id}
    ring.remove(party.node_id)
    ring.remove(party.node_id)  # An unknown id is ignored.
    assert party.node_id not in ring and len(ring) == 0 and ring.binding(party.node_id) is None


def test_sync_trusts_live_nodes_drops_revoked_ones_and_keeps_those_it_was_not_told_of() -> None:
    live, gone, newer = (make_device_party(_CLOCK) for _ in range(3))
    ring = _ring(gone, newer)
    revoked = make_swarm_node(gone, _CLOCK, status=NodeStatus.REVOKED, revoked_at=_CLOCK.now())

    ring.sync([make_swarm_node(live, _CLOCK), revoked])

    assert live.node_id in ring
    assert gone.node_id not in ring
    assert newer.node_id in ring  # Enrolled after the registry was read; a stale snapshot keeps it.
