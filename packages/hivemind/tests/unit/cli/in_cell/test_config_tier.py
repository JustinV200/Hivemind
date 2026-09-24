"""Tests for hivemind.cli.in_cell.config's tier rules: the Cell's tier comes from its bootstrap.

Roadmap step 10.3a: the Cell's Comb Shield tier is read from `HIVEMIND_COMB_SHIELD` (unset reads
as MEADOW), and the link must fit it: a Night Veil Cell dials a v3 onion service through a
loopback SOCKS proxy, an onion Queen URL belongs to a Night Veil Cell only, and PROPOLIS, which no
in-Cell attestation covers yet, is refused.

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/config.py (codingrules 5.1: split by feature from
    test_config.py).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.in_cell.config for build_runtime_config.
"""

from __future__ import annotations

import pytest

from hivemind.cell.tiers import CombShieldLevel
from hivemind.cli.in_cell.config import InCellRuntimeConfig, build_runtime_config
from hivemind.common.errors import ConfigurationError
from hivemind.manifest.env import read_in_cell_env
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_hive_id, new_node_id
from waggle.signing import Ed25519Signer

_CLOCK = FakeClock()
_ONION = "7jjm54ntxrtbp4fjhhw2gdk7zz2fshgnubimtmc5dcczncvdfo3lnbid.onion"  # A valid v3 address.
_ONION_URL = f"ws://{_ONION}:8710"
_TOR = "socks5h://127.0.0.1:9050"


def _config(**overrides: str) -> InCellRuntimeConfig:
    """Build this Cell's runtime config from a complete environment with `overrides`."""
    environ = {
        "HIVEMIND_QUEEN_WAGGLE_URL": "ws://host.docker.internal:8710",
        "HIVEMIND_CELL_ID": new_cell_id(_CLOCK),
        "HIVEMIND_HIVE_ID": new_hive_id(_CLOCK),
        "HIVEMIND_QUEEN_NODE_ID": new_node_id(_CLOCK),
        "HIVEMIND_CELL_SIGNING_KEY": Ed25519Signer.generate().private_key_bytes.hex(),
        "HIVEMIND_QUEEN_VERIFY_KEY": Ed25519Signer.generate().public_key_bytes.hex(),
    }
    environ.update(overrides)
    return build_runtime_config(read_in_cell_env(environ), _CLOCK)


def test_a_bootstrap_naming_no_tier_is_a_meadow_cell() -> None:
    assert _config().spawn_config.comb_shield is CombShieldLevel.MEADOW


@pytest.mark.parametrize("value", ["NIGHT_VEIL", "night_veil", " NIGHT_VEIL "])
def test_a_night_veil_bootstrap_is_a_night_veil_cell_dialling_tor(value: str) -> None:
    config = _config(
        HIVEMIND_COMB_SHIELD=value,
        HIVEMIND_QUEEN_WAGGLE_URL=_ONION_URL,
        HIVEMIND_SOCKS_PROXY_URL=_TOR,
    )

    assert config.spawn_config.comb_shield is CombShieldLevel.NIGHT_VEIL
    assert config.queen_waggle_url == _ONION_URL
    assert config.socks_proxy_url == _TOR


@pytest.mark.parametrize(
    ("overrides", "why"),
    [
        # A Night Veil Cell with a clearnet Queen URL, or with no Tor proxy at all.
        ({"HIVEMIND_QUEEN_WAGGLE_URL": "ws://host.docker.internal:8710"}, "onion service"),
        ({"HIVEMIND_SOCKS_PROXY_URL": ""}, "SOCKS"),
    ],
)
def test_a_night_veil_bootstrap_whose_link_is_not_tor_is_refused(
    overrides: dict[str, str], why: str
) -> None:
    environ = {
        "HIVEMIND_COMB_SHIELD": "NIGHT_VEIL",
        "HIVEMIND_QUEEN_WAGGLE_URL": _ONION_URL,
        "HIVEMIND_SOCKS_PROXY_URL": _TOR,
        **overrides,
    }
    if not environ["HIVEMIND_SOCKS_PROXY_URL"]:
        del environ["HIVEMIND_SOCKS_PROXY_URL"]

    with pytest.raises(ConfigurationError, match=why):
        _config(**environ)


def test_an_onion_queen_url_on_a_bootstrap_naming_no_tier_is_refused() -> None:
    # A missing tier can never pass a Night Veil link off as a MEADOW Cell.
    with pytest.raises(ConfigurationError, match="only a NIGHT_VEIL Cell dials"):
        _config(HIVEMIND_QUEEN_WAGGLE_URL=_ONION_URL, HIVEMIND_SOCKS_PROXY_URL=_TOR)


@pytest.mark.parametrize(
    "proxy", ["socks5://127.0.0.1:9050", "socks5h://10.0.0.5:9050", "http://127.0.0.1:8080"]
)
def test_a_proxy_that_would_leak_the_name_is_refused(proxy: str) -> None:
    with pytest.raises(ConfigurationError, match="HIVEMIND_SOCKS_PROXY_URL is invalid"):
        _config(HIVEMIND_SOCKS_PROXY_URL=proxy)


@pytest.mark.parametrize(
    ("value", "why"), [("SUNSHINE", "not a Comb Shield tier"), ("PROPOLIS", "attest")]
)
def test_a_tier_this_cell_cannot_announce_is_refused(value: str, why: str) -> None:
    with pytest.raises(ConfigurationError, match=why):
        _config(HIVEMIND_COMB_SHIELD=value)


def test_a_mistyped_onion_address_is_refused_as_an_invalid_queen_url() -> None:
    typo = f"ws://x{_ONION[1:]}:8710"  # Its checksum no longer holds.

    with pytest.raises(ConfigurationError, match="HIVEMIND_QUEEN_WAGGLE_URL"):
        _config(
            HIVEMIND_COMB_SHIELD="NIGHT_VEIL",
            HIVEMIND_QUEEN_WAGGLE_URL=typo,
            HIVEMIND_SOCKS_PROXY_URL=_TOR,
        )
