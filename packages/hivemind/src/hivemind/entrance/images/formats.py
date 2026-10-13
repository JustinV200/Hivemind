"""Name the image formats the Hive takes, and check that a body really is one of them.

A device labels its image with a ``Content-Type``; the label is a claim, and the image door
(roadmap step 12.12) takes only three: PNG, JPEG and WebP, the formats every phone camera,
screenshot tool and share sheet produces. ``ImageMediaType`` is those three, ``media_type_of``
reads a label (parameters and case ignored) into one, and ``looks_like`` checks the body's first
bytes against that format's signature. The check is deliberately shallow, a few bytes and no
decoder: the image is deposited as Nectar, never opened here, so what it guards is a mislabelled
or empty body being filed as an image, not a hostile file, which no decoder in this process ever
meets.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.images``. Used by the
    image route and the image door. Calls into the standard library only.

Key invariants:
    - A body passes ``looks_like`` only when its leading bytes are its format's signature.
    - Nothing here decodes, resizes or reads past a body's first twelve bytes.

See Also:
    - hivemind.entrance.images.door for the checks that come with these.
"""

from __future__ import annotations

from enum import StrEnum

# A WebP file starts with "RIFF", four bytes of container size, then the form type "WEBP".
_WEBP_FORM_START = 8
_WEBP_FORM_END = 12

__all__ = ["ImageMediaType", "looks_like", "media_type_of"]


class ImageMediaType(StrEnum):
    """The image formats the Hive takes; the value is the ``Content-Type`` that names it."""

    PNG = "image/png"
    JPEG = "image/jpeg"
    WEBP = "image/webp"


def media_type_of(label: str | None) -> ImageMediaType | None:
    """Read a ``Content-Type`` header into an accepted image format.

    Args:
        label: The header's value, e.g. ``image/png`` or ``Image/PNG; charset=binary``; or None
            when the request named none.

    Returns:
        The format, or None when there is no label or it names something else.
    """
    if label is None:
        return None
    # Parameters after the semicolon and the label's case say nothing about the format.
    essence = label.split(";", 1)[0].strip().lower()
    try:
        return ImageMediaType(essence)
    except ValueError:
        return None


def looks_like(media_type: ImageMediaType, data: bytes) -> bool:
    """Say whether ``data`` begins as a file of ``media_type`` does.

    Args:
        media_type: The format the device claimed.
        data: The body.

    Returns:
        True when the leading bytes are that format's signature.
    """
    if media_type is ImageMediaType.PNG:
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if media_type is ImageMediaType.JPEG:
        return data.startswith(b"\xff\xd8\xff")
    # WebP is a RIFF container: the form type sits after the container's own size field.
    return data.startswith(b"RIFF") and data[_WEBP_FORM_START:_WEBP_FORM_END] == b"WEBP"
