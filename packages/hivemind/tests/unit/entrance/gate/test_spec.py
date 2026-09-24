"""Test hivemind.entrance.gate.spec: what a row may declare about a raw body and a client's frames.

Fits into the Hive:
    Mirrors src/hivemind/entrance/gate/spec.py (codingrules section 3); the table as a whole is
    walked in tests/unit/entrance/test_app.py.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import pytest

from hivemind.entrance.gate import (
    BOTH_LISTENERS,
    PUBLIC,
    RawBody,
    RouteEffect,
    RouteSpec,
    Switch,
    session_with,
)

_CLIP = RawBody(media_types=("audio/wav",), max_bytes=1024, read_timeout_s=5.0)


async def _endpoint() -> None:
    """A route that does nothing; the rows below never serve."""


def _row(method: str = "POST", *, public: bool = False) -> RouteSpec:
    """A raw-body row, as the voice route declares one."""
    return RouteSpec(
        method=method,
        path="/v1/chat/audio",
        listeners=BOTH_LISTENERS,
        access=PUBLIC if public else session_with("entrance:submit"),
        effect=RouteEffect.INBOX,
        endpoint=_endpoint,
        summary="A clip.",
        mounted_when=Switch.VOICE,
        raw_body=_CLIP,
    )


def test_an_authenticated_write_may_take_a_raw_body_under_the_voice_switch() -> None:
    row = _row()

    assert (row.raw_body, row.mounted_when, row.refusals) == (_CLIP, Switch.VOICE, ())
    assert Switch.VOICE.value == "voice.enabled"


@pytest.mark.parametrize(("method", "public"), [("GET", False), ("POST", True)])
def test_a_read_or_a_public_row_never_takes_a_raw_body(method: str, public: bool) -> None:
    with pytest.raises(ValueError, match="raw body"):
        _row(method, public=public)


@pytest.mark.parametrize(
    ("media_types", "max_bytes", "read_timeout_s"),
    [((), 1, 1.0), (("audio/wav",), 0, 1.0), (("audio/wav",), 1, 0.0)],
)
def test_a_raw_body_names_its_types_and_a_positive_allowance(
    media_types: tuple[str, ...], max_bytes: int, read_timeout_s: float
) -> None:
    with pytest.raises(ValueError, match="media types"):
        RawBody(media_types, max_bytes, read_timeout_s)
