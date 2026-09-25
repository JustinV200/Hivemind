"""Tests for hivemind.entrance.push.destinations: the SSRF guard and the pinned request.

Fits into the Hive:
    Mirrors src/hivemind/entrance/push/destinations.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.push.destinations for the module under test.
"""

from __future__ import annotations

import ipaddress
from enum import Enum

import pytest
from unit.entrance.push.support import (
    HOOK_ADDRESS,
    HOOK_HOST,
    OWN_ADDRESS,
    StaticResolver,
    make_guard,
    make_policy,
)

from hivemind.entrance.push import (
    DeliveryOutcome,
    DestinationPolicy,
    DestinationRefusal,
    DestinationRefusedError,
    VettedDestination,
)
from hivemind.entrance.push.destinations import (
    check_addresses,
    parse_destination,
    refusal_outcome,
    system_resolver,
)
from hivemind.manifest.schema import EntranceSection


async def _refusal(
    url: str, resolver: StaticResolver | None = None, policy: DestinationPolicy | None = None
) -> Enum:
    """Vet ``url`` and return the refusal's reason (the test fails if it passes)."""
    with pytest.raises(DestinationRefusedError) as caught:
        await make_guard(resolver, policy).vet(url)
    return caught.value.reason


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("https://127.0.0.1/hook", DestinationRefusal.LOOPBACK),
        ("https://127.8.9.10:8710/v1/devices", DestinationRefusal.LOOPBACK),
        ("https://[::1]/hook", DestinationRefusal.LOOPBACK),
        ("https://0.0.0.0/hook", DestinationRefusal.UNSPECIFIED),
        ("http://0.1.2.3:8710/", DestinationRefusal.UNSPECIFIED),
        ("https://[::]/hook", DestinationRefusal.UNSPECIFIED),
        ("https://[::ffff:127.0.0.1]/hook", DestinationRefusal.LOOPBACK),
        ("https://[::ffff:169.254.169.254]/latest", DestinationRefusal.LINK_LOCAL),
        ("http://169.254.169.254/latest/meta-data/", DestinationRefusal.LINK_LOCAL),
        ("https://[fe80::1]/hook", DestinationRefusal.LINK_LOCAL),
        ("https://[fe80::1%25eth0]/hook", DestinationRefusal.LINK_LOCAL),
        ("https://224.0.0.1/hook", DestinationRefusal.MULTICAST),
        ("https://[ff02::1]/hook", DestinationRefusal.MULTICAST),
        (f"https://{OWN_ADDRESS}/hook", DestinationRefusal.HIVE_STAND),
        (f"https://[::ffff:{OWN_ADDRESS}]/hook", DestinationRefusal.HIVE_STAND),
    ],
)
async def test_vet_refuses_every_forbidden_literal(url: str, reason: DestinationRefusal) -> None:
    assert await _refusal(url) is reason


@pytest.mark.parametrize(
    "url", ["https://localhost/hook", "https://LOCALHOST.:8710/", "https://app.localhost/hook"]
)
async def test_vet_refuses_a_localhost_name_without_asking_the_resolver(url: str) -> None:
    resolver = StaticResolver()

    assert await _refusal(url, resolver) is DestinationRefusal.LOOPBACK
    assert resolver.lookups == []


async def test_vet_refuses_a_name_that_resolves_to_loopback() -> None:
    resolver = StaticResolver({"rebind.example.net": ["127.0.0.1"]})

    assert (
        await _refusal("https://rebind.example.net/hook", resolver) is DestinationRefusal.LOOPBACK
    )


async def test_vet_refuses_a_name_when_any_of_its_addresses_is_forbidden() -> None:
    resolver = StaticResolver({"mixed.example.net": [HOOK_ADDRESS, "169.254.169.254"]})

    reason = await _refusal("https://mixed.example.net/hook", resolver)

    assert reason is DestinationRefusal.LINK_LOCAL


async def test_vet_refuses_a_name_that_resolves_to_the_hive_stand() -> None:
    resolver = StaticResolver({"stand.example.net": [OWN_ADDRESS]})

    reason = await _refusal("https://stand.example.net/hook", resolver)

    assert reason is DestinationRefusal.HIVE_STAND


async def test_vet_refuses_plain_http_outside_the_vpn() -> None:
    assert await _refusal(f"http://{HOOK_HOST}/hook") is DestinationRefusal.INSECURE


async def test_vet_refuses_a_name_that_does_not_resolve() -> None:
    reason = await _refusal("https://nowhere.example.net/hook")

    assert reason is DestinationRefusal.UNRESOLVABLE


async def test_vet_refuses_loopback_even_when_the_allowlist_lists_it() -> None:
    # ADR-0042: never loopback, whatever webhook_allowlist lists.
    loopback = (ipaddress.ip_network("127.0.0.0/8"),)

    reason = await _refusal("http://127.0.0.1/hook", policy=make_policy(allowed_networks=loopback))

    assert reason is DestinationRefusal.LOOPBACK


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("/relative/hook", DestinationRefusal.MALFORMED),
        ("ftp://hook.example.net/hook", DestinationRefusal.MALFORMED),
        ("https:///no-host", DestinationRefusal.MALFORMED),
        ("https://hook.example.net/" + "a" * 2048, DestinationRefusal.MALFORMED),
        ("https://user:pw@hook.example.net/hook", DestinationRefusal.CREDENTIALS),
    ],
)
async def test_vet_refuses_what_is_not_a_plain_http_url(
    url: str, reason: DestinationRefusal
) -> None:
    assert await _refusal(url) is reason


async def test_vet_allows_https_to_a_public_name_and_pins_its_address() -> None:
    vetted = await make_guard().vet(f"https://{HOOK_HOST}/hive/notices?token=t0k3n")

    assert vetted.address == ipaddress.ip_address(HOOK_ADDRESS)
    assert str(vetted.request_url()) == f"https://{HOOK_ADDRESS}/hive/notices?token=t0k3n"
    assert vetted.headers() == {"Host": HOOK_HOST, "Connection": "close"}
    assert vetted.extensions() == {"sni_hostname": HOOK_HOST}


async def test_vet_allows_plain_http_inside_the_vpn() -> None:
    resolver = StaticResolver({"laptop.tailnet.example": ["100.101.102.103"]})

    vetted = await make_guard(resolver).vet("http://laptop.tailnet.example:8123/hook")

    assert str(vetted.request_url()) == "http://100.101.102.103:8123/hook"
    assert vetted.headers()["Host"] == "laptop.tailnet.example:8123"
    assert vetted.extensions() == {}


async def test_vet_allows_plain_http_to_an_allowlisted_host_or_network() -> None:
    by_host = make_guard(policy=make_policy(allowed_hosts=frozenset({HOOK_HOST})))
    by_network = make_guard(
        policy=make_policy(allowed_networks=(ipaddress.ip_network("203.0.113.0/24"),))
    )

    assert (await by_host.vet(f"http://{HOOK_HOST}/hook")).address == ipaddress.ip_address(
        HOOK_ADDRESS
    )
    assert (await by_network.vet(f"http://{HOOK_HOST}/hook")).address == ipaddress.ip_address(
        HOOK_ADDRESS
    )


async def test_vet_pins_an_ipv4_mapped_address_to_its_ipv4_form() -> None:
    vetted = await make_guard().vet(f"https://[::ffff:{HOOK_ADDRESS}]:8443/hook")

    assert vetted.address == ipaddress.ip_address(HOOK_ADDRESS)
    assert str(vetted.request_url()) == f"https://{HOOK_ADDRESS}:8443/hook"


async def test_vet_pins_an_ipv6_address_in_brackets() -> None:
    resolver = StaticResolver({"v6.example.net": ["2001:db8::10"]})

    vetted = await make_guard(resolver).vet("https://v6.example.net/hook")

    assert str(vetted.request_url()) == "https://[2001:db8::10]/hook"


def test_check_addresses_refuses_an_empty_answer() -> None:
    destination = parse_destination(f"https://{HOOK_HOST}/hook")

    assert check_addresses(destination, (), make_policy()) is DestinationRefusal.UNRESOLVABLE


def test_policy_from_manifest_splits_the_allowlist_into_hosts_and_networks() -> None:
    entrance = EntranceSection.model_validate(
        {"push": {"webhook_allowlist": ["Hub.Example.Net.", "10.0.0.0/8", "192.168.1.20"]}}
    )

    policy = DestinationPolicy.from_manifest(entrance, [ipaddress.ip_address("::ffff:192.0.2.5")])

    assert policy.allowed_hosts == frozenset({"hub.example.net"})
    assert policy.allowed_networks == (
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("192.168.1.20/32"),
    )
    assert ipaddress.ip_network("100.64.0.0/10") in policy.vpn_networks
    assert policy.own_addresses == frozenset({ipaddress.ip_address("192.0.2.5")})


def test_refusal_outcome_retries_an_unresolvable_name_and_refuses_the_rest() -> None:
    unresolvable = DestinationRefusedError(DestinationRefusal.UNRESOLVABLE)
    loopback = DestinationRefusedError(DestinationRefusal.LOOPBACK)

    assert refusal_outcome(unresolvable) is DeliveryOutcome.RETRY_LATER
    assert refusal_outcome(loopback) is DeliveryOutcome.REFUSED


def test_refusal_message_names_the_reason_never_the_url() -> None:
    error = DestinationRefusedError(DestinationRefusal.LINK_LOCAL)

    assert "link_local" in str(error)
    assert "169.254" not in str(error)


async def test_system_resolver_answers_an_address_literal_without_dns() -> None:
    addresses = await system_resolver("192.0.2.1", 443)

    assert addresses == (ipaddress.ip_address("192.0.2.1"),)


def test_vetted_destination_keeps_a_non_default_port_in_the_host_header() -> None:
    destination = parse_destination("https://hook.example.net:8443/x")
    vetted = VettedDestination(destination=destination, address=ipaddress.ip_address(HOOK_ADDRESS))

    assert vetted.headers()["Host"] == "hook.example.net:8443"
    assert str(vetted.request_url()) == f"https://{HOOK_ADDRESS}:8443/x"
