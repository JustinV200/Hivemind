"""Tests for hivemind.honey_store.nectar.intake: NectarIntake over a real SQLite Honey Store.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/nectar/intake.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.nectar.intake for the module under test.
    - docs/adr/0035-honey-store-sqlite-fts5-sqlite-vec.md for the rules these tests state.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from builders.honey import (
    make_deposit_chunks,
    make_honey_identity,
    make_nectar_deposit,
    make_nectar_submission,
    open_test_honey_store_with_trail,
)

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.honey_store.errors import (
    CellMismatchError,
    NectarTooLargeError,
    NightVeilRefusedError,
    OffsetMismatchError,
)
from hivemind.honey_store.models import NectarOrigin, NectarState
from hivemind.honey_store.nectar.intake import SHA256_PREFIX_CHARS, NectarIntake
from hivemind.honey_store.nectar.reassembly import CHUNK_GROUP_TIMEOUT_S
from hivemind.honey_store.nectar.submission import DepositSource, handoff_source_key
from hivemind.honey_store.store.sqlite import SqliteHoneyStore
from hivemind.manifest import HoneyStoreSection
from hivemind.pheromone import PheromoneEvent, SqlitePheromoneTrail, TrailQuery
from waggle.clock import FakeClock
from waggle.ids import CellId, new_cell_id, new_event_id
from waggle.messages.honey import NectarKind

_MAX_BYTES = 4_096  # Small enough that a test can go over it cheaply.


@dataclass(frozen=True, slots=True)
class _Harness:
    """A NectarIntake over a real store, with the trail it records to and the shared clock."""

    intake: NectarIntake
    store: SqliteHoneyStore
    trail: SqlitePheromoneTrail
    clock: FakeClock

    async def honey_events(self) -> tuple[PheromoneEvent, ...]:
        """Return every honey.* event on the trail, in trail order."""
        return await self.trail.query(TrailQuery(family="honey"))


@pytest.fixture
async def harness(tmp_path: Path) -> _Harness:
    """Build a NectarIntake with a 4 KiB cap and a C1 default label over a fresh store."""
    clock = FakeClock()
    store, trail = await open_test_honey_store_with_trail(tmp_path, clock)
    intake = NectarIntake(
        store,
        make_honey_identity(clock),
        clock,
        HoneyStoreSection(max_nectar_bytes=_MAX_BYTES),
        HoneyClearance.C1,
    )
    return _Harness(intake=intake, store=store, trail=trail, clock=clock)


def _source(cell_id: CellId, tier: CombShieldLevel = CombShieldLevel.MEADOW) -> DepositSource:
    """Build what the Queen knows about a Virtual Cell's Warden: not borrowed, `tier`."""
    return DepositSource(
        sender="warden_relay", cell_id=cell_id, from_borrowed_cell=False, tier=tier
    )


# ──────────────────────────────────────────────────────────────────────────────
# submit: labels, scopes, dedupe and their events
# ──────────────────────────────────────────────────────────────────────────────


async def test_submit_stores_a_new_deposit_and_records_nectar_received(harness: _Harness) -> None:
    submission = make_nectar_submission(harness.clock)

    result = await harness.intake.submit(submission)

    assert result.is_new and not result.ephemeral
    assert result.nectar.clearance is HoneyClearance.C1
    assert result.nectar.scope == "hive"
    assert result.nectar.state is NectarState.RECEIVED
    (event,) = await harness.honey_events()
    assert event.kind == "honey.nectar_received"
    assert event.subject_id == result.nectar.id
    assert event.payload == {
        "nectar_kind": "FINDING",
        "origin": "BEE",
        "clearance": "C1",
        "size_bytes": len(submission.content),
        "scope_kind": "hive",
    }


async def test_submit_labels_a_real_cells_c0_deposit_c2(harness: _Harness) -> None:
    submission = make_nectar_submission(
        harness.clock, declared=HoneyClearance.C0, from_borrowed_cell=True
    )

    result = await harness.intake.submit(submission)

    assert result.nectar.clearance is HoneyClearance.C2


async def test_submit_uses_the_default_label_when_nothing_was_declared(harness: _Harness) -> None:
    result = await harness.intake.submit(make_nectar_submission(harness.clock, declared=None))

    assert result.nectar.clearance is HoneyClearance.C1


async def test_submit_files_a_humans_note_in_its_proposed_folder_at_c2(harness: _Harness) -> None:
    submission = make_nectar_submission(
        harness.clock, origin=NectarOrigin.HUMAN, proposed_scope="task:task_notes", bee=None
    )

    result = await harness.intake.submit(submission)

    assert result.nectar.scope == "task:task_notes"
    assert result.nectar.clearance is HoneyClearance.C2


async def test_submit_dedupes_a_repeat_and_records_the_raise_of_its_label(
    harness: _Harness,
) -> None:
    first = make_nectar_submission(harness.clock)
    await harness.intake.submit(first)

    again = await harness.intake.submit(first.model_copy(update={"declared": HoneyClearance.C2}))

    assert not again.is_new
    assert again.nectar.clearance is HoneyClearance.C2
    kinds = [event.kind for event in await harness.honey_events()]
    assert kinds == ["honey.nectar_received", "honey.nectar_deduplicated", "honey.label_raised"]
    raised = (await harness.honey_events())[-1]
    assert raised.payload == {"from": "C1", "to": "C2"}


async def test_submit_refuses_an_oversized_deposit_and_records_the_rejection(
    harness: _Harness,
) -> None:
    submission = make_nectar_submission(harness.clock, content=b"x" * (_MAX_BYTES + 1))

    with pytest.raises(NectarTooLargeError):
        await harness.intake.submit(submission)

    (event,) = await harness.honey_events()
    assert event.kind == "honey.nectar_rejected"
    assert event.subject_id == submission.cell_id
    assert event.payload == {"code": NectarTooLargeError.code, "size_bytes": _MAX_BYTES + 1}
    assert (await harness.store.stats()).nectar_by_state == {}


# ──────────────────────────────────────────────────────────────────────────────
# The Night Veil boundary: nothing on the trail, ever
# ──────────────────────────────────────────────────────────────────────────────


async def test_submit_keeps_a_night_veil_deposit_ephemeral_with_no_trail_event(
    harness: _Harness,
) -> None:
    submission = make_nectar_submission(harness.clock, tier=CombShieldLevel.NIGHT_VEIL)

    result = await harness.intake.submit(submission)

    assert result.ephemeral
    assert result.nectar.state is NectarState.EPHEMERAL
    assert result.nectar.ephemeral_cell_id == submission.cell_id
    assert await harness.honey_events() == ()


async def test_submit_refuses_night_veil_ripened_honey_at_c2_with_no_trail_event(
    harness: _Harness,
) -> None:
    submission = make_nectar_submission(
        harness.clock,
        kind=NectarKind.RIPENED_HONEY,
        declared=HoneyClearance.C2,
        tier=CombShieldLevel.NIGHT_VEIL,
    )

    with pytest.raises(NightVeilRefusedError):
        await harness.intake.submit(submission)

    assert await harness.honey_events() == ()
    assert (await harness.store.stats()).nectar_by_state == {}


async def test_submit_exports_night_veil_ripened_honey_at_c1_as_ordinary_nectar(
    harness: _Harness,
) -> None:
    submission = make_nectar_submission(
        harness.clock, kind=NectarKind.RIPENED_HONEY, tier=CombShieldLevel.NIGHT_VEIL
    )

    result = await harness.intake.submit(submission)

    assert not result.ephemeral
    assert result.nectar.state is NectarState.RECEIVED
    assert result.nectar.origin_tier is CombShieldLevel.NIGHT_VEIL
    assert result.nectar.clearance is HoneyClearance.C1
    # ADR-0035: the one intentional export is ordinary Nectar, recorded like any other.
    assert [event.kind for event in await harness.honey_events()] == ["honey.nectar_received"]


async def test_submit_records_nothing_for_an_oversized_night_veil_deposit(
    harness: _Harness,
) -> None:
    submission = make_nectar_submission(
        harness.clock, content=b"x" * (_MAX_BYTES + 1), tier=CombShieldLevel.NIGHT_VEIL
    )

    with pytest.raises(NectarTooLargeError):
        await harness.intake.submit(submission)

    assert await harness.honey_events() == ()


async def test_submit_never_merges_a_night_veil_deposit_onto_an_ordinary_row(
    harness: _Harness,
) -> None:
    key = handoff_source_key(new_event_id(harness.clock))
    ordinary = await harness.intake.submit(
        make_nectar_submission(harness.clock, source_key=key, declared=HoneyClearance.C0)
    )
    veiled = make_nectar_submission(
        harness.clock,
        source_key=key,
        declared=HoneyClearance.C2,
        content=b"a Night Veil Cell's own copy",
        tier=CombShieldLevel.NIGHT_VEIL,
    )

    result = await harness.intake.submit(veiled)

    assert result.is_new and result.ephemeral
    assert result.nectar.id != ordinary.nectar.id
    assert result.nectar.source_key is None
    kept = await harness.store.get_nectar(ordinary.nectar.id)
    assert kept.clearance is HoneyClearance.C0


# ──────────────────────────────────────────────────────────────────────────────
# receive_chunk: Waggle deposits
# ──────────────────────────────────────────────────────────────────────────────


async def test_receive_chunk_stores_a_two_chunk_deposit_on_its_final_chunk(
    harness: _Harness,
) -> None:
    content = b"A finding long enough to arrive in two chunks over Waggle."
    template = make_nectar_deposit(harness.clock)
    first, second = make_deposit_chunks(content, 40, template)
    source = _source(template.cell_id)

    pending = await harness.intake.receive_chunk(first, source)
    result = await harness.intake.receive_chunk(second, source)

    assert pending is None
    assert result is not None and result.is_new
    assert result.nectar.bee == template.worker_id
    assert await harness.store.nectar_content(result.nectar.id) == content
    assert [event.kind for event in await harness.honey_events()] == ["honey.nectar_received"]


async def test_receive_chunk_refuses_another_cells_deposit_and_records_it(
    harness: _Harness,
) -> None:
    deposit = make_nectar_deposit(harness.clock)
    source = _source(new_cell_id(harness.clock))

    with pytest.raises(CellMismatchError):
        await harness.intake.receive_chunk(deposit, source)

    (event,) = await harness.honey_events()
    assert event.kind == "honey.nectar_rejected"
    assert event.subject_id == source.cell_id
    assert event.payload == {
        "code": CellMismatchError.code,
        "sender": "warden_relay",
        "sha256_prefix": deposit.sha256[:SHA256_PREFIX_CHARS],
        "total_bytes": deposit.total_bytes,
    }


async def test_receive_chunk_records_nothing_for_a_night_veil_refusal(harness: _Harness) -> None:
    template = make_nectar_deposit(harness.clock)
    chunks = make_deposit_chunks(b"0123456789" * 8, 20, template)
    source = _source(template.cell_id, CombShieldLevel.NIGHT_VEIL)
    await harness.intake.receive_chunk(chunks[0], source)

    with pytest.raises(OffsetMismatchError):
        await harness.intake.receive_chunk(chunks[2], source)

    assert await harness.honey_events() == ()


async def test_receive_chunk_keys_a_handoff_so_its_bee_bread_copy_dedupes(
    harness: _Harness,
) -> None:
    event_id = new_event_id(harness.clock)
    deposit = make_nectar_deposit(harness.clock, kind=NectarKind.HANDOFF, event_id=event_id)
    over_waggle = await harness.intake.receive_chunk(deposit, _source(deposit.cell_id))
    from_bee_bread = make_nectar_submission(
        harness.clock,
        kind=NectarKind.HANDOFF,
        origin=NectarOrigin.BEE_BREAD,
        content=b'{"goal": "the same handoff, serialised differently"}',
        cell_id=deposit.cell_id,
        source_key=handoff_source_key(event_id),
        event_id=event_id,
    )

    again = await harness.intake.submit(from_bee_bread)

    assert over_waggle is not None
    assert over_waggle.nectar.source_key == handoff_source_key(event_id)
    assert not again.is_new
    assert again.nectar.id == over_waggle.nectar.id


async def test_expire_groups_drops_a_deposit_idle_past_the_timeout(harness: _Harness) -> None:
    template = make_nectar_deposit(harness.clock)
    first = make_deposit_chunks(b"0123456789" * 8, 20, template)[0]
    await harness.intake.receive_chunk(first, _source(template.cell_id))
    harness.clock.advance(CHUNK_GROUP_TIMEOUT_S + 1)

    assert harness.intake.expire_groups(harness.clock.now()) == 1
    assert harness.intake.expire_groups(harness.clock.now()) == 0
