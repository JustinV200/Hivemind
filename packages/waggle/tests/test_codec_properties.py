"""Property tests for waggle.codec: round trips, canonical stability and tamper detection.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Generates arbitrary valid control payloads with
    hypothesis and pins three promises over all of them: every envelope round-trips through
    both codecs to an equal envelope, canonical bytes ignore key order at every level, and no
    single flipped byte of a signed frame changes what a receiver accepts. A fourth property
    pins that arbitrary bytes never escape as anything but a waggle error. The strategies here
    are the control family's; the registry step can reuse the pattern per family.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.codec for the module under test; test_codec.py for the example-based tests.
    - docs/waggle/spec.md section 11 (Conformance) for the three properties named there.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from waggle.clock import FakeClock
from waggle.codec import Codec, canonical_bytes
from waggle.envelope import Envelope, Hop, wrap
from waggle.errors import CodecError, SignatureError
from waggle.ids import IdKind
from waggle.messages.base import MessageShape, WaggleMessage
from waggle.messages.control import Cluster, ClusterCause, ErrorMessage, Ping, Pong, Shutdown, Wake
from waggle.messages.control_hive import (
    HumanMessage,
    MaskOverride,
    MaskOverrideAction,
    MaskTactic,
    QueenMoved,
)
from waggle.messages.labels import Urgency
from waggle.messages.registry import all_kinds, kind_for, spec_for
from waggle.minting import new_hive_id, new_message_id, new_node_id, new_warden_id
from waggle.signing import Ed25519Signer, Ed25519Verifier
from waggle.ulid import CROCKFORD_ALPHABET, ULID_LENGTH

# Built once at import rather than per example: hypothesis forbids function-scoped fixtures
# inside @given, and a keypair per example would dominate the run time.
CLOCK = FakeClock()
NODE_ID = new_node_id(CLOCK)
HOP = Hop(sender=new_hive_id(CLOCK), recipient=new_warden_id(CLOCK), node_id=NODE_ID)
CORRELATION_ID = new_message_id(CLOCK)
SIGNER = Ed25519Signer.generate()
SIGNED = Codec(signer=SIGNER, verifier=Ed25519Verifier({NODE_ID: SIGNER.public_key_bytes}))
PLAIN = Codec()
LOWERCASE = "abcdefghijklmnopqrstuvwxyz"
HEX = "0123456789abcdef"

# ──────────────────────────────────────────────────────────────────────────────
# Strategies
# ──────────────────────────────────────────────────────────────────────────────

ulids = st.text(alphabet=CROCKFORD_ALPHABET, min_size=ULID_LENGTH, max_size=ULID_LENGTH)
offsets = st.integers(min_value=-12, max_value=14).map(
    lambda hours: timezone(timedelta(hours=hours))
)
aware_datetimes = st.datetimes(
    min_value=datetime(2000, 1, 1),  # hypothesis wants naive bounds
    max_value=datetime(2100, 1, 1),
    timezones=offsets,
)
short_text = st.text(min_size=1, max_size=40)
reasons = st.text(max_size=40)
providers = st.none() | st.tuples(
    st.sampled_from(LOWERCASE), st.text(alphabet=LOWERCASE + "0123456789_", max_size=10)
).map("".join)
codes = st.lists(st.text(alphabet=LOWERCASE, min_size=1, max_size=6), min_size=2, max_size=4).map(
    ".".join
)
addresses = st.one_of(
    st.text(alphabet=LOWERCASE + "0123456789.-", min_size=1, max_size=30).map(
        lambda h: f"wss://{h}"
    ),
    st.sampled_from(["ws://127.0.0.1:9000", "ws://localhost", "ws://[::1]:9000/waggle"]),
)


def ids_of(kind: IdKind) -> st.SearchStrategy[str]:
    """Well-formed ids of ``kind``: any 26 Crockford characters decode as a ULID."""
    return ulids.map(lambda ulid: f"{kind.value}_{ulid}")


@st.composite
def queen_moves(draw: st.DrawFn) -> QueenMoved:
    """A QueenMoved whose grace_until never precedes effective_at."""
    effective_at = draw(aware_datetimes)
    return QueenMoved(
        sequence=draw(st.integers(min_value=1, max_value=10**6)),
        new_address=draw(addresses),
        new_node_id=draw(ids_of(IdKind.NODE)),
        new_node_public_key_hex=draw(st.text(alphabet=HEX, min_size=64, max_size=64)),
        effective_at=effective_at,
        grace_until=effective_at
        + timedelta(seconds=draw(st.integers(min_value=0, max_value=86_400))),
        is_rollback=draw(st.booleans()),
        reason=draw(reasons),
    )


control_payloads: st.SearchStrategy[WaggleMessage] = st.one_of(
    st.builds(Ping),
    st.builds(Pong, received_at=aware_datetimes),
    st.builds(
        ErrorMessage,
        code=codes,
        message=short_text,
        failed_kind=st.none() | st.sampled_from(all_kinds()),
        is_retryable=st.booleans(),
    ),
    st.builds(
        Shutdown,
        urgency=st.just(Urgency.GRACEFUL),
        deadline_s=st.floats(min_value=0, max_value=1e6, allow_nan=False),
        reason=reasons,
    ),
    st.builds(
        Shutdown, urgency=st.just(Urgency.IMMEDIATE), deadline_s=st.just(0.0), reason=reasons
    ),
    st.builds(Cluster, provider=providers, cause=st.sampled_from(ClusterCause), reason=reasons),
    st.builds(Wake, provider=providers, reason=reasons),
    st.builds(
        HumanMessage,
        text=short_text,
        task_id=st.none() | ids_of(IdKind.TASK),
        device_id=ids_of(IdKind.DEVICE),
    ),
    st.builds(
        MaskOverride,
        cell_id=ids_of(IdKind.CELL),
        action=st.just(MaskOverrideAction.FORCE),
        tactics=st.lists(st.sampled_from(MaskTactic), min_size=1, max_size=2, unique=True).map(
            tuple
        ),
        expires_at=aware_datetimes,
        reason=reasons,
    ),
    st.builds(
        MaskOverride,
        cell_id=ids_of(IdKind.CELL),
        action=st.just(MaskOverrideAction.CLEAR),
        tactics=st.just(()),
        expires_at=st.none(),
        reason=reasons,
    ),
    queen_moves(),
)


def _envelope_for(payload: WaggleMessage) -> Envelope:
    """Wrap ``payload`` with the correlation its registered shape requires."""
    is_reply = spec_for(kind_for(type(payload))).shape is MessageShape.REPLY
    return wrap(payload, HOP, clock=CLOCK, correlation_id=CORRELATION_ID if is_reply else None)


def _signature_span(frame: bytes) -> range:
    """The byte positions of the signature's base64 text inside a signed frame."""
    start = frame.index(b'"signature":"') + len(b'"signature":"')
    return range(start, frame.index(b'"', start))


# ──────────────────────────────────────────────────────────────────────────────
# Properties
# ──────────────────────────────────────────────────────────────────────────────


@settings(max_examples=150)
@given(payload=control_payloads)
def test_any_control_payload_round_trips_through_both_codecs(payload: WaggleMessage) -> None:
    envelope = _envelope_for(payload)

    signed = SIGNED.decode(SIGNED.encode(envelope))
    plain = PLAIN.decode(PLAIN.encode(envelope))

    assert type(signed.payload) is type(payload)
    assert signed.model_dump(exclude={"signature"}) == envelope.model_dump(exclude={"signature"})
    assert plain == envelope


@settings(max_examples=100)
@given(payload=control_payloads, data=st.data())
def test_canonical_bytes_ignore_key_order_at_every_level(
    payload: WaggleMessage, data: st.DataObject
) -> None:
    wire = _envelope_for(payload).model_dump(mode="json")
    inner = wire["payload"]
    assert isinstance(inner, dict)

    # Shuffle the envelope keys and the payload keys independently; the bytes must not move.
    shuffled_payload = dict(data.draw(st.permutations(list(inner.items()))))
    shuffled = dict(data.draw(st.permutations(list({**wire, "payload": shuffled_payload}.items()))))

    assert canonical_bytes(shuffled) == canonical_bytes(wire)


@settings(max_examples=150)
@given(
    payload=control_payloads,
    position=st.integers(min_value=0),
    flip=st.integers(min_value=1, max_value=255),
)
def test_flipping_any_single_byte_of_a_signed_frame_is_rejected_or_changes_nothing(
    payload: WaggleMessage, position: int, flip: int
) -> None:
    envelope = _envelope_for(payload)
    frame = SIGNED.encode(envelope)
    # XOR with a non-zero mask always changes the byte; wrapping the position keeps every draw
    # in range so hypothesis can shrink both freely.
    index = position % len(frame)
    tampered = bytearray(frame)
    tampered[index] ^= flip

    try:
        decoded = SIGNED.decode(bytes(tampered))
    except (CodecError, SignatureError):
        return

    # The one way a flip survives: it lands in the signature's base64 text and only touches the
    # bits base64 leaves unused before the "==" padding, so the decoded signature is unchanged
    # and still verifies. Nothing the signature covers can have changed.
    assert index in _signature_span(frame)
    assert decoded.model_dump(exclude={"signature"}) == envelope.model_dump(exclude={"signature"})


@settings(max_examples=100)
@given(frame=st.binary(max_size=200))
def test_arbitrary_bytes_never_escape_as_anything_but_a_waggle_error(frame: bytes) -> None:
    # A peer can send anything; the codec's whole contract is that only its documented errors
    # come out, never a json, unicode or pydantic exception a transport would not catch.
    with pytest.raises((CodecError, SignatureError)):
        SIGNED.decode(frame)
