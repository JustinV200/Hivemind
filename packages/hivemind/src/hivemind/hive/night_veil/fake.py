"""Define FakeNightVeilProbe: an in-memory NightVeilProbe, all-green by default.

Roadmap step 5.7b: "FakeNightVeilProbe in src with per-check switches." Shipped code, not
test-only (codingrules section 14.4: "fakes live in src/ beside their Protocol"), because a
composition root can run the whole Night Veil provision-and-attest path against this fake before
`images/night-veil-ubuntu` (roadmap step 5.3a) exists at all (ADR-0030's own Consequences: "the
image depends on desktop-ubuntu (6.1)... attestation is developed against a fake probe until it
does"). Every check defaults to `CheckStatus.PASS`; a caller flips one to red with `overrides`,
keyed by the exact `hive.night_veil.probe.NightVeilProbe` method name it wants to fail, rather than
this class taking thirteen keyword parameters (codingrules section 5.1's parameter limit).

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hive.night_veil`. Implements `hive.night_veil.probe.
    NightVeilProbe`. Constructed directly by tests and by any composition root that has not yet
    wired a real `SessionNightVeilProbe`. Calls into `hive.night_veil.results` (CheckResult,
    CheckStatus, CHECK_NAMES) only.

Key invariants:
    - `FakeNightVeilProbe()` (no overrides) passes every check: the permissive default a caller
      building a happy-path test wants without naming all thirteen.
    - `overrides` is validated against `CHECK_NAMES` at construction: an unknown key is a caller
      bug (a typo'd check name) worth failing loudly on, not silently ignoring.
    - Calling a check twice returns the same CheckResult both times: this fake holds no mutable
      state a call could advance (unlike, say, a flaky-connection fake elsewhere in the Hive).

See Also:
    - .claude/codingrules.md section 14.4 for "fakes live in src/, are shipped code."
    - hivemind.hive.night_veil.probe for NightVeilProbe, the Protocol this implements.
    - hivemind.hive.night_veil.results for CheckResult, CheckStatus and CHECK_NAMES.
"""

from __future__ import annotations

from collections.abc import Mapping

from hivemind.hive.night_veil.results import CHECK_NAMES, CheckResult, CheckStatus

_GREEN = CheckResult(status=CheckStatus.PASS, detail="FakeNightVeilProbe default: PASS.")

__all__ = ["FakeNightVeilProbe"]


class FakeNightVeilProbe:
    """An in-memory NightVeilProbe: every check green unless `overrides` names it red."""

    def __init__(self, overrides: Mapping[str, CheckResult] | None = None) -> None:
        """Build a FakeNightVeilProbe, all-green apart from `overrides`.

        Args:
            overrides: Check name (a `hivemind.hive.night_veil.results.CHECK_NAMES` entry) to the
                CheckResult that check should return instead of the green default; `None` (or an
                empty mapping) means every check passes.

        Raises:
            ValueError: `overrides` names a key that is not one of `CHECK_NAMES`.
        """
        self._overrides = dict(overrides) if overrides is not None else {}
        unknown = set(self._overrides) - set(CHECK_NAMES)
        if unknown:
            raise ValueError(f"FakeNightVeilProbe overrides names unknown check(s): {unknown!r}.")

    def _result(self, name: str) -> CheckResult:
        """Return `overrides[name]` if set, else the green default."""
        return self._overrides.get(name, _GREEN)

    async def kill_switch_active(self) -> CheckResult:
        """See `NightVeilProbe.kill_switch_active`."""
        return self._result("kill_switch_active")

    async def default_route_via_tunnel(self) -> CheckResult:
        """See `NightVeilProbe.default_route_via_tunnel`."""
        return self._result("default_route_via_tunnel")

    async def tor_healthy(self) -> CheckResult:
        """See `NightVeilProbe.tor_healthy`."""
        return self._result("tor_healthy")

    async def tor_browser_launchable(self) -> CheckResult:
        """See `NightVeilProbe.tor_browser_launchable`."""
        return self._result("tor_browser_launchable")

    async def hidden_service_reachable(self) -> CheckResult:
        """See `NightVeilProbe.hidden_service_reachable`."""
        return self._result("hidden_service_reachable")

    async def waggle_socket_via_socks(self) -> CheckResult:
        """See `NightVeilProbe.waggle_socket_via_socks`."""
        return self._result("waggle_socket_via_socks")

    async def dns_leak_free(self) -> CheckResult:
        """See `NightVeilProbe.dns_leak_free`."""
        return self._result("dns_leak_free")

    async def direct_egress_blocked(self) -> CheckResult:
        """See `NightVeilProbe.direct_egress_blocked`."""
        return self._result("direct_egress_blocked")

    async def geolocation_denied(self) -> CheckResult:
        """See `NightVeilProbe.geolocation_denied`."""
        return self._result("geolocation_denied")

    async def metadata_unreachable(self) -> CheckResult:
        """See `NightVeilProbe.metadata_unreachable`."""
        return self._result("metadata_unreachable")

    async def timezone_utc(self) -> CheckResult:
        """See `NightVeilProbe.timezone_utc`."""
        return self._result("timezone_utc")

    async def locale_pinned(self) -> CheckResult:
        """See `NightVeilProbe.locale_pinned`."""
        return self._result("locale_pinned")

    async def webrtc_leak_blocked(self) -> CheckResult:
        """See `NightVeilProbe.webrtc_leak_blocked`."""
        return self._result("webrtc_leak_blocked")
