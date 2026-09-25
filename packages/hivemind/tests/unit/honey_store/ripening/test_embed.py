"""Tests for hivemind.honey_store.ripening.embed: batched embedding and the per-pass re-embed.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/ripening/embed.py (codingrules section 3). Vectors come from
    FakeEmbedding, whose model id is always the binding's own, as the registry wires it.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.ripening.embed for the module under test.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
from collections.abc import Callable
from pathlib import Path

import pytest
from builders.honey import (
    make_honey_draft,
    make_nectar_draft,
    make_ripener_deps,
    open_test_honey_store_with_trail,
)
from builders.llm import make_bound_embedder

from hivemind.honey_store.identity import honey_event
from hivemind.honey_store.models import Honey, HoneyPart
from hivemind.honey_store.ripening.embed import (
    embed_pending_rows,
    embed_texts,
    embedding_text,
    is_zero_vector,
)
from hivemind.honey_store.store.sqlite import SqliteHoneyStore
from hivemind.llm import (
    BoundEmbedder,
    DirectEmbedGate,
    EmbeddingCapabilities,
    EmbeddingRequest,
    EmbeddingResponse,
    FakeEmbedding,
    Usage,
)
from hivemind.pheromone import TrailQuery
from waggle.clock import FakeClock

_MODEL = "test-embed"  # A neutral model id; the fake reports it back as its own.
# The module itself, for monkeypatching its timeout (see test_summarise.py for why not getattr).
_EMBED_MODULE = importlib.import_module("hivemind.honey_store.ripening.embed")


def _embedder(max_batch: int = 256, model: str = _MODEL) -> tuple[BoundEmbedder, FakeEmbedding]:
    """Bind a FakeEmbedding answering as `model` to an EMBEDDER binding for `_MODEL`."""
    capabilities = EmbeddingCapabilities(
        dimensions=16, max_batch=max_batch, max_input_chars=8_000, normalized=True
    )
    provider = FakeEmbedding(
        clock=FakeClock(), dimensions=16, capabilities=capabilities, model=model
    )
    return make_bound_embedder(provider=provider, model=_MODEL), provider


class _ReplyGate:
    """An EmbedGate that answers with whatever `reply` builds from the request."""

    def __init__(self, reply: Callable[[EmbeddingRequest], EmbeddingResponse]) -> None:
        """Keep the reply builder."""
        self._reply = reply

    async def embed(self, bound: BoundEmbedder, request: EmbeddingRequest) -> EmbeddingResponse:
        """Return the scripted reply."""
        return self._reply(request)


class _StuckGate:
    """An EmbedGate whose call never returns, standing in for a hung embedding server."""

    async def embed(self, bound: BoundEmbedder, request: EmbeddingRequest) -> EmbeddingResponse:
        """Wait forever; only the caller's own timeout ends this call."""
        await asyncio.Event().wait()
        raise AssertionError("unreachable: the event is never set")


def _zero_first(request: EmbeddingRequest) -> EmbeddingResponse:
    """Reply with a zero vector for the first text and a unit vector for the rest."""
    vectors = tuple(
        ((0.0, 0.0) if index == 0 else (1.0, 0.0)) for index in range(len(request.texts))
    )
    return EmbeddingResponse(
        vectors=vectors, model=_MODEL, dimensions=2, usage=Usage(input_tokens=1, output_tokens=0)
    )


# ──────────────────────────────────────────────────────────────────────────────
# embed_texts
# ──────────────────────────────────────────────────────────────────────────────


async def test_embed_texts_batches_by_the_providers_limit_and_keeps_order() -> None:
    bound, provider = _embedder(max_batch=2)
    texts = [f"text number {index}" for index in range(5)]

    vectors = await embed_texts(texts, bound, DirectEmbedGate(), batch=32)

    single = [await embed_texts([text], bound, DirectEmbedGate(), batch=32) for text in texts]
    assert provider.calls == 3 + 5
    assert vectors is not None
    assert [(vector,) for vector in vectors] == single


async def test_embed_texts_makes_no_call_for_no_texts() -> None:
    bound, provider = _embedder()

    assert await embed_texts([], bound, DirectEmbedGate(), batch=32) == ()
    assert provider.calls == 0


async def test_embed_texts_gives_none_when_the_provider_is_down() -> None:
    bound, provider = _embedder()
    provider.set_available(False)

    assert await embed_texts(["a", "b"], bound, DirectEmbedGate(), batch=32) is None


async def test_embed_texts_gives_none_for_vectors_from_another_model() -> None:
    bound, _ = _embedder(model="another-model")

    assert await embed_texts(["a"], bound, DirectEmbedGate(), batch=32) is None


async def test_embed_texts_gives_none_for_a_reply_short_of_vectors() -> None:
    bound, _ = _embedder()

    def short(request: EmbeddingRequest) -> EmbeddingResponse:
        """Answer one vector whatever was asked."""
        return _zero_first(request).model_copy(update={"vectors": ((1.0, 0.0),)})

    assert await embed_texts(["a", "b"], bound, _ReplyGate(short), batch=32) is None


async def test_embed_texts_gives_none_when_a_batch_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_EMBED_MODULE, "EMBED_BATCH_TIMEOUT_S", 0.01)
    bound, _ = _embedder()

    assert await embed_texts(["a"], bound, _StuckGate(), batch=32) is None


# ──────────────────────────────────────────────────────────────────────────────
# embedding_text and is_zero_vector
# ──────────────────────────────────────────────────────────────────────────────


def test_embedding_text_is_title_and_summary_for_a_summary_title_and_body_for_a_chunk() -> None:
    summary = make_honey_draft(title="T", summary="S", body="B")
    chunk = make_honey_draft(part=HoneyPart.CHUNK, chunk_index=1, title="T", summary="S", body="B")

    assert embedding_text(summary) == "T\n\nS"
    assert embedding_text(chunk) == "T\n\nB"


def test_embedding_text_is_never_empty() -> None:
    blank = make_honey_draft(title=" ", summary="", body="")

    assert embedding_text(blank) == "SUMMARY"


def test_is_zero_vector_is_true_only_without_any_direction() -> None:
    assert is_zero_vector((0.0, 0.0))
    assert not is_zero_vector((0.0, 1e-9))


# ──────────────────────────────────────────────────────────────────────────────
# embed_pending_rows
# ──────────────────────────────────────────────────────────────────────────────


async def _ripened_rows(store: SqliteHoneyStore, clock: FakeClock, count: int) -> list[Honey]:
    """Deposit and ripen `count` distinct one-row Nectar, returning their (vectorless) rows."""
    deps = make_ripener_deps(store, clock)
    rows: list[Honey] = []
    for index in range(count):
        draft = make_nectar_draft(clock=clock, content=f"finding {index}".encode())
        digest = hashlib.sha256(draft.content).hexdigest()
        added = await store.add_nectar(draft, digest, lambda _added: ())
        event = honey_event(deps.identity, clock, "honey.ripened", added.nectar.id)
        body = f"row {index}"
        rows.extend(await store.ripen(added.nectar.id, (make_honey_draft(body=body),), event))
    return rows


async def test_embed_pending_rows_embeds_pending_rows_and_records_one_event(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    store, trail = await open_test_honey_store_with_trail(tmp_path, clock)
    rows = await _ripened_rows(store, clock, 3)
    bound, _ = _embedder()
    deps = make_ripener_deps(store, clock, embedder=bound)

    embedded = await embed_pending_rows(deps)

    assert embedded == 3
    assert await store.pending_vectors(_MODEL, 10) == ()
    (event,) = await trail.query(TrailQuery(kind="honey.reembedded"))
    assert event.subject_id == rows[0].id
    assert event.payload == {"rows": 3, "skipped": 0, "model": _MODEL}
    assert await embed_pending_rows(deps) == 0


async def test_embed_pending_rows_does_nothing_without_an_embedder(tmp_path: Path) -> None:
    clock = FakeClock()
    store, _ = await open_test_honey_store_with_trail(tmp_path, clock)
    await _ripened_rows(store, clock, 1)

    assert await embed_pending_rows(make_ripener_deps(store, clock)) == 0


async def test_embed_pending_rows_leaves_rows_pending_when_the_embedder_fails(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    store, trail = await open_test_honey_store_with_trail(tmp_path, clock)
    await _ripened_rows(store, clock, 2)
    bound, provider = _embedder()
    provider.set_available(False)

    embedded = await embed_pending_rows(make_ripener_deps(store, clock, embedder=bound))

    assert embedded == 0
    assert len(await store.pending_vectors(_MODEL, 10)) == 2
    assert await trail.query(TrailQuery(kind="honey.reembedded")) == ()


async def test_embed_pending_rows_skips_and_counts_a_zero_vector(tmp_path: Path) -> None:
    clock = FakeClock()
    store, trail = await open_test_honey_store_with_trail(tmp_path, clock)
    await _ripened_rows(store, clock, 2)
    bound, _ = _embedder()
    deps = make_ripener_deps(store, clock, embedder=bound, embed_gate=_ReplyGate(_zero_first))

    embedded = await embed_pending_rows(deps)

    assert embedded == 1
    assert len(await store.pending_vectors(_MODEL, 10)) == 1
    (event,) = await trail.query(TrailQuery(kind="honey.reembedded"))
    assert event.payload == {"rows": 1, "skipped": 1, "model": _MODEL}
