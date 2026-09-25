"""Define Honey: ripened, retrievable knowledge, and the draft shape the Ripener builds it from.

Honey is Nectar (raw findings) once it has been chunked, summarised, embedded, deduped and
indexed (roadmap 7.5, a later dispatch) -- the shape a Worker actually queries before acting.
Every Nectar ripens into one `SUMMARY` row (title, summary, key facts) and one `CHUNK` row per
chunk of its text (ADR-0035); `HoneyPart` names which. `HoneyDraft` is what the Ripener builds per
part; `Honey` is the stored row, carrying every draft field plus the provenance, scope and
lifecycle fields copied from the Nectar it came from. `Honey.path` is both its browser path
(roadmap 7.10) and `waggle.messages.honey.hit.HoneyHit.honey_ref`; `Honey.to_hit` is the one place
a stored row becomes the wire shape a query response carries.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package. Built
    by `hivemind.honey_store.ripening` (a later dispatch) and persisted and read back by
    `hivemind.honey_store.store`; `to_hit` is called by `hivemind.honey_store.honey` (retrieval, a
    later dispatch) once a candidate has a score and an excerpt.

Key invariants:
    - `HoneyDraft`/`Honey` are frozen pydantic models (codingrules 8.5): a label raise, a retire or
      a re-embed always produces a new `Honey` via `model_copy`, never a mutation.
    - `(nectar_id, part, chunk_index)` is unique per `Honey` row (enforced by the schema); ripening
      is idempotent on that triple (`HoneyStore.ripen`'s own contract).
    - `embedding_model` names the most recently written vector's model as a convenience only; a
      row may hold vectors for more than one model at once while a re-embed is in flight
      (ADR-0036), so whether a row is current for a given model is decided by
      `HoneyStore.pending_vectors`, never by reading this field alone.

See Also:
    - docs/adr/0035-honey-store-sqlite-fts5-sqlite-vec.md for the fields this module's shape backs.
    - docs/adr/0036-embedding-provider-and-reembedding-policy.md for `embedding_model`'s caveat.
    - hivemind.honey_store.scope for `honey_ref`/`folder_for_scope`, `Honey.path`'s own logic.
    - waggle.messages.honey.hit for HoneyHit and HoneyProvenance, `to_hit`'s return shape.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.honey_store.models.nectar import NectarOrigin, _BeeIdField, _ScopeField
from waggle.ids import CellId, TaskId
from waggle.messages.base import HoneyIdField, NectarIdField, UtcDatetime
from waggle.messages.honey import NectarKind
from waggle.messages.honey.exchange import MAX_TITLE_CHARS
from waggle.messages.honey.hit import (
    MAX_EXCERPT_CHARS,
    MAX_HIT_TITLE_CHARS,
    MAX_SCORE,
    MIN_SCORE,
    HoneyHit,
    HoneyProvenance,
)

MAX_SUMMARY_CHARS = 1_000  # A paragraph: title, gist and key facts, never the whole chunk.
MAX_BODY_CHARS = 8_000  # A chunk's own text; bounded so one Honey row never dwarfs a result budget.
MIN_CHUNK_INDEX = 0  # A SUMMARY row is chunk_index 0 too; CHUNK rows count up from there.

# codingrules 8.5: frozen, extra-forbidding config every model in this module shares.
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")

# A ripener model id, exactly as recorded on the wire's LlmEvent.provider (a manifest provider
# name, never a bare model id -- codingrules 8.6 forbids literal model ids outside manifest/docs,
# but a *label saying which binding ran* is metadata, not a hard-coded choice).
_RipenerModelField = Annotated[str, Field(max_length=128)]

__all__ = [
    "MAX_BODY_CHARS",
    "MAX_SUMMARY_CHARS",
    "MIN_CHUNK_INDEX",
    "Honey",
    "HoneyDraft",
    "HoneyPart",
]


class HoneyPart(Enum):
    """Which part of a ripened Nectar one Honey row is (ADR-0035: one SUMMARY, many CHUNK)."""

    SUMMARY = "SUMMARY"  # Title, summary and key facts for the whole Nectar.
    CHUNK = "CHUNK"  # One chunk of the Nectar's text, indexed by chunk_index.


class HoneyDraft(BaseModel):
    """What the Ripener builds per part of a Nectar, before `HoneyStore.ripen` stores it.

    Provenance, scope, kind, origin and origin tier are not here: `HoneyStore.ripen` copies them
    straight from the Nectar row being ripened, so the Ripener never has to repeat them.
    """

    model_config = _MODEL_CONFIG

    part: HoneyPart = Field(description="SUMMARY or CHUNK.")
    chunk_index: int = Field(
        ge=MIN_CHUNK_INDEX, description="0 for SUMMARY; the chunk's position within the text."
    )
    title: str = Field(max_length=MAX_TITLE_CHARS, description="A one-line label.")
    summary: str = Field(
        max_length=MAX_SUMMARY_CHARS, description="Title, gist and key facts; SUMMARY rows only."
    )
    body: str = Field(max_length=MAX_BODY_CHARS, description="The indexed text of this part.")
    clearance: HoneyClearance = Field(
        description="May be raised above the Nectar's own label; never lowered here."
    )
    ripener_model: _RipenerModelField | None = Field(
        default=None, description="The RIPENER slot's binding that wrote this part, if any."
    )


class Honey(BaseModel):
    """A stored, retrievable Honey row: a `HoneyDraft` plus the Nectar's copied provenance.

    Returned by `HoneyStore.get_honey`/`honey_for_nectar`/`list_honey`/`ripen`.
    """

    model_config = _MODEL_CONFIG

    id: HoneyIdField = Field(description="This row's own id.")
    nectar_id: NectarIdField = Field(description="The Nectar row this was ripened from.")
    part: HoneyPart = Field(description="SUMMARY or CHUNK.")
    chunk_index: int = Field(ge=MIN_CHUNK_INDEX, description="0 for SUMMARY; the chunk's position.")
    title: str = Field(max_length=MAX_TITLE_CHARS, description="A one-line label.")
    summary: str = Field(max_length=MAX_SUMMARY_CHARS, description="Title, gist and key facts.")
    body: str = Field(max_length=MAX_BODY_CHARS, description="The indexed text of this part.")
    body_sha256: str = Field(description="Digest of `body`, for near-duplicate and change checks.")
    clearance: HoneyClearance = Field(description="This row's current clearance label.")
    ripener_model: _RipenerModelField | None = Field(
        default=None, description="The RIPENER slot's binding that wrote this part, if any."
    )
    kind: NectarKind = Field(description="Copied from the Nectar this was ripened from.")
    origin: NectarOrigin = Field(description="Copied from the Nectar this was ripened from.")
    scope: _ScopeField = Field(description="Copied from the Nectar this was ripened from.")
    origin_tier: CombShieldLevel = Field(
        description="Copied from the Nectar this was ripened from."
    )
    task_id: TaskId | None = Field(description="Copied from the Nectar this was ripened from.")
    cell_id: CellId = Field(description="Copied from the Nectar this was ripened from.")
    bee: _BeeIdField | None = Field(default=None, description="Copied from the source Nectar.")
    observed_at: UtcDatetime = Field(description="Copied from the Nectar this was ripened from.")
    created_at: UtcDatetime = Field(description="When this Honey row was written.")
    tainted: bool = Field(description="Reserved for phase 10's taint marker (codingrules 10.6d).")
    retired_at: UtcDatetime | None = Field(
        default=None, description="When this row was retired (superseded); None while live."
    )
    embedding_model: str | None = Field(
        default=None,
        description="The most recently written vector's model; see the module docstring's "
        "caveat about coexisting models during a re-embed.",
    )

    @property
    def path(self) -> str:
        """This row's browser path (roadmap 7.10), also `HoneyHit.honey_ref`.

        Returns:
            `"<folder>/<honey id>"`, where the folder comes from `self.scope`.
        """
        # Deferred import: hivemind.honey_store.scope imports
        # hivemind.honey_store.models.nectar for NectarOrigin, so a module-level import here would
        # cycle back before either module finished initialising (see that module's docstring).
        from hivemind.honey_store.scope import honey_ref

        return honey_ref(self.scope, self.id)

    def to_hit(self, score: float, excerpt: str) -> HoneyHit:
        """Build the wire hit a HoneyResponse carries for this row.

        Args:
            score: This hit's fused relevance score, 0 (no match) to 1 (perfect).
            excerpt: The passage that matched; capped to `MAX_EXCERPT_CHARS` before it is sent.

        Returns:
            A HoneyHit ready to ride on a HoneyResponse; nothing in it is ever executed, and the
            excerpt reaches a model delimited and labelled as untrusted content (codingrules 15).
        """
        return HoneyHit(
            honey_ref=self.path,
            title=self.title[:MAX_HIT_TITLE_CHARS],
            excerpt=excerpt[:MAX_EXCERPT_CHARS],
            score=max(MIN_SCORE, min(MAX_SCORE, score)),
            scope=self.scope,
            clearance=self.clearance.to_wire(),
            origin_tier=self.origin_tier.to_wire(),
            provenance=HoneyProvenance(
                task_id=self.task_id,
                cell_id=self.cell_id,
                bee=self.bee,
                observed_at=self.observed_at,
            ),
        )
