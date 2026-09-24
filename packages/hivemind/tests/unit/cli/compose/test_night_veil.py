"""Tests for hivemind.cli.compose.night_veil: the Night Veil pieces built from a manifest (10.3a).

Fits into the Hive:
    Mirrors src/hivemind/cli/compose/night_veil.py (codingrules section 3). Every manifest here
    is `builders.cli.fake_manifest`'s, with a `[security.tiers.NIGHT_VEIL]` table appended when a
    test configures the tier.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.compose.night_veil for the module under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.cli import fake_manifest

from hivemind.cli.compose.deps import _placement_policy
from hivemind.cli.compose.night_veil import (
    in_process_providers,
    local_providers,
    night_veil_constraints,
    night_veil_link,
    tor_socks_url,
)
from hivemind.hive import NetworkPolicy
from hivemind.hive.backends.bootstrap import NightVeilLink
from hivemind.manifest import HiveManifest, load_manifest

_ONION = "hivestandhiddenservice.onion"


def _manifest(tmp_path: Path, night_veil: str | None = None, extra: str = "") -> HiveManifest:
    """The fake manifest, with `night_veil` as its Night Veil tier table and `extra` appended."""
    path = fake_manifest(tmp_path)
    text = path.read_text(encoding="utf-8")
    if night_veil is not None:
        text += f"\n[security.tiers.NIGHT_VEIL]\n{night_veil}"
    path.write_text(text + extra, encoding="utf-8")
    return load_manifest(path)


_COMPLETE = (
    'egress_profile = "vpn_tor"\ncontrol_channel = "tor_hidden_service"\n'
    f'hidden_service_address = "{_ONION}"\ntor_socks = "127.0.0.1:9050"\n'
    'locale_profile = "C.UTF-8"\n'
)


@pytest.mark.parametrize(
    ("value", "url"),
    [
        ("127.0.0.1:9050", "socks5h://127.0.0.1:9050"),
        ("socks5h://127.0.0.1:9050", "socks5h://127.0.0.1:9050"),
        ("socks5://127.0.0.1:9050", "socks5://127.0.0.1:9050"),
        ("", ""),
    ],
)
def test_tor_socks_is_read_as_a_socks5h_url_unless_it_names_a_scheme(value: str, url: str) -> None:
    assert tor_socks_url(value) == url


def test_a_configured_profile_becomes_the_placement_policys_night_veil(tmp_path: Path) -> None:
    constraints = night_veil_constraints(_manifest(tmp_path, _COMPLETE))

    assert constraints is not None
    assert constraints.required_network_policy is NetworkPolicy.VPN_TOR
    assert constraints.hive_stand_onion_address == _ONION
    assert constraints.socks_proxy_url == "socks5h://127.0.0.1:9050"
    assert constraints.locale_profile == "C.UTF-8"


def test_the_shipped_default_profile_is_built_incomplete_so_placement_says_so(
    tmp_path: Path,
) -> None:
    # No table written: the shipped default names no hidden service and no Tor proxy.
    constraints = night_veil_constraints(_manifest(tmp_path))

    assert constraints is not None
    assert constraints.hive_stand_onion_address == ""
    assert constraints.socks_proxy_url == ""
    assert night_veil_link(_manifest(tmp_path)) is None


def test_the_link_names_the_hidden_service_and_the_tor_proxy(tmp_path: Path) -> None:
    link = night_veil_link(_manifest(tmp_path, _COMPLETE))

    assert link == NightVeilLink(
        waggle_url=f"ws://{_ONION}", socks_proxy_url="socks5h://127.0.0.1:9050"
    )


def test_the_fake_provider_runs_in_process_and_locally(tmp_path: Path) -> None:
    extra = '\n[llm.providers.hosted]\nkind = "anthropic"\napi_key_env = "HIVEMIND_HOSTED_KEY"\n'
    manifest = _manifest(tmp_path, extra=extra)

    assert in_process_providers(manifest) == frozenset({"fake"})
    assert local_providers(manifest) == frozenset({"fake"})


def test_the_production_queen_deps_carry_the_night_veil_profile(tmp_path: Path) -> None:
    # Roadmap step 10.3a's phase 5 gap: the composition root now builds it.
    manifest = _manifest(tmp_path, _COMPLETE)

    policy = _placement_policy(manifest)

    assert policy.night_veil == night_veil_constraints(manifest)
