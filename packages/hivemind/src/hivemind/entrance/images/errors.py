"""Define every way the image door refuses an image, each with the status a device is answered with.

The image intake (roadmap step 12.12, ``POST /v1/nectar/images``) refuses for reasons no other
route has: the body is not one of the three formats (a missing or foreign ``Content-Type``, a
body that does not begin as its label says, or an empty one), it is larger than ``[entrance]
max_image_bytes``, or the device has sent its image bytes for the minute. Each is an
``EntranceError`` with its own stable code, answered with an ``ErrorBody`` of its status
(``http_status`` for the ones no ``hivemind.common.errors`` category covers). Every refusal
happens before the image is deposited, and none carries a byte of it: a message names a size or a
format, never content.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.images``. Raised by the
    image door. Calls into ``hivemind.entrance.errors`` and the gate's ``RateLimitedError`` only.

Key invariants:
    - Every class sets its own code; no message carries image bytes or a caption.

See Also:
    - hivemind.entrance.gate.handlers for how a refusal becomes a status and a body.
    - hivemind.entrance.voice.errors, the audio route's counterpart.
"""

from __future__ import annotations

from typing import ClassVar

from hivemind.entrance.errors import EntranceError
from hivemind.entrance.gate.errors import RateLimitedError

PAYLOAD_TOO_LARGE = 413  # An image larger than [entrance] max_image_bytes.
UNSUPPORTED_MEDIA_TYPE = 415  # A Content-Type that is missing or not PNG, JPEG or WebP.
UNPROCESSABLE = 422  # Well-formed HTTP carrying a body that is not the image it says it is.

__all__ = [
    "PAYLOAD_TOO_LARGE",
    "UNPROCESSABLE",
    "UNSUPPORTED_MEDIA_TYPE",
    "ImageMalformedError",
    "ImageOverBudgetError",
    "ImageTooLargeError",
    "ImageTypeRefusedError",
]


class ImageTypeRefusedError(EntranceError):
    """Raise when an image's ``Content-Type`` is missing or not PNG, JPEG or WebP."""

    code: ClassVar[str] = "hivemind.entrance.image_type_refused"
    http_status: ClassVar[int] = UNSUPPORTED_MEDIA_TYPE

    def __init__(self) -> None:
        """Build the error; one fixed message, since the accepted formats are fixed."""
        super().__init__("Images must be sent as image/png, image/jpeg or image/webp.")


class ImageTooLargeError(EntranceError):
    """Raise when an image is larger than ``[entrance] max_image_bytes``."""

    code: ClassVar[str] = "hivemind.entrance.image_too_large"
    http_status: ClassVar[int] = PAYLOAD_TOO_LARGE

    def __init__(self, size: int, limit: int) -> None:
        """Build the error.

        Args:
            size: The image's size in bytes.
            limit: The largest the Hive takes (``max_image_bytes``).
        """
        super().__init__(f"The image is {size} bytes; the Hive takes at most {limit}.")


class ImageMalformedError(EntranceError):
    """Raise when a body is empty, or does not begin as the format its label claims."""

    code: ClassVar[str] = "hivemind.entrance.image_malformed"
    http_status: ClassVar[int] = UNPROCESSABLE

    def __init__(self, label: str) -> None:
        """Build the error.

        Args:
            label: The format the device claimed (an accepted media type, never body content).
        """
        super().__init__(f"The body is not a {label} image.")


class ImageOverBudgetError(RateLimitedError):
    """Raise when a device has sent more image bytes this minute than its budget."""

    code: ClassVar[str] = "hivemind.entrance.image_over_budget"

    def __init__(self) -> None:
        """Build the error; one fixed message, since the budget is the manifest's."""
        # Past RateLimitedError's own fixed sentence, which speaks of requests, not bytes.
        super(RateLimitedError, self).__init__(
            "This device has sent its image budget for now; send again shortly."
        )
