"""Define attest_cell: run every Night Veil check and record the result as one cell.attested event.

Roadmap step 5.7b: "attest_cell(probe, trail, cell_id) -> Attestation records one cell.attested
trail event carrying per-check pass/fail (this is one of the kinds that survives teardown,
codingrules 12)." The effectful edge (codingrules section 8.3): `hive.night_veil.runner.run_checks`
does every await, `hive.night_veil.results.attest` is the pure judgement, and this function's own
job is only to glue the two together and record the outcome -- it performs no I/O of its own beyond
the one `trail.trail.record` call. `trail` is a `hivemind.pheromone.TrailRecorder` (trail, clock,
hive_id, node_id bundled as one value, mirroring `hivemind.pheromone.retention.
NightVeilTeardownPurge`'s own use of the same shape for its `cell.purged` skeleton event), not a
bare `PheromoneTrail`, because a `CellEvent` needs all four of those to exist at all.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hive.night_veil`. Called by whatever gates `CellReady` for
    a NIGHT_VEIL Cell before it is handed to placement (a report item: the exact call site is
    `hivemind.queen.cell_gate.provider.LifecycleVirtualCellProvider.acquire`, before `mark_ready`,
    tearing the Cell down on a red result -- outside this dispatch's file list, so this module's own
    report names the call to add there rather than adding it). Calls into `hive.night_veil.results`
    (Attestation, attest), `hive.night_veil.runner` (run_checks), `hive.night_veil.probe`
    (NightVeilProbe), `hivemind.pheromone` (CellEvent, TrailRecorder) and `waggle.ids` only.

Key invariants:
    - `attest_cell` records exactly one `cell.attested` event, win or lose: a red attestation is
      still recorded, with every check's own status, so the trail explains why placement failed
      (ADR-0030: "a failed attestation is a per-check trail record, not a silent downgrade").
    - The recorded payload carries only each check's `CheckStatus` value and `detail` (already
      bounded and non-secret, `hive.night_veil.results.CheckResult`'s own contract) plus the
      overall `passed`/`red` verdict -- never a raw command transcript (codingrules section 12).

See Also:
    - .claude/codingrules.md section 12 for "cell.attested (pass or fail, per check)" among the
      Night Veil skeleton kinds that survive teardown.
    - .claude/roadmap.md step 5.7b for this module's own roadmap bullet.
    - hivemind.hive.night_veil.results for Attestation and attest, the pure judgement this wraps.
    - hivemind.hive.night_veil.runner for run_checks, which does every check's own await.
    - hivemind.pheromone.retention for TrailRecorder, the same value shape this module reuses.
"""

from __future__ import annotations

from pydantic import JsonValue

from hivemind.hive.night_veil.probe import NightVeilProbe
from hivemind.hive.night_veil.results import Attestation, attest
from hivemind.hive.night_veil.runner import run_checks
from hivemind.pheromone import CellEvent, TrailRecorder
from waggle.ids import CellId, new_event_id

_ATTESTED_KIND = "cell.attested"  # Already in CellEvent.KINDS (hivemind.pheromone.events.families).

__all__ = ["attest_cell"]


async def attest_cell(probe: NightVeilProbe, trail: TrailRecorder, cell_id: CellId) -> Attestation:
    """Run every Night Veil check on `probe`, judge the result, and record it for `cell_id`.

    Args:
        probe: The NightVeilProbe to run every check against (a `FakeNightVeilProbe` before
            `images/night-veil-ubuntu` exists, a `SessionNightVeilProbe` once it does).
        trail: The Queen-side trail-writer identity (trail, clock, hive_id, node_id) the recorded
            `cell.attested` event is written with.
        cell_id: The Night Veil Cell being attested; the event's own `subject_id`.

    Returns:
        The Attestation: `passed` and, when it is not, `red` naming every failing check. The
        caller decides what to do with a failed Attestation (fail placement, destroy the Cell);
        this function only runs the checks and records the result.
    """
    results = await run_checks(probe)
    attestation = attest(results)
    await trail.trail.record(_attested_event(trail, cell_id, attestation))
    return attestation


def _attested_event(trail: TrailRecorder, cell_id: CellId, attestation: Attestation) -> CellEvent:
    """Build the cell.attested CellEvent: every check's own status and detail, plus the verdict."""
    payload: dict[str, JsonValue] = {
        name: {"status": result.status.value, "detail": result.detail}
        for name, result in attestation.results.items()
    }
    payload["passed"] = attestation.passed
    payload["red"] = list(attestation.red)
    return CellEvent(
        id=new_event_id(trail.clock),
        hive_id=trail.hive_id,
        node_id=trail.node_id,
        at=trail.clock.now(),
        actor="system",
        kind=_ATTESTED_KIND,
        subject_id=cell_id,
        payload=payload,
    )
