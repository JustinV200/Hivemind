"""Tests for hivemind.entrance.expose.plan's refusals: every ExposureRule raised and quoted.

Fits into the Hive:
    Mirrors src/hivemind/entrance/expose/plan.py (codingrules section 3), split by feature from
    test_plan.py (codingrules 5.1): that file plans every mode, this one asserts every refusal.
    Every ``ExposureRule`` is raised by at least one case in ``_REFUSALS`` (a test checks the
    table covers them all), including phase 10's exit criterion that ``expose = "lan"`` without
    mutual TLS refuses to start, and no refusal echoes a credential, a query or the tunnel argv.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.expose.plan for the module under test.
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md for the rules.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from unit.entrance.expose.support import (
    KEY_PATH,
    LAN_V4,
    PUBLIC_HOST,
    START,
    TAILSCALE_V4,
    TAILSCALE_V6,
    host_facts,
    section,
    tls_facts,
)

from hivemind.entrance.expose import (
    ExposureFacts,
    ExposureRefusedError,
    ExposureRule,
    FileState,
    HostPlatform,
    TlsFacts,
    plan_exposure,
)
from hivemind.manifest.schema import EntranceExposure, EntranceSection

VPN = EntranceExposure.VPN
LAN = EntranceExposure.LAN
TUNNEL = EntranceExposure.TUNNEL
_ONE_DAY = timedelta(days=1)


# ──────────────────────────────────────────────────────────────────────────────
# Every refusal, asserted
# ──────────────────────────────────────────────────────────────────────────────


def _with_tls(tls: TlsFacts) -> ExposureFacts:
    """Default host facts with these TLS facts instead of the good ones."""
    return host_facts(tls=tls)


_CGNAT_WAN = {"eth0": ("100.70.1.2",), "tailscale0": (TAILSCALE_V4,)}  # ADR-0041's trap.
_ONLY_KEY = TlsFacts(
    cert_path=None,
    key_path=KEY_PATH,
    cert=FileState.NOT_CONFIGURED,
    key=FileState.VALID,
    key_matches_cert=False,
    dns_names=(),
    not_before=None,
    not_after=None,
)

_REFUSALS = [
    pytest.param(
        ExposureRule.MUTUAL_TLS_REQUIRED, section(LAN, mutual_tls=False), host_facts(), id="lan"
    ),
    pytest.param(
        ExposureRule.MUTUAL_TLS_REQUIRED,
        section(TUNNEL, mutual_tls=False),
        host_facts(),
        id="tunnel-without-mtls",
    ),
    pytest.param(
        ExposureRule.TUNNEL_COMMAND_MISSING,
        section(TUNNEL, tunnel_command=()),
        host_facts(),
        id="no-argv",
    ),
    pytest.param(
        ExposureRule.TUNNEL_COMMAND_MISSING,
        section(TUNNEL, tunnel_command=("", "run")),
        host_facts(),
        id="empty-program",
    ),
    pytest.param(
        ExposureRule.TUNNEL_COMMAND_MISSING,
        section(TUNNEL, tunnel_command=("bore", "local\x00")),
        host_facts(),
        id="nul",
    ),
    pytest.param(
        ExposureRule.REMOTE_BIND_MISSING, section(VPN, remote_bind=""), host_facts(), id="vpn"
    ),
    pytest.param(
        ExposureRule.REMOTE_BIND_MISSING,
        section(TUNNEL, remote_bind=""),
        host_facts(),
        id="tunnel-no-bind",
    ),
    pytest.param(
        ExposureRule.REMOTE_BIND_LOOPBACK,
        section(VPN, remote_bind="127.0.0.1:8711", vpn_cidrs=("127.0.0.0/8",), vpn_interface="lo"),
        host_facts(),
        id="vpn-loopback",
    ),
    pytest.param(
        ExposureRule.REMOTE_BIND_LOOPBACK,
        section(LAN, remote_bind="[::1]:8711"),
        host_facts(),
        id="lan-loopback",
    ),
    pytest.param(
        ExposureRule.VPN_BIND_GLOBAL,
        section(VPN, remote_bind="[2001:4860::7]:8711", vpn_cidrs=("::/0",)),
        host_facts({"tailscale0": ("2001:4860::7",)}),
        id="vpn-global-v6",
    ),
    pytest.param(
        ExposureRule.VPN_BIND_GLOBAL,
        section(VPN, remote_bind="8.8.4.4:8711", vpn_cidrs=("0.0.0.0/0",)),
        host_facts({"tailscale0": ("8.8.4.4",)}),
        id="vpn-global-v4-widened-cidrs",
    ),
    pytest.param(
        ExposureRule.VPN_BIND_OUTSIDE_CIDRS,
        section(VPN, remote_bind=f"{LAN_V4}:8711"),
        host_facts(),
        id="lan-address",
    ),
    pytest.param(
        ExposureRule.VPN_INTERFACE_UNNAMED,
        section(VPN),
        host_facts({"utun4": (TAILSCALE_V4,)}, platform=HostPlatform.MACOS),
        id="macos",
    ),
    pytest.param(
        ExposureRule.VPN_INTERFACE_UNNAMED,
        section(VPN),
        host_facts({"tailscale0": (TAILSCALE_V4,)}, platform=HostPlatform.OTHER),
        id="other-platform",
    ),
    pytest.param(
        ExposureRule.VPN_INTERFACE_ABSENT,
        section(VPN),
        host_facts({"eth0": (LAN_V4,)}),
        id="vpn-down",
    ),
    pytest.param(
        ExposureRule.VPN_BIND_NOT_ON_INTERFACE,
        section(VPN, remote_bind="100.70.1.2:8711"),
        host_facts(_CGNAT_WAN),
        id="cgnat-wan",
    ),
    pytest.param(
        ExposureRule.LAN_BIND_NOT_PRIVATE,
        section(LAN, remote_bind="8.8.4.4:8711"),
        host_facts({"eth0": ("8.8.4.4",)}),
        id="vps-public-address",
    ),
    pytest.param(
        ExposureRule.LAN_BIND_NOT_PRIVATE,
        section(LAN, remote_bind="[2001:4860::7]:8711"),
        host_facts({"eth0": ("2001:4860::7",)}),
        id="lan-global-v6",
    ),
    pytest.param(
        ExposureRule.LAN_BIND_NOT_PRIVATE,
        section(LAN, remote_bind="100.70.1.2:8711"),
        host_facts(_CGNAT_WAN),
        id="lan-cgnat-wan",
    ),
    pytest.param(
        ExposureRule.LAN_BIND_NOT_LOCAL,
        section(LAN, remote_bind="192.168.1.99:8711"),
        host_facts(),
        id="foreign",
    ),
    pytest.param(
        ExposureRule.TUNNEL_BIND_NOT_LOOPBACK,
        section(TUNNEL, remote_bind=f"{LAN_V4}:8711"),
        host_facts(),
        id="routable",
    ),
    pytest.param(
        ExposureRule.TUNNEL_BIND_CLASHES,
        section(TUNNEL, remote_bind="127.0.0.1:8710"),
        host_facts(),
        id="same-port",
    ),
    pytest.param(
        ExposureRule.PUBLIC_URL_MISSING, section(VPN, public_url=""), host_facts(), id="empty"
    ),
    pytest.param(
        ExposureRule.PUBLIC_URL_NOT_A_NAME,
        section(VPN, public_url=f"https://{TAILSCALE_V4}:8711"),
        host_facts(),
        id="ipv4",
    ),
    pytest.param(
        ExposureRule.PUBLIC_URL_NOT_A_NAME,
        section(VPN, public_url=f"https://[{TAILSCALE_V6}]:8711"),
        host_facts(),
        id="ipv6",
    ),
    pytest.param(
        ExposureRule.PUBLIC_URL_NOT_A_NAME,
        section(VPN, public_url="https://localhost:8711"),
        host_facts(),
        id="localhost",
    ),
    pytest.param(
        ExposureRule.PUBLIC_URL_NOT_A_NAME,
        section(VPN, public_url=f"https://operator@{PUBLIC_HOST}"),
        host_facts(),
        id="credentials",
    ),
    pytest.param(
        ExposureRule.RP_ID_OUTSIDE_PUBLIC_URL,
        section(VPN, rp_id="other.example"),
        host_facts(),
        id="unrelated",
    ),
    pytest.param(
        ExposureRule.RP_ID_OUTSIDE_PUBLIC_URL,
        section(VPN, rp_id="ample.test"),
        host_facts(),
        id="suffix-not-label",
    ),
    pytest.param(
        ExposureRule.RP_ID_OUTSIDE_PUBLIC_URL,
        section(VPN, rp_id=f"sub.{PUBLIC_HOST}"),
        host_facts(),
        id="child",
    ),
    pytest.param(
        ExposureRule.RP_ID_PUBLIC_SUFFIX,
        section(VPN, public_url="https://hive.tail1234.ts.net:8711", rp_id="ts.net"),
        host_facts(),
        id="tailscale-suffix",
    ),
    pytest.param(
        ExposureRule.RP_ID_PUBLIC_SUFFIX,
        section(VPN, rp_id="test"),
        host_facts(),
        id="top-level-domain",
    ),
    pytest.param(
        ExposureRule.TLS_NOT_CONFIGURED,
        section(VPN),
        host_facts(tls=TlsFacts.not_configured()),
        id="neither",
    ),
    pytest.param(
        ExposureRule.TLS_NOT_CONFIGURED, section(LAN), host_facts(tls=_ONLY_KEY), id="key-only"
    ),
    pytest.param(
        ExposureRule.TLS_CERT_UNREADABLE,
        section(VPN),
        _with_tls(replace(tls_facts(), cert=FileState.UNREADABLE)),
        id="cert-unreadable",
    ),
    pytest.param(
        ExposureRule.TLS_CERT_UNPARSEABLE,
        section(VPN),
        _with_tls(replace(tls_facts(), cert=FileState.UNPARSEABLE)),
        id="cert-garbage",
    ),
    pytest.param(
        ExposureRule.TLS_KEY_UNREADABLE,
        section(VPN),
        _with_tls(replace(tls_facts(), key=FileState.UNREADABLE)),
        id="key-unreadable",
    ),
    pytest.param(
        ExposureRule.TLS_KEY_UNPARSEABLE,
        section(VPN),
        _with_tls(replace(tls_facts(), key=FileState.UNPARSEABLE)),
        id="key-garbage",
    ),
    pytest.param(
        ExposureRule.TLS_KEY_MISMATCH,
        section(VPN),
        _with_tls(replace(tls_facts(), key_matches_cert=False)),
        id="mismatch",
    ),
    pytest.param(
        ExposureRule.TLS_CERT_NOT_CURRENT,
        section(VPN),
        _with_tls(replace(tls_facts(), not_after=START - _ONE_DAY)),
        id="expired",
    ),
    pytest.param(
        ExposureRule.TLS_CERT_NOT_CURRENT,
        section(VPN),
        _with_tls(replace(tls_facts(), not_before=START + _ONE_DAY)),
        id="not-yet",
    ),
    pytest.param(
        ExposureRule.TLS_NAME_MISMATCH,
        section(VPN),
        _with_tls(replace(tls_facts(), dns_names=("other.example.test",))),
        id="other-name",
    ),
    pytest.param(
        ExposureRule.TLS_NAME_MISMATCH,
        section(VPN),
        _with_tls(replace(tls_facts(), dns_names=())),
        id="no-san",
    ),
    pytest.param(
        ExposureRule.TLS_NAME_MISMATCH,
        section(VPN),
        _with_tls(replace(tls_facts(), dns_names=("*.test",))),
        id="tld-wildcard",
    ),
]


@pytest.mark.parametrize(("rule", "entrance", "facts"), _REFUSALS)
def test_plan_refuses_with_the_exact_rule(
    rule: ExposureRule, entrance: EntranceSection, facts: ExposureFacts
) -> None:
    with pytest.raises(ExposureRefusedError) as caught:
        plan_exposure(entrance, facts)

    assert caught.value.rule is rule
    assert caught.value.mode is entrance.expose
    assert rule.value in str(caught.value)
    assert rule.requirement in str(caught.value)


def test_every_rule_has_a_refusal_case() -> None:
    covered = {case.values[0] for case in _REFUSALS}

    assert covered == set(ExposureRule)


def test_lan_without_mutual_tls_refuses_to_start() -> None:
    # Phase 10's exit criterion, verbatim: `expose = "lan"` without mutual TLS refuses to start.
    with pytest.raises(ExposureRefusedError, match="mutual_tls"):
        plan_exposure(section(LAN, mutual_tls=False), host_facts())


def test_the_first_broken_rule_is_the_one_reported() -> None:
    # Mutual TLS off, no remote_bind, no public_url and no TLS files: the switch comes first.
    entrance = EntranceSection(expose=LAN, mutual_tls=False)

    with pytest.raises(ExposureRefusedError) as caught:
        plan_exposure(entrance, host_facts(tls=TlsFacts.not_configured()))

    assert caught.value.rule is ExposureRule.MUTUAL_TLS_REQUIRED


@pytest.mark.parametrize(
    "url",
    [
        "https://operator:hunter2@hive.example.test",
        "https://100.101.102.103/?token=hunter2",
    ],
)
def test_a_refusal_never_echoes_credentials_or_a_query_in_public_url(url: str) -> None:
    with pytest.raises(ExposureRefusedError) as caught:
        plan_exposure(section(VPN, public_url=url), host_facts())

    assert caught.value.rule is ExposureRule.PUBLIC_URL_NOT_A_NAME
    assert "hunter2" not in str(caught.value)


def test_a_refusal_never_echoes_the_tunnel_argv() -> None:
    entrance = section(TUNNEL, tunnel_command=("bore", "--secret=hunter2\x00"))

    with pytest.raises(ExposureRefusedError) as caught:
        plan_exposure(entrance, host_facts())

    assert "hunter2" not in str(caught.value)
