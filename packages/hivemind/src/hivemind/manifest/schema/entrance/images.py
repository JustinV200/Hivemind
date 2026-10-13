"""Define the Hive Entrance's image-intake limits: the three ``[entrance]`` keys that bound images.

``POST /v1/nectar/images`` (roadmap step 12.12, ADR-0050) takes a PNG, JPEG or WebP from an
enrolled device and keeps it as C2 Nectar. Three settings bound it, and they sit flat under
``[entrance]`` (``EntranceSection`` inherits this model, so the manifest reads them there):
``max_image_bytes``, the largest image accepted, never above ``IMAGE_BYTES_CEILING``, which is the
route's raw-body allowance, so the schema refuses at load what the body limit would otherwise
refuse mid-upload; ``image_bytes_per_minute``, each device's byte budget, at least
``max_image_bytes`` so the largest image always fits a full bucket; and ``image_keep_hours``, the
retention window after which the sweep deletes a deposited image.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside ``hivemind.manifest.schema.entrance``.
    Inherited by ``EntranceSection``; read by ``hivemind.entrance.images`` (Layer 7) and the image
    route's raw-body ceiling. Calls into pydantic only.

Key invariants:
    - The model is frozen and forbids unknown fields (codingrules section 8.5).
    - ``max_image_bytes`` is at most ``IMAGE_BYTES_CEILING``, and the byte budget is at least
      ``max_image_bytes``: no accepted image can be one the budget or the body limit refuses.

See Also:
    - docs/adr/0050-android-client-capacitor-and-push-delivery.md for the route these bound.
    - hivemind.entrance.auth.limits.ByteBudget for the budget bucket.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

_MIB = 1024 * 1024  # Bytes in a mebibyte, for the image limits below.
# The most one image may be, whatever the manifest says: the raw-body limit of the image route is
# this, so a larger `max_image_bytes` is refused when the manifest loads, never silently clamped.
IMAGE_BYTES_CEILING = 32 * _MIB

__all__ = ["IMAGE_BYTES_CEILING", "EntranceImageLimits"]


class EntranceImageLimits(BaseModel):
    """The image intake's three limits, as ``[entrance]`` keys."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_image_bytes: int = Field(
        default=8 * _MIB,
        ge=1,
        le=IMAGE_BYTES_CEILING,
        description="The largest image (PNG, JPEG or WebP) POST /v1/nectar/images accepts, in "
        "bytes; a bigger one is a 413. At most 32 MiB.",
    )
    image_bytes_per_minute: int = Field(
        default=32 * _MIB,
        ge=1,
        description="Bytes of images one device may send a minute: a token bucket of that many "
        "bytes, refilling at that rate, charged each image's size. At least max_image_bytes, so "
        "the largest image always fits a full bucket.",
    )
    image_keep_hours: float = Field(
        default=24.0,
        gt=0,
        description="How long a deposited image stays as C2 Nectar before the retention sweep "
        "deletes it.",
    )

    @model_validator(mode="after")
    def _image_budget_fits_an_image(self) -> EntranceImageLimits:
        """Refuse an image budget smaller than the largest image: it could never be sent."""
        if self.image_bytes_per_minute < self.max_image_bytes:
            raise ValueError(
                f"[entrance] image_bytes_per_minute ({self.image_bytes_per_minute}) is below "
                f"max_image_bytes ({self.max_image_bytes}): an image that large could never fit "
                "a device's image budget."
            )
        return self
