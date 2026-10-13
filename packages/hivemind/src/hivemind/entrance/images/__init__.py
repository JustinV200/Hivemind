"""Take images from enrolled devices at the Hive Entrance: raw bodies, deposited as C2 Nectar.

Roadmap step 12.12 (ADR-0047): the Android client's share sheet hands the Hive text, audio and
images, and the Landing Board had no route for the last. ``POST /v1/nectar/images`` takes a PNG,
JPEG or WebP as the raw request body, labelled by its ``Content-Type``, with an optional caption
and task in the query. The door refuses what it cannot take before anything is kept (a foreign
format, a body larger than ``[entrance] max_image_bytes``, one that does not begin as its format
does, a device past its image bytes for the minute) and deposits the rest as Nectar labelled
``C2``, since it comes from the human, with where it came from and a retention window, through the
same Nectar seam voice keeps its audio in. The image is never decoded, returned or logged.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance``. The route joins the
    route table (``hivemind.entrance.routes.registry``); the services are built by ``hive
    serve``'s composition root. Calls into the gate, the limiter's byte budget and voice's Nectar
    seam.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - Every deposited image is ``C2`` and expires; no refused image is kept or charged.

See Also:
    - .claude/codingrules.md section 8.15 for the rule.
    - docs/adr/0047-android-client-capacitor-and-push-delivery.md for why the route exists.

Public API:
    - deposit_image, Upload: the one door (door).
    - ImageMediaType, media_type_of, looks_like: the formats taken (formats).
    - ImageDepositView, DepositParams, MAX_CAPTION_CHARS: the Landing Board's shapes (models).
    - IMAGE_ROUTES, IMAGE_BODY, IMAGES_PATH: ``POST /v1/nectar/images`` (route).
    - ImageServices, ImageRules: what depositing needs (services).
    - ImageTypeRefusedError, ImageTooLargeError, ImageMalformedError, ImageOverBudgetError: the
      refusals (errors).
"""

from hivemind.entrance.images.door import Upload, deposit_image
from hivemind.entrance.images.errors import (
    ImageMalformedError,
    ImageOverBudgetError,
    ImageTooLargeError,
    ImageTypeRefusedError,
)
from hivemind.entrance.images.formats import ImageMediaType, looks_like, media_type_of
from hivemind.entrance.images.models import MAX_CAPTION_CHARS, DepositParams, ImageDepositView
from hivemind.entrance.images.route import IMAGE_BODY, IMAGE_ROUTES, IMAGES_PATH
from hivemind.entrance.images.services import ImageRules, ImageServices

__all__ = [
    "IMAGES_PATH",
    "IMAGE_BODY",
    "IMAGE_ROUTES",
    "MAX_CAPTION_CHARS",
    "DepositParams",
    "ImageDepositView",
    "ImageMalformedError",
    "ImageMediaType",
    "ImageOverBudgetError",
    "ImageRules",
    "ImageServices",
    "ImageTooLargeError",
    "ImageTypeRefusedError",
    "Upload",
    "deposit_image",
    "looks_like",
    "media_type_of",
]
