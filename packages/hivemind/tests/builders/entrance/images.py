"""Send a serving rig's Entrance an image as a device does, and build the rigs that need limits.

The image intake (roadmap step 12.12) is exercised over the builders' serving rig, which always
serves it over an in-memory Nectar seam a test can read back (``rig.nectar``). ``image_rig``
builds the rig's options with any ``[entrance]`` image limits a test needs, ``android_rig`` the
same with an ``[entrance.android]`` section (and a remote listener) for the origin and asset-links
tests; ``png``, ``jpeg`` and ``webp`` make bodies that begin as their format does; ``deposit``
sends one on ``POST /v1/nectar/images`` as a raw body signed as sent, the way ``speak`` sends a
clip; ``UPLOADER`` is what a device that shares images is granted.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tests under
    packages/hivemind/tests/unit/entrance/images and .../android.

Key invariants:
    - A body made here is only its format's signature and zeros: no test depends on a decoder.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx
from builders.entrance.landing import LandingClient, LandingSession
from builders.entrance.serving import RIG_SECTION, ProgramGrant, RigOptions

from hivemind.manifest.schema.entrance import EntranceAndroidSection, EntranceSection

IMAGES = "/v1/nectar/images"  # The image intake route.
UPLOADER = ProgramGrant(capabilities=("entrance:submit",))  # All a device that shares images needs.
# A SHA-256 signing-certificate fingerprint as `keytool` prints it, and the origin a Credential
# Manager assertion from an app signed with it reports (the unpadded base64url of its 32 bytes).
FINGERPRINT = ":".join(f"{byte:02X}" for byte in range(0x10, 0x30))
APK_ORIGIN = "android:apk-key-hash:EBESExQVFhcYGRobHB0eHyAhIiMkJSYnKCkqKywtLi8"
PACKAGE = "net.example.hivemind"  # The Android application id the asset links name.
WEBVIEW = "https://observation.hive.local"  # Capacitor's pinned WebView origin, the default.

__all__ = [
    "APK_ORIGIN",
    "FINGERPRINT",
    "IMAGES",
    "PACKAGE",
    "UPLOADER",
    "WEBVIEW",
    "android_rig",
    "deposit",
    "image_rig",
    "jpeg",
    "png",
    "webp",
]


def png(size: int = 64) -> bytes:
    """Return ``size`` bytes beginning as a PNG file does."""
    return (b"\x89PNG\r\n\x1a\n" + bytes(size))[:size]


def jpeg(size: int = 64) -> bytes:
    """Return ``size`` bytes beginning as a JPEG file does."""
    return (b"\xff\xd8\xff\xe0" + bytes(size))[:size]


def webp(size: int = 64) -> bytes:
    """Return ``size`` bytes beginning as a WebP file does (a RIFF container of form WEBP)."""
    return (b"RIFF" + (size - 8).to_bytes(4, "little") + b"WEBP" + bytes(size))[:size]


def image_rig(**limits: object) -> RigOptions:
    """Return a rig's options with ``[entrance]`` image limits applied.

    Args:
        **limits: ``max_image_bytes``, ``image_bytes_per_minute`` or ``image_keep_hours``.

    Returns:
        The options.
    """
    section = EntranceSection.model_validate({**RIG_SECTION.model_dump(), **limits})
    return RigOptions(section=section)


def android_rig(**android: object) -> RigOptions:
    """Return a remote rig's options whose ``[entrance.android]`` is set, or overridden.

    Args:
        **android: ``[entrance.android]`` fields to set; the package and fingerprint above by
            default.

    Returns:
        The options, with a remote listener.
    """
    configured = {"package_name": PACKAGE, "cert_fingerprints": [FINGERPRINT], **android}
    section = RIG_SECTION.model_copy(
        update={"android": EntranceAndroidSection.model_validate(configured)}
    )
    return RigOptions(remote=True, section=section)


async def deposit(
    client: LandingClient,
    session: LandingSession,
    body: bytes,
    media_type: str | None = "image/png",
    **query: object,
) -> httpx.Response:
    """Send ``body`` to ``POST /v1/nectar/images`` as a raw body signed as sent.

    Args:
        client: The device's client.
        session: Its session.
        body: The image.
        media_type: The body's ``Content-Type``; None sends no label.
        **query: Query parameters (``caption``, ``task_id``).

    Returns:
        The response, whatever its status.
    """
    target = f"{IMAGES}?{urlencode(query)}" if query else IMAGES
    # The image is the body: the signature covers its SHA-256, exactly as a device's does.
    headers = client.signed_headers(session, "POST", target, body)
    if media_type is not None:
        headers["Content-Type"] = media_type
    return await client.http.request("POST", target, content=body, headers=headers)
