"""Unit tests for hivemind.exoskeleton.browser.hosts: which hosts a lease's browser may request."""

from __future__ import annotations

import pytest

from hivemind.exoskeleton.browser.hosts import (
    LOCAL_SOURCES,
    LOOPBACK_SOURCES,
    HostScopes,
    page_policy,
    redirect_target,
    request_host,
)
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


@pytest.mark.parametrize(
    ("url", "host"),
    [("wss://Chat.Example.com/socket", "chat.example.com"), ("ws://127.0.0.1:9000/", "127.0.0.1")],
)
def test_request_host_reads_a_websockets_host_too(url: str, host: str) -> None:
    assert request_host(url) == host


@pytest.mark.parametrize(
    ("status", "location", "target"),
    [
        (302, "/next?a=1", "https://a.example/next?a=1"),
        (301, "https://b.example/", "https://b.example/"),
        (307, "../up", "https://a.example/up"),
        (303, "//c.example/x", "https://c.example/x"),  # Scheme-relative: still a new host.
        (200, "/next", None),  # Only a followed status leads anywhere.
        (304, "/next", None),
        (300, "/next", None),
        (302, None, None),  # A redirect with nowhere to go is shown as it is.
        (302, "", None),
    ],
)
def test_redirect_target_resolves_a_followed_redirect_only(
    status: int, location: str | None, target: str | None
) -> None:
    assert redirect_target("https://a.example/dir/page", status, location) == target


def _sources(policy: str | None) -> set[str]:
    assert policy is not None
    directive, *sources = policy.split()
    assert directive == "default-src"
    return set(sources)


def test_the_page_policy_names_each_granted_host_on_every_web_and_socket_scheme() -> None:
    policy = page_policy(HostScopes(CapabilitySet.parse("net:api.example.com", "net:*.cdn.net")))

    sources = _sources(policy)

    for scheme in ("http", "https", "ws", "wss"):
        assert f"{scheme}://api.example.com:*" in sources
        assert f"{scheme}://*.cdn.net:*" in sources
    # The page's own code and its in-browser URLs still work under the policy.
    assert set(LOCAL_SOURCES.split()) <= sources
    assert len(sources) == 8 + len(LOCAL_SOURCES.split())


def test_the_page_policy_never_names_a_host_the_rule_refuses() -> None:
    # Arrange: grants a source list cannot spell (a range, IPv6), grants for a loopback host by
    # another name (which the rule refuses), and a capability that is not `net` at all.
    granted = CapabilitySet.parse(
        "net:10.0.0.0/8",
        "net:fd00::1",
        "net:localhost",
        "net:*.localhost",
        "net:127.0.0.1",
        "net:10.1.2.3",
        "tool:api.example.com",
    )

    sources = _sources(page_policy(HostScopes(granted)))

    assert {source.split("://")[1] for source in sources if "://" in source} == {"10.1.2.3:*"}


@pytest.mark.parametrize(
    "hosts",
    [
        HostScopes(CapabilitySet.parse("net:loopback")),
        HostScopes(CapabilitySet.empty(), private_loopback=True),
    ],
)
def test_the_page_policy_spells_loopback_only_where_the_rule_allows_it(hosts: HostScopes) -> None:
    sources = _sources(page_policy(hosts))

    for host in LOOPBACK_SOURCES:
        assert f"ws://{host}:*" in sources
    assert not any("loopback" in source for source in sources)  # A word, never a host.


def test_the_page_policy_with_no_grant_allows_only_what_stays_in_the_browser() -> None:
    assert page_policy(HostScopes()) == f"default-src {LOCAL_SOURCES}"


def test_there_is_no_page_policy_when_every_host_is_allowed() -> None:
    assert page_policy(HostScopes(CapabilitySet.parse("net:*"))) is None
