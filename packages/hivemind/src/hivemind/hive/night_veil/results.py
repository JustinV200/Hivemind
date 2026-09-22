"""Define CheckStatus, CheckResult, Attestation, CHECK_NAMES and attest.

Roadmap step 5.7b / ADR-0030: "attest the night-veil-ubuntu image deterministically... Any red
check fails the placement... There is no degraded mode and no retry that skips a check." `attest`
is that all-or-nothing judgement, pure (codingrules section 8.3): given the same `results` mapping
it always returns the same `Attestation`. `CheckStatus` has a third member beyond PASS/FAIL,
NOT_APPLICABLE, for exactly one documented case (`webrtc_leak_blocked`, roadmap step 5.7b: "a
documented not_applicable when no browser automation exists yet"); `attest` treats NOT_APPLICABLE
as passing, never as a silent downgrade -- the Attestation's own `results` mapping still records it
by name, so `hive trail`/an operator can see exactly which check was skipped and why, and a later
phase that adds real browser automation only has to change what `webrtc_leak_blocked` returns, not
this module's own all-or-nothing rule.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hive.night_veil`. Called by `hivemind.hive.night_veil.trail.
    attest_cell` (the effectful edge: runs every `NightVeilProbe` check, then calls `attest`, then
    records the result). Calls into nothing beyond the standard library and pydantic.

Key invariants:
    - `attest` is pure: no I/O, no clock, no store.
    - `Attestation.passed` is True if and only if `results` contains zero CheckStatus.FAIL entries;
      NOT_APPLICABLE and PASS both count as clearing a check.
    - `Attestation.red` lists every FAILing check's name, in `results`' own iteration order,
      never more and never fewer -- there is no partial pass (module docstring).

See Also:
    - docs/adr/0030-night-veil-retention-and-clearance-boundary.md for "any red check fails the
      placement... there is no degraded mode".
    - .claude/codingrules.md section 8.7 for the exact checks CHECK_NAMES lists.
    - hivemind.hive.night_veil.probe for NightVeilProbe, the Protocol CHECK_NAMES mirrors method
      for method.
    - hivemind.hive.night_veil.trail for attest_cell, the effectful caller of attest.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

MAX_DETAIL_CHARS = 500  # A one-line rationale, never a full command transcript (codingrules 12).

# Every hivemind.hive.night_veil.probe.NightVeilProbe method name, in the order codingrules 8.7
# and roadmap step 5.7b list them; also the key order attest_cell's own per-check payload uses, so
# the trail event and this module agree on ordering.
CHECK_NAMES: tuple[str, ...] = (
    "kill_switch_active",
    "default_route_via_tunnel",
    "tor_healthy",
    "tor_browser_launchable",
    "hidden_service_reachable",
    "waggle_socket_via_socks",
    "dns_leak_free",
    "direct_egress_blocked",
    "geolocation_denied",
    "metadata_unreachable",
    "timezone_utc",
    "locale_pinned",
    "webrtc_leak_blocked",
)

__all__ = ["CHECK_NAMES", "MAX_DETAIL_CHARS", "Attestation", "CheckResult", "CheckStatus", "attest"]

# codingrules 8.5: frozen, extra-forbidding config every model in this module shares.
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class CheckStatus(Enum):
    """One Night Veil check's own outcome."""

    PASS = "PASS"  # noqa: S105 (a check verdict, not a credential). Green: confirmed clean.
    FAIL = "FAIL"  # Red: the check ran and found the condition it guards against.
    NOT_APPLICABLE = "NOT_APPLICABLE"  # Documented skip (module docstring); counts as clearing.


class CheckResult(BaseModel):
    """One check's status plus a short, non-secret rationale.

    `detail` is logged and trailed verbatim (codingrules section 12: "log identifiers and sizes",
    never secrets or full output), so every `NightVeilProbe` implementation must keep it to a short
    fact -- an exit code, a matched or missing substring, never a raw command transcript.
    """

    model_config = _MODEL_CONFIG

    status: CheckStatus = Field(description="Whether this check passed, failed, or does not apply.")
    detail: str = Field(
        max_length=MAX_DETAIL_CHARS,
        description="A short, non-secret rationale: what made this check pass or fail.",
    )


class Attestation(BaseModel):
    """The all-or-nothing verdict `attest` computes from a full set of CheckResults.

    Attributes:
        results: Every check's own CheckResult, keyed by name (CHECK_NAMES).
        passed: True only when `results` holds zero CheckStatus.FAIL entries.
        red: Every FAILing check's name, in `results`' own order; empty when `passed`.
    """

    model_config = _MODEL_CONFIG

    results: Mapping[str, CheckResult] = Field(description="Every check's own result, by name.")
    passed: bool = Field(description="True iff results holds zero FAIL entries.")
    red: tuple[str, ...] = Field(description="Every FAILing check's name; empty when passed.")


def attest(results: Mapping[str, CheckResult]) -> Attestation:
    """Judge a full set of Night Veil CheckResults: all-green, or the list of red checks.

    Pure (codingrules section 8.3): no I/O, no clock, no store. Deterministic: the same `results`
    always yields the same `Attestation`.

    Args:
        results: Every check's own CheckResult, normally one per `CHECK_NAMES` entry -- but this
            function does not itself require that set to be complete; a caller that skipped a
            check simply never clears it (`red` is computed from what is actually present, and a
            missing name is neither PASS nor FAIL, so completeness is `attest_cell`'s own concern,
            not this pure function's).

    Returns:
        An Attestation whose `passed` is True iff no result is CheckStatus.FAIL, and whose `red`
        names every one that is, in `results`' own order.
    """
    red = tuple(name for name, result in results.items() if result.status is CheckStatus.FAIL)
    return Attestation(results=dict(results), passed=not red, red=red)
