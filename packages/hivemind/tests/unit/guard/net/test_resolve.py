"""Tests for hivemind.guard.net.resolve and .fake: resolving a host, and the resolver tests use.

Fits into the Hive:
    Mirrors src/hivemind/guard/net/resolve.py and fake.py (codingrules section 3); the fake is
    tested with the seam it implements.

Key invariants:
    - None: this module holds tests only. Nothing here performs a real DNS lookup except
      `system_resolver` on a literal, which the operating system answers without the network.

See Also:
    - hivemind.guard.net.resolve for the module under test.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Sequence

import pytest

from hivemind.guard.net import (
    DEFAULT_ANSWER,
    FakeResolver,
    IPAddress,
    UnresolvableHostError,
    resolve_host,
    system_resolver,
)
from hivemind.guard.net import resolve as resolve_module


async def test_a_literal_is_its_own_answer_and_is_never_looked_up() -> None:
    resolver = FakeResolver()

    addresses = await resolve_host("::ffff:127.0.0.1", 80, resolver)

    assert addresses == (ipaddress.ip_address("127.0.0.1"),)
    assert resolver.lookups == []


async def test_a_name_resolves_through_the_seam_plain_and_deduplicated_in_order() -> None:
    resolver = FakeResolver({"api.example.com": ["::ffff:10.0.0.2", "10.0.0.1", "10.0.0.2"]})

    addresses = await resolve_host("api.example.com", 443, resolver)

    assert addresses == (ipaddress.ip_address("10.0.0.2"), ipaddress.ip_address("10.0.0.1"))
    assert resolver.lookups == [("api.example.com", 443)]


async def test_an_unknown_name_is_unresolvable() -> None:
    with pytest.raises(UnresolvableHostError, match="gaierror"):
        await resolve_host("nowhere.example", 80, FakeResolver(default=None))


async def test_a_name_with_no_address_is_unresolvable() -> None:
    with pytest.raises(UnresolvableHostError, match="no address"):
        await resolve_host("empty.example", 80, FakeResolver({"empty.example": []}))


async def test_a_lookup_that_never_answers_is_unresolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    # A zero deadline stands in for a resolver that hangs, so the test never waits in real time.
    monkeypatch.setattr(resolve_module, "RESOLVE_TIMEOUT_S", 0.0)

    async def hanging(host: str, port: int) -> Sequence[IPAddress]:
        await asyncio.Event().wait()
        return ()

    with pytest.raises(UnresolvableHostError, match="TimeoutError"):
        await resolve_host("slow.example", 80, hanging)


async def test_the_fake_answers_its_default_for_an_unlisted_name_case_insensitively() -> None:
    resolver = FakeResolver({"Known.Example.": ["10.1.1.1"]})

    assert await resolver("known.example", 80) == (ipaddress.ip_address("10.1.1.1"),)
    assert await resolver("other.example", 80) == (ipaddress.ip_address(DEFAULT_ANSWER[0]),)


async def test_the_fake_fails_like_a_real_resolver_without_a_default() -> None:
    with pytest.raises(socket.gaierror):
        await FakeResolver(default=None)("unknown.example", 80)


async def test_the_system_resolver_answers_a_literal_without_the_network() -> None:
    # getaddrinfo answers a numeric host itself (AI_NUMERICHOST is implied for a literal).
    assert await system_resolver("127.0.0.1", 80) == (ipaddress.ip_address("127.0.0.1"),)


def test_unresolvable_host_error_names_the_host_and_the_reason() -> None:
    error = UnresolvableHostError("x.example", "no address")

    assert error.host == "x.example"
    assert str(error) == "Host 'x.example' did not resolve to an address (no address)."
