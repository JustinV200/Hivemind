"""Define attest_propolis_cell: run every Propolis check and record the result as one event.

The Propolis counterpart of `hivemind.hive.night_veil.trail.attest_cell` (ADR-0057, carrying
ADR-0030's rule to this tier): the probe makes every check, `hive.night_veil.results.attest` is the
pure all-or-nothing judgement, and `hive.night_veil.trail.record_attestation` writes the one
`cell.attested` event, win or lose, naming the tier. What this function adds is the completeness
Night Veil gets from `run_checks` walking its list: a `PropolisProbe` returns a mapping, and
`attest` is satisfied by whatever it is given, so a probe that reported nothing, or left a check
out, would pass vacuously. Here every name in `PROPOLIS_CHECK_NAMES` that the probe did not report
is a failed check, and a check reported as not applicable counts as failed too: no Propolis check
has the documented skip Night Veil's WebRTC probe has, so "does not apply" can only be a probe that
did not look.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hive.propolis`. Called by `hivemind.hive.attestation.
    CellAttestation` for a PROPOLIS Cell, before the provider marks it ready. Calls into `hive.
    propolis.probe` (PropolisProbe, PROPOLIS_CHECK_NAMES), `hive.night_veil` (Attestation, attest,
    CheckResult, record_attestation), `hivemind.cell` (CombShieldLevel), `hivemind.pheromone`
    (TrailRecorder) and `waggle.ids` only.

Key invariants:
    - Exactly one `cell.attested` event is recorded, red or green, with every expected check in it.
    - The attestation passes only when every name in `PROPOLIS_CHECK_NAMES` was reported and
      passed; a missing check or a not-applicable one is red.
    - A probe that raises records nothing: the caller (the provider) refuses and tears down.

See Also:
    - docs/adr/0057-propolis-is-verified-by-looking.md for the decision.
    - hivemind.hive.night_veil.trail for attest_cell and record_attestation.
"""

from __future__ import annotations

from collections.abc import Mapping

from hivemind.cell import CombShieldLevel
from hivemind.hive.night_veil import (
    Attestation,
    CheckResult,
    CheckStatus,
    attest,
    record_attestation,
)
from hivemind.hive.propolis.probe import PROPOLIS_CHECK_NAMES, PropolisProbe
from hivemind.pheromone import TrailRecorder
from waggle.ids import CellId

_MISSING = CheckResult(status=CheckStatus.FAIL, detail="the probe did not report this check")
_NOT_A_PASS = CheckResult(
    status=CheckStatus.FAIL, detail="not applicable is not a pass at the Propolis tier"
)

__all__ = ["attest_propolis_cell"]


async def attest_propolis_cell(
    probe: PropolisProbe, trail: TrailRecorder, cell_id: CellId
) -> Attestation:
    """Run every Propolis check on `probe`, judge the result, and record it for `cell_id`.

    Args:
        probe: The PropolisProbe to run (a `SessionPropolisProbe` over the Cell's session, or a
            `FakePropolisProbe`).
        trail: The Queen-side trail-writer identity the `cell.attested` event is written with.
        cell_id: The Propolis Cell being attested; the event's own `subject_id`.

    Returns:
        The Attestation: `passed`, and when it is not, `red` naming every failing check. The
        caller decides what to do with a red one (refuse the Cell, tear it down); this function
        only runs the checks and records the result.
    """
    attestation = attest(_complete(await probe.checks()))
    await record_attestation(trail, cell_id, CombShieldLevel.PROPOLIS, attestation)
    return attestation


def _complete(reported: Mapping[str, CheckResult]) -> dict[str, CheckResult]:
    """Make the probe's report a full one: every expected check, in order, none vacuously passed."""
    results = {name: _strict(reported.get(name, _MISSING)) for name in PROPOLIS_CHECK_NAMES}
    # Anything else the probe reported (platform_supported, when no commands fit the Cell's
    # operating system) follows the expected names, so the record shows why they could not run.
    results.update(
        (name, _strict(result)) for name, result in reported.items() if name not in results
    )
    return results


def _strict(result: CheckResult) -> CheckResult:
    """Return `result`, except that a check that does not apply fails: none here may be skipped."""
    return _NOT_A_PASS if result.status is CheckStatus.NOT_APPLICABLE else result
