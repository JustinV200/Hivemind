"""Tests for hivemind.hive.night_veil.session_probe: SessionNightVeilProbe over a FakeSession.

Fits into the Hive:
    Mirrors src/hivemind/hive/night_veil/session_probe.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.night_veil.session_probe for the module under test.
"""

from __future__ import annotations

from pathlib import Path

from hivemind.cell import CompletedCommand, ExecSpec, FakeSession
from hivemind.hive.night_veil.results import CheckStatus
from hivemind.hive.night_veil.session_commands import (
    CMD_DEFAULT_ROUTE,
    CMD_DNS_PROBE,
    CMD_GEOLOCATION_SERVICE,
    CMD_KILL_SWITCH,
    CMD_LOCALE,
    CMD_TIMEZONE,
    CMD_TOR_BROWSER,
    CMD_TOR_HEALTHY,
    CMD_WAGGLE_SOCKET,
    SessionProbeConfig,
    direct_egress_cmd,
    metadata_cmd,
)
from hivemind.hive.night_veil.session_probe import SessionNightVeilProbe
from waggle.clock import FakeClock

_CLOCK = FakeClock()
_SCRATCH = Path("/scratch")


def _config() -> SessionProbeConfig:
    return SessionProbeConfig(
        hidden_service_address="abc123.onion", socks_proxy_port=9050, locale_profile="C.UTF-8"
    )


def _completed(exit_code: int, stdout: bytes = b"") -> CompletedCommand:
    return CompletedCommand(exit_code=exit_code, stdout=stdout, stderr=b"", duration_s=0.01)


def _probe(responses: dict[tuple[str, ...], CompletedCommand]) -> SessionNightVeilProbe:
    """Build a SessionNightVeilProbe over a FakeSession scripted by exact argv match."""

    def responder(spec: ExecSpec) -> CompletedCommand:
        return responses.get(tuple(spec.argv), _completed(127, b"not scripted"))

    session = FakeSession(scratch_dir=_SCRATCH, clock=_CLOCK, responder=responder)
    return SessionNightVeilProbe(session, _config())


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
    probe = _probe({CMD_TOR_BROWSER: _completed(0, b"/usr/local/bin/tor-browser")})

    result = await probe.tor_browser_launchable()

    assert result.status is CheckStatus.PASS


async def test_hidden_service_reachable_green_on_a_2xx_status() -> None:
    argv = (
        "curl",
        "-s",
        "-o",
        "/dev/null",
        "-w",
        "%{http_code}",
        "--socks5-hostname",
        "127.0.0.1:9050",
        "http://abc123.onion",
    )
    probe = _probe({argv: _completed(0, b"200")})

    result = await probe.hidden_service_reachable()

    assert result.status is CheckStatus.PASS


async def test_waggle_socket_via_socks_green_on_an_established_socks_peer() -> None:
    probe = _probe({CMD_WAGGLE_SOCKET: _completed(0, b"ESTAB 0 0 127.0.0.1:51522 127.0.0.1:9050")})

    result = await probe.waggle_socket_via_socks()

    assert result.status is CheckStatus.PASS


async def test_dns_leak_free_green_when_resolv_conf_reads_cleanly() -> None:
    probe = _probe({CMD_DNS_PROBE: _completed(0, b"nameserver 10.8.0.1")})

    result = await probe.dns_leak_free()

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


async def test_webrtc_leak_blocked_is_always_not_applicable_and_never_touches_the_session() -> None:
    # No scripted response at all: if this check ever called session.exec, FakeSession would
    # answer "command not found" rather than NOT_APPLICABLE, and the test would fail loudly.
    probe = _probe({})

    result = await probe.webrtc_leak_blocked()

    assert result.status is CheckStatus.NOT_APPLICABLE


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
