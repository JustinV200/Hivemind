"""Tests for hivemind.wardens.offline.diagnose.report: what a finished search leaves behind.

Fits into the Hive:
    Mirrors src/hivemind/wardens/offline/diagnose/report.py (codingrules section 3). The Nectar
    document says the outcome and what each Drone reported; the deposit is C2 Nectar the Warden
    sends itself, belonging to no task and no Worker; the `warden.diagnosed` event carries the
    outcome, a stable word and counts, and never a word a Drone wrote.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.offline.diagnose.report for the module under test.
    - docs/adr/0056-active-reconnection-diagnostic-drones.md for why the findings are Nectar.
"""

from __future__ import annotations

from builders.diagnose import diagnosed, nectar_text, says, start_nuc_scene

from hivemind.wardens.offline.diagnose import (
    Finding,
    Judged,
    Verdict,
    deposit_findings,
    findings_document,
    record_verdict,
)
from waggle.messages import CombShieldLevel, HoneyClearance
from waggle.messages.honey import NectarKind


def test_the_document_opens_with_the_outcome_and_the_host_then_each_drones_report() -> None:
    findings = (Finding(None, "CAUSE: none found\nAll fine."), Finding("x", "CAUSE: x\nFixed it."))

    document = findings_document(Verdict.FIXED, "hive.example", findings)

    assert document.startswith("# The link to hive.example was lost: FIXED\n")
    assert document.index("## Drone 1") < document.index("All fine.")
    assert document.index("## Drone 2") < document.index("Fixed it.")
    assert document.endswith("\n")


def test_a_drone_that_wrote_nothing_is_said_to_have_reported_nothing() -> None:
    document = findings_document(Verdict.NO_CAUSE, "", (Finding(None, "  \n"),))

    assert document.startswith("# The link was lost: NO_CAUSE\n")
    assert "The Drone reported nothing." in document


def test_a_search_stopped_before_any_drone_said_anything_still_has_a_document() -> None:
    document = findings_document(Verdict.NO_CAUSE, "hive.example", ())

    assert "The Drone reported nothing." in document and "## Drone" not in document


async def test_the_findings_are_c2_nectar_the_warden_itself_deposits_for_no_task() -> None:
    scene = await start_nuc_scene(says("CAUSE: none found"))

    assert await deposit_findings(scene.warden, "Why it went.\n") is True

    assert await nectar_text(scene) == "Why it went.\n"
    [chunk] = scene.queen.nectar
    assert chunk.kind is NectarKind.FINDING
    assert chunk.clearance is HoneyClearance.C2  # It describes the operator's machine.
    assert chunk.task_id is None and chunk.worker_id is None
    assert chunk.origin_tier is CombShieldLevel.MEADOW
    cell = scene.warden._cell
    assert cell is not None and chunk.cell_id == cell.id
    assert chunk.title == "Why the link to the Queen was lost"
    await scene.stop()


async def test_a_warden_with_no_cell_deposits_nothing() -> None:
    scene = await start_nuc_scene(says("CAUSE: none found"))
    scene.warden._cell = None

    assert await deposit_findings(scene.warden, "Why it went.\n") is False

    assert scene.queen.nectar == []
    await scene.stop()


async def test_the_event_carries_the_outcome_and_counts_and_no_word_a_drone_wrote() -> None:
    scene = await start_nuc_scene(says("CAUSE: none found"))

    await record_verdict(scene.warden, Judged(Verdict.DIAGNOSED, "cause_known", 2, 12.3456, True))

    [event] = await diagnosed(scene)
    assert event.payload == {
        "outcome": "DIAGNOSED",
        "reason": "cause_known",
        "attempts": 2,
        "spent_s": 12.346,
        "deposited": True,
    }
    assert event.subject_id == scene.warden._warden_id
    await scene.stop()
