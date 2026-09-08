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
    - waggle.messages.control_hive for QueenMoved, which applies the rule to new_address.
"""

from __future__ import annotations

import pytest

from waggle.uris import WAGGLE_URI_PATTERN, check_waggle_uri, is_loopback_host


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
