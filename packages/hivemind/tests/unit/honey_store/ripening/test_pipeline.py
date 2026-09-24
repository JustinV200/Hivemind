"""Tests for hivemind.honey_store.ripening.pipeline: whole ripening passes over a real Honey Store.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/ripening/pipeline.py (codingrules section 3). Every scenario
    deposits through the real NectarIntake, ripens with a real Ripener over a real SQLite store,
    a scripted FakeLLMProvider on the RIPENER slot and a FakeEmbedding on the EMBEDDER slot, and
    reads the results back through the store's own search and the Pheromone Trail.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.ripening.pipeline for the module under test.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest
from builders.honey import (
    make_honey_identity,
    make_nectar_submission,
    make_ripener_deps,
    open_test_honey_store_with_trail,
)
from builders.llm import make_bound, make_bound_embedder

from hivemind.cell import HoneyClearance
from hivemind.common.sqlite import connect
from hivemind.forage.slots import ModelSlot
from hivemind.honey_store.errors import HoneyStoreError
from hivemind.honey_store.models import Honey, HoneyDraft, NectarState, ReadFilter, RipenerReading
from hivemind.honey_store.nectar.intake import NectarIntake
from hivemind.honey_store.ripening.deps import RipenerDeps
from hivemind.honey_store.ripening.pipeline import PassOutcome, Ripener, RipenOutcome
from hivemind.honey_store.store import build_match
from hivemind.honey_store.store.sqlite import SqliteHoneyStore
from hivemind.llm import FakeEmbedding, FakeLLMProvider, text_response
from hivemind.manifest import HoneyRipeningSection, HoneyStoreSection
from hivemind.pheromone import HoneyEvent, PheromoneEvent, SqlitePheromoneTrail, TrailQuery
from waggle.clock import FakeClock
from waggle.ids import NectarId

_MODEL = "test-embed"
# Small chunks with no overlap, so every paragraph below is exactly one chunk.
_RIPENING = HoneyRipeningSection(chunk_chars=200, chunk_overlap_chars=0)
_READ_ALL = ReadFilter(readable=("*",), max_clearance=HoneyClearance.C2)


def _paragraph(word: str) -> str:
    """A ~150-character paragraph about `word`, distinct from every other paragraph's."""
    return f"The {word} report says the {word} pipeline was checked twice. " * 2


_LONG_TEXT = "\n\n".join(
    _paragraph(word) for word in ("flamingo", "kerosene", "tangerine", "kerosene")
)
_SHORT_TEXT = "The zeppelin hangar door code rotates weekly."


@dataclass(frozen=True, slots=True)
class _Harness:
    """A real store, its trail and intake, a scripted ripener and a fake embedder, one clock."""

    store: SqliteHoneyStore
    trail: SqlitePheromoneTrail
    intake: NectarIntake
    clock: FakeClock
    ripener: FakeLLMProvider
    embedder: FakeEmbedding

    def deps(self, **overrides: object) -> RipenerDeps:
        """Build RipenerDeps with both bindings wired, unless `overrides` says otherwise."""
        fields: dict[str, object] = {
            "ripening": _RIPENING,
            "ripener": make_bound(
                slot=ModelSlot.RIPENER, binding="ripener", provider=self.ripener, model="ripe-1"
            ),
            "embedder": make_bound_embedder(provider=self.embedder, model=_MODEL),
        }
        fields.update(overrides)
        return make_ripener_deps(self.store, self.clock, **fields)

    async def deposit(self, text: str | bytes, **overrides: object) -> NectarId:
        """Submit one whole deposit through intake and return its Nectar id."""
        content = text.encode() if isinstance(text, str) else text
        submission = make_nectar_submission(self.clock, content=content, **overrides)
        return (await self.intake.submit(submission)).nectar.id

    async def events(self, kind: str) -> tuple[PheromoneEvent, ...]:
        """Return every event of `kind` on the trail."""
        return await self.trail.query(TrailQuery(kind=kind))

    async def bodies(self, text: str) -> list[str]:
        """Return the bodies of every live row full-text search finds for `text`."""
        match = build_match(text)
        assert match is not None
        return [hit.honey.body for hit in await self.store.search_text(match, _READ_ALL, 20)]


@pytest.fixture
async def harness(tmp_path: Path) -> _Harness:
    """Build the store, trail and intake, plus an unscripted ripener and an up embedder."""
    clock = FakeClock()
    store, trail = await open_test_honey_store_with_trail(tmp_path, clock)
    intake = NectarIntake(
        store, make_honey_identity(clock), clock, HoneyStoreSection(), HoneyClearance.C1
    )
    embedder = FakeEmbedding(clock=clock, dimensions=64, model=_MODEL)
    return _Harness(store, trail, intake, clock, FakeLLMProvider(), embedder)


def _summary_reply(clearance: str) -> str:
    """Script one RipenedSummary reply for the long text."""
    return json.dumps(
        {
            "title": "Pipeline reports",
            "summary": "Three reports on pipelines that were each checked twice.",
            "key_facts": ["Pipelines are checked twice"],
            "clearance": clearance,
            "clearance_reason": "Internal operations.",
        }
    )


@dataclass(frozen=True, slots=True)
class _ThreeRipened:
    """The brief's scenario after one pass: its outcome and the three Nectar ids."""

    outcome: PassOutcome
    long_id: NectarId
    short_id: NectarId
    binary_id: NectarId


@pytest.fixture
async def three_ripened(harness: _Harness) -> _ThreeRipened:
    """Deposit a long text, a short text, a binary blob and a duplicate; ripen them in one pass."""
    harness.ripener.script(text_response(_summary_reply("C2")))
    long_id = await harness.deposit(_LONG_TEXT, title="Pipeline reports")
    short_id = await harness.deposit(_SHORT_TEXT, title="Hangar note")
    binary_id = await harness.deposit(
        b"\x00\x01firmware", title="Widget firmware image", media_type="application/octet-stream"
    )
    assert await harness.deposit(_SHORT_TEXT, title="Hangar note") == short_id  # Deduplicated.

    outcome = await Ripener(harness.deps()).run_pass()

    return _ThreeRipened(outcome, long_id, short_id, binary_id)


# ──────────────────────────────────────────────────────────────────────────────
# The brief's scenario: rows, findability, vectors, dedupe, events
# ──────────────────────────────────────────────────────────────────────────────


async def test_run_pass_ripens_every_pending_nectar_once(three_ripened: _ThreeRipened) -> None:
    ripen = three_ripened.outcome.ripen

    assert ripen == RipenOutcome(ripened=3, failed=0, discarded=0, rows=6, deduped=2, embedded=6)
    assert three_ripened.outcome.reembedded == 0


async def test_run_pass_builds_each_nectars_rows(
    harness: _Harness, three_ripened: _ThreeRipened
) -> None:
    long_rows = await harness.store.honey_for_nectar(three_ripened.long_id)
    short_rows = await harness.store.honey_for_nectar(three_ripened.short_id)
    binary_rows = await harness.store.honey_for_nectar(three_ripened.binary_id)

    # The long text: a SUMMARY and three CHUNKs (its repeated paragraph is stored once), all
    # raised to C2 by the model's label; the short text's one chunk repeated its own summary.
    assert sorted(_shape(long_rows)) == [("CHUNK", 0), ("CHUNK", 1), ("CHUNK", 2), ("SUMMARY", 0)]
    assert {row.clearance for row in long_rows} == {HoneyClearance.C2}
    assert {row.ripener_model for row in long_rows} == {"ripe-1"}
    assert _shape(short_rows) == [("SUMMARY", 0)]
    assert short_rows[0].body == _SHORT_TEXT and short_rows[0].clearance is HoneyClearance.C1
    assert _shape(binary_rows) == [("SUMMARY", 0)]
    assert binary_rows[0].body == "Binary content: application/octet-stream, 10 bytes."
    for nectar_id in (three_ripened.long_id, three_ripened.short_id, three_ripened.binary_id):
        assert (await harness.store.get_nectar(nectar_id)).state is NectarState.RIPENED


async def test_run_pass_stores_a_reading_only_for_a_model_summary(
    harness: _Harness, three_ripened: _ThreeRipened
) -> None:
    long_nectar = await harness.store.get_nectar(three_ripened.long_id)

    assert long_nectar.ripener_clearance is HoneyClearance.C2
    # The short text is its own summary and the blob has no text: no model read either one.
    for nectar_id in (three_ripened.short_id, three_ripened.binary_id):
        assert (await harness.store.get_nectar(nectar_id)).ripener_clearance is None


async def test_run_pass_keeps_a_reading_below_the_label_and_lowers_nothing(
    harness: _Harness,
) -> None:
    # A Hive Stand deposit: C2 by the Real Cell floor, read by the model as C0 (ADR-0034).
    harness.ripener.script(text_response(_summary_reply("C0")))
    nectar_id = await harness.deposit(_LONG_TEXT, from_borrowed_cell=True)

    await Ripener(harness.deps()).run_pass()

    nectar = await harness.store.get_nectar(nectar_id)
    assert (nectar.clearance, nectar.ripener_clearance) == (HoneyClearance.C2, HoneyClearance.C0)
    rows = await harness.store.honey_for_nectar(nectar_id)
    assert {row.clearance for row in rows} == {HoneyClearance.C2}


def _shape(rows: Sequence[Honey]) -> list[tuple[str, int]]:
    """Return each row's (part, chunk_index), in the given order."""
    return [(row.part.value, row.chunk_index) for row in rows]


async def test_run_pass_makes_every_row_findable_by_full_text(
    harness: _Harness, three_ripened: _ThreeRipened
) -> None:
    assert _paragraph("kerosene").strip() in [
        body.strip() for body in await harness.bodies("kerosene")
    ]
    assert _SHORT_TEXT in await harness.bodies("zeppelin hangar")
    assert await harness.bodies("firmware") == [
        "Binary content: application/octet-stream, 10 bytes."
    ]


async def test_run_pass_embeds_every_row_for_the_embedders_model(
    harness: _Harness, three_ripened: _ThreeRipened
) -> None:
    assert await harness.store.pending_vectors(_MODEL, 100) == ()
    assert (await harness.store.stats()).vectors_by_model == {_MODEL: 6}


async def test_run_pass_records_intake_ripening_and_label_events(
    harness: _Harness, three_ripened: _ThreeRipened
) -> None:
    ripened = {event.subject_id: event.payload for event in await harness.events("honey.ripened")}

    assert len(await harness.events("honey.nectar_received")) == 3
    assert len(await harness.events("honey.nectar_deduplicated")) == 1
    assert ripened[three_ripened.long_id] == {
        "rows": 4,
        "chunks": 4,
        "deduped": 1,
        "summarised": True,
        "embedded": 4,
    }
    assert ripened[three_ripened.short_id]["summarised"] is False
    (raised,) = await harness.events("honey.label_raised")
    assert raised.subject_id == three_ripened.long_id
    assert raised.payload == {"from": "C1", "to": "C2"}
    assert await harness.events("honey.ripen_failed") == ()


# ──────────────────────────────────────────────────────────────────────────────
# Near duplicates across passes, failures, and the embedder being down
# ──────────────────────────────────────────────────────────────────────────────


async def test_ripen_pending_drops_a_chunk_already_stored_in_the_same_scope(
    harness: _Harness,
) -> None:
    shared = _paragraph("tangerine")
    await harness.deposit(f"{_paragraph('flamingo')}\n\n{shared}", title="Field notes")
    await Ripener(harness.deps(ripener=None)).ripen_pending()
    later = await harness.deposit(f"{_paragraph('kerosene')}\n\n{shared}", title="Field notes")

    outcome = await Ripener(harness.deps(ripener=None)).ripen_pending()

    # The shared paragraph, chunk 2 of 2 in both deposits, is stored once, by the first.
    assert outcome == RipenOutcome(ripened=1, rows=2, deduped=1, embedded=2)
    rows = sorted(await harness.store.honey_for_nectar(later), key=lambda row: row.part.value)
    assert _shape(rows) == [("CHUNK", 0), ("SUMMARY", 0)]
    assert rows[0].body.startswith("The kerosene")


async def test_ripen_pending_stores_rows_without_vectors_while_the_embedder_is_down(
    harness: _Harness,
) -> None:
    await harness.deposit(_SHORT_TEXT)
    harness.embedder.set_available(False)

    first = await Ripener(harness.deps(ripener=None)).run_pass()
    harness.embedder.set_available(True)
    second = await Ripener(harness.deps(ripener=None)).run_pass()

    assert (first.ripen.ripened, first.ripen.embedded, first.reembedded) == (1, 0, 0)
    assert (second.ripen, second.reembedded) == (RipenOutcome(), 1)
    assert len(await harness.events("honey.reembedded")) == 1


async def test_ripen_pending_without_any_embedder_leaves_rows_to_full_text(
    harness: _Harness,
) -> None:
    await harness.deposit(_SHORT_TEXT)

    outcome = await Ripener(harness.deps(ripener=None, embedder=None)).run_pass()

    assert (outcome.ripen.rows, outcome.ripen.embedded, outcome.reembedded) == (1, 0, 0)
    assert _SHORT_TEXT in await harness.bodies("zeppelin")


class _FailingRipenStore(SqliteHoneyStore):
    """A real store whose `ripen` refuses the Nectar ids in `failing`, as a broken row would."""

    failing: frozenset[str] = frozenset()

    async def ripen(
        self,
        nectar_id: NectarId,
        drafts: Sequence[HoneyDraft],
        event: HoneyEvent,
        *,
        reading: RipenerReading | None = None,
    ) -> tuple[Honey, ...]:
        """Refuse a Nectar in `failing`; ripen every other one for real."""
        if nectar_id in self.failing:
            raise HoneyStoreError(f"Nectar {nectar_id} cannot be ripened in this test.")
        return await super().ripen(nectar_id, drafts, event, reading=reading)


async def test_ripen_pending_marks_a_failing_nectar_and_discards_it_at_the_cap(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    connection = connect(tmp_path / "hive.sqlite3")
    trail = await SqlitePheromoneTrail.create(connection, clock)
    store = await _FailingRipenStore.create(connection, clock)
    assert isinstance(store, _FailingRipenStore)
    intake = NectarIntake(
        store, make_honey_identity(clock), clock, HoneyStoreSection(), HoneyClearance.C1
    )
    bad = (await intake.submit(make_nectar_submission(clock, content=b"broken row"))).nectar.id
    await intake.submit(make_nectar_submission(clock, content=b"healthy row"))
    store.failing = frozenset({bad})
    settings = HoneyRipeningSection(max_attempts=2)
    ripener = Ripener(make_ripener_deps(store, clock, ripening=settings))

    first = await ripener.ripen_pending()
    second = await ripener.ripen_pending()
    third = await ripener.ripen_pending()

    # One bad deposit never stops the pass: the healthy one ripens beside it.
    assert (first.ripened, first.failed, first.discarded) == (1, 1, 0)
    assert (second.failed, second.discarded) == (0, 1)
    assert third == RipenOutcome()
    assert (await store.get_nectar(bad)).state is NectarState.DISCARDED
    failures = await trail.query(TrailQuery(kind="honey.ripen_failed"))
    assert [event.payload for event in failures] == [
        {"error": HoneyStoreError.code, "attempts": 1, "discarded": False},
        {"error": HoneyStoreError.code, "attempts": 2, "discarded": True},
    ]
