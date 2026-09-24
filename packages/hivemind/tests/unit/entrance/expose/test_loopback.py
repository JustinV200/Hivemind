"""Tests for hivemind.entrance.expose.loopback: only a loopback Host, and never through a proxy.

Fits into the Hive:
    Mirrors src/hivemind/entrance/expose/loopback.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.expose.loopback for the module under test.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hivemind.entrance.expose import loopback_request_allowed

PORT = 8710  # The loopback listener's port in these tests.
_PLAIN = ("host", "accept", "authorization", "x-hive-signature")  # What a real client sends.


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "LocalHost",
        f"localhost:{PORT}",
        "127.0.0.1",
        f"127.0.0.1:{PORT}",
        "127.8.9.10",
        f"127.255.255.254:{PORT}",
        "[::1]",
        f"[::1]:{PORT}",
        f"[0:0:0:0:0:0:0:1]:{PORT}",
    ],
)
def test_a_loopback_host_is_answered(host: str) -> None:
    assert loopback_request_allowed(host, _PLAIN, PORT) is True


@pytest.mark.parametrize(
    "host",
    [
        "hive.example.test",
        f"attacker.example:{PORT}",
        "192.168.1.20",
        "0.0.0.0",  # noqa: S104 -- a Host header value under test, not a bind address
        "localhost.",
        "hive.localhost",
        "127.1",
        "0x7f.0.0.1",
        "127.000.000.001",
        "::1",
        "[::1%lo]",
        "[::ffff:127.0.0.1]",
        "[::1",
        "[not-an-address]",
        "localhost:",
        "localhost:8710:1",
        "localhost:\uff18\uff17\uff11\uff10",  # Full-width digits: str.isdigit accepts them.
        "localhost:99999",
        "user@localhost",
        "",
        " localhost",
    ],
)
def test_anything_else_is_refused(host: str) -> None:
    assert loopback_request_allowed(host, _PLAIN, PORT) is False


def test_a_missing_host_is_refused() -> None:
    assert loopback_request_allowed(None, ("accept",), PORT) is False


@pytest.mark.parametrize("port", [PORT + 1, 80, 0])
def test_a_host_naming_another_port_is_refused(port: int) -> None:
    assert loopback_request_allowed(f"localhost:{port}", _PLAIN, PORT) is False
    assert loopback_request_allowed(f"[::1]:{port}", _PLAIN, PORT) is False


@pytest.mark.parametrize(
    "header",
    [
        "Forwarded",
        "X-Forwarded-For",
        "X-Forwarded-Host",
        "X-Forwarded-Proto",
        "X-Forwarded-Port",
        "x-forwarded-prefix",
        "X-Real-IP",
        "Via",
        "Tailscale-User-Login",
        "tailscale-user-name",
        "TAILSCALE-FUNNEL-REQUEST",
    ],
)
def test_any_forwarding_header_is_refused_whatever_the_host(header: str) -> None:
    assert loopback_request_allowed("localhost", (*_PLAIN, header), PORT) is False


@given(st.text(alphabet="abcdefghijklmnopqrstuvwxyz-", min_size=1, max_size=20))
def test_every_x_forwarded_header_is_refused(suffix: str) -> None:
    assert loopback_request_allowed("127.0.0.1", ("host", f"X-Forwarded-{suffix}"), PORT) is False


@given(st.from_regex(r"[a-z][a-z0-9-]{0,20}\.[a-z]{2,6}", fullmatch=True))
def test_no_dns_name_but_localhost_is_answered(name: str) -> None:
    # A rebinding page's name is whatever the attacker registered; none may get through.
    assert loopback_request_allowed(f"{name}:{PORT}", _PLAIN, PORT) is False
