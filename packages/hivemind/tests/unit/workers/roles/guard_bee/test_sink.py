"""Tests for hivemind.workers.roles.guard_bee.sink: every Guard report deposited at C2 in place.

A report naming a Cell belongs in that Cell's folder of the Honey browser, one naming none in the
Hive's; a deposit is C2 unless said otherwise; the in-memory sink keeps deposits in order; and
over a Guard Bee, every alert it records has exactly one deposit of the same report.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/guard_bee/sink.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.guard_bee.sink for GuardDeposit and InMemoryGuardReportSink.
"""

from __future__ import annotations

from builders.guard_bee import make_guard_bee, seed_episode

from hivemind.cell import HoneyClearance
from hivemind.guard import GuardAction, GuardConfidence, GuardReport, new_guard_report_id
from hivemind.workers.roles.guard_bee import HIVE_SCOPE, GuardDeposit, InMemoryGuardReportSink
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_event_id

_CLOCK = FakeClock()  # Mints every id and time here.


def _report(cell: str | None) -> GuardReport:
    return GuardReport(
        id=new_guard_report_id(_CLOCK),
        rule="denial_burst",
        event_ids=(new_event_id(_CLOCK),),
        cell_id=cell,
        recommended=GuardAction.OBSERVE,
        confidence=GuardConfidence.LOW,
        filed_at=_CLOCK.now(),
        summary="a burst of Guard refusals",
    )


def test_a_report_naming_a_cell_belongs_in_its_folder_and_one_naming_none_in_the_hives() -> None:
    cell = new_cell_id(_CLOCK)

    assert GuardDeposit(_report(cell)).scope == f"/cells/{cell}"
    assert GuardDeposit(_report(None)).scope == HIVE_SCOPE


def test_a_deposit_is_c2_unless_said_otherwise() -> None:
    assert GuardDeposit(_report(None)).clearance is HoneyClearance.C2


async def test_the_in_memory_sink_keeps_every_deposit_in_order() -> None:
    sink = InMemoryGuardReportSink()
    first, second = GuardDeposit(_report(None)), GuardDeposit(_report(None))

    await sink.deposit(first)
    await sink.deposit(second)

    assert sink.deposits == [first, second]


async def test_every_alert_a_guard_bee_records_has_one_deposit_of_its_report() -> None:
    rig = make_guard_bee()
    episode = await seed_episode(rig.seed)
    await rig.seed.injection(episode.bee, episode.task)
    await rig.seed.denied(episode.bee)
    for reason in ("request_replay", "request_signature"):
        await rig.seed.entrance("guard.entrance_login_failed", reason=reason, listener="remote")

    await rig.bee.tick()

    alerted = sorted(str(alert.payload["report_id"]) for alert in await rig.alerts())
    deposited = sorted(deposit.report.id for deposit in rig.sink.deposits)
    assert len(alerted) == 2 and alerted == deposited
    assert {deposit.scope for deposit in rig.sink.deposits} == {f"/cells/{episode.cell}", "/hive"}
