"""Define the image intake's Landing Board shapes: the query a device sends, and what it is told.

An image arrives at ``POST /v1/nectar/images`` as the raw request body (ADR-0040: no multipart
parser), labelled by its ``Content-Type``; the query carries the two optional things a device
knows about it, a short ``caption`` and the ``task_id`` it concerns. The Hive answers ``202`` with
``ImageDepositView``: the Nectar id the image was given, its size and format, when it was kept and
when the retention sweep will delete it. None of it is content, so the route needs no clearance
beyond ``entrance:submit``; the image itself is deposited as ``C2`` and never comes back out of
this route. Both models forbid unknown fields, so a stray query parameter is a 422 rather than
silently ignored.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.images``. Used by the
    image route and door; published in the OpenAPI document by ``hivemind.entrance.landing_board``.
    Calls into waggle's id fields, the image formats and pydantic.

Key invariants:
    - The view carries ids, sizes, a format and times only: no caption, no byte of the image.
    - A caption is short (a label, not a document): it is bounded where it is parsed.

See Also:
    - hivemind.entrance.images.door for what builds an ImageDepositView.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from hivemind.entrance.images.formats import ImageMediaType
from waggle.messages.base import NectarIdField, TaskIdField

MAX_CAPTION_CHARS = 280  # A caption labels an image; anything longer is a chat message.
_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Immutable, no strays.

__all__ = ["MAX_CAPTION_CHARS", "DepositParams", "ImageDepositView"]


class DepositParams(BaseModel):
    """``POST /v1/nectar/images``'s query: what the device knows about the image."""

    model_config = _CONFIG

    caption: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_CAPTION_CHARS,
        repr=False,
        description="A few words about the image (C2); kept beside it, never echoed back.",
    )
    task_id: TaskIdField | None = Field(
        default=None, description="The task the image concerns; absent when it concerns none."
    )


class ImageDepositView(BaseModel):
    """An image kept as C2 Nectar: where to find it, and until when."""

    model_config = _CONFIG

    nectar_id: NectarIdField = Field(description="The Nectar id the image was given.")
    size_bytes: int = Field(ge=1, description="The image's size in bytes.")
    media_type: ImageMediaType = Field(description="Its format: png, jpeg or webp.")
    deposited_at: datetime = Field(description="When the Hive kept it.")
    expires_at: datetime = Field(
        description="When the retention sweep deletes it ([entrance] image_keep_hours later)."
    )
