"""Tests for hivemind.guard.capabilities.hosts: the `net` scope grammar and its matching.

Fits into the Hive:
    Mirrors src/hivemind/guard/capabilities/hosts.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.capabilities.hosts for the module under test.
    - docs/adr/0031-capability-model-attenuation-and-enforcement-points.md, the host row.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hivemind.guard.capabilities.hosts import host_covers, host_error

# codingrules 14.3: a generous, deterministic example budget and no per-test deadline.
_SETTINGS = settings(max_examples=200, deadline=None)


@pytest.mark.parametrize(
    "scope",
    [
        "*",
        "api.example.com",
        "API.Example.COM",
        "localhost",
        "_service.example.com",
        "*.example.com",
        "*.com",
        "10.0.0.1",
        "fd00::1",
        "10.0.0.0/8",
        "fd00::/8",
        "192.168.1.0/24",
    ],
)
def test_host_error_accepts_every_documented_shape(scope: str) -> None:
    assert host_error(scope) is None


@pytest.mark.parametrize(
    "scope",
    [
        "10.0.0.*",  # A wildcard anywhere but a leading "*." or the whole scope.
        "api.*",
        "*example.com",  # A star with no label boundary after it.
        "*.*.com",
        "a.*.com",
        "*.",  # A subdomain wildcard with no domain.
        "example..com",  # An empty label.
        ".example.com",
        "example.com.",
        "10.0.0.1/8",  # A CIDR with host bits set: refused, not silently widened.
        "https://api.example.com",  # A URL, not a host.
        "api.example.com:443",  # A port is not part of a host.
        "a" * 64 + ".com",  # A label over 63 characters.
        ("a" * 63 + ".") * 4 + "com",  # A name over 253 characters.
        "[::1]",  # A bracketed IPv6 literal is URL syntax, not an address.
    ],
)
def test_host_error_refuses_every_other_shape(scope: str) -> None:
    assert host_error(scope) is not None


@pytest.mark.parametrize(
    ("held", "needed", "expected"),
    [
        ("*", "api.example.com", True),  # "*" covers any host...
        ("*", "10.1.2.3", True),  # ...and any address...
        ("*", "*.example.com", True),  # ...and any pattern.
        ("api.example.com", "api.example.com", True),
        ("api.example.com", "API.EXAMPLE.com", True),  # DNS names compare case-insensitively.
        ("api.example.com", "www.example.com", False),
        ("api.example.com", "*", False),  # An exact host never covers "any host".
        ("*.example.com", "a.example.com", True),
        ("*.example.com", "a.b.example.com", True),
        ("*.example.com", "A.Example.com", True),
        ("*.example.com", "example.com", False),  # Strict subdomains only.
        ("*.example.com", "badexample.com", False),  # On a label boundary only.
        ("*.example.com", "*.example.com", True),  # A pattern covers itself...
        ("*.example.com", "*.sub.example.com", True),  # ...and any narrower subdomain pattern...
        ("*.sub.example.com", "*.example.com", False),  # ...never a wider one.
        ("*.example.com", "10.0.0.1", False),  # No lookup: a name never covers an address.
        ("*.example.com", "*", False),
        ("10.0.0.1", "10.0.0.1", True),
        ("10.0.0.1", "10.0.0.2", False),
        ("fd00::1", "fd00:0:0::1", True),  # IP literals compare as addresses, not text.
        ("10.0.0.0/8", "10.1.2.3", True),
        ("10.0.0.0/8", "11.0.0.1", False),
        ("10.0.0.0/8", "10.1.0.0/16", True),  # A narrower network inside the held one.
        ("10.1.0.0/16", "10.0.0.0/8", False),
        ("fd00::/8", "fd12::1", True),
        ("fd00::/8", "10.0.0.1", False),  # IPv4 and IPv6 never cover each other.
        ("10.0.0.0/8", "fd00::1", False),
        ("::ffff:10.0.0.1", "10.0.0.1", False),  # An IPv4-mapped literal is IPv6.
        ("10.0.0.1", "example.com", False),  # An address never covers a name.
    ],
)
def test_host_covers(held: str, needed: str, expected: bool) -> None:
    assert host_covers(held, needed) is expected


def test_host_covers_refuses_an_invalid_scope_on_either_side() -> None:
    # Both sides are validated when their Capability is built; a caller that bypasses that
    # still gets the safe answer.
    assert host_covers("10.0.0.*", "10.0.0.1") is False
    assert host_covers("*", "not a host") is False


_LABELS = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-", min_size=1, max_size=12)
_NAMES = st.lists(_LABELS, min_size=1, max_size=4).map(".".join)
_SCOPES = st.one_of(
    st.just("*"),
    _NAMES,
    _NAMES.map(lambda name: f"*.{name}"),
    st.ip_addresses().map(str),
    st.builds(lambda addr, bits: f"{addr}/{bits}", st.ip_addresses(v=4), st.integers(0, 32)),
)


@given(scope=_SCOPES)
@_SETTINGS
def test_every_valid_scope_covers_itself(scope: str) -> None:
    # Reflexivity is what lets CapabilitySet.attenuate hand a set to itself.
    if host_error(scope) is None:
        assert host_covers(scope, scope) is True


@given(held=_NAMES, needed=_NAMES)
@_SETTINGS
def test_a_subdomain_pattern_covers_exactly_the_strict_subdomains(held: str, needed: str) -> None:
    covered = host_covers(f"*.{held}", needed)

    assert covered is needed.endswith(f".{held}")
