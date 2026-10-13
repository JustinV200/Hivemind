"""Re-export the ``[entrance]`` section and its sub-tables: listeners, voice, push, TLS, Android.

The Hive Entrance's settings are one concern across five files (codingrules 5.2): ``section``
holds ``EntranceSection``, the listeners, sessions and thresholds; ``images`` holds the image
intake's three limits, which ``EntranceSection`` inherits so they sit flat under ``[entrance]``;
``subsections`` holds the TLS, push and voice tables; ``android`` holds the Android client's
identity. A caller
imports any of them from here, or from ``hivemind.manifest.schema``, without knowing which file
defines it.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside ``hivemind.manifest.schema``.
    Imported by ``hivemind.manifest.schema``'s own face and by ``hivemind.entrance`` (Layer 7).
    Calls into this package's own modules only.

Key invariants:
    - This file holds re-exports and ``__all__`` only; no model or validator is defined here
      (codingrules section 5.4).

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md for the listeners.
    - docs/adr/0047-android-client-capacitor-and-push-delivery.md for the Android section.

Public API:
    - EntranceSection, EntranceExposure, split_host_port, DEFAULT_ENTRANCE_BIND,
      DEFAULT_VPN_CIDRS (section).
    - EntranceImageLimits, IMAGE_BYTES_CEILING: the image intake's limits (images).
    - EntranceTlsSection, EntrancePushSection, EntranceVoiceSection (subsections).
    - EntranceAndroidSection, DEFAULT_WEBVIEW_ORIGIN (android).
"""

from hivemind.manifest.schema.entrance.android import DEFAULT_WEBVIEW_ORIGIN, EntranceAndroidSection
from hivemind.manifest.schema.entrance.images import IMAGE_BYTES_CEILING, EntranceImageLimits
from hivemind.manifest.schema.entrance.section import (
    DEFAULT_ENTRANCE_BIND,
    DEFAULT_VPN_CIDRS,
    EntranceExposure,
    EntranceSection,
    split_host_port,
)
from hivemind.manifest.schema.entrance.subsections import (
    EntrancePushSection,
    EntranceTlsSection,
    EntranceVoiceSection,
)

__all__ = [
    "DEFAULT_ENTRANCE_BIND",
    "DEFAULT_VPN_CIDRS",
    "DEFAULT_WEBVIEW_ORIGIN",
    "IMAGE_BYTES_CEILING",
    "EntranceAndroidSection",
    "EntranceExposure",
    "EntranceImageLimits",
    "EntrancePushSection",
    "EntranceSection",
    "EntranceTlsSection",
    "EntranceVoiceSection",
    "split_host_port",
]
