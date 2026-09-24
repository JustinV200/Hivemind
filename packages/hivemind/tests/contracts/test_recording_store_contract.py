"""Contract suite for RecordingStore: one contract, run over both implementations.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Each test states one clause of the
    hivemind.exoskeleton.recorder.store.RecordingStore contract (the flight recorder's store,
    roadmap step 6.6) and runs against both implementations that ship:
    hivemind.exoskeleton.recorder.store.InMemoryRecordingStore and
    hivemind.exoskeleton.recorder.sqlite.SqliteRecordingStore (on a real tmp_path SQLite file). A
    new implementation joins the fixture's params and must pass here before it is used anywhere
    else (codingrules 14.3).

Key invariants:
    - None: this module holds tests only. The retention clauses (`prune_before`) run on every
      store that offers it; `InMemoryRecordingStore` does not yet (its module is outside this
      step's files), so those clauses skip for it with that reason until it does.

See Also:
    - hivemind.exoskeleton.recorder.store for the RecordingStore protocol under test.
    - packages/hivemind/tests/contracts/test_memory_store_contract.py for the pattern this mirrors.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol, runtime_checkable

import pytest
from builders.recordings import (
    LOGIN_GUI,
    RECORDING_START,
    make_recorded_action,
    make_recording_info,
)

from hivemind.common.sqlite import connect
from hivemind.exoskeleton.errors import RecordingNotFoundError
from hivemind.exoskeleton.recorder import (
    MASK,
    Evidence,
    InMemoryRecordingStore,
    RecordingStore,
    SqliteRecordingStore,
)
from waggle.clock import FakeClock
from waggle.messages.capping import GuiOp

_STORE_KINDS = ("memory", "sqlite")
_HOUR = timedelta(hours=1)


@runtime_checkable
class _Prunable(Protocol):
    """A store that also runs the retention sweep (SqliteRecordingStore.prune_before)."""

    async def prune_before(self, cutoff: datetime) -> int:
        """Delete every recording with nothing in it at or after `cutoff`; return how many."""
        ...


@pytest.fixture(params=_STORE_KINDS)
async def store(request: pytest.FixtureRequest, tmp_path: Path) -> RecordingStore:
    """A RecordingStore of the parametrised kind; the SQLite one on a fresh file."""
    if request.param == "memory":
        return InMemoryRecordingStore()
    return await SqliteRecordingStore.create(connect(tmp_path / "hive.sqlite3"), FakeClock())


def _prunable(store: RecordingStore) -> _Prunable:
    """Return `store` as a store with a retention sweep, or skip the clause for one without."""
    if not isinstance(store, _Prunable):
        pytest.skip(
            "InMemoryRecordingStore has no prune_before yet (hivemind.exoskeleton.recorder.store "
            "is outside this step's files); the clause runs on every store that offers it"
        )
    return store


# ──────────────────────────────────────────────────────────────────────────────
# open / add / actions
# ──────────────────────────────────────────────────────────────────────────────


async def test_open_twice_keeps_the_first_header(store: RecordingStore) -> None:
    first = make_recording_info()

    await store.open(first)
    await store.open(first.model_copy(update={"task_id": "task_other"}))

    assert await store.info(first.recording_id) == first
    assert await store.recordings() == (first,)


async def test_open_twice_keeps_the_actions_already_added(store: RecordingStore) -> None:
    info = make_recording_info()
    action = make_recorded_action()
    await store.open(info)
    await store.add(info.recording_id, action)

    await store.open(info)

    assert await store.actions(info.recording_id) == (action,)


async def test_actions_come_back_in_the_order_they_were_added(store: RecordingStore) -> None:
    info = make_recording_info()
    await store.open(info)
    # Deliberately not in proposal-id order, so the store cannot pass by sorting on anything.
    added = [make_recorded_action(pid, at=RECORDING_START) for pid in ("p_c", "p_a", "p_b")]

    for action in added:
        await store.add(info.recording_id, action)

    assert [a.proposal_id for a in await store.actions(info.recording_id)] == ["p_c", "p_a", "p_b"]


async def test_a_recording_with_no_actions_reads_back_empty(store: RecordingStore) -> None:
    info = make_recording_info()

    await store.open(info)

    assert await store.actions(info.recording_id) == ()


async def test_frames_round_trip_byte_identical(store: RecordingStore) -> None:
    info = make_recording_info()
    action = make_recorded_action()
    await store.open(info)

    await store.add(info.recording_id, action)
    (stored,) = await store.actions(info.recording_id)

    assert stored == action
    assert stored.before.frame is not None and action.before.frame is not None
    assert stored.before.frame.png == action.before.frame.png
    assert stored.after is not None and action.after is not None
    assert stored.after.frame is not None and action.after.frame is not None
    assert stored.after.frame.png == action.after.frame.png
    assert stored.after.frame.sha256 == action.after.frame.sha256


async def test_the_typed_redacted_steps_round_trip(store: RecordingStore) -> None:
    # The typed steps (roadmap step 6.7 exports them as a BrowserProcedure) come back equal, the
    # secret fill still marked secret and its text still only the mask.
    info = make_recording_info()
    await store.open(info)

    await store.add(info.recording_id, make_recorded_action())
    (stored,) = await store.actions(info.recording_id)

    assert stored.gui == LOGIN_GUI
    fill = stored.gui[0]
    assert (fill.op, fill.secret, fill.text) == (GuiOp.BROWSER_FILL, True, MASK)
    assert MASK == "[redacted]"


async def test_an_action_rejected_before_it_ran_round_trips_without_evidence(
    store: RecordingStore,
) -> None:
    info = make_recording_info()
    rejected = make_recorded_action(state="REJECTED").model_copy(
        update={"before": Evidence(), "after": None, "postconditions": ()}
    )
    await store.open(info)

    await store.add(info.recording_id, rejected)

    assert await store.actions(info.recording_id) == (rejected,)


# ──────────────────────────────────────────────────────────────────────────────
# Not found
# ──────────────────────────────────────────────────────────────────────────────


async def test_add_to_a_recording_never_opened_raises(store: RecordingStore) -> None:
    with pytest.raises(RecordingNotFoundError) as caught:
        await store.add("rec_missing", make_recorded_action())

    assert caught.value.recording_id == "rec_missing"


async def test_info_of_a_recording_never_opened_raises(store: RecordingStore) -> None:
    with pytest.raises(RecordingNotFoundError):
        await store.info("rec_missing")


async def test_actions_of_a_recording_never_opened_raises(store: RecordingStore) -> None:
    with pytest.raises(RecordingNotFoundError):
        await store.actions("rec_missing")


# ──────────────────────────────────────────────────────────────────────────────
# recordings
# ──────────────────────────────────────────────────────────────────────────────


async def test_recordings_are_listed_newest_first(store: RecordingStore) -> None:
    oldest = make_recording_info("rec_1", started_at=RECORDING_START)
    middle = make_recording_info("rec_2", started_at=RECORDING_START + _HOUR)
    newest = make_recording_info("rec_3", started_at=RECORDING_START + 2 * _HOUR)
    # Opened out of time order, so insertion order cannot pass for time order.
    for info in (middle, newest, oldest):
        await store.open(info)

    assert await store.recordings() == (newest, middle, oldest)


async def test_recordings_filters_by_cell_and_honours_the_limit(store: RecordingStore) -> None:
    a_old = make_recording_info("rec_a1", cell_id="cell_a", started_at=RECORDING_START)
    a_mid = make_recording_info("rec_a2", cell_id="cell_a", started_at=RECORDING_START + _HOUR)
    a_new = make_recording_info("rec_a3", cell_id="cell_a", started_at=RECORDING_START + 3 * _HOUR)
    b_new = make_recording_info("rec_b1", cell_id="cell_b", started_at=RECORDING_START + 2 * _HOUR)
    for info in (a_old, b_new, a_new, a_mid):
        await store.open(info)

    assert await store.recordings(cell_id="cell_a") == (a_new, a_mid, a_old)
    assert await store.recordings(cell_id="cell_a", limit=2) == (a_new, a_mid)
    assert await store.recordings(limit=2) == (a_new, b_new)
    assert await store.recordings(cell_id="cell_nobody") == ()


# ──────────────────────────────────────────────────────────────────────────────
# purge_cell (the Night Veil boundary)
# ──────────────────────────────────────────────────────────────────────────────


async def test_purge_cell_removes_that_cells_recordings_and_their_actions(
    store: RecordingStore,
) -> None:
    later = RECORDING_START + _HOUR
    doomed = (
        make_recording_info("rec_v1", cell_id="cell_v"),
        make_recording_info("rec_v2", cell_id="cell_v", started_at=later),
    )
    kept = make_recording_info("rec_k", cell_id="cell_k")
    for info in (*doomed, kept):
        await store.open(info)
        await store.add(info.recording_id, make_recorded_action())

    purged = await store.purge_cell("cell_v")

    assert purged == 2
    for info in doomed:
        with pytest.raises(RecordingNotFoundError):
            await store.actions(info.recording_id)
    assert await store.recordings() == (kept,)
    assert len(await store.actions(kept.recording_id)) == 1


async def test_purge_cell_of_a_cell_with_no_recordings_returns_zero(store: RecordingStore) -> None:
    await store.open(make_recording_info(cell_id="cell_a"))

    assert await store.purge_cell("cell_b") == 0
    assert len(await store.recordings()) == 1


# ──────────────────────────────────────────────────────────────────────────────
# prune_before (the retention sweep)
# ──────────────────────────────────────────────────────────────────────────────


async def test_prune_before_removes_recordings_with_nothing_after_the_cutoff(
    store: RecordingStore,
) -> None:
    prunable = _prunable(store)
    stale = make_recording_info("rec_old", started_at=RECORDING_START)
    fresh = make_recording_info("rec_new", started_at=RECORDING_START + 48 * _HOUR)
    for info in (stale, fresh):
        await store.open(info)
        await store.add(info.recording_id, make_recorded_action(at=info.started_at))

    pruned = await prunable.prune_before(RECORDING_START + 24 * _HOUR)

    assert pruned == 1
    assert await store.recordings() == (fresh,)
    with pytest.raises(RecordingNotFoundError):
        await store.actions(stale.recording_id)


async def test_prune_before_keeps_a_recording_whose_last_action_is_after_the_cutoff(
    store: RecordingStore,
) -> None:
    prunable = _prunable(store)
    live = make_recording_info("rec_live", started_at=RECORDING_START)
    await store.open(live)
    await store.add(live.recording_id, make_recorded_action("p_1", at=RECORDING_START))
    await store.add(live.recording_id, make_recorded_action("p_2", at=RECORDING_START + 30 * _HOUR))

    pruned = await prunable.prune_before(RECORDING_START + 24 * _HOUR)

    assert pruned == 0
    assert len(await store.actions(live.recording_id)) == 2
