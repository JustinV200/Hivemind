"""Define Nectar: raw findings at rest in the Honey Store, before and after they are received.

Nectar is unprocessed information a Worker (a bee performing one role in a task) brings back --
a transcript, a tool result, a Patrol summary -- deposited before it has been ripened into
anything queryable (the Ripener, a later dispatch, turns it into Honey). `NectarOrigin` is *how*
a deposit reached the store (a bee over Waggle, a verified task outcome, Bee Bread aged into
ripening, cleared Cell Wax, the human, watch mode); `NectarKind`
(`waggle.messages.honey.NectarKind`) is *what kind of finding* it is. `NectarDraft` is what a
caller builds before calling `HoneyStore.add_nectar`; `Nectar` is the stored row `add_nectar`
returns, carrying every draft field but the content bytes, which the store serves separately
(`HoneyStore.nectar_content`) so a caller listing pending Nectar never pulls megabytes of content
it does not need. `NectarSource` is one extra provenance record: when a later deposit deduplicates
onto an existing `Nectar` row by content rather than by `source_key`, and its provenance differs
from the stored row's own, the store keeps this record of who else sent it (ADR-0037), returned by
`HoneyStore.nectar_sources`.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package. Built
    by `hivemind.honey_store.scope` and `.clearance` (which compute `scope` and `clearance`
    before a draft is constructed) and by `hivemind.honey_store.nectar` (intake, a later
    dispatch); persisted by `hivemind.honey_store.store`.

Key invariants:
    - `NectarDraft`/`Nectar` are frozen pydantic models (codingrules 8.5): a state change (RECEIVED
      -> RIPENED, a raised label) always produces a new `Nectar` via `model_copy`, never a mutation.
    - `content` never appears on `Nectar`, only on `NectarDraft`: once stored, content is read back
      only through `HoneyStore.nectar_content`, never inlined into a row a caller might log.
    - `bee` accepts a WorkerId or a WardenId (a Warden may deposit Patrol summaries and watch
      observations with no Worker involved), validated the same way
      `waggle.messages.honey.hit.HoneyProvenance.bee` is.

See Also:
    - docs/adr/0035-honey-store-sqlite-fts5-sqlite-vec.md for the fields this module's shape backs.
    - hivemind.honey_store.scope for `scope_for_nectar`, which computes `NectarDraft.scope`.
    - hivemind.honey_store.clearance for `intake_label`, which computes `NectarDraft.clearance`.
    - waggle.messages.honey for NectarKind, the wire enum this module reuses unchanged.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.manifest.schema.honey import MAX_NECTAR_BYTES_CEILING
from waggle.ids import CellId, EventId, IdKind, TaskId, WardenId, WorkerId
from waggle.messages.base import (
    NectarIdField,
    UtcDatetime,
    id_validator,
)
from waggle.messages.honey import NectarKind
from waggle.messages.honey.exchange import (
    MAX_MEDIA_TYPE_CHARS,
    MAX_TITLE_CHARS,
    MIN_MEDIA_TYPE_CHARS,
)
from waggle.messages.honey.hit import MAX_SCOPE_CHARS, SCOPE_PATTERN

MAX_SOURCE_KEY_CHARS = 200  # "handoff:<event id>" and friends (ADR-0035); short, always fits.
MIN_NECTAR_BYTES = 1  # An empty deposit carries nothing to ripen.

# codingrules 8.5: frozen, extra-forbidding config every model in this module shares.
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")

# A scope string, shaped like waggle.messages.honey.hit.HoneyHit's own: the Hive, one Cell, one
# bee or one task. Re-validated here (rather than trusted from the caller) because a NectarDraft
# can be built directly in tests and by future dispatches, not only through hivemind.honey_store
# .scope's own builders.
_ScopeField = Annotated[str, Field(max_length=MAX_SCOPE_CHARS, pattern=SCOPE_PATTERN)]
# The bee a deposit came from: a Worker or a Warden, exactly as
# waggle.messages.honey.hit.HoneyProvenance.bee validates it.
_BeeIdField = Annotated[WorkerId | WardenId, id_validator(IdKind.WORKER, IdKind.WARDEN)]

__all__ = [
    "MAX_SOURCE_KEY_CHARS",
    "MIN_NECTAR_BYTES",
    "Nectar",
    "NectarDraft",
    "NectarOrigin",
    "NectarSource",
    "NectarState",
]


class NectarOrigin(Enum):
    """How a Nectar deposit reached the store; distinct from `NectarKind`, what it contains.

    Drives both the intake clearance floor (`hivemind.honey_store.clearance.intake_floor`) and
    the scope a deposit lands in (`hivemind.honey_store.scope.scope_for_nectar`), per ADR-0035.
    """

    BEE = "BEE"  # A Worker or Warden deposit over Waggle; scope follows its NectarKind.
    TASK_OUTCOME = "TASK_OUTCOME"  # A verified task's own outcome: shared knowledge, hive scope.
    BEE_BREAD = "BEE_BREAD"  # An aged Bee Bread entry the House Bee is ripening (warm -> cold).
    CELL_WAX = "CELL_WAX"  # Cleared or expired Cell Wax, ripened into that Cell's history.
    HUMAN = "HUMAN"  # The operator's own proposed note.
    WATCH = "WATCH"  # A Real Cell's Warden observing in WATCH mode (codingrules 8.7).


class NectarState(Enum):
    """A Nectar row's own lifecycle; Appendix C's "Knowledge tier" row: NECTAR -> HONEY."""

    RECEIVED = "RECEIVED"  # Stored, not yet ripened; a candidate for the next ripening pass.
    RIPENED = "RIPENED"  # At least one Honey row exists for it; ripening will not retry it.
    EPHEMERAL = (
        "EPHEMERAL"  # A Night Veil Cell's own side channel; never ripened, purged at teardown.
    )
    DISCARDED = "DISCARDED"  # Failed ripening past the attempt cap; kept for audit, never retried.


class NectarDraft(BaseModel):
    """What a caller builds before `HoneyStore.add_nectar`: one deposit's content and provenance.

    Every field here is already decided by the caller (intake validates and fills `clearance` and
    `scope` from `hivemind.honey_store.clearance`/`.scope` before construction); the store only
    persists it, dedupes it and stamps `id`, `sha256`, `size_bytes` and `received_at`.
    """

    model_config = _MODEL_CONFIG

    kind: NectarKind = Field(description="What sort of finding this is (waggle.messages.honey).")
    origin: NectarOrigin = Field(description="How this deposit reached the store.")
    media_type: str = Field(
        min_length=MIN_MEDIA_TYPE_CHARS,
        max_length=MAX_MEDIA_TYPE_CHARS,
        description="MIME type of the content.",
    )
    title: str = Field(
        max_length=MAX_TITLE_CHARS, description="A one-line label for the browser and the ripener."
    )
    content: bytes = Field(
        min_length=MIN_NECTAR_BYTES,
        max_length=MAX_NECTAR_BYTES_CEILING,
        description="The raw content; served back only through HoneyStore.nectar_content.",
    )
    task_id: TaskId | None = Field(
        description="The task it came from; None for a Patrol or watch summary."
    )
    cell_id: CellId = Field(description="The Cell where it was gathered.")
    bee: _BeeIdField | None = Field(
        default=None, description="The Worker or Warden that gathered it; None otherwise."
    )
    observed_at: UtcDatetime = Field(description="When the finding was observed.")
    clearance: HoneyClearance = Field(
        description="Already raised to the intake floor (hivemind.honey_store.clearance)."
    )
    origin_tier: CombShieldLevel = Field(
        description="The Comb Shield tier of the Cell it came from, from the Queen's own record."
    )
    scope: _ScopeField = Field(description="Already computed (hivemind.honey_store.scope).")
    source_key: str | None = Field(
        default=None,
        max_length=MAX_SOURCE_KEY_CHARS,
        description="An internal dedupe key ('handoff:<event id>'); None for a bee's own deposit.",
    )
    event_id: EventId | None = Field(
        default=None, description="For a HANDOFF, the memory.checkpoint event id it came from."
    )
    ephemeral_cell_id: CellId | None = Field(
        default=None,
        description="Set to the Night Veil Cell's id when this row must land EPHEMERAL "
        "(ADR-0035); None for an ordinary deposit.",
    )


class Nectar(BaseModel):
    """A stored Nectar row: every `NectarDraft` field but `content`, plus what the store adds.

    Returned by `HoneyStore.get_nectar`/`pending_nectar`/`mark_nectar_failed`; the content bytes
    are fetched separately through `HoneyStore.nectar_content` so listing pending Nectar never
    pulls megabytes a caller does not need.
    """

    model_config = _MODEL_CONFIG

    id: NectarIdField = Field(description="This row's own id.")
    sha256: str = Field(description="Digest of the complete content; the store's own dedupe key.")
    size_bytes: int = Field(ge=MIN_NECTAR_BYTES, description="Length of the content, in bytes.")
    kind: NectarKind = Field(description="What sort of finding this is.")
    origin: NectarOrigin = Field(description="How this deposit reached the store.")
    media_type: str = Field(
        min_length=MIN_MEDIA_TYPE_CHARS,
        max_length=MAX_MEDIA_TYPE_CHARS,
        description="MIME type of the content.",
    )
    title: str = Field(max_length=MAX_TITLE_CHARS, description="A one-line label.")
    task_id: TaskId | None = Field(description="The task it came from, if any.")
    cell_id: CellId = Field(description="The Cell where it was gathered.")
    bee: _BeeIdField | None = Field(
        default=None, description="The Worker or Warden that gathered it."
    )
    observed_at: UtcDatetime = Field(description="When the finding was observed.")
    received_at: UtcDatetime = Field(description="When this row was stored.")
    clearance: HoneyClearance = Field(description="This row's current clearance label.")
    origin_tier: CombShieldLevel = Field(description="The Comb Shield tier it came from.")
    scope: _ScopeField = Field(description="Where this row is filed for read capability checks.")
    source_key: str | None = Field(
        default=None,
        max_length=MAX_SOURCE_KEY_CHARS,
        description="The internal dedupe key, if any.",
    )
    event_id: EventId | None = Field(default=None, description="The originating checkpoint event.")
    ephemeral_cell_id: CellId | None = Field(
        default=None, description="The Night Veil Cell this row's EPHEMERAL state belongs to."
    )
    state: NectarState = Field(description="This row's place in the Nectar -> Honey pipeline.")
    ripen_attempts: int = Field(ge=0, description="How many ripening passes have tried and failed.")
    tainted: bool = Field(description="Reserved for phase 10's taint marker (codingrules 10.6d).")


class NectarSource(BaseModel):
    """One extra source whose deposit deduplicated onto `nectar_id` by content (ADR-0037).

    Recorded once per distinct provenance: a duplicate found by `source_key` (the same source
    delivered again) or whose (source_key, task, Cell, bee) already equals the stored Nectar row's
    own never adds a row (`HoneyStore.add_nectar`'s own uniqueness rule), so this table grows with
    distinct sources, never with retries. `has_source` answers true for a key recorded here just as
    it does for `Nectar.source_key`.
    """

    model_config = _MODEL_CONFIG

    nectar_id: NectarIdField = Field(description="The Nectar row this source deduplicated onto.")
    source_key: str | None = Field(
        default=None,
        max_length=MAX_SOURCE_KEY_CHARS,
        description="This source's own internal dedupe key, if it had one.",
    )
    task_id: TaskId | None = Field(description="The task this source came from, if any.")
    cell_id: CellId = Field(description="The Cell where this source was gathered.")
    bee: _BeeIdField | None = Field(
        default=None, description="The Worker or Warden that gathered this source."
    )
    observed_at: UtcDatetime = Field(description="When this source's finding was observed.")
    received_at: UtcDatetime = Field(description="When this source's deposit reached intake.")
    origin: NectarOrigin = Field(description="How this source's deposit reached the store.")
    origin_tier: CombShieldLevel = Field(description="The Comb Shield tier this source came from.")
    clearance: HoneyClearance = Field(description="The label this source declared on arrival.")
    event_id: EventId | None = Field(
        default=None, description="For a HANDOFF, the memory.checkpoint event id it came from."
    )
