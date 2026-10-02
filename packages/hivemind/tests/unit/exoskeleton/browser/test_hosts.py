"""Unit tests for hivemind.exoskeleton.browser.hosts: which hosts a lease's browser may request."""

from __future__ import annotations

import pytest

from hivemind.exoskeleton.browser.hosts import HostScopes, request_host
from hivemind.guard import CapabilitySet

_GRANTED = CapabilitySet.parse("net:api.example.com", "net:*.cdn.example.net", "net:10.0.0.0/8")


def test_the_default_scopes_allow_no_host_at_all() -> None:
    hosts = HostScopes()

    assert not hosts.allows("api.example.com")
    assert not hosts.allows("127.0.0.1")
    assert not hosts.allows("localhost")


@pytest.mark.parametrize(
    ("host", "allowed"),
    [
        ("api.example.com", True),
        ("img.cdn.example.net", True),
        ("10.1.2.3", True),
        ("example.com", False),
        ("badapi.example.com", False),
        ("cdn.example.net", False),  # *.domain covers strict subdomains only.
        ("192.168.1.1", False),
        ("exa mple.com", False),  # Not a host the grammar can hold: refused, never raised.
    ],
)
def test_a_host_is_allowed_only_by_a_net_scope_the_bee_holds(host: str, allowed: bool) -> None:
    assert HostScopes(_GRANTED).allows(host) is allowed


@pytest.mark.parametrize("host", ["127.0.0.1", "127.9.9.9", "::1", "localhost", "app.localhost"])
def test_loopback_needs_net_loopback_unless_the_cells_loopback_is_private(host: str) -> None:
    assert not HostScopes(_GRANTED).allows(host)
    assert HostScopes(CapabilitySet.parse("net:loopback")).allows(host)
    assert HostScopes(CapabilitySet.empty(), private_loopback=True).allows(host)


def test_a_grant_for_one_loopback_spelling_does_not_cover_loopback() -> None:
    # Arrange: a CIDR scope that happens to contain a loopback address.
    hosts = HostScopes(CapabilitySet.parse("net:127.0.0.0/8"))

    assert not hosts.allows("127.0.0.1")


def test_a_private_loopback_allows_no_other_host() -> None:
    assert not HostScopes(CapabilitySet.empty(), private_loopback=True).allows("example.com")


@pytest.mark.parametrize(
    ("url", "host"),
    [
        ("https://API.Example.com:8443/x?y=1", "api.example.com"),
        ("http://[::1]:8000/", "::1"),
        ("HTTP://127.0.0.1/", "127.0.0.1"),
        ("file:///etc/passwd", None),
        ("data:text/plain,hi", None),
        ("about:blank", None),
        ("http:///no-host", None),
        ("http://[::1/", None),  # An unbalanced bracket: no host can be read.
    ],
)
def test_request_host_reads_a_web_urls_host_only(url: str, host: str | None) -> None:
    assert request_host(url) == host
