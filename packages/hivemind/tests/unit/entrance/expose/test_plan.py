"""Tests for hivemind.entrance.expose.plan: every mode planned, every refusal asserted.

Fits into the Hive:
    Mirrors src/hivemind/entrance/expose/plan.py (codingrules section 3). The roadmap's "a test
    starts the Entrance in every mode and asserts every refusal" is proved on the plan the
    Entrance starts its listeners from: here each mode yields exactly its listeners, and
    test_plan_refusals.py (split by feature, codingrules 5.1) raises every ``ExposureRule``.
    The facts are built by hand, so any host (a Mac, a server with carrier-grade NAT on its WAN
    port, a laptop on WireGuard) can stand in front of the check.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.expose.plan for the module under test.
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md for the rules.
"""

from __future__ import annotations

import ssl
from dataclasses import replace

import pytest
from unit.entrance.expose.support import (
    CERT_PATH,
    KEY_PATH,
    LAN_V4,
    PUBLIC_HOST,
    TAILSCALE_V4,
    TAILSCALE_V6,
    host_facts,
    section,
    tls_facts,
)

from hivemind.entrance.expose import (
    HostPlatform,
    ListenerPlan,
    ListenerTls,
    TlsFacts,
    plan_exposure,
)
from hivemind.manifest.schema import EntranceExposure, EntranceSection

VPN = EntranceExposure.VPN
LAN = EntranceExposure.LAN
TUNNEL = EntranceExposure.TUNNEL


# ──────────────────────────────────────────────────────────────────────────────
# Every mode, planned
# ──────────────────────────────────────────────────────────────────────────────


def test_loopback_plans_only_the_loopback_listener() -> None:
    plan = plan_exposure(EntranceSection(), host_facts())

    assert plan.mode is EntranceExposure.LOOPBACK
    assert plan.loopback == ListenerPlan(host="127.0.0.1", port=8710, tls=None)
    assert plan.remote is None
    assert (plan.public_origin, plan.rp_id, plan.tunnel_argv) == (None, None, ())


def test_loopback_never_reads_the_remote_settings() -> None:
    # Every remote setting is present and unusable; loopback must not care.
    entrance = EntranceSection(
        remote_bind="203.0.113.9:8711", public_url="https://100.64.0.1", mutual_tls=False
    )

    plan = plan_exposure(entrance, host_facts({}, tls=TlsFacts.not_configured()))

    assert plan.remote is None


def test_vpn_plans_the_overlay_address_behind_mutual_tls_when_asked() -> None:
    # Omitted, mutual_tls is off in vpn mode (the overlay authenticates); here it is turned on.
    plan = plan_exposure(section(VPN, mutual_tls=True), host_facts())

    assert plan.remote == ListenerPlan(
        host=TAILSCALE_V4,
        port=8711,
        tls=ListenerTls(
            cert_path=CERT_PATH,
            key_path=KEY_PATH,
            server_name=PUBLIC_HOST,
            client_certificate_required=True,
        ),
    )
    assert plan.remote.tls is not None
    assert plan.remote.tls.minimum_version is ssl.TLSVersion.TLSv1_3
    assert plan.public_origin == f"https://{PUBLIC_HOST}:8711"
    assert plan.rp_id == PUBLIC_HOST
    assert plan.tunnel_argv == ()


def test_vpn_asks_for_no_client_certificate_by_default() -> None:
    plan = plan_exposure(section(VPN), host_facts())

    assert plan.remote is not None and plan.remote.tls is not None
    assert plan.remote.tls.client_certificate_required is False


def test_vpn_without_mutual_tls_serves_tls_from_version_1_2() -> None:
    plan = plan_exposure(section(VPN, mutual_tls=False), host_facts())

    assert plan.remote is not None
    assert plan.remote.tls is not None
    assert plan.remote.tls.client_certificate_required is False
    assert plan.remote.tls.minimum_version is ssl.TLSVersion.TLSv1_2


def test_vpn_accepts_the_overlays_ipv6_address() -> None:
    plan = plan_exposure(section(VPN, remote_bind=f"[{TAILSCALE_V6}]:8711"), host_facts())

    assert plan.remote is not None
    assert plan.remote.host == TAILSCALE_V6


@pytest.mark.parametrize(
    ("platform", "interface"),
    [(HostPlatform.LINUX, "tailscale0"), (HostPlatform.WINDOWS, "Tailscale")],
)
def test_vpn_defaults_to_the_platforms_tailscale_interface(
    platform: HostPlatform, interface: str
) -> None:
    facts = host_facts({interface: (TAILSCALE_V4,)}, platform=platform)

    assert plan_exposure(section(VPN), facts).remote is not None


def test_vpn_on_macos_binds_the_named_utun_interface() -> None:
    facts = host_facts({"utun4": (TAILSCALE_V4,)}, platform=HostPlatform.MACOS)

    plan = plan_exposure(section(VPN, vpn_interface="utun4"), facts)

    assert plan.remote is not None
    assert plan.remote.host == TAILSCALE_V4


def test_vpn_over_plain_wireguard_names_its_interface_and_range() -> None:
    entrance = section(
        VPN, remote_bind="10.8.0.1:8711", vpn_interface="wg0", vpn_cidrs=("10.8.0.0/24",)
    )

    plan = plan_exposure(entrance, host_facts({"wg0": ("10.8.0.1",)}))

    assert plan.remote is not None
    assert plan.remote.host == "10.8.0.1"


def test_lan_plans_a_local_address_behind_mutual_tls() -> None:
    plan = plan_exposure(section(LAN), host_facts())

    assert plan.remote is not None
    assert (plan.remote.host, plan.remote.port) == (LAN_V4, 8711)
    assert plan.remote.tls is not None
    assert plan.remote.tls.client_certificate_required is True
    assert plan.remote.tls.minimum_version is ssl.TLSVersion.TLSv1_3


@pytest.mark.parametrize("address", ["fd12:3456::20", "fe80::1"])
def test_lan_accepts_an_ipv6_unique_local_or_link_local_address(address: str) -> None:
    facts = host_facts({"eth0": (LAN_V4, address)})

    plan = plan_exposure(section(LAN, remote_bind=f"[{address}]:8711"), facts)

    assert plan.remote is not None
    assert plan.remote.host == address


def test_tunnel_plans_a_loopback_remote_listener_and_the_client_argv() -> None:
    plan = plan_exposure(section(TUNNEL), host_facts({}))

    assert plan.remote is not None
    assert (plan.remote.host, plan.remote.port) == ("127.0.0.1", 8711)
    assert plan.remote.tls is not None
    assert plan.remote.tls.client_certificate_required is True
    assert plan.tunnel_argv == ("cloudflared", "tunnel", "run")


def test_tunnel_may_let_the_os_choose_both_ports() -> None:
    entrance = section(TUNNEL, bind="127.0.0.1:0", remote_bind="127.0.0.1:0")

    plan = plan_exposure(entrance, host_facts({}))

    assert plan.remote is not None
    assert plan.remote.port == 0


def test_the_relying_party_may_be_a_parent_domain_of_public_url() -> None:
    plan = plan_exposure(section(VPN, rp_id="Example.Test."), host_facts())

    assert plan.rp_id == "example.test"


def test_a_wildcard_certificate_covers_public_url() -> None:
    facts = host_facts(tls=replace(tls_facts(), dns_names=("*.example.test",)))

    assert plan_exposure(section(VPN), facts).remote is not None


def test_public_origin_omits_the_default_https_port() -> None:
    plan = plan_exposure(section(VPN, public_url=f"https://{PUBLIC_HOST}/"), host_facts())

    assert plan.public_origin == f"https://{PUBLIC_HOST}"
