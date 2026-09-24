"""Tests for hivemind.manifest.schema.entrance: the [entrance] section and its sub-tables.

Fits into the Hive:
    Mirrors src/hivemind/manifest/schema/entrance.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.manifest.schema.entrance for the module under test.
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for the rules the
      validators encode.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from hivemind.manifest.loader import load_manifest
from hivemind.manifest.schema.entrance import (
    DEFAULT_ENTRANCE_BIND,
    DEFAULT_VPN_CIDRS,
    EntranceExposure,
    EntranceSection,
    split_host_port,
)

TAILSCALE_V4 = "100.101.102.103:8711"  # An address inside Tailscale's CGNAT range.
TAILSCALE_V6 = "[fd7a:115c:a1e0::1]:8711"  # An address inside Tailscale's IPv6 prefix.
# The shipped minimal example manifest, which omits [entrance] entirely.
MINIMAL_MANIFEST = Path(__file__).parents[6] / "docs" / "manifests" / "minimal.toml"


def test_entrance_section_defaults_to_loopback_only() -> None:
    section = EntranceSection()

    assert section.bind == DEFAULT_ENTRANCE_BIND
    assert section.expose is EntranceExposure.LOOPBACK
    assert section.remote_bind == ""
    assert section.vpn_cidrs == DEFAULT_VPN_CIDRS
    assert section.mutual_tls is True
    assert section.operators == 1
    assert section.steward_devices is False


def test_entrance_section_defaults_match_codingrules_section_13() -> None:
    section = EntranceSection()

    assert section.session_ttl_hours == 12.0
    assert section.idle_timeout_minutes == 30.0
    assert section.step_up_window_minutes == 5.0
    assert section.step_up_spend == 5.0
    assert section.lockout_attempts == 5
    assert section.rate_limit_per_device == 60
    assert section.push.webhooks is True
    assert section.push.web_push is True
    assert section.voice.confirm_goals is True
    assert section.voice.keep_audio is False
    assert section.voice.max_clip_seconds == 120.0


def test_hive_manifest_defaults_the_entrance_section_when_omitted() -> None:
    # minimal.toml has no [entrance] table at all: the section must default, loopback-only.
    manifest = load_manifest(MINIMAL_MANIFEST)

    assert manifest.entrance == EntranceSection()


@pytest.mark.parametrize("bind", ["127.0.0.1:8710", "127.9.9.9:1", "localhost:0", "[::1]:8710"])
def test_entrance_bind_accepts_loopback_hosts(bind: str) -> None:
    assert EntranceSection(bind=bind).bind == bind


@pytest.mark.parametrize("bind", ["0.0.0.0:8710", "10.0.0.1:8710", "[::]:8710", "hive.lan:8710"])
def test_entrance_bind_rejects_a_routable_or_wildcard_host(bind: str) -> None:
    with pytest.raises(ValidationError, match="bind"):
        EntranceSection(bind=bind)


@pytest.mark.parametrize("remote_bind", [TAILSCALE_V4, TAILSCALE_V6, "192.168.1.20:8711"])
def test_entrance_remote_bind_accepts_a_specific_address(remote_bind: str) -> None:
    assert EntranceSection(remote_bind=remote_bind).remote_bind == remote_bind


@pytest.mark.parametrize(
    ("remote_bind", "reason"),
    [
        ("0.0.0.0:8711", "wildcard"),
        ("[::]:8711", "wildcard"),
        ("127.0.0.1:8711", "loopback"),
        ("hive.example:8711", "does not appear to be an IPv4 or IPv6 address"),
    ],
)
def test_entrance_remote_bind_rejects_wildcard_loopback_and_names(
    remote_bind: str, reason: str
) -> None:
    with pytest.raises(ValidationError, match=reason):
        EntranceSection(remote_bind=remote_bind)


def test_entrance_exposure_has_no_public_mode() -> None:
    # ADR-0033: the schema itself refuses the one exposure codingrules 8.15 forbids.
    assert {mode.value for mode in EntranceExposure} == {"loopback", "vpn", "lan", "tunnel"}
    with pytest.raises(ValidationError):
        EntranceSection.model_validate({"expose": "public"})


def test_entrance_vpn_cidrs_reject_a_range_with_host_bits_set() -> None:
    with pytest.raises(ValidationError):
        EntranceSection(vpn_cidrs=("100.64.0.1/10",))


def test_entrance_public_url_must_be_https() -> None:
    with pytest.raises(ValidationError, match="https"):
        EntranceSection(public_url="http://hivestand.example")

    assert EntranceSection(public_url="https://hivestand.example").public_url


def test_entrance_section_round_trips_through_json() -> None:
    section = EntranceSection(
        expose=EntranceExposure.VPN, remote_bind=TAILSCALE_V4, tunnel_command=("bore", "local")
    )

    assert EntranceSection.model_validate_json(section.model_dump_json()) == section


def test_entrance_section_rejects_an_unknown_key() -> None:
    with pytest.raises(ValidationError):
        EntranceSection.model_validate({"public_mode": True})


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("127.0.0.1:8710", ("127.0.0.1", 8710)),
        ("[::1]:0", ("::1", 0)),
        ("localhost:65535", ("localhost", 65535)),
    ],
)
def test_split_host_port_parses_ipv4_ipv6_and_names(value: str, expected: tuple[str, int]) -> None:
    assert split_host_port(value) == expected


@pytest.mark.parametrize("value", ["127.0.0.1", ":8710", "127.0.0.1:65536", "127.0.0.1:x"])
def test_split_host_port_rejects_missing_or_bad_ports(value: str) -> None:
    with pytest.raises(ValueError):
        split_host_port(value)
