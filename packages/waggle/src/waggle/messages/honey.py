"""Define the honey family: Nectar deposits into the Honey Store and Honey queries out of it.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). The honey
family is knowledge in and out of the Honey Store (the persistent knowledge base): Nectar (raw,
unprocessed findings a bee brings back) goes in with provenance and clearance, and Honey
(distilled, indexed knowledge) comes out on query, filtered by scope and clearance.
``NectarDeposit`` is one chunk of a deposit, content-addressed by the sha256 of the whole so
intake can hash, dedupe and reassemble; ``HoneyQuery`` searches by text within scopes, under a
hit count, a token budget and a clearance ceiling; ``HoneyResponse`` returns the hits that
survived filtering, with counts and a reason so the reader knows what was withheld without
seeing it. Nothing retrieved is ever executed; it reaches a model delimited and labelled as
untrusted content. The retrieval result and its provenance (``HoneyHit``, ``HoneyProvenance``)
live in ``waggle.messages.honey_hit``, split out by responsibility so each file stays under the
codingrules 5.1 size limit. Every bound is a named constant here; the number, not the name, is
normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Registered by waggle.messages.registry, which maps
    each class to its kind; built by Workers and Wardens (deposits, queries) and by the Queen
    (the central orchestrator, which owns the store and answers queries); calls into
    waggle.messages.base, waggle.messages.labels and waggle.messages.honey_hit only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - Every message is frozen and forbids extras through WaggleMessage's config.
    - Every rule the spec marks (validator) is a pydantic validator on the class; every rule it
      marks (receiver rule) is deliberately absent, because the receiver enforces it.
    - A NectarDeposit chunk is at most MAX_CHUNK_BYTES; a larger content is a sequence of
      deposits sharing sha256 and the envelope sender (spec section 5, the chunking rule).

See Also:
    - docs/waggle/spec.md section 8.7 for the normative fields, bounds and validators.
    - docs/waggle/spec.md section 5 for the chunking and reassembly rules NectarDeposit follows.
    - waggle.messages.honey_hit for HoneyHit and HoneyProvenance, the rest of section 8.7.
    - waggle.messages.registry for the kinds these classes are registered under.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import Field, model_validator

from waggle.ids import IdKind, WardenId, WorkerId
from waggle.messages.base import (
    MAX_CHUNK_BYTES,
    MAX_REASON_CHARS,
    SHA256_PATTERN,
    CellIdField,
    EventIdField,
    TaskIdField,
    UtcDatetime,
    WaggleMessage,
    WorkerIdField,
    id_validator,
)
from waggle.messages.honey_hit import MAX_SCOPE_CHARS, SCOPE_PATTERN, HoneyHit
from waggle.messages.labels import CombShieldLevel, HoneyClearance

MIN_MEDIA_TYPE_CHARS = 1  # A deposit always names its MIME type; the ripener picks by it.
MAX_MEDIA_TYPE_CHARS = 128  # A MIME type with parameters (text/plain; charset=utf-8), no more.
MAX_TITLE_CHARS = 200  # A one-line label for the browser and the ripener, never a summary.
MIN_OFFSET = 0  # A chunk's byte offset within the whole; the first chunk starts at 0.
MIN_TOTAL_BYTES = 1  # An empty deposit carries nothing to ripen.
DEFAULT_MAX_NECTAR_BYTES = 16_777_216  # 16 MiB: the receiver's default cap on one whole deposit.
MIN_QUERY_CHARS = 1  # An empty query matches nothing worth returning.
MAX_QUERY_CHARS = 2_000  # A question or a paragraph of context, never a document.
MAX_SCOPES = 16  # A bee's own task, Cell and a few shared scopes; empty means every readable one.
MIN_MAX_HITS = 1  # A query that wants no hits should not be sent.
MAX_MAX_HITS = 50  # More ranked hits than any model context can use at once.
DEFAULT_MAX_HITS = 10  # A page of results, the size a bee reads without a budget of its own.
MIN_MAX_TOKENS = 1  # A zero token budget can hold no hit.
MIN_TOKEN_COUNT = 0  # An empty response occupies no tokens.
MIN_FILTERED_COUNT = 0  # Nothing withheld is a valid, and common, answer.

__all__ = [
    "DEFAULT_MAX_HITS",
    "DEFAULT_MAX_NECTAR_BYTES",
    "MAX_MAX_HITS",
    "MAX_MEDIA_TYPE_CHARS",
    "MAX_QUERY_CHARS",
    "MAX_SCOPES",
    "MAX_TITLE_CHARS",
    "MIN_FILTERED_COUNT",
    "MIN_MAX_HITS",
    "MIN_MAX_TOKENS",
    "MIN_MEDIA_TYPE_CHARS",
    "MIN_OFFSET",
    "MIN_QUERY_CHARS",
    "MIN_TOKEN_COUNT",
    "MIN_TOTAL_BYTES",
    "HoneyQuery",
    "HoneyResponse",
    "NectarDeposit",
    "NectarKind",
]


class NectarKind(Enum):
    """What sort of finding a Nectar deposit is; Ripening (turning Nectar into Honey) picks by it.

    honey.nectar_deposit is the one kind permitted to carry transcript bytes; the test that no
    transcript travels up the tree whitelists it.
    """

    FINDING = "FINDING"
    TRANSCRIPT = "TRANSCRIPT"
    TOOL_RESULT = "TOOL_RESULT"
    PATROL_SUMMARY = "PATROL_SUMMARY"
    AUDIT_FINDING = "AUDIT_FINDING"
    FLIGHT_RECORDING = "FLIGHT_RECORDING"
    HANDOFF = "HANDOFF"  # A Handoff document, so every checkpoint reaches the Queen's Bee Bread.
    RIPENED_HONEY = "RIPENED_HONEY"  # Ripened on a Cell's own slots; outlives NIGHT_VEIL.


# A reason field, as the catalogue conventions fix it: always named `reason`, always bounded by
# the shared MAX_REASON_CHARS, so the Pheromone Trail (the append-only audit log) records why.
_Reason = Annotated[str, Field(max_length=MAX_REASON_CHARS)]
# A scope to search, shaped like HoneyHit.scope: the Hive, one Cell, one bee or one task.
_Scope = Annotated[str, Field(max_length=MAX_SCOPE_CHARS, pattern=SCOPE_PATTERN)]
# The bee asking a query: a Worker or a Warden, validated against both prefixes.
_RequesterId = Annotated[WorkerId | WardenId, id_validator(IdKind.WORKER, IdKind.WARDEN)]


class NectarDeposit(WaggleMessage):
    """Deposit one chunk of raw Nectar with provenance and clearance (honey.nectar_deposit).

    An event from a Worker or Warden to the Queen. Chunked and content-addressed by the sha256
    of the whole content, so intake can hash, dedupe and reassemble; the envelope sender plus
    sha256 is the group key every chunk of one deposit shares.
    """

    sha256: str = Field(
        pattern=SHA256_PATTERN,
        description="Digest of the complete content; with the envelope sender it is the key "
        "every chunk of one deposit shares, and alone the dedupe key of the completed row.",
    )
    kind: NectarKind = Field(description="What sort of finding.")
    media_type: str = Field(
        min_length=MIN_MEDIA_TYPE_CHARS,
        max_length=MAX_MEDIA_TYPE_CHARS,
        description="MIME type of the content.",
    )
    title: str = Field(
        max_length=MAX_TITLE_CHARS,
        description="A one-line label for the browser and the ripener.",
    )
    task_id: TaskIdField | None = Field(
        description="The task it came from; None for a Patrol or watch summary."
    )
    cell_id: CellIdField = Field(description="The Cell where it was gathered.")
    worker_id: WorkerIdField | None = Field(
        description="The Worker that gathered it; None when a Warden deposits."
    )
    observed_at: UtcDatetime = Field(
        description="When the finding was observed, not when this chunk was sent."
    )
    clearance: HoneyClearance = Field(
        description="Assigned from provenance; intake may raise it, never lower it."
    )
    origin_tier: CombShieldLevel = Field(
        description="The tier of the Cell it came from as the sender believes it; intake sets "
        "the stored tier from the Queen's record for cell_id, never from this field (receiver "
        "rule). From a NIGHT_VEIL Cell intake accepts only RIPENED_HONEY at C0 or C1 and keys "
        "every other deposit to the Cell's ephemeral segment (receiver rule).",
    )
    event_id: EventIdField | None = Field(
        description="For HANDOFF, the memory.checkpoint trail event the Handoff was recorded "
        "under; None for every other kind.",
    )
    chunk: bytes = Field(max_length=MAX_CHUNK_BYTES, description="This slice of the content.")
    offset: int = Field(ge=MIN_OFFSET, description="Byte offset within the content.")
    total_bytes: int = Field(
        ge=MIN_TOTAL_BYTES,
        description="Length of the complete content, so intake can pre-check its cap "
        "(DEFAULT_MAX_NECTAR_BYTES unless the manifest says otherwise; receiver rule).",
    )
    final: bool = Field(description="True on the last chunk; intake then verifies sha256.")

    @model_validator(mode="after")
    def _ripened_honey_from_night_veil_stays_below_c2(self) -> NectarDeposit:
        """Reject RIPENED_HONEY from a NIGHT_VEIL Cell labelled above C1."""
        # Ripened Honey is the one kind that outlives a Night Veil teardown, and only public or
        # internal knowledge may cross that boundary (codingrules section 12); a C2 label would
        # carry out the very data the tier exists to contain. Ranks are compared, never values.
        if (
            self.kind is NectarKind.RIPENED_HONEY
            and self.origin_tier is CombShieldLevel.NIGHT_VEIL
            and self.clearance.rank > HoneyClearance.C1.rank
        ):
            raise ValueError(
                f"RIPENED_HONEY from a NIGHT_VEIL Cell must be labelled C0 or C1, got "
                f"{self.clearance.value}."
            )
        return self

    @model_validator(mode="after")
    def _event_id_exactly_for_handoff(self) -> NectarDeposit:
        """Require event_id for a HANDOFF deposit and refuse it on every other kind."""
        # A Handoff is looked up in Bee Bread by the trail event that recorded it, so a HANDOFF
        # without one can never be resumed from; on any other kind an event id is a confused
        # sender, and refusing it keeps the field's meaning single.
        if (self.kind is NectarKind.HANDOFF) != (self.event_id is not None):
            raise ValueError(
                f"NectarDeposit event_id is set exactly when kind is HANDOFF, got kind "
                f"{self.kind.value} with event_id {self.event_id}."
            )
        return self


class HoneyQuery(WaggleMessage):
    """Search Honey by text within scopes, under a hit count, a token budget and a clearance cap.

    honey.query, a request from any bee to the Queen; answered by a HoneyResponse.
    """

    text: str = Field(
        min_length=MIN_QUERY_CHARS, max_length=MAX_QUERY_CHARS, description="The query."
    )
    requester: _RequesterId = Field(
        description="The bee asking, so scope, clearance and tier filtering key on it and not "
        "on the Warden that relays the query. Equals the envelope sender on the first hop "
        "(receiver rule).",
    )
    scopes: tuple[_Scope, ...] = Field(
        max_length=MAX_SCOPES,
        description="Scopes to search; empty means every scope the caller may read.",
    )
    max_hits: int = Field(
        default=DEFAULT_MAX_HITS,
        ge=MIN_MAX_HITS,
        le=MAX_MAX_HITS,
        description="Upper bound on hits.",
    )
    max_tokens: int = Field(ge=MIN_MAX_TOKENS, description="Result token budget.")
    max_clearance: HoneyClearance = Field(
        description="The highest label the caller wants back; the store caps further by the "
        "caller's capabilities and tier policy.",
    )
    task_id: TaskIdField | None = Field(
        description="The task the query serves; None when no task is involved (a Patrol, or "
        "the Queen's or Warden's own work).",
    )


class HoneyResponse(WaggleMessage):
    """Return the hits that survived scope, clearance and budget filtering (honey.response).

    A reply from the Queen to the asking bee, with counts and a reason so the reader knows what
    was withheld without seeing it.
    """

    hits: tuple[HoneyHit, ...] = Field(max_length=MAX_MAX_HITS, description="Ranked results.")
    token_count: int = Field(ge=MIN_TOKEN_COUNT, description="Tokens the hits occupy.")
    is_truncated: bool = Field(description="True when the budget or max_hits cut ranked results.")
    filtered_count: int = Field(
        ge=MIN_FILTERED_COUNT,
        description="Ranked results withheld by scope, clearance or tier policy.",
    )
    reason: _Reason = Field(description="Why the result is what it is.")
