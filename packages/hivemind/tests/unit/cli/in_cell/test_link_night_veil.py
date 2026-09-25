"""Tests for hivemind.cli.in_cell.link's Night Veil checks: the Cell attests its own control link.

Roadmap step 10.3a: a Night Veil Cell's `CellReady` carries the two checks it can make of its own
control link (Waggle only through the SOCKS proxy; the Queen reached at her onion service), each
passed or failed as the facts are, and its first `CellHeartbeat` calls the shield verified only
when every check passed. The names mirror the Queen's own attestation's.

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/link.py (codingrules 5.1: split by feature from
    test_link.py).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.in_cell.link for control_link_attestation.
    - test_main_night_veil.py for the same checks sent over a real proxied link.
"""

from __future__ import annotations

from hivemind.cli.in_cell.link import (
    HIDDEN_SERVICE_CHECK,
    VIA_SOCKS_CHECK,
    control_link_attestation,
)
from hivemind.hive.night_veil import CHECK_NAMES
from waggle.transport.socks import SocksProxy

_ONION = "7jjm54ntxrtbp4fjhhw2gdk7zz2fshgnubimtmc5dcczncvdfo3lnbid.onion"  # A valid v3 address.
_TOR = SocksProxy.parse("socks5h://127.0.0.1:9050")


def test_both_checks_pass_for_an_onion_link_through_the_proxy() -> None:
    checks = control_link_attestation(f"ws://{_ONION}:8710", _TOR)

    assert [(check.name, check.has_passed) for check in checks] == [
        (VIA_SOCKS_CHECK, True),
        (HIDDEN_SERVICE_CHECK, True),
    ]
    assert "socks5h://127.0.0.1:9050" in checks[0].detail
    assert _ONION in checks[1].detail


def test_a_direct_clearnet_link_fails_both_checks_honestly() -> None:
    checks = control_link_attestation("ws://host.docker.internal:8710", None)

    assert [check.has_passed for check in checks] == [False, False]
    assert "directly" in checks[0].detail
    assert "not an onion service" in checks[1].detail


def test_the_check_names_are_the_queens_own_attestation_names() -> None:
    assert {VIA_SOCKS_CHECK, HIDDEN_SERVICE_CHECK} <= set(CHECK_NAMES)
