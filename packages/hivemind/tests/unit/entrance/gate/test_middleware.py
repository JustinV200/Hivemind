"""Test hivemind.entrance.gate.middleware's body limit and the loopback gate's stand key.

Every body is read whole before a route sees it, within ``MAX_BODY_BYTES``; a route that takes a
raw body (the voice clip) declares its own allowance, which applies to that method and path alone.
A loopback-only row needs this boot's stand key in the ``X-Hive-Stand`` header, because a
Virtual Cell's guest forwarded onto loopback is otherwise indistinguishable (ADR-0068).

Fits into the Hive:
    Mirrors src/hivemind/entrance/gate/middleware.py (codingrules section 3); its other wrappers
    are exercised over real listeners in tests/unit/entrance/test_app.py.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import httpx
import pytest
from starlette.routing import compile_path
from starlette.types import ASGIApp, Receive, Scope, Send

from hivemind.entrance.auth.limits import RateLimiter
from hivemind.entrance.expose import STAND_HEADER
from hivemind.entrance.gate import MAX_BODY_BYTES, AddressLimit, BodyLimit, LoopbackGate, RawBody
from hivemind.manifest import EntranceSection
from waggle.clock import FakeClock

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


# The stand key's tests (ADR-0068). A Virtual Cell's guest reaches the loopback listener through
# QEMU's SLIRP host alias or Docker Desktop's host gateway: the forwarder connects from 127.0.0.1
# and the guest writes a loopback Host itself, so these requests look exactly like the guest's.
_PORT = 8710
_KEY = "stand-key-for-this-boot"
_GUEST = {"Host": f"localhost:{_PORT}"}
_APPROVE = "/v1/entrance/pending/device_0000000000000000000000000A/approve"


def _gate(app: ASGIApp = _echo_length) -> httpx.AsyncClient:
    """A client of the loopback gate guarding the approve row, as SLIRP or Docker forwards it."""
    rows = [("POST", compile_path("/v1/entrance/pending/{device_id}/approve")[0])]
    gate = LoopbackGate(app, _PORT, _KEY, rows)
    transport = httpx.ASGITransport(app=gate, client=("127.0.0.1", 50000))
    return httpx.AsyncClient(transport=transport, base_url=f"http://localhost:{_PORT}")


@pytest.mark.parametrize(
    ("method", "path", "extra", "status"),
    [
        ("POST", _APPROVE, {}, 403),  # The guest: loopback in every way it can show.
        ("POST", _APPROVE, {STAND_HEADER: "a-guess"}, 403),
        ("POST", _APPROVE + "/", {}, 404),  # Not the row (the echo app's 404 stands for routing).
        ("POST", _APPROVE, {STAND_HEADER: _KEY}, 200),  # The console's CLI.
        # No cookie channel: a localhost cookie reaches every port, so the key is a header alone.
        ("POST", _APPROVE, {"Cookie": f"hive_stand={_KEY}"}, 403),
        ("POST", "/v1/auth/challenge", {}, 200),  # A row on both listeners needs no stand key.
        ("GET", _APPROVE, {}, 200),  # Another method is another row.
    ],
)
async def test_a_loopback_only_row_is_refused_without_the_stand_key(
    method: str, path: str, extra: dict[str, str], status: int
) -> None:
    async def echo_or_404(scope: Scope, receive: Receive, send: Send) -> None:
        """Answer 404 for a path no row has, else 200, as the router would."""
        found = scope["path"] != _APPROVE + "/"
        await send({"type": "http.response.start", "status": 200 if found else 404, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async with _gate(echo_or_404) as client:
        response = await client.request(method, path, headers={**_GUEST, **extra})

    assert response.status_code == status


async def test_a_guest_flooding_loopback_cannot_starve_the_stand_key_holder() -> None:
    section = EntranceSection(rate_limit_per_address=2)
    limited = AddressLimit(_echo_length, RateLimiter.from_section(section, FakeClock()))
    async with _gate(limited) as client:
        guest = [(await client.get("/v1/devices", headers=_GUEST)).status_code for _ in range(3)]
        console = await client.get("/v1/devices", headers={**_GUEST, STAND_HEADER: _KEY})

    assert guest == [200, 200, 429]
    assert console.status_code == 200
