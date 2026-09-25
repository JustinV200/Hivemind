"""Tests for hivemind.entrance.expose.names: public_url's host, certificate names, relying party.

Fits into the Hive:
    Mirrors src/hivemind/entrance/expose/names.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.expose.names for the module under test.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hivemind.entrance.expose.names import (
    MAX_DNS_NAME_CHARS,
    certificate_covers,
    describe_public_host,
    is_dns_name,
    is_public_suffix,
    is_same_or_parent_domain,
    public_host,
    public_origin,
)


@pytest.mark.parametrize(
    ("url", "host"),
    [
        ("https://hive.example.test", "hive.example.test"),
        ("https://Hive.Example.TEST.:8711/landing", "hive.example.test"),
        ("https://bücher.example", "xn--bcher-kva.example"),
        ("https://machine.tailnet-1234.ts.net", "machine.tailnet-1234.ts.net"),
        ("https://hive", "hive"),
    ],
)
def test_public_host_returns_the_canonical_dns_name(url: str, host: str) -> None:
    assert public_host(url) == host


@pytest.mark.parametrize(
    "url",
    [
        "https://",
        "https://[::1",
        "https://100.64.0.1",
        "https://[fd7a:115c:a1e0::1]:8711",
        "https://localhost:8711",
        "https://hive.localhost",
        "https://operator:secret@hive.example.test",
        "https://hive.example.test:99999",
        "https://exa mple.test",
        "https://hive..example.test",
        "https://-hive.example.test",
        "https://*.example.test",
        "https://hive.example.123",
        "https://hive_underscore.example.test",
    ],
)
def test_public_host_refuses_anything_that_is_not_a_usable_dns_name(url: str) -> None:
    assert public_host(url) is None


def test_public_origin_is_what_a_browser_sends() -> None:
    assert public_origin("https://Hive.Example.Test:8711/x?y") == "https://hive.example.test:8711"
    assert public_origin("https://hive.example.test:443/") == "https://hive.example.test"


@pytest.mark.parametrize(
    ("url", "sentence"),
    [
        ("https://100.64.0.1:8711/?token=t0k3n", "public_url's host is '100.64.0.1'."),
        (
            "https://operator:t0k3n@hive.example.test",
            "public_url carries credentials before its host.",
        ),
        ("https://", "public_url names no host."),
        ("https://[::1", "public_url does not parse as a URL."),
    ],
)
def test_describe_public_host_names_the_host_and_nothing_else(url: str, sentence: str) -> None:
    assert describe_public_host(url) == sentence


def test_is_dns_name_bounds_the_whole_name() -> None:
    label = "a" * 63
    too_long = ".".join([label] * 4)  # 255 characters.

    assert len(too_long) > MAX_DNS_NAME_CHARS
    assert not is_dns_name(too_long)
    assert is_dns_name(f"{'a' * 62}.example.test")
    assert not is_dns_name(f"{'a' * 64}.example.test")


@pytest.mark.parametrize(
    ("names", "host", "covered"),
    [
        (("hive.example.test",), "hive.example.test", True),
        (("HIVE.Example.test.",), "hive.example.test", True),
        (("other.example.test", "hive.example.test"), "hive.example.test", True),
        (("*.example.test",), "hive.example.test", True),
        (("*.example.test",), "a.hive.example.test", False),
        (("*.example.test",), "example.test", False),
        (("h*.example.test",), "hive.example.test", False),
        (("*.test",), "example.test", False),
        (("*.hive.example.test",), "hive.example.test", False),
        ((), "hive.example.test", False),
    ],
)
def test_certificate_covers_matches_as_browsers_do(
    names: tuple[str, ...], host: str, covered: bool
) -> None:
    assert certificate_covers(names, host) is covered


@pytest.mark.parametrize(
    ("domain", "host", "allowed"),
    [
        ("hive.example.test", "hive.example.test", True),
        ("example.test", "hive.example.test", True),
        ("Example.Test.", "hive.example.test", True),
        ("ample.test", "hive.example.test", False),
        ("sub.hive.example.test", "hive.example.test", False),
        ("", "hive.example.test", False),
    ],
)
def test_is_same_or_parent_domain_follows_whole_labels(
    domain: str, host: str, allowed: bool
) -> None:
    assert is_same_or_parent_domain(domain, host) is allowed


@given(st.ip_addresses())
def test_no_ip_address_is_ever_a_dns_name(address: object) -> None:
    assert not is_dns_name(str(address))


@pytest.mark.parametrize(
    ("domain", "public"),
    [
        ("ts.net", True),  # Tailscale's MagicDNS domain: every tailnet's hosts live under it.
        ("ngrok-free.app", True),
        ("net", True),  # One label is a top-level domain.
        ("tail1234.ts.net", False),  # One operator's own tailnet.
        ("hive.tail1234.ts.net", False),
        ("example.test", False),
    ],
)
def test_is_public_suffix_names_the_suffixes_no_relying_party_may_use(
    domain: str, public: bool
) -> None:
    assert is_public_suffix(domain) is public
