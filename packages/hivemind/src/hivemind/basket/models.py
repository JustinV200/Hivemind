"""Define the Basket's value models: Blob, DepositRequest, HoneyRecord and the Honey port.

The Basket holds the bytes work produces that Honey cannot represent (a wheel, a dataset, a tool
package). A `Blob` is the immutable description of one: its sha256, its size, what kind of thing it
is (a closed `MediaKind` tag), which operating systems it runs on, how sensitive it is
(`HoneyClearance`, from its provenance) and where it came from (Cell, task, bee). A blob is
content-addressed: the sha256 *is* its identity, so the same bytes deposited twice are one blob.
`DepositRequest` is what a bee's Warden hands the deposit door (bytes plus the title and summary
that become the blob's one Honey record); `HoneyRecord` is what the door hands the Honey port, the
only way a bee later finds the blob (roadmap 9.2a). The signed wrapper lives in `manifest`.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.basket`. Built by
    `hivemind.basket.deposits` and read by `hivemind.basket.store`, `clearance`, `residency` and
    `transfer`. Calls into `hivemind.cell` (HoneyClearance, CombShieldLevel, OsFamily) and waggle
    ids only; never `honey_store`, which receives a `HoneyRecord` through a port instead.

Key invariants:
    - A blob is inert: nothing here describes how to run, load or install it.
    - A `Blob`'s sha256 is lowercase hex of 32 bytes (`waggle.messages.base.SHA256_PATTERN`) and
      its size is at least one byte; `platform_tags` hold each OS at most once, and an empty tuple
      means "any platform".
    - `Blob.state` is the one mutable fact about a blob (PRESENT to RETIRED); everything else is
      fixed at deposit and covered by the manifest signature.
    - A `DepositRequest` never prints its bytes (`repr=False`), so a log line cannot leak them.

See Also:
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md for the decisions behind these.
    - hivemind.basket.manifest for BlobManifest, the signed form of a Blob.
    - hivemind.honey_store.nectar for the deposit door whose shape `DepositRequest` mirrors.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hivemind.cell import CombShieldLevel, HoneyClearance, OsFamily
from waggle.ids import CellId, EventId, IdKind, TaskId
from waggle.messages.base import (
    SHA256_PATTERN,
    CellIdField,
    EventIdField,
    TaskIdField,
    UtcDatetime,
    id_validator,
)

MAX_MEDIA_TYPE_CHARS = 255  # RFC 6838's own bound on a type/subtype pair.
MAX_TITLE_CHARS = 200  # A Honey title is a line a bee scans in a list, not prose.
MAX_SUMMARY_CHARS = 2_000  # One paragraph: enough to say what a blob is for, short enough to rank.
MAX_BEE_CHARS = 64  # A worker_ or warden_ id, the same bound the trail puts on an actor.
MAX_PLATFORM_TAGS = len(
    OsFamily
)  # Each OS at most once, so there can never be more tags than OSes.
MAX_DEPOSIT_BYTES = (
    1 << 30
)  # 1 GiB: the model's hard ceiling; each door applies its own smaller cap.
# type/subtype, lowercase, the characters RFC 6838 allows in a name: no parameters, no spaces.
MEDIA_TYPE_PATTERN = r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$"
_BLANK_LINE = re.compile(r"\n\s*\n")  # A paragraph break: what "one paragraph" forbids.

__all__ = [
    "MAX_BEE_CHARS",
    "MAX_DEPOSIT_BYTES",
    "MAX_MEDIA_TYPE_CHARS",
    "MAX_PLATFORM_TAGS",
    "MAX_SUMMARY_CHARS",
    "MAX_TITLE_CHARS",
    "MEDIA_TYPE_PATTERN",
    "Blob",
    "BlobState",
    "DepositRequest",
    "HoneyRecord",
    "HoneyRecordPort",
    "MediaKind",
    "Sha256",
]

_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")  # codingrules 8.5: boundary values.

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
# A Worker or a Warden: the two kinds of bee that propose or deposit an artefact.
_BeeId = Annotated[str, id_validator(IdKind.WORKER, IdKind.WARDEN), Field(max_length=MAX_BEE_CHARS)]
_MediaType = Annotated[
    str, Field(min_length=3, max_length=MAX_MEDIA_TYPE_CHARS, pattern=MEDIA_TYPE_PATTERN)
]


class BlobState(Enum):
    """Whether a blob may still be read: PRESENT, or RETIRED (kept on disk, refused to readers)."""

    PRESENT = "PRESENT"  # The normal state: readable, shippable, resident-able.
    RETIRED = "RETIRED"  # Withdrawn by `retire`: the row and bytes stay, `open_bytes` refuses.


class MediaKind(Enum):
    """A closed, descriptive tag for what a blob is; it grants nothing (a blob is inert)."""

    WHEEL = "WHEEL"  # A Python wheel (or a set of them) for an offline install.
    ARCHIVE = "ARCHIVE"  # A tar or zip of files.
    DATASET = "DATASET"  # Tabular or structured data a task produced or needs.
    TOOL_PACKAGE = "TOOL_PACKAGE"  # A Comb tool's deterministic archive (ADR-0046).
    IMAGE = "IMAGE"  # A picture, a fixture screenshot or a container image layer.
    MODEL_WEIGHTS = "MODEL_WEIGHTS"  # The weights a Nuc will serve.
    TEXT = "TEXT"  # Plain text too large or too structured to be Nectar.
    OTHER = "OTHER"  # Anything else; the Honey record's summary says what.


def _unique_tags(tags: tuple[OsFamily, ...]) -> tuple[OsFamily, ...]:
    """Refuse a platform tuple that lists an OS twice (shared by every model carrying tags)."""
    if len(set(tags)) != len(tags):
        raise ValueError("platform_tags must list each operating system at most once.")
    return tags


class Blob(BaseModel):
    """The immutable description of one blob in the Basket, keyed by its content hash.

    Crosses the store boundary (a row), the signing boundary (`BlobManifest`) and the Waggle
    boundary (an offer), so every field is bounded and the whole model is frozen.
    """

    model_config = _MODEL_CONFIG

    sha256: Sha256 = Field(description="Lowercase hex SHA-256 of the blob's bytes; its identity.")
    size_bytes: int = Field(ge=1, le=MAX_DEPOSIT_BYTES, description="Length of the bytes.")
    media_kind: MediaKind = Field(description="A descriptive tag; it grants nothing.")
    media_type: _MediaType = Field(description="The blob's MIME type, lowercase, no parameters.")
    platform_tags: tuple[OsFamily, ...] = Field(
        default=(),
        max_length=MAX_PLATFORM_TAGS,
        description="The operating systems the blob runs on; empty means any platform.",
    )
    clearance: HoneyClearance = Field(description="Sensitivity label: declared, raised to floor.")
    origin_cell_id: CellIdField = Field(description="The Cell the bytes were produced on.")
    origin_task_id: TaskIdField | None = Field(
        default=None, description="The task that produced them, if one did."
    )
    origin_bee: _BeeId | None = Field(
        default=None, description="The Worker or Warden that proposed keeping them, if any."
    )
    deposited_at: UtcDatetime = Field(description="When the Basket took the blob in.")
    deposit_event_id: EventIdField | None = Field(
        default=None, description="Id of the `basket.deposited` trail event, once recorded."
    )
    tainted: bool = Field(
        default=False,
        description="True for a blob from an isolated or quarantined source; never shipped.",
    )
    state: BlobState = Field(default=BlobState.PRESENT, description="PRESENT or RETIRED.")

    @field_validator("platform_tags")
    @classmethod
    def _tags_unique(cls, tags: tuple[OsFamily, ...]) -> tuple[OsFamily, ...]:
        """Refuse a duplicated operating system in the tag tuple."""
        return _unique_tags(tags)


class DepositRequest(BaseModel):
    """What a Warden hands the deposit door: the bytes, their Honey text and their provenance.

    The door (`BasketDeposits`) enforces its own `max_blob_bytes`; `MAX_DEPOSIT_BYTES` is only the
    model's absolute ceiling. `title` and `summary` are written for a bee ranking Honey hits, so
    they never reach the blob's signed description or any trail event.
    """

    model_config = _MODEL_CONFIG

    content: bytes = Field(
        min_length=1, max_length=MAX_DEPOSIT_BYTES, repr=False, description="The blob's bytes."
    )
    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS, description="Honey title.")
    summary: str = Field(
        min_length=1, max_length=MAX_SUMMARY_CHARS, description="One-paragraph Honey summary."
    )
    media_kind: MediaKind = Field(description="What kind of thing the bytes are.")
    media_type: _MediaType = Field(description="The bytes' MIME type.")
    platform_tags: tuple[OsFamily, ...] = Field(
        default=(), max_length=MAX_PLATFORM_TAGS, description="Operating systems; empty is any."
    )
    declared: HoneyClearance | None = Field(
        default=None, description="The depositor's own label; the floor may raise it."
    )
    origin_cell_id: CellIdField = Field(description="The Cell the bytes were produced on.")
    origin_task_id: TaskIdField | None = Field(default=None, description="The producing task.")
    origin_bee: _BeeId | None = Field(default=None, description="The proposing Worker or Warden.")
    from_borrowed_cell: bool = Field(
        default=False, description="True when produced on a Real (borrowed) Cell: floor is C2."
    )
    tier: CombShieldLevel = Field(
        default=CombShieldLevel.MEADOW, description="Comb Shield tier of the origin Cell."
    )
    tainted: bool = Field(
        default=False, description="From the depositing Warden's taint ledger (ADR-0043)."
    )

    @field_validator("platform_tags")
    @classmethod
    def _tags_unique(cls, tags: tuple[OsFamily, ...]) -> tuple[OsFamily, ...]:
        """Refuse a duplicated operating system in the tag tuple."""
        return _unique_tags(tags)

    @field_validator("summary")
    @classmethod
    def _one_paragraph(cls, summary: str) -> str:
        """Refuse a summary with a blank line: the Honey record is one paragraph (roadmap 9.2a)."""
        if _BLANK_LINE.search(summary):
            raise ValueError("summary must be a single paragraph, with no blank line.")
        return summary


class HoneyRecord(BaseModel):
    """The one Honey record a new blob gets: its text plus the hash that locates it.

    Handed to a `HoneyRecordPort`; the composition root's adapter turns it into a Nectar
    submission, so this package never imports the Honey Store.
    """

    model_config = _MODEL_CONFIG

    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS, description="Honey title.")
    summary: str = Field(
        min_length=1, max_length=MAX_SUMMARY_CHARS, description="One-paragraph summary."
    )
    sha256: Sha256 = Field(description="The hash that locates the blob.")
    media_kind: MediaKind = Field(description="What kind of thing the blob is.")
    platform_tags: tuple[OsFamily, ...] = Field(default=(), description="Operating systems.")
    clearance: HoneyClearance = Field(description="The blob's label; the record carries it too.")
    origin_cell_id: CellId = Field(description="The Cell the blob came from.")
    origin_task_id: TaskId | None = Field(default=None, description="The producing task.")
    origin_bee: str | None = Field(default=None, description="The proposing Worker or Warden.")
    deposited_at: datetime = Field(description="When the blob was deposited.")
    deposit_event_id: EventId | None = Field(default=None, description="The deposit's trail event.")


class HoneyRecordPort(Protocol):
    """Where the deposit door writes a new blob's Honey record (the composition root plugs in).

    The adapter submits a `NectarSubmission` with `source_key` `basket:<sha256>`, so a retried
    call for the same blob is deduplicated by Honey itself.
    """

    async def record(self, record: HoneyRecord) -> None:
        """Write the Honey record for a newly deposited blob.

        Args:
            record: The title, summary, hash, kind, platforms, label and origin to keep.

        Raises:
            Exception: Whatever the adapter's Honey intake raises; the door lets it propagate so
                no blob is stored whose record was never written.
        """
        ...
