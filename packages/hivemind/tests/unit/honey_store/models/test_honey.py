"""Tests for hivemind.honey_store.models.honey: HoneyDraft/Honey round trips, path, to_hit.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/models/honey.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.models.honey for the module under test.
"""

from __future__ import annotations

import pytest
from builders.honey import make_honey_draft
from pydantic import ValidationError

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.honey_store.models.honey import MAX_BODY_CHARS, Honey
from hivemind.honey_store.models.nectar import NectarOrigin
from hivemind.honey_store.scope import cell_scope
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_honey_id, new_nectar_id
from waggle.messages.honey import NectarKind
from waggle.messages.honey.hit import MAX_EXCERPT_CHARS


def _make_honey(**overrides: object) -> Honey:
    clock = FakeClock()
    cell_id = new_cell_id(clock)
    fields: dict[str, object] = {
        "id": new_honey_id(clock),
        "nectar_id": new_nectar_id(clock),
        "part": "SUMMARY",
        "chunk_index": 0,
        "title": "A finding",
        "summary": "A short summary.",
        "body": "A short summary.",
        "body_sha256": "a" * 64,
        "clearance": HoneyClearance.C1,
        "ripener_model": None,
        "kind": NectarKind.FINDING,
        "origin": NectarOrigin.BEE,
        "scope": cell_scope(cell_id),
        "origin_tier": CombShieldLevel.MEADOW,
        "task_id": None,
        "cell_id": cell_id,
        "bee": None,
        "observed_at": clock.now(),
        "created_at": clock.now(),
        "tainted": False,
    }
    fields.update(overrides)
    return Honey(**fields)


def test_honey_draft_round_trips_through_json() -> None:
    draft = make_honey_draft()

    restored = draft.model_validate_json(draft.model_dump_json())

    assert restored == draft


def test_honey_draft_rejects_a_body_over_the_limit() -> None:
    with pytest.raises(ValidationError):
        make_honey_draft(body="x" * (MAX_BODY_CHARS + 1))


def test_honey_round_trips_through_json() -> None:
    honey = _make_honey()

    restored = honey.model_validate_json(honey.model_dump_json())

    assert restored == honey


def test_honey_rejects_a_negative_chunk_index() -> None:
    with pytest.raises(ValidationError):
        _make_honey(chunk_index=-1)


def test_honey_path_is_derived_from_its_scope_and_id() -> None:
    honey = _make_honey()

    assert honey.path == f"/cells/{honey.cell_id}/{honey.id}"


def test_honey_to_hit_caps_excerpt_and_converts_labels() -> None:
    # Honey.title is already bounded by MAX_TITLE_CHARS (== MAX_HIT_TITLE_CHARS), so a stored row
    # can never itself carry an over-long title; to_hit's own title slice is exercised together
    # with the excerpt slice, which HoneyHit.excerpt has no equivalent bound preventing.
    honey = _make_honey()

    hit = honey.to_hit(score=0.75, excerpt="y" * (MAX_EXCERPT_CHARS + 50))

    assert hit.honey_ref == honey.path
    assert hit.title == honey.title
    assert len(hit.excerpt) == MAX_EXCERPT_CHARS
    assert hit.score == 0.75
    assert hit.scope == honey.scope
    assert hit.clearance.value == honey.clearance.value
    assert hit.origin_tier.value == honey.origin_tier.value
    assert hit.provenance.cell_id == honey.cell_id


def test_honey_to_hit_clamps_an_out_of_range_score() -> None:
    honey = _make_honey()

    hit = honey.to_hit(score=1.5, excerpt="fine")

    assert hit.score == 1.0
