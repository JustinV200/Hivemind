"""Tests for hivemind.swarm.packet.tailscale: the key Tailscale is asked for, and its refusals.

Fits into the Hive:
    Mirrors src/hivemind/swarm/packet/tailscale.py (codingrules section 3). Drives
    `TailscaleApiKeys` through `httpx.MockTransport`, so no request leaves the machine, and checks
    the two calls it makes: the client-credentials grant, and a key request whose terms are the
    ones the trust model depends on (single use, preauthorized, not ephemeral, tagged).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.packet.tailscale for the module under test.
"""

from __future__ import annotations

import json
from datetime import timedelta
from urllib.parse import parse_qs

import httpx
import pytest

from hivemind.swarm.errors import PacketError
from hivemind.swarm.packet import TAILSCALE_API, EnvSecretSource, TailscaleApiKeys

_SECRET = "tskey-client-kTEST-not-real"  # noqa: S105 -- a probe value, not a credential
_TOKEN = "access-token-not-real"  # noqa: S105 -- a probe value, not a credential
_KEY = "tskey-auth-kKEY-not-real"
_SOURCE = EnvSecretSource("TS", {"TS_CLIENT_ID": "kTEST", "TS_CLIENT_SECRET": _SECRET})


@pytest.mark.parametrize("key_status", [200, 403])
async def test_mint_asks_for_a_single_use_preauthorized_tagged_key_and_hides_secrets(
    key_status: int,
) -> None:
    # Arrange a Tailscale that grants the token and answers the key request with key_status.
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/v2/oauth/token":
            return httpx.Response(200, json={"access_token": _TOKEN, "expires_in": 3600})
        return httpx.Response(key_status, json={"id": "kKEY", "key": _KEY})

    transport = httpx.MockTransport(answer)
    async with httpx.AsyncClient(base_url=TAILSCALE_API, transport=transport) as client:
        keys = TailscaleApiKeys(_SOURCE, client)

        if key_status == 200:
            key = await keys.mint(timedelta(minutes=15))
        else:
            with pytest.raises(PacketError) as refused:
                await keys.mint(timedelta(minutes=15))

    grant, create = seen
    assert parse_qs(grant.content.decode()) == {
        "client_id": ["kTEST"],
        "client_secret": [_SECRET],
        "grant_type": ["client_credentials"],
    }
    assert create.url.path == "/api/v2/tailnet/-/keys"
    assert create.headers["Authorization"] == f"Bearer {_TOKEN}"
    body = json.loads(create.content)
    assert body["expirySeconds"] == 900
    assert body["capabilities"]["devices"]["create"] == {
        "reusable": False,
        "ephemeral": False,
        "preauthorized": True,
        "tags": ["tag:hive-device"],
    }
    if key_status == 200:
        assert key.key_id == "kKEY" and key.key.get_secret_value() == _KEY
        assert _KEY not in repr(key)
    else:
        assert all(s not in str(refused.value) for s in (_SECRET, _TOKEN, _KEY))
