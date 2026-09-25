"""Define the shapes a Honey Store search passes between the store and its ranker.

`ReadFilter` is the one filter every `HoneyStore` read applies, always before ranking or limiting
(ADR-0035): a reader's scope globs, an optional set of exact scopes to narrow to, and a clearance
ceiling. `TextCandidate`/`VectorCandidate` are one raw hit each from the full-text and vector sides
of a hybrid search, before `hivemind.honey_store.honey` (retrieval, a later dispatch) fuses their
scores; kept as plain dataclasses rather than pydantic models because nothing here crosses a
process boundary; codingrules 8.5 reserves pydantic for that. `HoneyStats` is `HoneyStore.stats`'s
own boundary value (read by `hive honey stats` and, later, the Observation Hive), so it stays a
pydantic model like every value a caller outside this process eventually reads.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package. Built
    by `hivemind.honey_store.scope` (`ReadFilter.readable`, via `readable_globs`) and by
    `hivemind.honey_store.store` (every field of every shape here); consumed by
    `hivemind.honey_store.honey` (retrieval, a later dispatch) and by `hive honey stats`.

Key invariants:
    - `ReadFilter.readable`/`.requested` never mix scope kinds with clearance: every filtering
      axis this module names is independent, and `hivemind.honey_store.store` applies all of them
      before ranking, per ADR-0035 ("Filtering is policy, not ranking").
    - `HoneyStats` carries only counts and labels, never a hit's content, id or query text
      (codingrules section 12: the same rule the honey.* trail events themselves follow).

See Also:
    - docs/adr/0035-honey-store-sqlite-fts5-sqlite-vec.md for the filter-before-rank rule.
    - hivemind.honey_store.scope for `readable_globs`, which builds `ReadFilter.readable`.
    - hivemind.honey_store.store.protocol for the HoneyStore methods these shapes cross.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import HoneyClearance
from hivemind.honey_store.models.honey import Honey, HoneyPart
from hivemind.honey_store.models.nectar import NectarState

# codingrules 8.5: frozen, extra-forbidding config HoneyStats shares with every boundary model.
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")

__all__ = ["HoneyStats", "ReadFilter", "TextCandidate", "VectorCandidate"]


@dataclass(frozen=True, slots=True)
class ReadFilter:
    """The filter every `HoneyStore` read applies before ranking or limiting (ADR-0035).

    Attributes:
        readable: `honey:read` scope globs the caller holds (`hivemind.honey_store.scope.
            readable_globs`); a row's `scope` must GLOB-match at least one.
        max_clearance: The caller's clearance ceiling, already the three-way minimum
            `hivemind.honey_store.clearance.reader_ceiling` computes.
        requested: Exact scopes to narrow the search to; empty means every scope `readable`
            already allows. Ordering constraint (dataclasses put defaulted fields last): this is
            why `requested` follows `max_clearance` even though ADR-0035's prose lists it first.
    """

    readable: tuple[str, ...]
    max_clearance: HoneyClearance
    requested: tuple[str, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class TextCandidate:
    """One full-text search hit before ranking fusion: a Honey row and its raw bm25 score."""

    honey: Honey
    bm25: float


@dataclass(frozen=True, slots=True)
class VectorCandidate:
    """One vector search hit before ranking fusion: a Honey row and its cosine distance."""

    honey: Honey
    distance: float


class HoneyStats(BaseModel):
    """Aggregate counts over the Honey Store, for `hive honey stats` and the Observation Hive.

    Everything here is a count or a label; no content, id or query ever appears in it.
    """

    model_config = _MODEL_CONFIG

    nectar_by_state: dict[NectarState, int] = Field(
        description="Nectar rows grouped by state (RECEIVED, RIPENED, EPHEMERAL, DISCARDED)."
    )
    honey_by_part: dict[HoneyPart, int] = Field(
        description="Live (not tainted, not retired) Honey rows grouped by part."
    )
    honey_tainted: int = Field(ge=0, description="Honey rows marked tainted (never returned).")
    honey_retired: int = Field(ge=0, description="Honey rows retired (superseded, not returned).")
    honey_by_clearance: dict[HoneyClearance, int] = Field(
        description="Live Honey rows grouped by clearance label."
    )
    honey_by_scope_kind: dict[str, int] = Field(
        description="Live Honey rows grouped by scope kind: 'hive', 'cell', 'bee' or 'task'."
    )
    vectors_by_model: dict[str, int] = Field(
        description="Vector rows grouped by the embedding model that produced them."
    )
    vector_backend: str = Field(
        description="Which vector search backend answered the last query: 'sqlite_vec' or "
        "'python' (ADR-0035)."
    )
