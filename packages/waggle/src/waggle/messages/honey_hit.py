"""Define the honey family's retrieval result: one Honey hit and the provenance it carries.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). Its honey
family moves knowledge in and out of the Honey Store (the persistent knowledge base), and a
query's answer is a ranked list of hits. ``HoneyHit`` is one such result: the store's row key,
a title and a bounded excerpt, a score, the scope it was found in, its clearance (the
data-sensitivity label every quoted datum carries), the tier of the Cell (a unit of compute) it
originated on, and its ``HoneyProvenance`` (the task, Cell and bee it came from and when it was
observed). The two value models are split out of ``waggle.messages.honey`` by responsibility so
each file stays under the codingrules 5.1 size limit; ``SCOPE_PATTERN`` lives here because both
a hit and a query name scopes in the same shape. Every bound is a named constant here; the
number, not the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Imported by waggle.messages.honey, whose HoneyResponse
    carries a tuple of hits and whose HoneyQuery reuses the scope shape; calls into
    waggle.messages.base, waggle.messages.labels and waggle.ids only.

Key invariants:
    - Every model is frozen and forbids extras through VALUE_MODEL_CONFIG, like a message, but
      none subclasses WaggleMessage, so none can ever be registered as a kind.
    - A scope is exactly one of the Hive, one Cell, one bee or one task (SCOPE_PATTERN), so the
      store's scope filter never has to guess.

See Also:
    - docs/waggle/spec.md section 8.7 for the normative fields and bounds.
    - waggle.messages.honey for NectarDeposit, HoneyQuery and HoneyResponse.
    - waggle.messages.base for VALUE_MODEL_CONFIG and the id aliases.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

from waggle.ids import IdKind, WardenId, WorkerId
from waggle.messages.base import (
    VALUE_MODEL_CONFIG,
    CellIdField,
    TaskIdField,
    UtcDatetime,
    id_validator,
)
from waggle.messages.labels import CombShieldLevel, HoneyClearance

MAX_HONEY_REF_CHARS = 256  # The store's row key, also its browser path: a short slug, no more.
MAX_HIT_TITLE_CHARS = 200  # One line in the browser and in a model's context.
MAX_EXCERPT_CHARS = 2_000  # A paragraph or two: enough to judge relevance, never the whole row.
MIN_SCORE = 0.0  # A relevance score is a fraction; nothing ranks below no match.
MAX_SCORE = 1.0  # A perfect match; the store normalises whatever its ranker produces.
MAX_SCOPE_CHARS = 64  # A scope label plus one id (cell:<CellId>); the longest id fits twice.
SCOPE_PATTERN = r"^(hive|cell:[^:]+|bee:[^:]+|task:[^:]+)$"  # The Hive, one Cell, bee or task.

__all__ = [
    "MAX_EXCERPT_CHARS",
    "MAX_HIT_TITLE_CHARS",
    "MAX_HONEY_REF_CHARS",
    "MAX_SCOPE_CHARS",
    "MAX_SCORE",
    "MIN_SCORE",
    "SCOPE_PATTERN",
    "HoneyHit",
    "HoneyProvenance",
]

# The bee a hit came from: a Worker or a Warden, validated against both prefixes.
_BeeId = Annotated[WorkerId | WardenId, id_validator(IdKind.WORKER, IdKind.WARDEN)]


class HoneyProvenance(BaseModel):
    """Where a Honey hit came from: the task, the Cell, the bee, and when it was observed.

    Rides on every HoneyHit so a reader can weigh a result by its source before trusting it.
    """

    model_config = VALUE_MODEL_CONFIG

    task_id: TaskIdField | None = Field(
        description="The task the finding came from; None when no task was involved."
    )
    cell_id: CellIdField | None = Field(
        description="The Cell the finding was gathered on; None when the store cannot say."
    )
    bee: _BeeId | None = Field(
        description="The Worker or Warden that gathered it; None when unknown."
    )
    observed_at: UtcDatetime = Field(description="When the finding was observed.")


class HoneyHit(BaseModel):
    """One retrieval result from the Honey Store, as a HoneyResponse lists it.

    Crosses the wire from the Queen to the asking bee; nothing in it is ever executed, and the
    excerpt reaches a model delimited and labelled as untrusted content.
    """

    model_config = VALUE_MODEL_CONFIG

    honey_ref: str = Field(
        max_length=MAX_HONEY_REF_CHARS,
        description="The store's row key, also its browser path.",
    )
    title: str = Field(max_length=MAX_HIT_TITLE_CHARS, description="The row's one-line title.")
    excerpt: str = Field(
        max_length=MAX_EXCERPT_CHARS,
        description="The passage that matched, bounded; the full row is fetched by honey_ref.",
    )
    score: float = Field(
        ge=MIN_SCORE, le=MAX_SCORE, description="Relevance, 0 (no match) to 1 (perfect)."
    )
    scope: str = Field(
        max_length=MAX_SCOPE_CHARS,
        pattern=SCOPE_PATTERN,
        description="The scope the hit was found in: hive, cell:<id>, bee:<id> or task:<id>.",
    )
    clearance: HoneyClearance = Field(description="The row's data-sensitivity label.")
    origin_tier: CombShieldLevel = Field(
        description="The tier of the Cell the Honey originated on; NIGHT_VEIL marks Honey that "
        "crossed that boundary.",
    )
    provenance: HoneyProvenance = Field(description="Where the hit came from.")
