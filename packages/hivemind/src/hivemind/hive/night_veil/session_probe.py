"""Define SessionNightVeilProbe: the real NightVeilProbe, run over a live CellSession.

Roadmap step 5.7b: "SessionNightVeilProbe that runs each check as a fixed, documented shell command
through a CellSession." Every check calls `hivemind.cell.session.run` with one of `hive.night_veil.
session_commands`'s own named commands, then judges the `CompletedCommand` with one of that same
module's pure `judge_*` helpers. `webrtc_leak_blocked` is the one documented exception: it runs no
command at all, because no headless browser automation exists yet to drive Tor Browser through a
WebRTC probe (roadmap step 5.7b's own words), and returns `CheckStatus.NOT_APPLICABLE` instead.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hive.night_veil`. Implements `hive.night_veil.probe.
    NightVeilProbe`. Constructed by whichever composition root wires a real Night Veil attestation
    (a report item: the call site sits in `hivemind.queen.cell_gate.provider`, outside this
    dispatch's file list). Calls into `hivemind.cell` (CellSession, ExecSpec, run),
    `hive.night_veil.results` (CheckResult, CheckStatus) and `hive.night_veil.session_commands`
    (every command and judge function) only.

Key invariants:
    - Every check runs at most one command on `self._session`, with `_TIMEOUT_S` as its own
      `ExecSpec.timeout_s`: a hung probe command must never hang attestation itself indefinitely.
    - `webrtc_leak_blocked` never calls `self._session.exec`: it is the one check this
      implementation answers without touching the Cell at all (module docstring).

See Also:
    - .claude/roadmap.md step 5.7b for this module's own roadmap bullet.
    - hivemind.hive.night_veil.probe for NightVeilProbe, the Protocol this implements.
    - hivemind.hive.night_veil.session_commands for every command and judge function this module
      calls.
    - hivemind.cell.session for CellSession, ExecSpec and run, the terminal contract this module
      drives.
"""

from __future__ import annotations

from hivemind.cell import CellSession, ExecSpec, run
from hivemind.hive.night_veil.results import CheckResult, CheckStatus
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
    hidden_service_cmd,
    judge_contains,
    judge_exit_zero,
    judge_fails,
    metadata_cmd,
)

_TIMEOUT_S = 15.0  # Generous for an nft/ip/curl query; a probe must never hang attestation.
_WEBRTC_NOT_APPLICABLE_DETAIL = (
    "no headless browser automation available yet; WebRTC leak probe deferred (roadmap 5.7b)."
)

__all__ = ["SessionNightVeilProbe"]


class SessionNightVeilProbe:
    """NightVeilProbe over a live CellSession: each check is one documented, argv-only command."""

    def __init__(self, session: CellSession, config: SessionProbeConfig) -> None:
        """Build a SessionNightVeilProbe bound to `session` and `config`.

        Args:
            session: The Night Veil Cell's own terminal session, already open.
            config: This Hive's own onion address, SOCKS port, locale profile and probe hosts.
        """
        self._session = session
        self._config = config

    async def kill_switch_active(self) -> CheckResult:
        """See `NightVeilProbe.kill_switch_active`: runs CMD_KILL_SWITCH."""
        completed = await run(self._session, ExecSpec(argv=CMD_KILL_SWITCH, timeout_s=_TIMEOUT_S))
        return judge_contains(completed, "policy drop", ok_detail="default-drop policy present")

    async def default_route_via_tunnel(self) -> CheckResult:
        """See `NightVeilProbe.default_route_via_tunnel`: runs CMD_DEFAULT_ROUTE."""
        completed = await run(self._session, ExecSpec(argv=CMD_DEFAULT_ROUTE, timeout_s=_TIMEOUT_S))
        return judge_contains(completed, "tun0", ok_detail="default route via tun0")

    async def tor_healthy(self) -> CheckResult:
        """See `NightVeilProbe.tor_healthy`: runs CMD_TOR_HEALTHY."""
        completed = await run(self._session, ExecSpec(argv=CMD_TOR_HEALTHY, timeout_s=_TIMEOUT_S))
        return judge_contains(completed, "active", ok_detail="tor.service is active")

    async def tor_browser_launchable(self) -> CheckResult:
        """See `NightVeilProbe.tor_browser_launchable`: runs CMD_TOR_BROWSER."""
        completed = await run(self._session, ExecSpec(argv=CMD_TOR_BROWSER, timeout_s=_TIMEOUT_S))
        return judge_exit_zero(completed, ok_detail="tor-browser is on PATH")

    async def hidden_service_reachable(self) -> CheckResult:
        """See `NightVeilProbe.hidden_service_reachable`: curls through the SOCKS proxy."""
        argv = hidden_service_cmd(self._config)
        completed = await run(self._session, ExecSpec(argv=argv, timeout_s=_TIMEOUT_S))
        return judge_contains(completed, "2", ok_detail="hidden service answered 2xx/3xx")

    async def waggle_socket_via_socks(self) -> CheckResult:
        """See `NightVeilProbe.waggle_socket_via_socks`: runs CMD_WAGGLE_SOCKET."""
        completed = await run(self._session, ExecSpec(argv=CMD_WAGGLE_SOCKET, timeout_s=_TIMEOUT_S))
        return judge_contains(
            completed,
            f":{self._config.socks_proxy_port}",
            ok_detail="an established peer through the SOCKS port",
        )

    async def dns_leak_free(self) -> CheckResult:
        """See `NightVeilProbe.dns_leak_free`: runs CMD_DNS_PROBE."""
        completed = await run(self._session, ExecSpec(argv=CMD_DNS_PROBE, timeout_s=_TIMEOUT_S))
        return judge_exit_zero(completed, ok_detail="resolv.conf read; no direct resolver found")

    async def direct_egress_blocked(self) -> CheckResult:
        """See `NightVeilProbe.direct_egress_blocked`: a direct curl that must fail."""
        argv = direct_egress_cmd(self._config)
        completed = await run(self._session, ExecSpec(argv=argv, timeout_s=_TIMEOUT_S))
        return judge_fails(completed, ok_detail="direct egress refused by the kill-switch")

    async def geolocation_denied(self) -> CheckResult:
        """See `NightVeilProbe.geolocation_denied`: runs CMD_GEOLOCATION_SERVICE."""
        completed = await run(
            self._session, ExecSpec(argv=CMD_GEOLOCATION_SERVICE, timeout_s=_TIMEOUT_S)
        )
        return judge_fails(completed, ok_detail="no location-service daemon running")

    async def metadata_unreachable(self) -> CheckResult:
        """See `NightVeilProbe.metadata_unreachable`: a curl that must fail (null-routed)."""
        argv = metadata_cmd(self._config)
        completed = await run(self._session, ExecSpec(argv=argv, timeout_s=_TIMEOUT_S))
        return judge_fails(completed, ok_detail="metadata endpoint unreachable (null-routed)")

    async def timezone_utc(self) -> CheckResult:
        """See `NightVeilProbe.timezone_utc`: runs CMD_TIMEZONE."""
        completed = await run(self._session, ExecSpec(argv=CMD_TIMEZONE, timeout_s=_TIMEOUT_S))
        return judge_contains(completed, "UTC", ok_detail="timezone is UTC")

    async def locale_pinned(self) -> CheckResult:
        """See `NightVeilProbe.locale_pinned`: runs CMD_LOCALE."""
        completed = await run(self._session, ExecSpec(argv=CMD_LOCALE, timeout_s=_TIMEOUT_S))
        return judge_contains(
            completed, self._config.locale_profile, ok_detail="locale matches the profile"
        )

    async def webrtc_leak_blocked(self) -> CheckResult:
        """See `NightVeilProbe.webrtc_leak_blocked`: always NOT_APPLICABLE (module docstring)."""
        # Module docstring: the one check answered without touching the Cell at all.
        return CheckResult(status=CheckStatus.NOT_APPLICABLE, detail=_WEBRTC_NOT_APPLICABLE_DETAIL)
