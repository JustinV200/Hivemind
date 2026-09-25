"""Tests for the Queen's GuardRequestDoor and the inbox items her tick drains from its table.

Roadmap step 10.6a (ADR-0043): the Guard Bee files a request through the door; the door refuses a
report that asks for nothing, writes the request durably, wakes the Queen and decides nothing.
Each undecided request becomes one GUARD_REQUEST item under the Guard principal, aged from when
it was filed, with nothing a report names able to lift its score.

Fits into the Hive:
    Mirrors src/hivemind/queen/guard_requests/door.py and inbox.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.report for the GuardRequestDoor contract.
"""

from __future__ import annotations

import pytest
from builders.isolation import make_guard_report
from builders.queen import make_queen_deps

from hivemind.guard import GuardAction, GuardRequestDoor
from hivemind.queen import Queen
from hivemind.queen.guard_requests import (
    GUARD_REQUEST_PAYLOAD_KIND,
    QueenGuardDoor,
    guard_door,
    guard_items,
)
from hivemind.queen.human_inbox import HumanInbox
from hivemind.supervision.attendant import GUARD_PRINCIPAL, InboxKind
from waggle.clock import FakeClock


async def test_the_door_files_the_request_and_wakes_the_queen() -> None:
    clock = FakeClock()
    deps, _link, warden_end = make_queen_deps(clock)
    deps.wake.clear()
    report = make_guard_report(clock)

    await QueenGuardDoor(deps, HumanInbox()).file_guard_request(report)

    [request] = await deps.guard.requests.pending()
    assert request.report == report and request.filed_at == clock.now()
    assert request.decision is None  # The door decides nothing; her tick does.
    assert deps.wake.is_set()
    await warden_end.close()


async def test_the_door_refuses_a_report_that_asks_for_nothing() -> None:
    deps, _link, warden_end = make_queen_deps()
    report = make_guard_report(recommended=GuardAction.OBSERVE, cell_id=None)

    with pytest.raises(ValueError, match="asks the Queen for nothing"):
        await guard_door(deps, HumanInbox()).file_guard_request(report)
    assert await deps.guard.requests.pending() == ()
    await warden_end.close()


async def test_the_running_queen_is_a_guard_request_door_herself() -> None:
    deps, _link, warden_end = make_queen_deps()
    queen = Queen(deps)
    door: GuardRequestDoor = queen  # The composition root hands the Queen to the Guard Bee.
    report = make_guard_report()

    await door.file_guard_request(report)
    await door.file_guard_request(report)  # A retry files nothing twice.

    assert [request.id for request in await deps.guard.requests.pending()] == [report.id]
    await warden_end.close()


async def test_every_pending_request_is_one_guard_item_aged_from_its_filing() -> None:
    clock = FakeClock()
    deps, _link, warden_end = make_queen_deps(clock)
    first = make_guard_report(clock)
    await guard_door(deps, HumanInbox()).file_guard_request(first)
    filed_at = clock.now()
    clock.advance(30.0)
    await guard_door(deps, HumanInbox()).file_guard_request(make_guard_report(clock))

    items = await guard_items(deps)

    assert items[0].id == first.id
    assert {item.kind for item in items} == {InboxKind.GUARD_REQUEST}
    assert {item.principal for item in items} == {GUARD_PRINCIPAL}
    assert {item.payload_kind for item in items} == {GUARD_REQUEST_PAYLOAD_KIND}
    assert items[0].received_at == filed_at and items[0].payload == first
    assert all(item.task_id is None and item.latency_budget_s is None for item in items)
    await warden_end.close()
