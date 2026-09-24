"""Tests for hivemind.workers.roles.guard_bee.requests: the floor, coalescing and the hourly cap.

A report below `[guard] request_confidence` is never filed; a repeat of one rule against one
target inside the coalescing window folds into the request already filed; no more than
`[guard] requests_per_hour` are filed in any hour; only a filed request counts toward either; and
a ledger restored from the Guard Bee's own alerts after a restart applies the same limits. A
CRITICAL request is filed past coalescing and the cap. Over a whole Guard Bee, each limit is
still a `guard.alert`, recorded with what became of it.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/guard_bee/requests.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.guard_bee.requests for RequestLedger.
    - docs/adr/0035-guard-bee-requests-queen-only-isolation-and-tainted-memory.md.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from builders.guard_bee import RigOptions, make_guard_bee, seed_episode

from hivemind.guard import GuardAction, GuardConfidence, GuardReport, new_guard_report_id
from hivemind.manifest.schema.guard import GuardBeeSection, GuardSection
from hivemind.workers.roles.guard_bee import Disposition, RequestLedger, request_target
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_event_id, new_worker_id

_T0 = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)  # When the first request in a test is filed.
_CLOCK = FakeClock()  # Mints ids only.


def _report(confidence: GuardConfidence = GuardConfidence.HIGH, **fields: object) -> GuardReport:
    base: dict[str, object] = {
        "id": new_guard_report_id(_CLOCK),
        "rule": "denial_burst",
        "event_ids": (new_event_id(_CLOCK),),
        "cell_id": new_cell_id(_CLOCK),
        "bee_ids": (new_worker_id(_CLOCK),),
        "recommended": GuardAction.QUARANTINE_BEE,
        "confidence": confidence,
        "filed_at": _T0,
        "summary": "a burst of Guard refusals for one bee",
    }
    return GuardReport.model_validate({**base, **fields})


def _ledger(per_hour: int = 6, coalesce_s: float = 900.0) -> RequestLedger:
    return RequestLedger(GuardConfidence.HIGH, per_hour, coalesce_s)


def test_a_report_below_the_floor_is_never_filed_whatever_else_holds() -> None:
    ledger = _ledger()

    assert ledger.admit(_report(GuardConfidence.MEDIUM), _T0) is Disposition.BELOW_FLOOR
    assert ledger.admit(_report(GuardConfidence.CRITICAL), _T0) is Disposition.FILED


def test_a_repeat_against_one_target_coalesces_inside_the_window_only() -> None:
    ledger = _ledger()
    first = _report()
    ledger.record(first, _T0)
    repeat = _report(cell_id=first.cell_id)

    inside = ledger.admit(repeat, _T0 + timedelta(minutes=14))
    after = ledger.admit(repeat, _T0 + timedelta(minutes=15))
    elsewhere = ledger.admit(_report(), _T0 + timedelta(minutes=1))

    assert inside is Disposition.COALESCED
    assert after is Disposition.FILED
    assert elsewhere is Disposition.FILED  # Another Cell is another target.


def test_the_hourly_cap_counts_only_the_last_hour_of_filed_requests() -> None:
    ledger = _ledger(per_hour=2, coalesce_s=0.0)
    ledger.record(_report(), _T0)
    ledger.record(_report(), _T0 + timedelta(minutes=10))

    capped = ledger.admit(_report(), _T0 + timedelta(minutes=30))
    freed = ledger.admit(_report(), _T0 + timedelta(minutes=61))

    assert capped is Disposition.CAPPED
    assert freed is Disposition.FILED


def test_a_critical_request_is_filed_past_coalescing_and_the_cap() -> None:
    ledger = _ledger(per_hour=1)
    first = _report()
    ledger.record(first, _T0)
    critical = _report(GuardConfidence.CRITICAL, cell_id=first.cell_id)

    admitted = ledger.admit(critical, _T0 + timedelta(minutes=1))

    # The same target inside the window, and the hour's one request already filed: still filed.
    assert admitted is Disposition.FILED


def test_a_restored_ledger_applies_the_same_limits_after_a_restart() -> None:
    ledger = _ledger(per_hour=1)
    report = _report()

    ledger.restore(report.rule, request_target(report), _T0)

    assert ledger.admit(_report(cell_id=report.cell_id), _T0 + timedelta(minutes=5)) is (
        Disposition.COALESCED
    )
    assert ledger.admit(_report(), _T0 + timedelta(minutes=5)) is Disposition.CAPPED


def test_a_request_aims_at_its_cell_else_its_first_bee_or_task() -> None:
    bee = new_worker_id(_CLOCK)

    assert request_target(_report(cell_id=None, bee_ids=(bee,))) == bee


async def test_over_a_guard_bee_every_limited_request_is_still_an_alert() -> None:
    guard = GuardSection(requests_per_hour=1, bee=GuardBeeSection(coalesce_window_s=900.0))
    rig = make_guard_bee(RigOptions(guard=guard))
    first, second = await seed_episode(rig.seed), await seed_episode(rig.seed)
    for episode in (first, second):
        await rig.seed.injection(episode.bee, episode.task)
        await rig.seed.denied(episode.bee)

    await rig.bee.tick()

    dispositions = sorted(str(alert.payload["disposition"]) for alert in await rig.alerts())
    assert dispositions == [Disposition.CAPPED.value, Disposition.FILED.value]
    assert len(rig.door.filed) == 1


async def test_over_a_guard_bee_a_report_below_the_floor_never_reaches_the_door() -> None:
    rig = make_guard_bee(RigOptions(guard=GuardSection(request_confidence="critical")))
    episode = await seed_episode(rig.seed)
    await rig.seed.injection(episode.bee, episode.task)
    await rig.seed.denied(episode.bee)

    await rig.bee.tick()

    [alert] = await rig.alerts()
    assert alert.payload["disposition"] == Disposition.BELOW_FLOOR.value
    assert rig.door.filed == []


async def test_over_a_guard_bee_a_second_burst_on_one_cell_is_coalesced() -> None:
    rig = make_guard_bee()
    episode = await seed_episode(rig.seed)
    await rig.seed.injection(episode.bee, episode.task)
    await rig.seed.denied(episode.bee)
    await rig.bee.tick()

    await rig.seed.injection(episode.bee, episode.task)  # A new flag, after the first report.
    await rig.seed.denied(episode.bee)
    await rig.round()

    dispositions = [alert.payload["disposition"] for alert in await rig.alerts()]
    assert dispositions == [Disposition.FILED.value, Disposition.COALESCED.value]
    assert len(rig.door.filed) == 1
