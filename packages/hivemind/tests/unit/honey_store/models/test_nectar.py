"""Tests for hivemind.honey_store.models.nectar: NectarDraft/Nectar round trips and rejections.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/models/nectar.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.models.nectar for the module under test.
"""

from __future__ import annotations

import pytest
from builders.honey import make_nectar_draft
from pydantic import ValidationError

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.honey_store.models.nectar import MAX_SOURCE_KEY_CHARS, Nectar, NectarState
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_nectar_id
from waggle.messages.honey import NectarKind


def test_nectar_draft_round_trips_through_json() -> None:
    draft = make_nectar_draft()

    restored = draft.model_validate_json(draft.model_dump_json())

    assert restored == draft


def test_nectar_draft_rejects_empty_content() -> None:
    with pytest.raises(ValidationError):
        make_nectar_draft(content=b"")


def test_nectar_draft_rejects_an_invalid_scope() -> None:
    with pytest.raises(ValidationError):
        make_nectar_draft(scope="not-a-scope")


def test_nectar_draft_rejects_a_source_key_over_the_limit() -> None:
    with pytest.raises(ValidationError):
        make_nectar_draft(source_key="x" * (MAX_SOURCE_KEY_CHARS + 1))


def test_nectar_draft_rejects_an_unknown_field() -> None:
    with pytest.raises(ValidationError):
        make_nectar_draft(nonsense="nope")


def test_nectar_draft_is_frozen() -> None:
    draft = make_nectar_draft()

    with pytest.raises(ValidationError, match="frozen"):
        draft.title = "changed"  # The assignment is the test.


def test_nectar_round_trips_through_json() -> None:
    clock = FakeClock()
    nectar = Nectar(
        id=new_nectar_id(clock),
        sha256="a" * 64,
        size_bytes=10,
        kind=NectarKind.FINDING,
        origin="BEE",
        media_type="text/plain",
        title="A finding",
        task_id=None,
        cell_id=new_cell_id(clock),
        bee=None,
        observed_at=clock.now(),
        received_at=clock.now(),
        clearance=HoneyClearance.C1,
        origin_tier=CombShieldLevel.MEADOW,
        scope="hive",
        state=NectarState.RECEIVED,
        ripen_attempts=0,
        tainted=False,
    )

    restored = nectar.model_validate_json(nectar.model_dump_json())

    assert restored == nectar


def test_nectar_rejects_a_bee_id_of_the_wrong_kind() -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError):
        Nectar(
            id=new_nectar_id(clock),
            sha256="a" * 64,
            size_bytes=10,
            kind=NectarKind.FINDING,
            origin="BEE",
            media_type="text/plain",
            title="A finding",
            task_id=None,
            cell_id=new_cell_id(clock),
            bee="cell_not_a_bee_id",
            observed_at=clock.now(),
            received_at=clock.now(),
            clearance=HoneyClearance.C1,
            origin_tier=CombShieldLevel.MEADOW,
            scope="hive",
            state=NectarState.RECEIVED,
            ripen_attempts=0,
            tainted=False,
        )
