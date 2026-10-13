"""Serve ``POST /v1/nectar/images``: an image, as a raw body with its media type, kept as C2 Nectar.

An enrolled device sends the Hive a picture with one request (ADR-0050; ADR-0040: a raw body with
its media type, so no multipart parser is added): the image as the body, labelled by its
``Content-Type`` (``image/png``, ``image/jpeg`` or ``image/webp``), and an optional query with a
short ``caption`` and the ``task_id`` it concerns. The body is read whole, within the row's own
allowance (the manifest's ceiling, 32 MiB), before the request's signature is checked, since the
signature covers its SHA-256; the door then holds it to ``[entrance] max_image_bytes``. It needs
``entrance:submit`` and nothing else: the answer is an id, a size, a format and times, never
content. Both listeners serve it. Every refusal is the ``ErrorBody`` of its status: 413 (larger
than ``max_image_bytes``), 415 (a format the Hive does not take), 422 (empty, or not the format it
claims), 429 (the device's image budget or its request rate).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.images``. Registered in
    the route table by ``hivemind.entrance.routes.registry``. Calls into the image door.

Key invariants:
    - The image is checked before anything is deposited, and deposited only as C2.
    - The answer never carries the image or its caption.

See Also:
    - docs/adr/0050-android-client-capacitor-and-push-delivery.md, "The share sheet is an intake".
    - hivemind.entrance.images.door for what depositing does.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Query, Request

from hivemind.entrance.gate.params import CallerParam, Services
from hivemind.entrance.gate.spec import (
    BOTH_LISTENERS,
    RawBody,
    RouteEffect,
    RouteSpec,
    session_with,
)
from hivemind.entrance.images.door import Upload, deposit_image
from hivemind.entrance.images.errors import PAYLOAD_TOO_LARGE, UNSUPPORTED_MEDIA_TYPE
from hivemind.entrance.images.formats import ImageMediaType
from hivemind.entrance.images.models import DepositParams, ImageDepositView
from hivemind.manifest.schema.entrance import IMAGE_BYTES_CEILING

IMAGES_PATH = "/v1/nectar/images"  # Nectar is where an image goes; the resource is its intake.
IMAGE_READ_TIMEOUT_S = 120.0  # A 32 MiB photo over a slow phone uplink; a stalled one lets go.
SUBMIT = "entrance:submit"  # Giving the Hive something to read is giving it work.
CONTENT_TYPE_HEADER = "content-type"  # The body's label; what the image's format is read from.
# The raw bodies the route takes: every accepted format, up to the manifest's own ceiling.
IMAGE_BODY = RawBody(
    media_types=tuple(format_.value for format_ in ImageMediaType),
    max_bytes=IMAGE_BYTES_CEILING,
    read_timeout_s=IMAGE_READ_TIMEOUT_S,
)
# Beyond the refusals every row declares: what only an image can be answered with.
IMAGE_REFUSALS: tuple[tuple[int, str], ...] = (
    (PAYLOAD_TOO_LARGE, "The image is larger than [entrance] max_image_bytes."),
    (UNSUPPORTED_MEDIA_TYPE, "The Content-Type is missing or not image/png, jpeg or webp."),
)

__all__ = ["IMAGES_PATH", "IMAGE_BODY", "IMAGE_ROUTES", "deposit"]


async def deposit(
    request: Request,
    caller: CallerParam,
    services: Services,
    params: Annotated[DepositParams, Query()],
) -> ImageDepositView:
    """Keep the request's body as an image in Nectar, C2, for its retention window.

    Args:
        request: The request; its body is the image and its Content-Type the image's format.
        caller: The admitted caller.
        services: The Entrance's services.
        params: The optional caption and task.

    Returns:
        Where the image was kept: its Nectar id, size, format and times.
    """
    # Latency: none; the body limit already read and buffered it, and the gate hashed it.
    upload = Upload(await request.body(), request.headers.get(CONTENT_TYPE_HEADER), params)
    return await deposit_image(services, caller, upload)


IMAGE_ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="POST",
        path=IMAGES_PATH,
        listeners=BOTH_LISTENERS,
        access=session_with(SUBMIT),
        effect=RouteEffect.INBOX,
        endpoint=deposit,
        summary="Hand the Hive an image: kept as C2 Nectar for its retention window.",
        status_code=202,
        response_model=ImageDepositView,
        raw_body=IMAGE_BODY,
        refusals=IMAGE_REFUSALS,
    ),
)
