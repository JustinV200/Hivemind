"""Test composition root for the waggle package: the fixtures every waggle test shares.

Per codingrules section 8.2 the composition root for any entry point, a test suite included, is
exactly one file; for waggle's tests it is this one. It wires the collaborators the envelope,
codec, transport and outbox tests all need: a FakeClock (the injected time source, driven by
hand so nothing sleeps), one node id and the bee addresses of a Queen (the central
orchestrator), a Warden (the supervisor of one Cell), a Worker and a device carrying a Pollen
Packet (the thin gateway on an enrolled machine), a factory that wraps any payload into an
Envelope (the outer wrapper every Waggle message travels in), and the signing pair plus the two
codecs (one that signs and verifies, one that does neither) that exercise both signature
policies.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Loaded automatically by pytest before every test
    module under packages/waggle/tests/. Depends on waggle.clock, waggle.ids,
    waggle.envelope, waggle.codec, waggle.signing and the control family; nothing depends on it.

Key invariants:
    - Every fixture is function-scoped and built fresh, so no test can see another's clock,
      keys or envelopes.
    - The signed codec's verifier trusts exactly the ``node_id`` fixture's key, so a frame from
      any other node is unknown to it by construction.

See Also:
    - .claude/codingrules.md section 8.2 for the composition-root rule this file follows.
    - waggle.envelope for wrap() and Hop, which make_envelope builds on.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop, wrap
from waggle.ids import (
    MessageId,
    NodeId,
    new_device_id,
    new_hive_id,
    new_node_id,
    new_warden_id,
    new_worker_id,
)
from waggle.messages.base import WaggleMessage
from waggle.messages.control.protocol import Ping
from waggle.signing import Ed25519Signer, Ed25519Verifier


@pytest.fixture
def fake_clock() -> FakeClock:
    """A FakeClock at its fixed default start; advance it by hand to move time."""
    return FakeClock()


@pytest.fixture
def node_id(fake_clock: FakeClock) -> NodeId:
    """The id of the one node (process) the tests send from and sign as."""
    return new_node_id(fake_clock)


@pytest.fixture
def queen_address(fake_clock: FakeClock) -> str:
    """A hive_ id: the Queen's bee address."""
    return new_hive_id(fake_clock)


@pytest.fixture
def warden_address(fake_clock: FakeClock) -> str:
    """A warden_ id: one Warden's bee address."""
    return new_warden_id(fake_clock)


@pytest.fixture
def worker_address(fake_clock: FakeClock) -> str:
    """A worker_ id: one Worker's bee address."""
    return new_worker_id(fake_clock)


@pytest.fixture
def device_address(fake_clock: FakeClock) -> str:
    """A device_ id: the bee address of a device carrying a Pollen Packet."""
    return new_device_id(fake_clock)


@pytest.fixture
def make_envelope(
    fake_clock: FakeClock, node_id: NodeId, queen_address: str, warden_address: str
) -> Callable[..., Envelope]:
    """A factory wrapping any payload (a Ping by default) from the Queen to a Warden.

    Call as ``make_envelope(payload, correlation_id=..., sender=..., recipient=...)``; every
    argument is optional and defaults to the fixtures above, so a test states only what it
    is about.
    """

    def factory(
        payload: WaggleMessage | None = None,
        *,
        correlation_id: MessageId | None = None,
        sender: str | None = None,
        recipient: str | None = None,
    ) -> Envelope:
        """Wrap ``payload`` for the given hop, defaulting to Queen -> Warden from ``node_id``."""
        # `is None` rather than `or`, so a test can pass an empty string to see it rejected.
        hop = Hop(
            sender=queen_address if sender is None else sender,
            recipient=warden_address if recipient is None else recipient,
            node_id=node_id,
        )
        return wrap(
            Ping() if payload is None else payload,
            hop,
            clock=fake_clock,
            correlation_id=correlation_id,
        )

    return factory


@pytest.fixture
def signer() -> Ed25519Signer:
    """A fresh Ed25519 keypair for the test's node."""
    return Ed25519Signer.generate()


@pytest.fixture
def verifier(node_id: NodeId, signer: Ed25519Signer) -> Ed25519Verifier:
    """A verifier trusting exactly the ``signer`` fixture's key under ``node_id``."""
    return Ed25519Verifier({node_id: signer.public_key_bytes})


@pytest.fixture
def signed_codec(signer: Ed25519Signer, verifier: Ed25519Verifier) -> Codec:
    """A codec that signs every frame it encodes and requires a signature on every decode."""
    return Codec(signer=signer, verifier=verifier)


@pytest.fixture
def plain_codec() -> Codec:
    """A codec with no signer and no verifier: the in-process policy."""
    return Codec()
