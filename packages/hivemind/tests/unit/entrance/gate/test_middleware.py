"""Test hivemind.entrance.gate.middleware's body limit: small JSON, or a row's own allowance.

Every body is read whole before a route sees it, within ``MAX_BODY_BYTES``; a route that takes a
raw body (the voice clip) declares its own allowance, which applies to that method and path alone.

Fits into the Hive:
    Mirrors src/hivemind/entrance/gate/middleware.py (codingrules section 3); its other wrappers
    are exercised over real listeners in tests/unit/entrance/test_app.py.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import httpx
from starlette.types import Receive, Scope, Send

from hivemind.entrance.gate import MAX_BODY_BYTES, BodyLimit, RawBody

_BIG = RawBody(media_types=("audio/wav",), max_bytes=4 * MAX_BODY_BYTES, read_timeout_s=5.0)


async def _echo_length(scope: Scope, receive: Receive, send: Send) -> None:
    """Answer 200 with the length of the body the wrapper replayed."""
    message = await receive()
    body = message.get("body", b"")
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": str(len(body)).encode()})


def _client() -> httpx.AsyncClient:
    """A client of the body limit, with a raw-body allowance on POST /v1/chat/audio."""
    limited = BodyLimit(_echo_length, allowances={("POST", "/v1/chat/audio"): _BIG})
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=limited), base_url="http://hive")


async def test_a_raw_body_row_reads_past_the_json_limit_and_no_other_row_does() -> None:
    body = b"\x00" * (2 * MAX_BODY_BYTES)
    async with _client() as client:
        audio = await client.post("/v1/chat/audio", content=body)
        json_route = await client.post("/v1/chat", content=body)
        other_method = await client.put("/v1/chat/audio", content=body)

    assert (audio.status_code, audio.text) == (200, str(len(body)))
    assert json_route.status_code == 413
    assert other_method.status_code == 413


async def test_a_raw_body_row_is_still_bounded_by_its_own_allowance() -> None:
    async with _client() as client:
        refused = await client.post("/v1/chat/audio", content=b"\x00" * (_BIG.max_bytes + 1))

    assert refused.status_code == 413
