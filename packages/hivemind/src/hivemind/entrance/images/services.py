"""Define what the image door needs: the Nectar seam, the device byte budget and the rules.

The image intake (roadmap step 12.12) is handed one ``ImageServices`` by the composition root, as
voice is handed ``VoiceServices``: the Nectar seam an image is deposited through (the same
instance voice keeps its clips in, so one retention sweep and one set of memory bounds cover
both), ``ByteBudget``, the per-device bucket of image bytes a minute, and ``ImageRules``, the
``[entrance]`` settings the door decides with. Unlike voice there is no model to bind and no
switch, so a Hive always has one.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.images``. Built by
    ``hive serve``'s composition root (``hivemind.cli.compose.entrance``) and by tests; read by the
    image door. Calls into the voice package's Nectar seam, the limiter's ``ByteBudget`` and the
    manifest's ``[entrance]`` section.

Key invariants:
    - ``max_bytes`` is the manifest's, and the manifest refuses one above the route's raw-body
      ceiling, so the body limit never refuses an image the door would have accepted.

See Also:
    - hivemind.entrance.images.door for how each is used.
    - hivemind.entrance.voice.nectar for the seam.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from hivemind.entrance.auth.limits import ByteBudget
from hivemind.entrance.voice.nectar import MediaNectar
from hivemind.manifest.schema.entrance import EntranceSection

__all__ = ["ImageRules", "ImageServices"]


@dataclass(frozen=True, slots=True)
class ImageRules:
    """The ``[entrance]`` settings the image door decides with.

    Attributes:
        max_bytes: The largest image accepted, in bytes.
        retention: How long a deposited image stays before the sweep deletes it.
    """

    max_bytes: int
    retention: timedelta

    @classmethod
    def from_section(cls, section: EntranceSection) -> ImageRules:
        """Read the rules from ``[entrance]``.

        Args:
            section: The manifest's entrance section.

        Returns:
            The rules.
        """
        return cls(
            max_bytes=section.max_image_bytes,
            retention=timedelta(hours=section.image_keep_hours),
        )


@dataclass(frozen=True, slots=True)
class ImageServices:
    """Everything the image door needs besides the Entrance's own services.

    Attributes:
        nectar: Where an image is deposited; in memory until phase 7.
        budget: Each device's bytes a minute, charged whole before an image is kept.
        rules: The ``[entrance]`` settings.
    """

    nectar: MediaNectar
    budget: ByteBudget
    rules: ImageRules
