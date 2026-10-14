"""Tests for hivemind.manifest.schema.swarm: SwarmSection and its ladder and Nuc sub-tables.

Fits into the Hive:
    Mirrors src/hivemind/manifest/schema/swarm.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). Pins every default of roadmap phase 11's `[swarm]`, each bound, the three
    validators (the certificate and key as a pair, both required off loopback, an advertised URL
    Waggle may dial), the placement of the section in HiveManifest, and that
    docs/manifests/full.toml writes every key out and every example manifest still loads.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.manifest.schema.swarm for the module under test.
    - docs/manifests/full.toml for the written-out example the last tests load.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from hivemind.manifest import HiveManifest, load_manifest
from hivemind.manifest.schema import SwarmLadderSection, SwarmNucSection, SwarmSection
from waggle.messages.labels import AccessLevel

# The repository root, seven parents up from this test file
# (packages/hivemind/tests/unit/manifest/schema/test_swarm.py).
_MANIFESTS_DIR = Path(__file__).resolve().parents[6] / "docs" / "manifests"
_TLS = {"tls_cert": "tls/swarm.crt", "tls_key": "tls/swarm.key"}


def _section(**overrides: object) -> SwarmSection:
    """A SwarmSection built through validation from ``overrides`` alone."""
    return SwarmSection.model_validate(overrides)


# ──────────────────────────────────────────────────────────────────────────────
# Defaults
# ──────────────────────────────────────────────────────────────────────────────


def test_the_swarm_is_disabled_and_loopback_by_default() -> None:
    section = SwarmSection()

    assert section.enabled is False
    assert (section.listen_host, section.listen_port) == ("127.0.0.1", 8720)
    assert section.advertise_url == ""
    assert (section.tls_cert, section.tls_key) == ("", "")


def test_the_liveness_and_invite_defaults_are_the_documented_ones() -> None:
    section = SwarmSection()

    assert section.invite_ttl_minutes == 15.0
    assert (section.heartbeat_interval_s, section.heartbeat_miss_limit) == (15.0, 4)
    assert (section.dead_man_s, section.unreachable_grace_s) == (120.0, 300.0)
    assert section.default_access is AccessLevel.FULL


def test_a_device_lease_is_held_to_the_packets_own_figure_unless_the_manifest_says_more() -> None:
    # 512 MiB is what the Pollen Packet falls back on, so a manifest that never mentions the key
    # asks a device for exactly what it would have enforced on its own.
    assert SwarmSection().scratch_quota_mb == 512


def test_the_ladder_and_nuc_defaults_are_the_documented_thresholds() -> None:
    section = SwarmSection()

    assert section.ladder == SwarmLadderSection()
    assert section.nuc == SwarmNucSection()
    assert section.ladder.relief_pressure == 0.75
    assert section.ladder.min_free_memory_bytes == 1_073_741_824
    assert section.nuc.seat_pressure == 0.75


def test_each_rung_waits_a_quarter_hour_before_undoing_and_five_minutes_after_a_failure() -> None:
    section = SwarmSection()

    # What keeps the Queen from flapping a device: when she undoes a move she made on her own,
    # and how soon she tries a device again after an attempt on it failed.
    assert (section.ladder.lower_after_s, section.ladder.retry_after_s) == (900.0, 300.0)
    assert (section.nuc.lower_after_s, section.nuc.retry_after_s) == (900.0, 300.0)


def test_a_manifest_that_omits_the_section_gets_the_disabled_default() -> None:
    manifest = load_manifest(_MANIFESTS_DIR / "minimal.toml")

    assert manifest.swarm == SwarmSection()
    assert HiveManifest.model_fields["swarm"].default_factory is SwarmSection


def test_the_section_round_trips_through_its_own_dump() -> None:
    section = _section(
        enabled=True,
        listen_host="10.0.0.5",
        listen_port=9000,
        advertise_url="wss://hive.example.org:9000/",
        default_access="SCRATCH",
        scratch_quota_mb=64,
        ladder={
            "relief_pressure": 0.5,
            "min_free_memory_bytes": 0,
            "lower_after_s": 60.0,
            "retry_after_s": 30.0,
        },
        nuc={"seat_pressure": 1.0, "lower_after_s": 120.0, "retry_after_s": 45.0},
        **_TLS,
    )

    assert SwarmSection.model_validate(section.model_dump(mode="json")) == section


# ──────────────────────────────────────────────────────────────────────────────
# Bounds
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"listen_port": -1}, "greater than or equal to 0"),
        ({"listen_port": 65536}, "less than or equal to 65535"),
        ({"invite_ttl_minutes": 0}, "greater than 0"),
        ({"heartbeat_interval_s": 0}, "greater than 0"),
        ({"heartbeat_miss_limit": 0}, "greater than or equal to 1"),
        ({"dead_man_s": 0}, "greater than 0"),
        ({"scratch_quota_mb": 0}, "greater than 0"),
        ({"unreachable_grace_s": -1}, "greater than or equal to 0"),
        ({"default_access": "ALL"}, "default_access"),
        ({"default_access": "full"}, "default_access"),
        ({"ladder": {"relief_pressure": 0}}, "greater than 0"),
        ({"ladder": {"relief_pressure": 1.5}}, "less than or equal to 1"),
        ({"ladder": {"min_free_memory_bytes": -1}}, "greater than or equal to 0"),
        ({"nuc": {"seat_pressure": 0}}, "greater than 0"),
        ({"nuc": {"seat_pressure": 1.01}}, "less than or equal to 1"),
        ({"ladder": {"lower_after_s": 0}}, "greater than 0"),
        ({"ladder": {"retry_after_s": -1}}, "greater than 0"),
        ({"nuc": {"lower_after_s": 0}}, "greater than 0"),
        ({"nuc": {"retry_after_s": 0}}, "greater than 0"),
        ({"enabled": "perhaps"}, "enabled"),
        ({"ladder": {"relief_pressure": 0.5, "headroom": 1}}, "extra"),
        ({"workers": 4}, "extra"),
    ],
)
def test_the_section_rejects_a_bad_value(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _section(**changes)


def test_the_boundary_values_are_accepted() -> None:
    section = _section(
        listen_port=0,
        unreachable_grace_s=0,
        heartbeat_miss_limit=1,
        default_access="READ_ONLY",
        ladder={"relief_pressure": 1.0, "min_free_memory_bytes": 0},
        nuc={"seat_pressure": 1.0},
    )

    assert section.listen_port == 0
    assert section.unreachable_grace_s == 0
    assert section.default_access is AccessLevel.READ_ONLY


def test_every_access_level_is_a_valid_default_access() -> None:
    for level in AccessLevel:
        assert _section(default_access=level.value).default_access is level


def test_the_sections_are_frozen() -> None:
    section = SwarmSection()

    with pytest.raises(ValidationError, match="frozen"):
        section.enabled = True  # The assignment is the test.
    with pytest.raises(ValidationError, match="frozen"):
        section.ladder.relief_pressure = 0.1  # The assignment is the test.


# ──────────────────────────────────────────────────────────────────────────────
# listen_host, the certificate pair and advertise_url
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("host", ["127.0.0.1", "127.1.2.3", "::1", "localhost"])
def test_a_loopback_listener_needs_no_certificate(host: str) -> None:
    section = _section(listen_host=host)

    assert section.listen_host == host
    assert (section.tls_cert, section.tls_key) == ("", "")


# Hosts that are not loopback; the wildcard literal is the case under test, not a real bind.
_ROUTABLE_HOSTS = [
    "10.0.0.5",
    "192.168.1.20",
    "0.0.0.0",  # noqa: S104  # A wildcard bind-all literal is exactly the input under test.
    "2001:db8::1",
    "hive.example.org",
    "LOCALHOST",  # Only the lowercase name is trusted as loopback: safe-side.
]


@pytest.mark.parametrize("host", _ROUTABLE_HOSTS)
def test_any_other_listener_needs_both_the_certificate_and_the_key(host: str) -> None:
    with pytest.raises(ValidationError, match="not a loopback address"):
        _section(listen_host=host)

    assert _section(listen_host=host, **_TLS).listen_host == host


def test_a_loopback_listener_may_serve_tls_too() -> None:
    section = _section(**_TLS)

    assert section.tls_cert == "tls/swarm.crt"
    assert section.tls_key == "tls/swarm.key"


@pytest.mark.parametrize("half", ["tls_cert", "tls_key"])
@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.5"])
def test_the_certificate_and_the_key_come_as_a_pair(half: str, host: str) -> None:
    with pytest.raises(ValidationError, match="must be set together or both left empty"):
        _section(listen_host=host, **{half: _TLS[half]})


@pytest.mark.parametrize(
    "host",
    [
        "",
        " ",
        "wss://hive.example.org",
        "hive.example.org:8720",
        "[::1]",
        "a b",
        "-bad",
        "bad-",
        "/",
        "999.999.999.999",  # A mistyped address is not a name.
        "1.2.3",
    ],
)
def test_listen_host_must_be_an_ip_address_or_a_host_name(host: str) -> None:
    with pytest.raises(ValidationError, match="not an IP address or a host name"):
        _section(listen_host=host, **_TLS)


@pytest.mark.parametrize(
    "url",
    [
        "wss://hive.example.org:8721",
        "wss://100.101.102.103:8721/swarm",
        "ws://127.0.0.1:8720",
        "ws://localhost:8720",
    ],
)
def test_advertise_url_accepts_what_waggle_may_dial(url: str) -> None:
    assert _section(advertise_url=url).advertise_url == url


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("https://hive.example.org", "not a WebSocket URI"),
        ("hive.example.org:8721", "not a WebSocket URI"),
        ("ws://hive.example.org:8720", "plaintext endpoint is allowed only"),
        ("ws://10.0.0.5:8720", "plaintext endpoint is allowed only"),
        ("wss://host with space", "not a WebSocket URI"),
    ],
)
def test_advertise_url_rejects_what_waggle_could_never_dial(url: str, reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _section(advertise_url=url)


# ──────────────────────────────────────────────────────────────────────────────
# The example manifests
# ──────────────────────────────────────────────────────────────────────────────


def test_full_toml_writes_out_every_key_of_the_swarm_tables() -> None:
    swarm = load_manifest(_MANIFESTS_DIR / "full.toml").swarm

    assert swarm.model_fields_set == set(SwarmSection.model_fields)
    assert swarm.ladder.model_fields_set == set(SwarmLadderSection.model_fields)
    assert swarm.nuc.model_fields_set == set(SwarmNucSection.model_fields)


def test_full_toml_shows_a_non_loopback_listener_with_its_certificate_and_key() -> None:
    swarm = load_manifest(_MANIFESTS_DIR / "full.toml").swarm

    assert swarm.enabled is True
    assert swarm.listen_host != "127.0.0.1"
    assert swarm.tls_cert and swarm.tls_key
    assert swarm.advertise_url.startswith("wss://")


@pytest.mark.parametrize("filename", ["minimal.toml", "local.toml", "offline.toml", "full.toml"])
def test_every_example_manifest_still_loads_with_the_swarm_section(filename: str) -> None:
    manifest = load_manifest(_MANIFESTS_DIR / filename)

    # Only full.toml turns the Swarm on; the others leave it at the disabled default.
    assert manifest.swarm.enabled is (filename == "full.toml")
    assert SwarmSection.model_validate(manifest.swarm.model_dump(mode="json")) == manifest.swarm
