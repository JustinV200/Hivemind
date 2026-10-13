"""Test building the enrolment request: identities, token and probes in one signed-to-be message.

The first thing a new device says to the Queen (the Hive's central orchestrator) is one
``swarm.enrol_request`` (enrolment.md section 5). The Pollen Packet (the thin gateway program on a
device the Hive borrows) puts in it the single-use token from the invite, the ids it minted for
itself, the matching public key, the device's hostname, its version and the three probes everything
the Hive later decides about the device reads. Building the request is separate from sending it so
the probes and the identity can be tested without a connection. The token goes in here and nowhere
else.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.enrol.request.

Key invariants:
    - The request's identities are the ones given, and its token is the invite's.
    - The token never appears in the request's repr.

See Also:
    - docs/waggle/enrolment.md section 5.2.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from pollen.enrol import DeviceIdentity, Invite, build_request
from pollen.platform import select_shim
from waggle.clock import FakeClock
from waggle.ids import new_device_id, new_hive_id, new_node_id
from waggle.messages.labels import AccessLevel
from waggle.messages.swarm import EnrolRequest
from waggle.signing import Ed25519Signer, public_key_hex

CLOCK = FakeClock()
CODE = "H7QK-3MZP-WD2F-XNVA-R5TG-C4YB-J6"


def _invite() -> Invite:
    """An invite for a loopback Queen."""
    return Invite(
        token=CODE,
        url="ws://127.0.0.1:8720",
        hive_id=new_hive_id(CLOCK),
        hive_node_id=new_node_id(CLOCK),
        hive_key="ab" * 32,
        tls_sha256=None,
        expires_at=CLOCK.now() + timedelta(minutes=15),
        label=None,
    )


def _identity() -> DeviceIdentity:
    """The ids and public key a device would have minted."""
    key = public_key_hex(Ed25519Signer.generate().public_key_bytes)
    return DeviceIdentity(new_device_id(CLOCK), new_node_id(CLOCK), key)


async def test_the_request_carries_the_identity_the_token_and_the_probes(tmp_path: Path) -> None:
    identity = _identity()
    request = await build_request(_invite(), identity, select_shim(), tmp_path, AccessLevel.SCRATCH)
    assert (request.device_id, request.node_id) == (identity.device_id, identity.node_id)
    assert request.public_key_hex == identity.public_key_hex
    assert request.invite_token == CODE and request.requested_access is AccessLevel.SCRATCH
    assert request.hostname and request.runtime_version
    assert request.capacity.cores >= 1 and request.capabilities.can_host_model is False
    assert request.max_sub_bees is None


async def test_the_token_is_hidden_from_a_representation_of_the_request(tmp_path: Path) -> None:
    request = await build_request(_invite(), _identity(), select_shim(), tmp_path, AccessLevel.FULL)
    assert CODE not in repr(request)


async def test_the_request_round_trips_and_refuses_a_token_that_is_too_short(
    tmp_path: Path,
) -> None:
    request = await build_request(_invite(), _identity(), select_shim(), tmp_path, AccessLevel.FULL)
    assert EnrolRequest.model_validate_json(request.model_dump_json()) == request
    good = request.model_dump(mode="json")
    with pytest.raises(ValidationError):
        EnrolRequest.model_validate({**good, "invite_token": "short"})
