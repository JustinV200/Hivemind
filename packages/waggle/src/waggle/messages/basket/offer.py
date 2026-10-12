"""Define the basket family's negotiation half: offering a blob, and refusing one.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). The Basket
is the Hive's store of content-addressed, signed blobs (an archive, a wheel, a dataset, a tool
package) that bytes travel in but that grant nothing: holding a blob never lets a bee run it. A
shipment starts with ``BasketOffer``, which names the blob by its sha256 and carries everything
the receiver needs to refuse it by rule before a single byte moves (its clearance, its taint, its
platform tags, its size and the signed manifest); the receiver answers with a ``BasketAck`` (see
``waggle.messages.basket.chunk``) to proceed or a ``BasketRefuse`` carrying a stable code. The
bytes themselves travel in ``BasketChunk`` messages, split out into ``chunk`` by responsibility so
each file stays under the codingrules 5.1 size limit. Every bound is a named constant here; the
number, not the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Registered by waggle.messages.registry, which maps
    each class to its kind; built by the Queen (the central orchestrator) and by Wardens (the
    always-on supervisors of one Cell, a unit of compute); calls into waggle.messages.base,
    waggle.messages.labels and waggle.messages.tool.json_text only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - Every message is frozen and forbids extras through WaggleMessage's config.
    - Every rule the spec marks (validator) is a pydantic validator on the class; every rule it
      marks (receiver rule) is deliberately absent, because the receiver enforces it.
    - The offer carries no bytes: the largest thing in it is the bounded manifest text.

See Also:
    - docs/waggle/spec.md section 8.12 for the normative fields, bounds and validators.
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md for the decision behind them.
    - waggle.messages.basket.chunk for BasketChunk and BasketAck, the family's other half.
    - waggle.messages.tool.json_text for the JSON-object check on manifest_json.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import AfterValidator, Field, field_validator

from waggle.ids import IdKind, WardenId, WorkerId
from waggle.messages.base import (
    MAX_REASON_CHARS,
    SHA256_PATTERN,
    CellIdField,
    TaskIdField,
    WaggleMessage,
    id_validator,
)
from waggle.messages.labels import HoneyClearance, OsFamily
from waggle.messages.tool.json_text import check_json_object_text

MIN_BLOB_BYTES = 1  # An empty blob holds nothing, and a zero-length transfer has no last chunk.
MAX_BLOB_BYTES = (
    68_719_476_736  # 64 GiB: a sanity bound on the claim; each receiver's ceiling is lower.
)
MEDIA_KIND_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"  # A descriptive tag: wheel, archive, tool_package.
MIN_MEDIA_TYPE_CHARS = 1  # A blob with no MIME type cannot be scanned or shown.
MAX_MEDIA_TYPE_CHARS = 128  # The longest registered MIME type fits with room to spare.
MAX_MANIFEST_CHARS = 8_192  # The signed BlobManifest: a blob record, a node id and a signature.

__all__ = [
    "MAX_BLOB_BYTES",
    "MAX_MANIFEST_CHARS",
    "MAX_MEDIA_TYPE_CHARS",
    "MEDIA_KIND_PATTERN",
    "MIN_BLOB_BYTES",
    "MIN_MEDIA_TYPE_CHARS",
    "BasketOffer",
    "BasketRefusal",
    "BasketRefuse",
]


class BasketRefusal(Enum):
    """Why a receiver refused a blob; a stable code a sender can act on without reading prose."""

    CLEARANCE = "CLEARANCE"  # The blob's label is above what the receiving Cell may hold.
    TAINTED = "TAINTED"  # The blob came from an isolated or quarantined source; never shipped.
    NO_ROOM = "NO_ROOM"  # Not even evicting every older blob would fit it under the ceiling.
    UNKNOWN_SIGNER = "UNKNOWN_SIGNER"  # The manifest was signed by a node the receiver trusts not.
    INTEGRITY = "INTEGRITY"  # The assembled bytes or the manifest signature failed verification.
    PLATFORM = "PLATFORM"  # None of the blob's platform tags fits the receiving Cell.
    UNKNOWN_BLOB = "UNKNOWN_BLOB"  # The sender holds no blob by this digest (a pull, or a resume).


# A reason field, as the catalogue conventions fix it: always named `reason`, always bounded by
# the shared MAX_REASON_CHARS, so the Pheromone Trail (the append-only audit log) records why.
_Reason = Annotated[str, Field(max_length=MAX_REASON_CHARS)]
# The bee a blob came from: a Worker or a Warden, validated against both prefixes.
_BeeId = Annotated[WorkerId | WardenId, id_validator(IdKind.WORKER, IdKind.WARDEN)]
# A descriptive media kind tag, snake_case and short, so a Honey record can filter on it.
_MediaKind = Annotated[str, Field(pattern=MEDIA_KIND_PATTERN)]
# The signed BlobManifest as text: bounded first, then proven to parse as a JSON object.
_ManifestText = Annotated[
    str, Field(max_length=MAX_MANIFEST_CHARS), AfterValidator(check_json_object_text)
]


class BasketOffer(WaggleMessage):
    """Offer one blob to a Cell's Warden, or to the Queen (basket.offer, a request).

    From the Queen to a Warden (ship a blob to a Cell) or from a Warden to the Queen (a deposit
    from a Cell). It carries no bytes: the receiver checks the claims against its own rules and
    answers with a BasketAck (send the chunks, or tell me where to resume from) or a
    BasketRefuse, before the first chunk moves.
    """

    sha256: str = Field(
        pattern=SHA256_PATTERN,
        description="Digest of the complete blob; it names the blob everywhere and is the key "
        "every chunk of this shipment shares.",
    )
    size_bytes: int = Field(
        ge=MIN_BLOB_BYTES,
        le=MAX_BLOB_BYTES,
        description="Length of the complete blob, so the receiver can check room before a byte "
        "moves.",
    )
    media_kind: _MediaKind = Field(
        description="A descriptive tag: wheel, archive, dataset, tool_package, text. It names "
        "what the blob is for a reader, and grants nothing.",
    )
    media_type: str = Field(
        min_length=MIN_MEDIA_TYPE_CHARS,
        max_length=MAX_MEDIA_TYPE_CHARS,
        description="MIME type of the content.",
    )
    platform_tags: tuple[OsFamily, ...] = Field(
        description="Platforms the blob is built for; empty means platform-independent. "
        "Members unique.",
    )
    clearance: HoneyClearance = Field(
        description="The blob's data-sensitivity label, set at deposit from its provenance; a "
        "receiver whose ceiling is lower refuses it (receiver rule)."
    )
    origin_cell_id: CellIdField = Field(description="The Cell the blob was first deposited from.")
    origin_task_id: TaskIdField | None = Field(
        description="The task it came from; None when no task was involved (an operator's deposit)."
    )
    origin_bee: _BeeId | None = Field(
        description="The Worker or Warden that produced it; None when unknown."
    )
    tainted: bool = Field(
        description="True when the blob's provenance names an isolated or quarantined source; a "
        "tainted blob is never shipped or loaded, and the receiver refuses it (receiver rule)."
    )
    manifest_json: _ManifestText = Field(
        description="The signed BlobManifest as JSON object text: the blob's record plus the "
        "signer's node id and an Ed25519 signature over those bytes. The receiver verifies it "
        "before keeping a byte (receiver rule).",
    )
    destination_cell_id: CellIdField = Field(
        description="The Cell whose resident Basket should hold the blob; must be the "
        "recipient's own Cell, or one the recipient relays for (receiver rule)."
    )
    reason: _Reason = Field(description="Why the blob is being shipped now.")

    @field_validator("platform_tags")
    @classmethod
    def _platform_tags_unique(cls, value: tuple[OsFamily, ...]) -> tuple[OsFamily, ...]:
        """Reject a platform listed twice."""
        # A repeated member is a sender bug; refusing it keeps the receiver's set semantics
        # honest, as ToolPromoted.supported_os does.
        if len(set(value)) != len(value):
            raise ValueError("BasketOffer platform_tags must be unique.")
        return value


class BasketRefuse(WaggleMessage):
    """Refuse one blob, with a stable code and a reason (basket.refuse, a reply).

    From the receiver of a BasketOffer back to its sender, before the first chunk moves; also
    sent mid-transfer for an INTEGRITY failure, when the assembled bytes do not hash to the
    offered digest. A refused blob is not retried by the same sender without a changed claim.
    """

    sha256: str = Field(pattern=SHA256_PATTERN, description="The blob refused.")
    code: BasketRefusal = Field(description="The stable reason code.")
    reason: _Reason = Field(
        description="The explanation for the Pheromone Trail: which rule, which limit."
    )
