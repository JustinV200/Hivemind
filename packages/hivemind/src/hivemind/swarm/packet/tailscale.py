"""Provide TailscaleApiKeys: mint and revoke a packet's tailnet key through Tailscale's API.

Tailscale issues auth keys to an OAuth client: the client trades its id and secret for a short-
lived access token (`POST /api/v2/oauth/token`, the client-credentials grant), and the token
creates a key (`POST /api/v2/tailnet/-/keys`, `-` meaning the client's own tailnet) or deletes one
(`DELETE /api/v2/tailnet/-/keys/<id>`). Every call takes a fresh token: a packet is built by hand
a few times a month, so caching one would only add a secret that lingers in memory. The HTTP client
is injected, so tests drive this with `httpx.MockTransport` and never reach the network.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.packet`.
    Built by `hive swarm packet`'s composition; called by `hivemind.swarm.packet.builder` through
    `TailnetKeys`. Calls into `httpx` and `hivemind.swarm.packet.credentials`.

Key invariants:
    - The key's terms are `TailnetKeys`' fixed ones: single use, preauthorized, not ephemeral,
      tagged `tag:hive-device`.
    - No error message carries the client secret, the token or the key; a refusal names the HTTP
      status and the step.

See Also:
    - https://tailscale.com/kb/1215/oauth-clients for the client-credentials grant and scopes.
    - https://tailscale.com/api for the keys endpoints.
    - hivemind.swarm.packet.tailnet for the protocol.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
from pydantic import SecretStr

from hivemind.swarm.errors import PacketError
from hivemind.swarm.packet.credentials import SecretSource
from hivemind.swarm.packet.tailnet import HIVE_DEVICE_TAG, TailnetKey

TAILSCALE_API = "https://api.tailscale.com"  # Tailscale's control-plane API; no other host.
OAUTH_GRANT_PATH = "/api/v2/oauth/token"  # Client credentials in, a bearer token out.
KEYS_PATH = "/api/v2/tailnet/-/keys"  # `-`: the tailnet the OAuth client belongs to.
API_TIMEOUT_S = 15.0  # A control-plane call answers in under a second; a stuck one fails the build.
KEY_DESCRIPTION = "HiveMind Pollen Packet"  # Shown beside the key in the admin console.

__all__ = ["API_TIMEOUT_S", "TAILSCALE_API", "TailscaleApiKeys"]


class TailscaleApiKeys:
    """Mint and revoke tailnet keys as the OAuth client a `SecretSource` holds."""

    def __init__(self, source: SecretSource, client: httpx.AsyncClient) -> None:
        """Build the minter over its credentials and an HTTP client.

        Args:
            source: Where the OAuth client is read from, once per call.
            client: The HTTP client; its base URL should be `TAILSCALE_API`.
        """
        self._source = source
        self._client = client

    async def mint(self, ttl: timedelta) -> TailnetKey:
        """Mint one single-use, preauthorized, non-ephemeral, tagged key that lapses after `ttl`.

        Args:
            ttl: The key's lifetime; at least one second.

        Returns:
            The key and its id.

        Raises:
            PacketError: Tailscale refused, could not be reached, or answered without a key.
        """
        create = {
            "reusable": False,
            "ephemeral": False,
            "preauthorized": True,
            "tags": [HIVE_DEVICE_TAG],
        }
        body = {
            "capabilities": {"devices": {"create": create}},
            "expirySeconds": max(1, int(ttl.total_seconds())),
            "description": KEY_DESCRIPTION,
        }
        # One control-plane round trip after the token's; API_TIMEOUT_S bounds each.
        answer = await self._call("POST", KEYS_PATH, "mint a tailnet key", body)
        key_id, key = answer.get("id"), answer.get("key")
        if not isinstance(key_id, str) or not isinstance(key, str) or not key:
            raise PacketError("Tailscale answered the key request without a key and its id.")
        return TailnetKey(key_id=key_id, key=SecretStr(key))

    async def revoke(self, key_id: str) -> None:
        """Delete a key minted for a packet that was not built.

        Args:
            key_id: The id `mint` returned.

        Raises:
            PacketError: Tailscale refused or could not be reached.
        """
        await self._call("DELETE", f"{KEYS_PATH}/{key_id}", "revoke the tailnet key", None)

    async def _call(
        self, method: str, path: str, step: str, body: dict[str, object] | None
    ) -> dict[str, object]:
        """Make one authorised API call and return its JSON object (empty for no body)."""
        token = await self._token()
        headers = {"Authorization": f"Bearer {token.get_secret_value()}"}
        # A control-plane call; API_TIMEOUT_S turns a hang into a refusal.
        request = self._client.build_request(
            method, path, headers=headers, json=body, timeout=API_TIMEOUT_S
        )
        return _json_object(await self._send(request, step))

    async def _token(self) -> SecretStr:
        """Trade the OAuth client for a bearer token (client-credentials grant)."""
        client = self._source.read()
        form = {
            "client_id": client.client_id,
            "client_secret": client.client_secret.get_secret_value(),
            "grant_type": "client_credentials",
        }
        request = self._client.build_request(
            "POST", OAUTH_GRANT_PATH, data=form, timeout=API_TIMEOUT_S
        )
        response = await self._send(request, "get an OAuth token")
        token = _json_object(response).get("access_token")
        if not isinstance(token, str) or not token:
            raise PacketError("Tailscale answered the OAuth token request without a token.")
        return SecretStr(token)

    async def _send(self, request: httpx.Request, step: str) -> httpx.Response:
        """Send one request; turn a transport failure or an error status into a PacketError."""
        try:
            response = await self._client.send(request)
        except httpx.HTTPError as exc:
            # The exception's type only: its text could echo a request that carried the secret.
            raise PacketError(f"Could not {step}: {type(exc).__name__}.") from None
        if response.is_error:
            raise PacketError(f"Tailscale refused to {step}: HTTP {response.status_code}.")
        return response


def _json_object(response: httpx.Response) -> dict[str, object]:
    """Return a response's JSON object, or an empty one for no body, other JSON or none at all."""
    try:
        parsed = response.json() if response.content else {}
    except ValueError:
        return {}  # Not JSON: the caller reports the missing field, never the body.
    return parsed if isinstance(parsed, dict) else {}
