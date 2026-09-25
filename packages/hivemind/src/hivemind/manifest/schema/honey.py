"""Define the ``[honey.store]``, ``[honey.ripening]`` and ``[honey.retrieval]`` manifest sections.

Phase 7 makes the Honey Store (the Hive's cold-tier knowledge base: raw Nectar deposits ripened
into searchable Honey) real, and every number it runs on is an operator's choice, so it lives
here as schema with a documented default rather than as a constant in code (codingrules section
13). ``HoneyStoreSection`` caps what one deposit may weigh; ``HoneyRipeningSection`` sets how the
House Bee (the maintenance Worker that ripens Nectar) paces and shapes its work: how often it
runs, how much it takes per pass, how text is chunked, when a model summarises, how embeddings
are batched and when two chunks count as the same; ``HoneyRetrievalSection`` sets how a query is
ranked and budgeted: the weight of full-text against vector evidence, the floor a hit must clear,
how much of a reader's context window a result may fill, and how many hits the Queen's pre-check
attaches to an assignment. The three sit beside ``[honey.clearance]`` under ``[honey]``
(``hivemind.manifest.schema.security.HoneySection``), which is where a dotted TOML header nests.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Embedded by
    ``hivemind.manifest.schema.security.HoneySection``; read by ``hivemind.honey_store`` (Layer
    2, which receives these slices directly, the same way ``hivemind.cell.local.config`` reads
    its own section) and by the composition root. Calls into pydantic and
    ``waggle.messages.honey`` only, for the wire's own default deposit cap.

Key invariants:
    - Every model here is frozen and forbids unknown fields (codingrules section 8.5).
    - A chunk's overlap is always shorter than the chunk itself, so chunking always advances.
    - The two retrieval weights are never both zero, so a hit's score is always defined.
    - The deposit cap never exceeds the wire's own default ceiling times four: a deposit is a
      finding or a transcript, and anything larger belongs in the Basket (roadmap 9.2a).

See Also:
    - docs/adr/0035-honey-store-sqlite-fts5-sqlite-vec.md for what each number governs.
    - docs/adr/0036-embedding-provider-and-reembedding-policy.md for the embedding pass fields.
    - hivemind.manifest.schema.security for HoneySection, the ``[honey]`` table these nest in.
    - docs/manifests/full.toml for every field shown with its default.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from waggle.messages.honey.exchange import DEFAULT_MAX_NECTAR_BYTES

MAX_NECTAR_BYTES_CEILING = 4 * DEFAULT_MAX_NECTAR_BYTES  # 64 MiB: past this it is a Basket blob.
DEFAULT_RIPEN_INTERVAL_S = 30.0  # Twice a minute: fresh Nectar is searchable well before a rerun.
DEFAULT_MAX_NECTAR_PER_PASS = 16  # One pass stays short even when every Nectar needs a summary.
DEFAULT_MAX_RIPEN_ATTEMPTS = 3  # A deposit that fails three passes is kept for audit, not retried.
DEFAULT_CHUNK_CHARS = 1_600  # About 400 tokens: one idea per chunk, several chunks per prompt.
DEFAULT_CHUNK_OVERLAP_CHARS = 200  # One or two sentences, so an idea split at a boundary survives.
MIN_CHUNK_CHARS = 200  # Shorter chunks carry too little context to be worth a vector each.
MAX_CHUNK_CHARS = 8_000  # Longer chunks blur what an embedding says and crowd a result budget.
DEFAULT_SUMMARISE_MIN_CHARS = 400  # A paragraph or less already is its own summary.
DEFAULT_SUMMARISE_MAX_INPUT_CHARS = 12_000  # About 3,000 tokens: fits every ripener window.
DEFAULT_EMBED_BATCH = 32  # A typical server's comfortable batch; the provider may cap it lower.
MAX_EMBED_BATCH = 256  # No embedding endpoint the Hive targets accepts more in one request.
DEFAULT_MAX_EMBED_PER_PASS = 256  # Re-embedding a large store is spread across many short passes.
DEFAULT_NEAR_DUPLICATE_SIMILARITY = 0.97  # Cosine similarity at which two chunks say the same.
DEFAULT_BEE_BREAD_AFTER_S = 86_400.0  # A day: Bee Bread is recent memory; older entries ripen.
DEFAULT_MAX_BEE_BREAD_PER_SWEEP = 200  # A slow week catches up over a few sweeps, not one.
DEFAULT_FTS_WEIGHT = 0.4  # Exact terms matter (paths, flags, errors) but paraphrase matters more.
DEFAULT_VECTOR_WEIGHT = 0.6  # The semantic half; renormalised away when no vector side exists.
DEFAULT_MIN_SCORE = 0.15  # Drops the nearest-of-the-irrelevant a vector search always returns:
# measured with a real local embedder, unrelated text sits at cosine 0.18 or less (0.11 fused at
# the default weights) and related text at 0.33 or more (0.2 fused). Calibrate per embedder.
DEFAULT_CANDIDATE_MULTIPLIER = 4  # Each side ranks four times the hits asked for before fusion.
MAX_CANDIDATE_MULTIPLIER = 20  # Past this a query costs more than the ranking gains.
DEFAULT_MAX_HITS_PER_NECTAR = 2  # One deposit's summary and its best chunk; never a whole page.
DEFAULT_BUDGET_FRACTION = 0.15  # A retrieved section never outweighs the task it serves.
MAX_BUDGET_FRACTION = 0.5  # Half a window is already more reference than any task reads.
DEFAULT_MAX_BUDGET_TOKENS = 6_000  # A hard ceiling on one result, however large the window.
DEFAULT_PRECHECK_MAX_HITS = 6  # A handful for the assignment; the bee may query for more.
MAX_PRECHECK_MAX_HITS = 16  # waggle.messages.task.assignment.MAX_ASSIGN_HONEY_ITEMS.
DEFAULT_EMBED_TIMEOUT_S = 10.0  # A query never waits longer than this for its own embedding.

__all__ = [
    "DEFAULT_BUDGET_FRACTION",
    "DEFAULT_CHUNK_CHARS",
    "DEFAULT_CHUNK_OVERLAP_CHARS",
    "DEFAULT_MAX_NECTAR_PER_PASS",
    "DEFAULT_PRECHECK_MAX_HITS",
    "DEFAULT_RIPEN_INTERVAL_S",
    "MAX_NECTAR_BYTES_CEILING",
    "HoneyRetrievalSection",
    "HoneyRipeningSection",
    "HoneyStoreSection",
]

# A frozen, extras-forbidding config every section here shares (codingrules section 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class HoneyStoreSection(BaseModel):
    """``[honey.store]``: what one Nectar deposit may weigh when it reaches intake."""

    model_config = _MODEL_CONFIG

    max_nectar_bytes: int = Field(
        default=DEFAULT_MAX_NECTAR_BYTES,
        gt=0,
        le=MAX_NECTAR_BYTES_CEILING,
        description="The largest complete deposit intake accepts, in bytes; a first chunk whose "
        "total_bytes exceeds it is refused (docs/waggle/spec.md section 5).",
    )


class HoneyRipeningSection(BaseModel):
    """``[honey.ripening]``: the House Bee's ripening pace, chunking, summaries and embeddings."""

    model_config = _MODEL_CONFIG

    interval_s: float = Field(
        default=DEFAULT_RIPEN_INTERVAL_S,
        gt=0,
        description="Seconds between two House Bee ripening passes.",
    )
    max_nectar_per_pass: int = Field(
        default=DEFAULT_MAX_NECTAR_PER_PASS, gt=0, description="Nectar ripened per pass, at most."
    )
    max_attempts: int = Field(
        default=DEFAULT_MAX_RIPEN_ATTEMPTS,
        gt=0,
        description="Passes a deposit may fail before it is set aside as DISCARDED, kept for "
        "audit and never retried.",
    )
    chunk_chars: int = Field(
        default=DEFAULT_CHUNK_CHARS,
        ge=MIN_CHUNK_CHARS,
        le=MAX_CHUNK_CHARS,
        description="The longest chunk a deposit's text is split into, in characters.",
    )
    chunk_overlap_chars: int = Field(
        default=DEFAULT_CHUNK_OVERLAP_CHARS,
        ge=0,
        description="Characters each chunk repeats from the end of the one before it.",
    )
    summarise: bool = Field(
        default=True,
        description="Whether the ripener slot writes each deposit's title, summary and key "
        "facts; false keeps ripening free of model calls apart from embeddings.",
    )
    summarise_min_chars: int = Field(
        default=DEFAULT_SUMMARISE_MIN_CHARS,
        ge=0,
        description="Deposits shorter than this are their own summary; no model call is made.",
    )
    summarise_max_input_chars: int = Field(
        default=DEFAULT_SUMMARISE_MAX_INPUT_CHARS,
        gt=0,
        description="The most of a deposit's text the ripener is shown; every chunk is still "
        "indexed whatever its length.",
    )
    embed_batch: int = Field(
        default=DEFAULT_EMBED_BATCH,
        gt=0,
        le=MAX_EMBED_BATCH,
        description="Texts sent in one embedding request; the provider's own limit wins if lower.",
    )
    max_embed_per_pass: int = Field(
        default=DEFAULT_MAX_EMBED_PER_PASS,
        gt=0,
        description="Honey rows embedded per pass, fresh and re-embedded alike (ADR-0036).",
    )
    near_duplicate_similarity: float = Field(
        default=DEFAULT_NEAR_DUPLICATE_SIMILARITY,
        gt=0,
        le=1,
        description="Cosine similarity at or above which a new chunk duplicates a live one in "
        "the same scope and is not stored again.",
    )
    bee_bread_after_s: float = Field(
        default=DEFAULT_BEE_BREAD_AFTER_S,
        ge=0,
        description="Age in seconds past which a Bee Bread entry is deposited for ripening.",
    )
    max_bee_bread_per_sweep: int = Field(
        default=DEFAULT_MAX_BEE_BREAD_PER_SWEEP,
        gt=0,
        description="Bee Bread entries deposited per House Bee sweep, at most.",
    )

    @model_validator(mode="after")
    def _overlap_shorter_than_chunk(self) -> HoneyRipeningSection:
        """Reject an overlap that is not strictly shorter than a chunk (chunking must advance)."""
        if self.chunk_overlap_chars >= self.chunk_chars:
            raise ValueError(
                f"[honey.ripening] chunk_overlap_chars ({self.chunk_overlap_chars}) must be "
                f"shorter than chunk_chars ({self.chunk_chars})."
            )
        return self


class HoneyRetrievalSection(BaseModel):
    """``[honey.retrieval]``: hybrid ranking weights, the score floor and the result budget."""

    model_config = _MODEL_CONFIG

    fts_weight: float = Field(
        default=DEFAULT_FTS_WEIGHT, ge=0, description="Weight of the full-text score in a hit."
    )
    vector_weight: float = Field(
        default=DEFAULT_VECTOR_WEIGHT, ge=0, description="Weight of the vector score in a hit."
    )
    min_score: float = Field(
        default=DEFAULT_MIN_SCORE,
        ge=0,
        le=1,
        description="The lowest fused score a hit may have and still be returned.",
    )
    candidate_multiplier: int = Field(
        default=DEFAULT_CANDIDATE_MULTIPLIER,
        ge=1,
        le=MAX_CANDIDATE_MULTIPLIER,
        description="Each side ranks this many times the hits asked for before fusion.",
    )
    max_hits_per_nectar: int = Field(
        default=DEFAULT_MAX_HITS_PER_NECTAR,
        ge=1,
        description="The most hits one deposit may contribute to a single result.",
    )
    budget_fraction: float = Field(
        default=DEFAULT_BUDGET_FRACTION,
        gt=0,
        le=MAX_BUDGET_FRACTION,
        description="The share of a reader's context window one result may fill; the caller "
        "scales HoneyQuery.max_tokens by it.",
    )
    max_budget_tokens: int = Field(
        default=DEFAULT_MAX_BUDGET_TOKENS,
        gt=0,
        description="A hard ceiling on one result's token budget, whatever the window.",
    )
    precheck_max_hits: int = Field(
        default=DEFAULT_PRECHECK_MAX_HITS,
        ge=0,
        le=MAX_PRECHECK_MAX_HITS,
        description="Hits the Queen's pre-check attaches to an assignment (roadmap step 7.9), "
        "live Cell Wax included; 0 turns the pre-check off.",
    )
    embed_timeout_s: float = Field(
        default=DEFAULT_EMBED_TIMEOUT_S,
        gt=0,
        description="The most a query waits for its own embedding before searching text only.",
    )

    @model_validator(mode="after")
    def _weights_never_both_zero(self) -> HoneyRetrievalSection:
        """Reject two zero weights: a fused score divides by their sum."""
        if self.fts_weight + self.vector_weight <= 0:
            raise ValueError("[honey.retrieval] fts_weight and vector_weight cannot both be zero.")
        return self
