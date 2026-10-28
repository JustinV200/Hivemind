"""Tests for hivemind.swarm.packet.credentials: where the Tailscale OAuth client is read from.

Fits into the Hive:
    Mirrors src/hivemind/swarm/packet/credentials.py (codingrules section 3). One table of
    `[swarm.packet] tailscale_oauth` strings and environments: what reads, and what is refused
    before anything is minted, with the secret never in the refusal.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.packet.credentials for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.swarm.errors import PacketError
from hivemind.swarm.packet import parse_secret_source

_SECRET = "tskey-client-kTEST-not-real"  # noqa: S105 -- a probe value; never in a refusal
_BOTH = {"TS_OAUTH_CLIENT_ID": "kTEST", "TS_OAUTH_CLIENT_SECRET": _SECRET}


@pytest.mark.parametrize(
    ("spec", "environ", "client_id"),
    [
        ("env:TS_OAUTH", _BOTH, "kTEST"),
        ("env:TS_OAUTH", {"TS_OAUTH_CLIENT_SECRET": _SECRET}, None),  # Half a client.
        ("env:OTHER", _BOTH, None),  # The variables the prefix names are unset.
        ("credman:hivemind-test-no-such-credential", {}, None),  # Absent, or not on Windows.
        ("", _BOTH, None),  # Not set up yet.
        ("vault:TS_OAUTH", _BOTH, None),  # A kind there is no source for.
    ],
)
def test_parse_secret_source_reads_the_named_client_or_refuses_without_the_secret(
    spec: str, environ: dict[str, str], client_id: str | None
) -> None:
    if client_id is not None:
        client = parse_secret_source(spec, environ).read()

        assert client.client_id == client_id
        assert client.client_secret.get_secret_value() == _SECRET and _SECRET not in repr(client)
        assert _SECRET not in repr(parse_secret_source(spec, environ))
        return

    with pytest.raises(PacketError) as refused:
        parse_secret_source(spec, environ).read()

    assert _SECRET not in str(refused.value)
