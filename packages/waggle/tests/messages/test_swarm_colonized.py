"""Tests for waggle.messages.swarm_colonized: NucPromote, NucPromoted and TrailSegmentSync.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Pins for the three classes construction, the
    JSON round trip (a chunk holding every byte value included), the bounds and the id kinds,
    and every validator spec section 8.10 names in both directions: NucPromote's server_id and
    initial_models against its action, NucPromoted's outcome-against-action matrix in full and
    its server_id and loaded_models against the outcome, and TrailSegmentSync's to_at against
    from_at and its digest exactly on the final chunk. The family's EXAMPLES live in
    test_swarm.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.messages.swarm_colonized for the module under test.
    - docs/waggle/spec.md section 8.10 for the fields, bounds and validators pinned here.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id
from waggle.messages.base import MAX_CHUNK_BYTES
from waggle.messages.swarm import NucAction, NucOutcome, RuntimeLevel
from waggle.messages.swarm_colonized import (
    MAX_MODEL_ID_CHARS,
    MAX_MODELS,
    MAX_SERVER_ID_CHARS,
    NucPromote,
    NucPromoted,
    TrailSegmentSync,
)

CLOCK = FakeClock()
NOW = CLOCK.now()
LATER = NOW + timedelta(hours=1)
DIGEST = "0123456789abcdef" * 4  # 32 bytes of digest as 64 lowercase hex characters.
DEVICE_ID = new_id(IdKind.DEVICE, CLOCK)
NODE_ID = new_id(IdKind.NODE, CLOCK)
CELL_ID = new_id(IdKind.CELL, CLOCK)
WARDEN_ID = new_id(IdKind.WARDEN, CLOCK)
# The one success outcome each action can report; FAILED answers either.
SUCCESS_FOR_ACTION = {NucAction.PROMOTE: NucOutcome.PROMOTED, NucAction.DEMOTE: NucOutcome.DEMOTED}


def _promote(**overrides: object) -> NucPromote:
    """A promotion order naming one server and one model, then ``overrides``."""
    fields: dict[str, object] = {
        "cell_id": CELL_ID,
        "device_id": DEVICE_ID,
        "action": NucAction.PROMOTE,
        "server_id": "laptop_local_server",
        "initial_models": ("coder-7b",),
        "deadline_s": 120.0,
        "reason": "The Cell has a free GPU.",
    }
    return NucPromote.model_validate({**fields, **overrides})


def _promoted(**overrides: object) -> NucPromoted:
    """A successful promotion report, then ``overrides``."""
    fields: dict[str, object] = {
        "cell_id": CELL_ID,
        "device_id": DEVICE_ID,
        "action": NucAction.PROMOTE,
        "outcome": NucOutcome.PROMOTED,
        "level": RuntimeLevel.NUC,
        "server_id": "laptop_local_server",
        "loaded_models": ("coder-7b",),
        "reason": "The server answered its health probe.",
    }
    return NucPromoted.model_validate({**fields, **overrides})


def _sync(**overrides: object) -> TrailSegmentSync:
    """A three-byte segment shipped whole in one final chunk, then ``overrides``."""
    fields: dict[str, object] = {
        "node_id": NODE_ID,
        "cell_id": CELL_ID,
        "warden_id": WARDEN_ID,
        "from_at": NOW,
        "to_at": LATER,
        "first_event_id": new_id(IdKind.EVENT, CLOCK),
        "last_event_id": new_id(IdKind.EVENT, CLOCK),
        "event_count": 12,
        "segment_format_version": 1,
        "chunk": b"{}\n",
        "offset": 0,
        "total_bytes": 3,
        "final": True,
        "sha256": DIGEST,
    }
    return TrailSegmentSync.model_validate({**fields, **overrides})


def _fitting_server_fields(outcome: NucOutcome) -> dict[str, object]:
    """The server_id and loaded_models that fit ``outcome``."""
    promoted = outcome is NucOutcome.PROMOTED
    return {
        "server_id": "laptop_local_server" if promoted else None,
        "loaded_models": ("coder-7b",) if promoted else (),
    }


# ──────────────────────────────────────────────────────────────────────────────
# NucPromote
# ──────────────────────────────────────────────────────────────────────────────


def test_nuc_promote_round_trips_a_promotion_and_a_demotion() -> None:
    promotion = _promote(initial_models=())
    demotion = _promote(action="DEMOTE", server_id=None, initial_models=())

    assert NucPromote.model_validate(promotion.model_dump(mode="json")) == promotion
    assert NucPromote.model_validate(demotion.model_dump(mode="json")) == demotion
    assert demotion.action is NucAction.DEMOTE
    assert _promote(
        server_id="s" * MAX_SERVER_ID_CHARS,
        initial_models=("m" * MAX_MODEL_ID_CHARS,) * MAX_MODELS,
    )


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"server_id": None}, "server_id is required exactly when action is PROMOTE"),
        ({"action": "DEMOTE", "initial_models": ()}, "server_id is required exactly when action"),
        ({"action": "DEMOTE", "server_id": None}, "initial_models must be empty for DEMOTE"),
        ({"server_id": "s" * (MAX_SERVER_ID_CHARS + 1)}, f"at most {MAX_SERVER_ID_CHARS}"),
        ({"initial_models": ("m",) * (MAX_MODELS + 1)}, f"at most {MAX_MODELS}"),
        ({"initial_models": ("m" * (MAX_MODEL_ID_CHARS + 1),)}, f"at most {MAX_MODEL_ID_CHARS}"),
        ({"deadline_s": 0}, "greater than 0"),
        ({"action": "RESTART"}, "action"),
        ({"cell_id": DEVICE_ID}, "cell_"),
        ({"device_id": CELL_ID}, "device_"),
        ({"gpu_index": 0}, "extra"),
    ],
)
def test_nuc_promote_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _promote(**changes)


# ──────────────────────────────────────────────────────────────────────────────
# NucPromoted
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("outcome", list(NucOutcome))
@pytest.mark.parametrize("action", list(NucAction))
def test_nuc_promoted_outcome_is_the_action_s_success_or_failed(
    action: NucAction, outcome: NucOutcome
) -> None:
    fits = outcome is NucOutcome.FAILED or outcome is SUCCESS_FOR_ACTION[action]
    fields = {"action": action, "outcome": outcome, **_fitting_server_fields(outcome)}

    if fits:
        assert _promoted(**fields).outcome is outcome
    else:
        with pytest.raises(ValidationError, match="cannot answer action"):
            _promoted(**fields)


def test_nuc_promoted_round_trips_every_level_and_each_consistent_shape() -> None:
    for level in RuntimeLevel:
        assert _promoted(level=level).level is level
    shapes = (
        _promoted(),
        _promoted(outcome="FAILED", level="COLONIZED", server_id=None, loaded_models=()),
        _promoted(
            action="DEMOTE", outcome="DEMOTED", level="COLONIZED", server_id=None, loaded_models=()
        ),
        _promoted(loaded_models=()),
    )

    for shape in shapes:
        assert NucPromoted.model_validate(shape.model_dump(mode="json")) == shape


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"server_id": None}, "server_id is set exactly when outcome is PROMOTED"),
        ({"outcome": "FAILED", "loaded_models": ()}, "server_id is set exactly when outcome"),
        ({"outcome": "FAILED", "server_id": None}, "loaded_models must be empty unless outcome"),
        ({"server_id": "s" * (MAX_SERVER_ID_CHARS + 1)}, f"at most {MAX_SERVER_ID_CHARS}"),
        ({"loaded_models": ("m",) * (MAX_MODELS + 1)}, f"at most {MAX_MODELS}"),
        ({"loaded_models": ("m" * (MAX_MODEL_ID_CHARS + 1),)}, f"at most {MAX_MODEL_ID_CHARS}"),
        ({"level": "HIVE_STAND"}, "level"),
        ({"cell_id": DEVICE_ID}, "cell_"),
        ({"device_id": CELL_ID}, "device_"),
        ({"vram_free_bytes": 0}, "extra"),
    ],
)
def test_nuc_promoted_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _promoted(**changes)


# ──────────────────────────────────────────────────────────────────────────────
# TrailSegmentSync
# ──────────────────────────────────────────────────────────────────────────────


def test_trail_segment_sync_round_trips_every_byte_value_and_defaults_final_false() -> None:
    whole = _sync(chunk=bytes(range(256)), total_bytes=256)
    first = _sync(final=False, sha256=None, total_bytes=1_000)
    no_final_given = TrailSegmentSync.model_validate(
        {key: value for key, value in first.model_dump().items() if key != "final"}
    )

    assert isinstance(whole.model_dump(mode="json")["chunk"], str)
    assert TrailSegmentSync.model_validate(whole.model_dump(mode="json")) == whole
    assert TrailSegmentSync.model_validate(first.model_dump(mode="json")) == first
    assert no_final_given.final is False


def test_trail_segment_sync_segment_may_be_instantaneous_but_never_reversed() -> None:
    assert _sync(to_at=NOW).to_at == NOW
    with pytest.raises(ValidationError, match="precedes from_at"):
        _sync(to_at=NOW - timedelta(seconds=1))
    with pytest.raises(ValidationError, match="timezone-aware"):
        _sync(from_at=datetime(2020, 1, 1))  # naive on purpose


def test_trail_segment_sync_requires_the_digest_exactly_on_the_final_chunk() -> None:
    with pytest.raises(ValidationError, match="required exactly when final"):
        _sync(final=True, sha256=None)
    with pytest.raises(ValidationError, match="required exactly when final"):
        _sync(final=False, sha256=DIGEST)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"chunk": b"x" * (MAX_CHUNK_BYTES + 1)}, f"at most {MAX_CHUNK_BYTES} bytes"),
        ({"offset": -1}, "greater than or equal to 0"),
        ({"total_bytes": 0}, "greater than or equal to 1"),
        ({"event_count": 0}, "greater than or equal to 1"),
        ({"segment_format_version": 0}, "greater than or equal to 1"),
        ({"sha256": DIGEST.upper()}, "pattern"),
        ({"node_id": DEVICE_ID}, "node_"),
        ({"cell_id": NODE_ID}, "cell_"),
        ({"warden_id": DEVICE_ID}, "warden_"),
        ({"first_event_id": CELL_ID}, "event_"),
        ({"last_event_id": CELL_ID}, "event_"),
        ({"compression": "zstd"}, "extra"),
    ],
)
def test_trail_segment_sync_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _sync(**changes)
