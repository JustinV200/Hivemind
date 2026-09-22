"""Tests for waggle.uris: the wss-anywhere, ws-on-loopback-only rule.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises check_waggle_uri and
    is_loopback_host with the endpoint forms a Hive meets: TLS endpoints on any host, plaintext
    endpoints on the three loopback forms, and the plaintext, non-WebSocket and unparseable
    forms that must be refused.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.uris for the module under test.
    - waggle.messages.control.hive for QueenMoved, which applies the rule to new_address.
"""

from __future__ import annotations

import pytest

from waggle.uris import (
    WAGGLE_URI_PATTERN,
    check_waggle_uri,
    is_loopback_host,
    is_virtual_cell_gateway_host,
)


@pytest.mark.parametrize(
    "uri",
    [
        "wss://queen.example.org:8443/waggle",
        "wss://abcdefghijklmnopqrstuvwxyz234567.onion",
        "ws://127.0.0.1:9000",
        "ws://127.255.255.254",
        "ws://localhost",
        "ws://localhost:9000/waggle",
        "ws://[::1]:9000",
    ],
)
def test_check_waggle_uri_returns_an_acceptable_uri_unchanged(uri: str) -> None:
    assert check_waggle_uri(uri) == uri


@pytest.mark.parametrize(
    ("uri", "reason"),
    [
        ("ws://example.org", "loopback"),
        ("ws://10.0.0.1:9000", "loopback"),
        ("ws://localhost.example.org", "loopback"),
        ("ws://[::2]", "loopback"),
        ("ws://", "not a WebSocket URI"),  # nothing after the scheme fails the pattern
        ("http://127.0.0.1", "not a WebSocket URI"),
        ("wss://queen example.org", "not a WebSocket URI"),  # whitespace fails \S+
        ("ws://[::1", "cannot be parsed"),
    ],
)
def test_check_waggle_uri_rejects_with_a_full_sentence(uri: str, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        check_waggle_uri(uri)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "127.1.2.3", "::1"])
def test_is_loopback_host_accepts_the_loopback_forms(host: str) -> None:
    assert is_loopback_host(host)


@pytest.mark.parametrize("host", ["example.org", "10.0.0.1", "::2", "LOCALHOST", "", "127"])
def test_is_loopback_host_rejects_everything_else(host: str) -> None:
    # Only a literal can be judged without a lookup; a name other than "localhost" is not
    # trusted even if it would resolve to loopback, and the name is matched exactly.
    assert not is_loopback_host(host)


def test_pattern_is_the_one_queen_moved_declares() -> None:
    assert WAGGLE_URI_PATTERN == r"^wss?://\S+$"


# ──────────────────────────────────────────────────────────────────────────────
# Roadmap step 5.6: allow_virtual_cell_gateway_host, the Virtual Cell control-link exception.
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "uri",
    [
        "ws://host.docker.internal:9500",
        "ws://10.0.2.2:9500",  # QEMU's own SLIRP gateway; inside 10.0.0.0/8.
        "ws://10.0.0.1:9000",  # RFC 1918.
        "ws://172.16.0.5:9000",  # RFC 1918.
        "ws://192.168.1.5:9000",  # RFC 1918.
        "ws://169.254.1.1:9000",  # Link-local.
        "ws://127.0.0.1:9000",  # Loopback still accepted when the flag is set.
    ],
)
def test_check_waggle_uri_allows_gateway_hosts_when_flagged(uri: str) -> None:
    assert check_waggle_uri(uri, allow_virtual_cell_gateway_host=True) == uri


@pytest.mark.parametrize("uri", ["ws://host.docker.internal:9500", "ws://10.0.0.1:9000"])
def test_check_waggle_uri_still_refuses_gateway_hosts_by_default(uri: str) -> None:
    """The flag is opt-in: every existing call site (default False) keeps today's behaviour."""
    with pytest.raises(ValueError, match="loopback host"):
        check_waggle_uri(uri)


def test_check_waggle_uri_still_refuses_a_public_host_even_when_flagged() -> None:
    with pytest.raises(ValueError, match="loopback"):
        check_waggle_uri("ws://example.org:9000", allow_virtual_cell_gateway_host=True)


@pytest.mark.parametrize(
    "host",
    ["host.docker.internal", "10.0.2.2", "10.0.0.1", "172.16.0.5", "192.168.1.5", "169.254.1.1"],
)
def test_is_virtual_cell_gateway_host_accepts_the_documented_forms(host: str) -> None:
    assert is_virtual_cell_gateway_host(host)


@pytest.mark.parametrize("host", ["example.org", "8.8.8.8", ""])
def test_is_virtual_cell_gateway_host_rejects_public_hosts_and_unresolvable_names(
    host: str,
) -> None:
    # A public address is never a Virtual Cell gateway, and an unresolvable name is refused
    # rather than looked up (Python's ipaddress.is_private also covers loopback, so this
    # function may return True for a loopback literal too -- harmless, since check_waggle_uri
    # already accepts loopback through is_loopback_host before this function is even consulted).
    assert not is_virtual_cell_gateway_host(host)
