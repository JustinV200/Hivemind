"""Define NightVeilProbe: the Protocol one check per Night Veil attestation requirement.

Roadmap step 5.7b: "before CellReady, attest the night-veil-ubuntu image deterministically...
firewall kill-switch active, default route via VPN tunnel, Tor daemon healthy, Tor Browser
installed and launchable, the Hive Stand's Waggle hidden service reachable and the Waggle client's
socket routed through the Tor SOCKS proxy (not the VPN interface), DNS leak checks passing, and
direct egress blocked... geolocation APIs denied, metadata endpoints unreachable, timezone pinned
to UTC, locale pinned to profile, and WebRTC local-IP leak test blocked." One Protocol method per
requirement, each returning a `hivemind.hive.night_veil.results.CheckResult`, so a caller (`hive.
night_veil.trail.attest_cell`) can run every one and hand the collected mapping to `hive.
night_veil.results.attest` without knowing which implementation it is talking to (codingrules
section 8.1: "Protocols at every seam").

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hive.night_veil`. Implemented by `hive.night_veil.fake.
    FakeNightVeilProbe` (tests, and any composition root before a real one exists) and `hive.
    night_veil.session_probe.SessionNightVeilProbe` (the real, Queen-side implementation, over a
    `hivemind.cell.CellSession`). Called by `hive.night_veil.trail.attest_cell`. Calls into
    `hive.night_veil.results` (CheckResult) only.

Key invariants:
    - Every method name here appears, in the same order, in `hive.night_veil.results.CHECK_NAMES`
      (a sync test in `tests/unit/hive/night_veil/test_probe.py` checks this via
      `typing.get_type_hints`/`dir`, since a Protocol carries no runtime member list of its own).
    - Every method takes no arguments beyond `self`: whatever a concrete probe needs (a
      `CellSession`, an onion address, a locale profile) is bound at construction, never passed
      per call, so `attest_cell` can run every check the same way regardless of implementation.
    - No method ever raises for "the thing I checked for is present" (a red kill-switch, a
      reachable direct-egress endpoint, ...): that is a `CheckResult(status=FAIL, ...)`, not an
      exception. A method may still raise for a genuine failure to even run the check (a closed
      session, a malformed command); `hive.night_veil.trail.attest_cell`'s own docstring names how
      that is meant to propagate.

See Also:
    - .claude/codingrules.md section 8.7 for the exact list of checks this Protocol implements.
    - .claude/roadmap.md step 5.7b for this module's own roadmap bullet.
    - docs/adr/0030-night-veil-retention-and-clearance-boundary.md for why every check runs before
      CellReady, never as a runtime install.
    - hivemind.hive.night_veil.results for CheckResult and CHECK_NAMES.
    - hivemind.hive.night_veil.fake for FakeNightVeilProbe, the in-memory implementation.
    - hivemind.hive.night_veil.session_probe for SessionNightVeilProbe, the real implementation.
"""

from __future__ import annotations

from typing import Protocol

from hivemind.hive.night_veil.results import CheckResult

__all__ = ["NightVeilProbe"]


class NightVeilProbe(Protocol):
    """One async method per Night Veil attestation check (codingrules section 8.7, roadmap 5.7b)."""

    async def kill_switch_active(self) -> CheckResult:
        """Check the nftables kill-switch's default-drop policy is loaded and active."""
        ...

    async def default_route_via_tunnel(self) -> CheckResult:
        """Check the Cell's default route goes through the OpenVPN tunnel interface."""
        ...

    async def tor_healthy(self) -> CheckResult:
        """Check the Tor daemon is running and healthy."""
        ...

    async def tor_browser_launchable(self) -> CheckResult:
        """Check Tor Browser is installed and can be launched."""
        ...

    async def hidden_service_reachable(self) -> CheckResult:
        """Check the Hive Stand's Waggle hidden service answers through Tor."""
        ...

    async def waggle_socket_via_socks(self) -> CheckResult:
        """Check the Waggle client's socket is routed through the Tor SOCKS proxy, not the VPN."""
        ...

    async def dns_leak_free(self) -> CheckResult:
        """Check every DNS resolution goes through the tunnel, none direct."""
        ...

    async def direct_egress_blocked(self) -> CheckResult:
        """Check a direct (non-tunnelled, non-Tor) outbound request is refused."""
        ...

    async def geolocation_denied(self) -> CheckResult:
        """Check no geolocation API or service is reachable from the Cell."""
        ...

    async def metadata_unreachable(self) -> CheckResult:
        """Check cloud metadata endpoints (169.254.169.254 and friends) are unreachable."""
        ...

    async def timezone_utc(self) -> CheckResult:
        """Check the Cell's own timezone is pinned to UTC."""
        ...

    async def locale_pinned(self) -> CheckResult:
        """Check the Cell's own locale matches the configured Night Veil profile."""
        ...

    async def webrtc_leak_blocked(self) -> CheckResult:
        """Check a WebRTC local-IP leak probe is blocked.

        May return `CheckResult(status=CheckStatus.NOT_APPLICABLE, ...)` when no browser
        automation exists yet to run the probe against (roadmap step 5.7b's own documented
        exception; `hive.night_veil.results.attest` treats this as clearing the check, not as a
        silent downgrade -- see that module's own docstring).
        """
        ...
