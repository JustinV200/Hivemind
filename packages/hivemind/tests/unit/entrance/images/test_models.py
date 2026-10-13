"""Tests for hivemind.entrance.images.models: the query a device sends and the view it gets.

Fits into the Hive:
    Mirrors src/hivemind/entrance/images/models.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from hivemind.entrance.images import (
    MAX_CAPTION_CHARS,
    DepositParams,
    ImageDepositView,
    ImageMediaType,
)

_NECTAR = "nectar_01M221E4C10R4XDPNQNRX85AAA"
_TASK = "task_01M221E4C10R4XDPNQNRX85AAA"
_AT = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)


def test_the_query_is_optional_throughout() -> None:
    params = DepositParams()

    assert (params.caption, params.task_id) == (None, None)


def test_the_query_carries_a_caption_and_a_task() -> None:
    params = DepositParams.model_validate({"caption": "The back fence", "task_id": _TASK})

    assert (params.caption, params.task_id) == ("The back fence", _TASK)


@pytest.mark.parametrize(
    "query",
    [
        {"caption": ""},
        {"caption": "x" * (MAX_CAPTION_CHARS + 1)},
        {"task_id": "not-a-task-id"},
        {"stray": "1"},
    ],
    ids=["empty-caption", "long-caption", "bad-task", "stray"],
)
def test_the_query_refuses_what_it_does_not_define(query: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        DepositParams.model_validate(query)


def test_the_view_says_where_the_image_is_and_nothing_of_what_it_shows() -> None:
    view = ImageDepositView(
        nectar_id=_NECTAR,
        size_bytes=64,
        media_type=ImageMediaType.PNG,
        deposited_at=_AT,
        expires_at=_AT,
    )

    assert set(view.model_dump()) == {
        "nectar_id",
        "size_bytes",
        "media_type",
        "deposited_at",
        "expires_at",
    }


def test_the_view_refuses_an_empty_image_or_a_stray_member() -> None:
    fields = {
        "nectar_id": _NECTAR,
        "size_bytes": 0,
        "media_type": "image/png",
        "deposited_at": _AT,
        "expires_at": _AT,
    }

    with pytest.raises(ValidationError):
        ImageDepositView.model_validate(fields)
    with pytest.raises(ValidationError):
        ImageDepositView.model_validate({**fields, "size_bytes": 1, "caption": "leak"})
