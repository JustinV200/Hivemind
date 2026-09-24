"""Unit tests for hivemind.hive.backends.bootstrap's Night Veil link (roadmap step 10.3a).

A Night Veil Cell reaches the Queen only through her Tor hidden service (codingrules 8.7), so the
link is built from the tier profile, chosen per Cell by `cell_endpoint`, and a Night Veil Cell on
a Hive with no link is refused rather than handed a clearnet address.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors src/hivemind/hive/backends/bootstrap.py
    (codingrules section 5.1: one module's tests split by feature, here the Night Veil link).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.backends.bootstrap for NightVeilLink, cell_endpoint and night_veil_waggle_url.
"""

from __future__ import annotations

import dataclasses

import pytest
from builders.forage import make_capacity

from hivemind.cell import CombShieldLevel
from hivemind.hive.backends.bootstrap import (
    NightVeilLink,
    QueenEndpoint,
    cell_endpoint,
    mint_cell_bootstrap,
    night_veil_waggle_url,
)
from hivemind.hive.errors import CellProvisionError
from hivemind.hive.models import CellReservation, NetworkPolicy, VirtualCellSpec
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id

_HOST = "7jjm54ntxrtbp4fjhhw2gdk7zz2fshgnubimtmc5dcczncvdfo3lnbid.onion"  # A valid v3 address.
_ONION = f"{_HOST}:8710"
_TOR = "socks5h://127.0.0.1:9050"
_LINK = NightVeilLink(waggle_url=f"ws://{_ONION}", socks_proxy_url=_TOR)


def _endpoint(night_veil: NightVeilLink | None = _LINK) -> QueenEndpoint:
    """A Queen endpoint on her ordinary URL, carrying `night_veil` beside it."""
    return QueenEndpoint(
        waggle_url="ws://localhost:8710",
        queen_node_id=new_node_id(FakeClock()),
        queen_verify_key_hex="ab" * 32,
        night_veil=night_veil,
    )


def _spec(tier: CombShieldLevel) -> VirtualCellSpec:
    """A Cell spec on `tier`, with the network policy that tier requires."""
    night_veil = tier is CombShieldLevel.NIGHT_VEIL
    return VirtualCellSpec(
        image="night-veil-ubuntu" if night_veil else "hivemind/base-ubuntu:dev",
        cpu_cores=1.0,
        memory_bytes=1024**3,
        disk_bytes=1024**3,
        capacity=make_capacity(),
        hive_id=new_hive_id(FakeClock()),
        comb_shield=tier,
        network_policy=NetworkPolicy.VPN_TOR if night_veil else NetworkPolicy.EGRESS_ONLY,
    )


@pytest.mark.parametrize(
    ("address", "url"),
    [
        (_ONION, f"ws://{_ONION}"),
        (_HOST, f"ws://{_HOST}"),
        (f"wss://{_ONION}/waggle", f"wss://{_ONION}/waggle"),
    ],
)
def test_night_veil_waggle_url_adds_a_scheme_only_when_none_is_written(
    address: str, url: str
) -> None:
    assert night_veil_waggle_url(address) == url


def test_a_link_is_built_from_a_complete_profile() -> None:
    assert NightVeilLink.from_profile(_ONION, _TOR) == _LINK


@pytest.mark.parametrize(("address", "socks"), [("", _TOR), (_ONION, ""), ("", "")])
def test_an_incomplete_profile_builds_no_link(address: str, socks: str) -> None:
    assert NightVeilLink.from_profile(address, socks) is None


def test_a_meadow_cell_keeps_the_ordinary_endpoint_and_is_told_its_reservation() -> None:
    endpoint = _endpoint()
    spec = _spec(CombShieldLevel.MEADOW)

    chosen = cell_endpoint(endpoint, spec, "docker")

    assert chosen == dataclasses.replace(endpoint, reservation=CellReservation.of(spec))


def test_a_propolis_cell_keeps_the_ordinary_link_and_is_told_its_tier() -> None:
    endpoint = _endpoint()
    spec = _spec(CombShieldLevel.PROPOLIS)

    chosen = cell_endpoint(endpoint, spec, "docker")

    assert chosen == dataclasses.replace(
        endpoint, comb_shield=CombShieldLevel.PROPOLIS, reservation=CellReservation.of(spec)
    )
    environment = mint_cell_bootstrap(new_hive_id(FakeClock()), chosen, FakeClock()).environment()
    assert environment["HIVEMIND_COMB_SHIELD"] == "PROPOLIS"


@pytest.mark.parametrize("tier", list(CombShieldLevel))
def test_every_cell_is_told_the_reservation_its_spec_asks_for(tier: CombShieldLevel) -> None:
    # The Cell reports this as its capacity rather than probing the host it shares a kernel with.
    spec = _spec(tier)

    chosen = cell_endpoint(_endpoint(), spec, "docker")
    environment = mint_cell_bootstrap(new_hive_id(FakeClock()), chosen, FakeClock()).environment()

    reservation = CellReservation.model_validate_json(environment["HIVEMIND_RESERVATION"])
    assert reservation == CellReservation(
        cpu_cores=spec.cpu_cores,
        memory_bytes=spec.memory_bytes,
        disk_bytes=spec.disk_bytes,
        max_sub_bees=spec.capacity.max_sub_bees,
    )


def test_a_night_veil_cell_dials_the_hidden_service_through_tor_and_carries_no_link() -> None:
    chosen = cell_endpoint(_endpoint(), _spec(CombShieldLevel.NIGHT_VEIL), "docker")

    assert chosen.waggle_url == _LINK.waggle_url
    assert chosen.socks_proxy_url == _LINK.socks_proxy_url
    # Nothing downstream can choose again: the chosen endpoint has no link of its own.
    assert chosen.night_veil is None


def test_the_minted_night_veil_environment_names_only_the_hidden_service() -> None:
    chosen = cell_endpoint(_endpoint(), _spec(CombShieldLevel.NIGHT_VEIL), "docker")

    environment = mint_cell_bootstrap(new_hive_id(FakeClock()), chosen, FakeClock()).environment()

    assert environment["HIVEMIND_QUEEN_WAGGLE_URL"] == _LINK.waggle_url
    assert environment["HIVEMIND_SOCKS_PROXY_URL"] == _TOR
    assert environment["HIVEMIND_COMB_SHIELD"] == "NIGHT_VEIL"
    assert "localhost" not in "".join(environment.values())


def test_a_night_veil_cell_on_a_hive_with_no_link_is_refused() -> None:
    with pytest.raises(CellProvisionError, match="hidden service") as caught:
        cell_endpoint(_endpoint(None), _spec(CombShieldLevel.NIGHT_VEIL), "qemu")

    assert "qemu" in str(caught.value)


@pytest.mark.parametrize(
    ("link", "why"),
    [
        (NightVeilLink("ws://hivestand.example:8710", _TOR), "not a v3 onion service"),
        (NightVeilLink(f"ws://x{_HOST[1:]}:8710", _TOR), "not a v3 onion service"),  # A typo.
        (NightVeilLink(f"ws://{_ONION}", "socks5://127.0.0.1:9050"), "cannot be dialled"),
        (NightVeilLink(f"ws://{_ONION}", "socks5h://10.0.0.1:9050"), "cannot be dialled"),
    ],
)
def test_a_link_the_cell_could_never_dial_is_refused_before_anything_exists(
    link: NightVeilLink, why: str
) -> None:
    with pytest.raises(CellProvisionError, match=why):
        cell_endpoint(_endpoint(link), _spec(CombShieldLevel.NIGHT_VEIL), "docker")
