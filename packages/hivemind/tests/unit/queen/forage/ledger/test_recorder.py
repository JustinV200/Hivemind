"""Tests for hivemind.queen.forage.ledger.recorder: LedgerRecorder.

Fits into the Hive:
    Mirrors src/hivemind/queen/forage/ledger/recorder.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.forage.ledger.recorder for the module under test.
"""

from __future__ import annotations

from builders.forage import make_grant

from hivemind.forage.grant_state import GrantState
from hivemind.queen.forage.ledger.book import ForageLedger
from hivemind.queen.forage.ledger.recorder import LedgerRecorder
from waggle.clock import FakeClock


async def test_call_started_and_finished_feed_the_ledgers_own_seat_book() -> None:
    ledger = ForageLedger()
    await ledger.seats.set_capacity("src_1", 2)
    recorder = LedgerRecorder(ledger)

    await recorder.call_started("src_1", "fake")

    assert ledger.seats.in_use("src_1") == 1

    await recorder.call_finished("src_1", "fake")

    assert ledger.seats.in_use("src_1") == 0


async def test_call_started_with_no_source_id_is_a_no_op() -> None:
    ledger = ForageLedger()
    recorder = LedgerRecorder(ledger)

    await recorder.call_started(None, "fake")  # Must not raise, and touches no source.

    assert ledger.seats.in_use("") == 0


async def test_six_calls_against_a_two_seat_source_never_show_more_than_two_in_use() -> None:
    ledger = ForageLedger()
    await ledger.seats.set_capacity("src_1", 2)
    recorder = LedgerRecorder(ledger)

    for _ in range(6):
        await recorder.call_started("src_1", "fake")
        assert ledger.seats.in_use("src_1") <= 2
        await recorder.call_finished("src_1", "fake")

    assert ledger.seats.in_use("src_1") == 0


async def test_record_ignores_every_kind_other_than_llm_call() -> None:
    ledger = ForageLedger()
    recorder = LedgerRecorder(ledger)

    await recorder.record("llm.spill", "evt_1", {"grant_id": "g1", "goal_id": "t1"})

    assert ledger.spend.for_goal("t1") == 0.0  # type: ignore[arg-type]


async def test_record_attributes_llm_call_cost_to_the_grant_and_goal_when_both_are_given() -> None:
    clock = FakeClock()
    ledger = ForageLedger()
    grant = make_grant(clock=clock, state=GrantState.ACTIVE, spent=0.0)
    await ledger.record_grant(grant)
    recorder = LedgerRecorder(ledger)

    await recorder.record(
        "llm.call",
        "evt_1",
        {
            "slot": "WORKER",
            "provider": "fake",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cached_tokens": 0,
                "cost_usd": 0.25,
            },
            "grant_id": grant.id,
            "goal_id": "task_abc",
        },
    )

    assert ledger.spend.for_goal("task_abc") == 0.25  # type: ignore[arg-type]
    updated = ledger.grant(grant.id)
    assert updated is not None
    assert updated.spent == 0.25


async def test_record_ignores_llm_call_with_no_attribution() -> None:
    ledger = ForageLedger()
    recorder = LedgerRecorder(ledger)

    await recorder.record(
        "llm.call",
        "evt_1",
        {
            "slot": "WORKER",
            "provider": "fake",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cached_tokens": 0,
                "cost_usd": 0.25,
            },
        },
    )

    # Nothing to attribute to: no grant_id, no goal_id on this payload.
    assert ledger.live_grants() == ()
