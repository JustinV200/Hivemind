"""Take one image at the door: every check, then the deposit as C2 Nectar.

An enrolled device (an Android share sheet handing over a photo, a glasses companion app) sends an
image on ``POST /v1/nectar/images`` (roadmap step 12.12, ADR-0050, codingrules 8.15: "images a
client sends are deposited as Nectar with clearance C2"). Everything that can refuse it runs first
and in the cheapest order, so a refused image is never kept and costs the device nothing: its
``Content-Type`` is PNG, JPEG or WebP (415), its size is within ``[entrance] max_image_bytes``
(413), its first bytes are the format it claims and it is not empty (422), and the device still
has image bytes in its minute's budget (429), charged the image's whole size. Only then is it
deposited through the Nectar seam voice shares, labelled C2 whoever sent it, with where it came
from (the device, when, its caption and task) and the retention window that deletes it. Nothing is
decoded and nothing is read from the image: it is raw material for the Hive's ripening, not
content the Entrance understands. The image's bytes and caption never reach a log line or the
trail; the one log line carries the device, the size and the format.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.images``. Called by the
    image route. Calls into the gate (the admitted caller), the byte budget, the Nectar seam and
    waggle's ids.

Key invariants:
    - No image is deposited, and no budget spent, unless every check passed.
    - Every deposited image is C2 and expires: deposited_at plus the retention window.
    - The image's bytes and caption are never logged or recorded; only size and format are.

See Also:
    - .claude/codingrules.md section 8.15, "Voice is transcribed at the door", for the C2 rule.
    - hivemind.entrance.voice.door, the audio door this one mirrors.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hivemind.common.logging import get_logger
from hivemind.entrance.gate.admit import Caller
from hivemind.entrance.gate.services import EntranceServices
from hivemind.entrance.images.errors import (
    ImageMalformedError,
    ImageOverBudgetError,
    ImageTooLargeError,
    ImageTypeRefusedError,
)
from hivemind.entrance.images.formats import ImageMediaType, looks_like, media_type_of
from hivemind.entrance.images.models import DepositParams, ImageDepositView
from hivemind.entrance.images.services import ImageRules
from hivemind.entrance.voice.nectar import KeptImage
from waggle.ids import new_nectar_id

log = get_logger(__name__)

__all__ = ["Upload", "deposit_image"]


@dataclass(frozen=True, slots=True)
class Upload:
    """One image as a request carried it, not yet checked.

    Attributes:
        body: The raw body; C2, never in a repr.
        label: The request's ``Content-Type`` header, or None when it named none.
        params: The query: an optional caption and task.
    """

    body: bytes = field(repr=False)
    label: str | None
    params: DepositParams = field(repr=False)


async def deposit_image(
    services: EntranceServices, caller: Caller, upload: Upload
) -> ImageDepositView:
    """Check an image from an admitted device and deposit it as C2 Nectar.

    Args:
        services: The Entrance's services (the image services, the clock).
        caller: The admitted device (its session, capabilities and standing checked).
        upload: The body, its ``Content-Type`` and the query.

    Returns:
        Where the image was kept: its Nectar id, size, format and times.

    Raises:
        ImageTypeRefusedError: The ``Content-Type`` is missing or not PNG, JPEG or WebP.
        ImageTooLargeError: The body is larger than ``[entrance] max_image_bytes``.
        ImageMalformedError: The body is empty or does not begin as its format does.
        ImageOverBudgetError: The device has sent its image bytes for now.
    """
    images = services.images
    media_type = media_type_of(upload.label)
    if media_type is None:
        raise ImageTypeRefusedError()
    _within_rules(images.rules, media_type, upload.body)
    size = len(upload.body)
    # Charged whole, after every free check: an image the budget cannot pay for is never kept.
    if not images.budget.allow(caller.device.id, size):
        raise ImageOverBudgetError()
    kept = _kept(services, caller, media_type, upload)
    await images.nectar.deposit(kept)
    # Size and format only: the bytes and the caption are the human's own (C2).
    log.info(
        "entrance.image_deposited",
        device_id=caller.device.id,
        size_bytes=size,
        media_type=media_type.value,
    )
    return ImageDepositView(
        nectar_id=kept.ref,
        size_bytes=size,
        media_type=media_type,
        deposited_at=kept.kept_at,
        expires_at=kept.expires_at,
    )


def _kept(
    services: EntranceServices, caller: Caller, media_type: ImageMediaType, upload: Upload
) -> KeptImage:
    """Build the C2 Nectar item: the image, where it came from and when it expires."""
    now = services.clock.now()
    return KeptImage(
        ref=new_nectar_id(services.clock),
        device_id=caller.device.id,
        media_type=media_type.value,
        data=upload.body,
        caption=upload.params.caption,
        task_id=upload.params.task_id,
        kept_at=now,
        expires_at=now + services.images.rules.retention,
    )


def _within_rules(rules: ImageRules, media_type: ImageMediaType, body: bytes) -> None:
    """Refuse a body that is too large, or empty, or not the format its label claims."""
    if len(body) > rules.max_bytes:
        raise ImageTooLargeError(len(body), rules.max_bytes)
    if not looks_like(media_type, body):
        raise ImageMalformedError(media_type.value)
