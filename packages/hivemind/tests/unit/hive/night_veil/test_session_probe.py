"""Tests for hivemind.hive.night_veil.session_probe: SessionNightVeilProbe over a FakeSession.

Most checks are one fixed command and a word in its output. Four conclude more: the hidden service
must answer a plain GET with a WebSocket endpoint's own `426`, the DNS resolver must be Tor's or
behind the tunnel (four fixed reads), the hostname must be of the random shape, and the browser
profile must switch WebRTC off. Each of those has its green and its red here, and the red cases
include the ones the earlier checks let through (a `2` anywhere in the status, any readable
`resolv.conf`, a check that was always skipped).

Fits into the Hive:
    Mirrors src/hivemind/hive/night_veil/session_probe.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.night_veil.session_probe for the module under test.
    - tests/unit/hive/night_veil/judges for the pure judges these checks hand their text to.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from builders.swarm import linux_route, linux_route6, resolv_conf
from builders.swarm.routes import LINUX_HEADER

from hivemind.cell import CompletedCommand, ExecSpec, FakeSession
from hivemind.hive.night_veil.results import CheckStatus
from hivemind.hive.night_veil.session_commands import (
    CMD_DEFAULT_ROUTE,
    CMD_GEOLOCATION_SERVICE,
    CMD_HOSTNAME,
    CMD_KILL_SWITCH,
    CMD_LOCALE,
    CMD_TIMEZONE,
    CMD_TOR_BROWSER,
    CMD_TOR_HEALTHY,
    CMD_TORRC,
    CMD_WAGGLE_SOCKET,
    DNS_COMMANDS,
    WEBRTC_COMMANDS,
    SessionProbeConfig,
    direct_egress_cmd,
    hidden_service_cmd,
    metadata_cmd,
)
from hivemind.hive.night_veil.session_probe import SessionNightVeilProbe
from waggle.clock import FakeClock

_CLOCK = FakeClock()
_SCRATCH = Path("/scratch")
_USER_JS, _PREFS_JS = (command.argv for command in WEBRTC_COMMANDS)
_WEBRTC_OFF = b'user_pref("media.peerconnection.enabled", false);\n'
_WEBRTC_ON = b'user_pref("media.peerconnection.enabled", true);\n'
_BROWSER = b"/usr/local/bin/tor-browser"  # What `which tor-browser` prints when it is there.
# OpenVPN's full tunnel: the whole IPv4 space through tun0, the network's own default beneath it.
_TUNNEL = (("tun0", "0.0.0.0/1"), ("tun0", "128.0.0.0/1"), ("eth0", "0.0.0.0/0"))
_LEAKING = (("eth0", "0.0.0.0/0"),)


def _config() -> SessionProbeConfig:
    return SessionProbeConfig(
        hidden_service_address="abc123.onion", socks_proxy_port=9050, locale_profile="C.UTF-8"
    )


def _completed(exit_code: int, stdout: bytes = b"") -> CompletedCommand:
    return CompletedCommand(exit_code=exit_code, stdout=stdout, stderr=b"", duration_s=0.01)


def _probe(
    responses: dict[tuple[str, ...], CompletedCommand],
    asked: list[tuple[str, ...]] | None = None,
    config: SessionProbeConfig | None = None,
) -> SessionNightVeilProbe:
    """Build a SessionNightVeilProbe over a FakeSession scripted by exact argv match.

    A command with no scripted answer exits 127, as a missing binary or file would, and every
    command asked is appended to `asked` when one is given, so a test can say what was run.
    """

    def responder(spec: ExecSpec) -> CompletedCommand:
        if asked is not None:
            asked.append(tuple(spec.argv))
        return responses.get(tuple(spec.argv), _completed(127, b"not scripted"))

    session = FakeSession(scratch_dir=_SCRATCH, clock=_CLOCK, responder=responder)
    return SessionNightVeilProbe(session, config if config is not None else _config())


def _dns_cell(
    *nameservers: str,
    routes: tuple[tuple[str, str], ...] = _TUNNEL,
    torrc: str = "",
) -> dict[tuple[str, ...], CompletedCommand]:
    """What a Cell with these resolvers, routing table and torrc answers the four reads with."""
    table = "\n".join([LINUX_HEADER, *(linux_route(i, n) for i, n in routes)]) + "\n"
    texts = {
        "routes": table,
        "routes6": linux_route6("tun0", "::/0") + "\n",
        "dns": resolv_conf(*nameservers),
        "torrc": torrc,
    }
    return {c.argv: _completed(0, texts[c.key].encode()) for c in DNS_COMMANDS}


# ──────────────────────────────────────────────────────────────────────────────
# Green paths: every command a named constant, so every check is exercised once.
# ──────────────────────────────────────────────────────────────────────────────


async def test_kill_switch_active_green_when_default_drop_is_loaded() -> None:
    probe = _probe({CMD_KILL_SWITCH: _completed(0, b"table inet filter {\n  policy drop\n}")})

    result = await probe.kill_switch_active()

    assert result.status is CheckStatus.PASS


async def test_default_route_via_tunnel_green_when_the_route_names_tun0() -> None:
    probe = _probe({CMD_DEFAULT_ROUTE: _completed(0, b"default via 10.8.0.1 dev tun0")})

    result = await probe.default_route_via_tunnel()

    assert result.status is CheckStatus.PASS


async def test_tor_healthy_green_when_the_service_is_active() -> None:
    probe = _probe({CMD_TOR_HEALTHY: _completed(0, b"active")})

    result = await probe.tor_healthy()

    assert result.status is CheckStatus.PASS


async def test_tor_browser_launchable_green_when_which_finds_it() -> None:
    probe = _probe({CMD_TOR_BROWSER: _completed(0, _BROWSER)})

    result = await probe.tor_browser_launchable()

    assert result.status is CheckStatus.PASS


async def test_waggle_socket_via_socks_green_on_an_established_socks_peer() -> None:
    probe = _probe({CMD_WAGGLE_SOCKET: _completed(0, b"ESTAB 0 0 127.0.0.1:51522 127.0.0.1:9050")})

    result = await probe.waggle_socket_via_socks()

    assert result.status is CheckStatus.PASS


async def test_direct_egress_blocked_green_when_the_direct_curl_itself_fails() -> None:
    argv = direct_egress_cmd(_config())
    probe = _probe({argv: _completed(7)})  # curl exit 7: could not connect.

    result = await probe.direct_egress_blocked()

    assert result.status is CheckStatus.PASS


async def test_direct_egress_blocked_red_when_the_direct_curl_unexpectedly_succeeds() -> None:
    argv = direct_egress_cmd(_config())
    probe = _probe({argv: _completed(0, b"200")})

    result = await probe.direct_egress_blocked()

    assert result.status is CheckStatus.FAIL


async def test_geolocation_denied_green_when_no_gpsd_process_is_found() -> None:
    probe = _probe({CMD_GEOLOCATION_SERVICE: _completed(1)})  # pgrep: no matching process.

    result = await probe.geolocation_denied()

    assert result.status is CheckStatus.PASS


async def test_metadata_unreachable_green_when_the_curl_fails() -> None:
    argv = metadata_cmd(_config())
    probe = _probe({argv: _completed(28)})  # curl exit 28: timed out.

    result = await probe.metadata_unreachable()

    assert result.status is CheckStatus.PASS


async def test_metadata_unreachable_red_when_the_curl_unexpectedly_succeeds() -> None:
    argv = metadata_cmd(_config())
    probe = _probe({argv: _completed(0, b"200")})

    result = await probe.metadata_unreachable()

    assert result.status is CheckStatus.FAIL


async def test_timezone_utc_green_when_timedatectl_reports_utc() -> None:
    probe = _probe({CMD_TIMEZONE: _completed(0, b"UTC")})

    result = await probe.timezone_utc()

    assert result.status is CheckStatus.PASS


async def test_locale_pinned_green_when_locale_matches_the_configured_profile() -> None:
    probe = _probe({CMD_LOCALE: _completed(0, b"LANG=C.UTF-8\nLC_ALL=")})

    result = await probe.locale_pinned()

    assert result.status is CheckStatus.PASS


async def test_locale_pinned_red_when_locale_does_not_match() -> None:
    probe = _probe({CMD_LOCALE: _completed(0, b"LANG=en_US.UTF-8")})

    result = await probe.locale_pinned()

    assert result.status is CheckStatus.FAIL


# ──────────────────────────────────────────────────────────────────────────────
# hidden_service_reachable: the WebSocket endpoint's own 426, nothing merely containing a 2.
# ──────────────────────────────────────────────────────────────────────────────


async def test_hidden_service_reachable_green_on_the_websocket_endpoints_426() -> None:
    probe = _probe({hidden_service_cmd(_config()): _completed(0, b"426")})

    result = await probe.hidden_service_reachable()

    assert result.status is CheckStatus.PASS


@pytest.mark.parametrize("printed", [b"200", b"502", b"521"])
async def test_hidden_service_reachable_red_on_any_other_status_even_one_with_a_2_in_it(
    printed: bytes,
) -> None:
    # The old check passed all three: it looked for a `2` anywhere in what curl printed.
    probe = _probe({hidden_service_cmd(_config()): _completed(0, printed)})

    result = await probe.hidden_service_reachable()

    assert result.status is CheckStatus.FAIL


async def test_hidden_service_reachable_red_when_tor_could_not_reach_it() -> None:
    # curl's exit 97 (the SOCKS5 handshake failed) with the "000" it prints for no response at all.
    probe = _probe({hidden_service_cmd(_config()): _completed(97, b"000")})

    result = await probe.hidden_service_reachable()

    assert result.status is CheckStatus.FAIL


# ──────────────────────────────────────────────────────────────────────────────
# dns_leak_free: where the resolver is, not whether resolv.conf can be read.
# ──────────────────────────────────────────────────────────────────────────────


async def test_dns_leak_free_green_when_the_resolver_is_only_reached_through_the_tunnel() -> None:
    probe = _probe(_dns_cell("10.8.0.1"))

    result = await probe.dns_leak_free()

    assert (result.status, result.detail) == (CheckStatus.PASS, "tunnel")


async def test_dns_leak_free_green_when_the_resolver_is_tors_own_dns_port_on_loopback() -> None:
    probe = _probe(_dns_cell("127.0.0.1", torrc="SocksPort 9050\nDNSPort 127.0.0.1:53\n"))

    result = await probe.dns_leak_free()

    assert (result.status, result.detail) == (CheckStatus.PASS, "tor_dns_port")


@pytest.mark.parametrize("nameserver", ["8.8.8.8", "192.168.1.1"])
async def test_dns_leak_free_red_for_a_readable_resolv_conf_naming_a_resolver_off_the_tunnel(
    nameserver: str,
) -> None:
    # Every one of these answers the old check's only question: could resolv.conf be read.
    probe = _probe(_dns_cell(nameserver, routes=_LEAKING))

    result = await probe.dns_leak_free()

    assert (result.status, result.detail) == (CheckStatus.FAIL, "dns_not_via_tunnel")


async def test_dns_leak_free_red_for_a_loopback_stub_that_is_not_tors() -> None:
    probe = _probe(_dns_cell("127.0.0.53"))

    result = await probe.dns_leak_free()

    assert (result.status, result.detail) == (CheckStatus.FAIL, "local_stub_resolver")


async def test_dns_leak_free_red_when_nothing_could_be_read() -> None:
    probe = _probe({})  # Every read answers "command not found".

    result = await probe.dns_leak_free()

    assert (result.status, result.detail) == (CheckStatus.FAIL, "no_nameservers")


async def test_dns_leak_free_reads_the_routes_the_resolvers_and_tors_config_and_nothing_else() -> (
    None
):
    asked: list[tuple[str, ...]] = []
    probe = _probe(_dns_cell("10.8.0.1"), asked)

    await probe.dns_leak_free()

    assert asked == [
        ("cat", "/proc/net/route"),
        ("cat", "/proc/net/ipv6_route"),
        ("cat", "/etc/resolv.conf"),
        CMD_TORRC,
    ]


# ──────────────────────────────────────────────────────────────────────────────
# randomized_hostname: a Cell's name is its own, random, and not the Hive Stand's.
# ──────────────────────────────────────────────────────────────────────────────


async def test_randomized_hostname_green_for_a_name_of_the_random_shape() -> None:
    probe = _probe({CMD_HOSTNAME: _completed(0, b"night-veil-0123abcd\n")})

    result = await probe.randomized_hostname()

    assert (result.status, result.detail) == (CheckStatus.PASS, "random_hostname")


async def test_randomized_hostname_red_for_a_name_derived_from_the_cells_id() -> None:
    probe = _probe({CMD_HOSTNAME: _completed(0, b"cell-01h8x7f3k5m9n2p4q6r8s0t1v3\n")})

    result = await probe.randomized_hostname()

    assert (result.status, result.detail) == (CheckStatus.FAIL, "not_random_shape")


async def test_randomized_hostname_red_for_the_hive_stands_own_name() -> None:
    config = replace(_config(), stand_hostname="night-veil-0123abcd")
    probe = _probe({CMD_HOSTNAME: _completed(0, b"night-veil-0123abcd\n")}, config=config)

    result = await probe.randomized_hostname()

    assert (result.status, result.detail) == (CheckStatus.FAIL, "hive_stand_hostname")


async def test_randomized_hostname_red_when_the_hostname_could_not_be_read() -> None:
    probe = _probe({})

    result = await probe.randomized_hostname()

    assert (result.status, result.detail) == (CheckStatus.FAIL, "no_hostname")


# ──────────────────────────────────────────────────────────────────────────────
# webrtc_leak_blocked: the browser profile switches it off, or there is no browser at all.
# ──────────────────────────────────────────────────────────────────────────────


async def test_webrtc_leak_blocked_green_when_the_profile_switches_it_off() -> None:
    probe = _probe({CMD_TOR_BROWSER: _completed(0, _BROWSER), _USER_JS: _completed(0, _WEBRTC_OFF)})

    result = await probe.webrtc_leak_blocked()

    assert (result.status, result.detail) == (CheckStatus.PASS, "webrtc_disabled")


async def test_webrtc_leak_blocked_red_when_the_profile_leaves_webrtc_on() -> None:
    probe = _probe({CMD_TOR_BROWSER: _completed(0, _BROWSER), _PREFS_JS: _completed(0, _WEBRTC_ON)})

    result = await probe.webrtc_leak_blocked()

    assert (result.status, result.detail) == (CheckStatus.FAIL, "webrtc_enabled")


async def test_webrtc_leak_blocked_red_when_a_browser_is_there_but_its_profile_says_nothing() -> (
    None
):
    # A browser is present, so the check applies; a profile with no setting is red, never skipped.
    probe = _probe({CMD_TOR_BROWSER: _completed(0, _BROWSER)})

    result = await probe.webrtc_leak_blocked()

    assert (result.status, result.detail) == (CheckStatus.FAIL, "pref_not_set")


async def test_webrtc_leak_blocked_is_not_applicable_only_with_no_browser_at_all() -> None:
    asked: list[tuple[str, ...]] = []
    probe = _probe({CMD_TOR_BROWSER: _completed(1)}, asked)  # which: not found.

    result = await probe.webrtc_leak_blocked()

    assert result.status is CheckStatus.NOT_APPLICABLE
    assert asked == [CMD_TOR_BROWSER]  # With no browser there is no profile to read.


# ──────────────────────────────────────────────────────────────────────────────
# Red paths beyond the "must fail to be green" checks above.
# ──────────────────────────────────────────────────────────────────────────────


async def test_kill_switch_active_red_when_no_default_drop_policy_is_present() -> None:
    probe = _probe({CMD_KILL_SWITCH: _completed(0, b"table inet filter {\n  policy accept\n}")})

    result = await probe.kill_switch_active()

    assert result.status is CheckStatus.FAIL


async def test_tor_healthy_red_when_the_service_is_inactive() -> None:
    probe = _probe({CMD_TOR_HEALTHY: _completed(3, b"inactive")})

    result = await probe.tor_healthy()

    assert result.status is CheckStatus.FAIL
