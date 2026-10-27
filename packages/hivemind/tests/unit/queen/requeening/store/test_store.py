"""Tests for hivemind.queen.requeening.store: both RequeeningStores keep the same contract.

The snapshot is replaced, never accumulated; a handled row that carries a question's wiring is
never lost to a later plain note for the same envelope, and a window query and a prune cut at the
right instant; a listener's port is remembered by name. Each case runs against the in-process
store and the SQLite one, so the Queen's tests (on the first) prove what production (the second)
does.

Fits into the Hive:
    Mirrors src/hivemind/queen/requeening/store/ (codingrules section 3): protocol, memory and
    sqlite, as one contract.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.requeening.store for the stores under test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest

from hivemind.common.sqlite import connect
from hivemind.queen.requeening import (
    HandledEnvelope,
    MemoryRequeeningStore,
    OpenLease,
    QueenSnapshot,
    QuestionWiring,
    RequeeningStore,
    SqliteRequeeningStore,
)
from waggle.clock import FakeClock

_CLOCK = FakeClock()


@pytest.fixture(params=["memory", "sqlite"])
async def store(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[RequeeningStore]:
    """Each RequeeningStore implementation, fresh."""
    if request.param == "memory":
        yield MemoryRequeeningStore()
        return
    connection = connect(tmp_path / "hive.sqlite3")
    try:
        yield await SqliteRequeeningStore.create(connection, _CLOCK)
    finally:
        connection.close()


def _snapshot(lease_id: str) -> QueenSnapshot:
    """A snapshot with one open lease named `lease_id`."""
    now = _CLOCK.now()
    lease = OpenLease(
        lease_id=lease_id, cell_id="cell_x", holder="warden_x", task_id=None, leased_at=now
    )
    return QueenSnapshot(taken_at=now, cursor=now, open_leases=(lease,), wardens=("warden_x",))


async def test_only_the_newest_snapshot_is_kept(store: RequeeningStore) -> None:
    assert await store.latest_snapshot() is None

    await store.put_snapshot(_snapshot("lease_a"))
    await store.put_snapshot(_snapshot("lease_b"))

    latest = await store.latest_snapshot()
    assert latest is not None
    assert [lease.lease_id for lease in latest.open_leases] == ["lease_b"]


async def test_a_questions_wiring_survives_a_later_plain_note_and_the_window_cuts_cleanly(
    store: RequeeningStore,
) -> None:
    now = _CLOCK.now()
    wiring = QuestionWiring(chamber_question_id="msg_c", wire_question_id="msg_w", task_id="task_t")
    old = HandledEnvelope(principal="warden_a", envelope_id="msg_old", handled_at=now)
    wired = HandledEnvelope(
        principal="warden_a", envelope_id="msg_q", handled_at=now, question=wiring
    )
    later = timedelta(seconds=1)

    await store.note_handled(old)
    await store.note_handled(wired)
    # The tick notes every item it handled, after the question path wrote its wiring.
    await store.note_handled(wired.model_copy(update={"question": None, "handled_at": now + later}))
    kept = {row.envelope_id: row for row in await store.handled_since(now)}
    pruned = await store.prune_handled(now + later)  # Strictly before the instant: both go.

    assert kept["msg_q"].question == wiring
    assert set(kept) == {"msg_old", "msg_q"}
    assert pruned == 2
    assert await store.handled_since(now) == ()


async def test_a_listeners_port_is_remembered_by_name(store: RequeeningStore) -> None:
    assert await store.recall_port("virtual_cells") is None

    await store.remember_port("virtual_cells", 8711)
    await store.remember_port("virtual_cells", 8712)

    assert await store.recall_port("virtual_cells") == 8712
    assert await store.recall_port("swarm") is None
