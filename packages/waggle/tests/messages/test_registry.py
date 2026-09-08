"""Tests for waggle.messages.registry: the kind list, the lookups and the import-time checks.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Pins MESSAGE_SPECS to the catalogue row for
    row, the shape rules every spec obeys (a request never replies, a reply target is always a
    request), the three lookups in both directions on the control family as the sample, the
    UnknownKindError each raises on a miss, and the duplicate and dangling-reply checks the
    module runs on itself at import. The full sixty-six-kind order and one example of every
    kind are in test_registry_catalogue.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.messages.registry for the module under test.
    - tests/messages/test_registry_catalogue.py for the whole catalogue and the family examples.
    - docs/waggle/spec.md section 8 (the catalogue table) and section 3 (shapes).
"""

from __future__ import annotations

import re
from dataclasses import FrozenInstanceError

import pytest

from waggle.errors import UnknownKindError
from waggle.messages import registry
from waggle.messages.base import KIND_PATTERN, MessageShape, WaggleMessage
from waggle.messages.catalogue import CATALOGUE
from waggle.messages.control import Cluster, ErrorMessage, Ping, Pong, Shutdown, Wake
from waggle.messages.control_hive import HumanMessage, MaskOverride, QueenMoved
from waggle.messages.registry import (
    MESSAGE_SPECS,
    MessageSpec,
    all_kinds,
    kind_for,
    model_for,
    spec_for,
)

# The control rows of the spec's catalogue table, in its order: the sample the lookup tests use.
_CONTROL_ROWS: list[tuple[str, type[WaggleMessage], MessageShape, str | None]] = [
    ("control.ping", Ping, MessageShape.REQUEST, None),
    ("control.pong", Pong, MessageShape.REPLY, "control.ping"),
    ("control.error", ErrorMessage, MessageShape.REPLY, None),
    ("control.shutdown", Shutdown, MessageShape.EVENT, None),
    ("control.cluster", Cluster, MessageShape.EVENT, None),
    ("control.wake", Wake, MessageShape.EVENT, None),
    ("control.human_message", HumanMessage, MessageShape.EVENT, None),
    ("control.mask_override", MaskOverride, MessageShape.EVENT, None),
    ("control.queen_moved", QueenMoved, MessageShape.EVENT, None),
]


def test_message_specs_are_the_catalogue_rows_in_order() -> None:
    assert tuple(MessageSpec(*row) for row in CATALOGUE) == MESSAGE_SPECS
    assert len(MESSAGE_SPECS) == len(CATALOGUE)


def test_the_control_family_closes_the_catalogue_as_the_table_does() -> None:
    tail = MESSAGE_SPECS[-len(_CONTROL_ROWS) :]

    assert [(s.kind, s.model, s.shape, s.replies_to) for s in tail] == _CONTROL_ROWS


def test_all_kinds_preserves_catalogue_order() -> None:
    assert all_kinds() == tuple(row[0] for row in CATALOGUE)


@pytest.mark.parametrize(("kind", "model", "shape", "replies_to"), _CONTROL_ROWS)
def test_lookups_agree_in_both_directions(
    kind: str, model: type[WaggleMessage], shape: MessageShape, replies_to: str | None
) -> None:
    spec = spec_for(kind)

    assert spec == MessageSpec(kind, model, shape, replies_to)
    assert model_for(kind) is model
    assert kind_for(model) == kind


def test_every_kind_matches_the_kind_pattern() -> None:
    for kind in all_kinds():
        assert re.fullmatch(KIND_PATTERN, kind), kind


def test_a_request_never_replies_and_every_reply_target_is_a_request() -> None:
    # Spec section 3: a request opens an exchange, so it names no antecedent; a reply or an
    # event that names one always names a request.
    for spec in MESSAGE_SPECS:
        if spec.shape is MessageShape.REQUEST:
            assert spec.replies_to is None, spec.kind
        elif spec.replies_to is not None:
            assert spec_for(spec.replies_to).shape is MessageShape.REQUEST, spec.kind


def test_every_reply_but_control_error_names_the_request_it_answers() -> None:
    # control.error answers any kind (spec section 8.11), so it alone is a reply with no target.
    for spec in MESSAGE_SPECS:
        if spec.shape is MessageShape.REPLY and spec.kind != "control.error":
            assert spec.replies_to is not None, spec.kind
    assert spec_for("control.error").replies_to is None


@pytest.mark.parametrize("lookup", [spec_for, model_for])
def test_unknown_kind_raises_the_codec_error_naming_it(lookup: object) -> None:
    assert callable(lookup)
    with pytest.raises(
        UnknownKindError, match=re.escape("'control.nope' is not registered")
    ) as caught:
        lookup("control.nope")

    assert caught.value.code == "waggle.codec.unknown_kind"


def test_unregistered_and_subclassed_models_are_unknown() -> None:
    class PingLike(Ping):
        """A subclass of a registered class is a different class on the wire."""

    with pytest.raises(UnknownKindError, match="WaggleMessage is not registered"):
        kind_for(WaggleMessage)
    with pytest.raises(UnknownKindError, match="PingLike"):
        kind_for(PingLike)


def test_message_spec_is_frozen() -> None:
    spec = spec_for("control.ping")

    with pytest.raises(FrozenInstanceError):
        spec.kind = "control.pong"  # type: ignore[misc]  # The assignment is the test.


def test_lookup_tables_are_read_only() -> None:
    with pytest.raises(TypeError):
        registry._SPEC_BY_KIND["x"] = spec_for("control.ping")  # type: ignore[index]  # read-only


# ──────────────────────────────────────────────────────────────────────────────
# Import-time checks (exercised through the private builder they run in)
# ──────────────────────────────────────────────────────────────────────────────


def test_a_duplicate_kind_is_refused() -> None:
    twice = (*MESSAGE_SPECS, MessageSpec("control.ping", Cluster, MessageShape.EVENT, None))

    with pytest.raises(ValueError, match=re.escape("'control.ping' is registered more than once")):
        registry._index(twice)


def test_a_duplicate_model_is_refused() -> None:
    twice = (*MESSAGE_SPECS, MessageSpec("control.ping_again", Ping, MessageShape.EVENT, None))

    with pytest.raises(ValueError, match="Ping is registered more than once"):
        registry._index(twice)


def test_a_reply_to_an_unregistered_kind_is_refused() -> None:
    dangling = (MessageSpec("control.pong", Pong, MessageShape.REPLY, "control.ping"),)

    with pytest.raises(
        ValueError, match=re.escape("replies to 'control.ping', which is not registered")
    ):
        registry._index(dangling)


def test_a_kind_outside_the_pattern_is_refused() -> None:
    bad = (MessageSpec("Control.Ping", Ping, MessageShape.REQUEST, None),)

    with pytest.raises(ValueError, match="does not match"):
        registry._index(bad)
