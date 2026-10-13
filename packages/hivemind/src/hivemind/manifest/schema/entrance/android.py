"""Define the ``[entrance.android]`` section: the Android client's identity, for the Entrance.

The Android client (roadmap step 12.12, ADR-0047) is the Observation Hive wrapped by Capacitor as
an APK. Three of the Entrance's behaviours depend on which app that is, and all three are
configuration, because a signing key is the operator's own: the Digital Asset Links file at
``/.well-known/assetlinks.json`` (the app's package name and the SHA-256 fingerprints of its
signing certificates), the extra passkey origins the Android Credential Manager reports
(``android:apk-key-hash:<fingerprint>``, one per fingerprint) and the origin the bundled WebView
is pinned to, which CORS and the sockets accept beside ``public_url``. The section is *configured*
only when both a package name and at least one fingerprint are given; unconfigured, the Entrance
serves no asset-links file and accepts no origin it did not before. The schema checks only what a
value looks like; a fingerprint is stored upper-case and colon-separated, the form Google's tools
print, whatever case it was written in.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside ``hivemind.manifest.schema.entrance``.
    Embedded by ``EntranceSection``; read by ``hivemind.entrance.android`` (Layer 7). Calls into
    pydantic and the standard library only.

Key invariants:
    - The section is frozen and forbids unknown fields (codingrules section 8.5).
    - A package name without a fingerprint, or the reverse, is refused: half an app identity can
      neither be served nor trusted, and silently serving nothing would hide the typo.
    - Every stored fingerprint is exactly 32 bytes, upper-case hex pairs joined by colons.
    - ``webview_origin`` is a bare ``https`` origin: a host and an optional port, nothing else.
    - No secret lives here: a certificate fingerprint is public by design.

See Also:
    - docs/adr/0047-android-client-capacitor-and-push-delivery.md for what each setting does.
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md for the passkey
      relying party the APK origins join.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# What Capacitor's `server.hostname` and `androidScheme` pin the bundled build to (ADR-0047).
DEFAULT_WEBVIEW_ORIGIN = "https://observation.hive.local"
_MAX_FINGERPRINTS = 8  # Signing certificates: a debug key, a release key and a rotation or two.
_MAX_PACKAGE_CHARS = 255  # Android's own limit on an application id.
_MAX_ORIGIN_CHARS = 253  # A DNS name's limit; an origin adds a scheme and a port to it.
# A Java-style application id: dot-separated segments, each starting with a letter.
_PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+")
# A SHA-256 fingerprint as `keytool` prints it: 32 hex pairs joined by colons.
_FINGERPRINT = re.compile(r"([0-9A-Fa-f]{2}:){31}[0-9A-Fa-f]{2}")

__all__ = ["DEFAULT_WEBVIEW_ORIGIN", "EntranceAndroidSection"]


class EntranceAndroidSection(BaseModel):
    """``[entrance.android]``: the Android app the Entrance vouches for (ADR-0047)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    package_name: str = Field(
        default="",
        max_length=_MAX_PACKAGE_CHARS,
        description="The Android application id (e.g. `net.example.hivemind`), published in "
        "/.well-known/assetlinks.json; empty leaves the Android client unconfigured.",
    )
    cert_fingerprints: tuple[str, ...] = Field(
        default=(),
        max_length=_MAX_FINGERPRINTS,
        description="SHA-256 fingerprints of the app's signing certificates, as `AA:BB:...` "
        "(32 pairs). Each is published in assetlinks.json and adds the passkey origin "
        "`android:apk-key-hash:<base64url of the fingerprint>`; rotating the signing key means "
        "updating this list.",
    )
    webview_origin: str = Field(
        default=DEFAULT_WEBVIEW_ORIGIN,
        max_length=_MAX_ORIGIN_CHARS,
        description="The origin the bundled WebView is pinned to (Capacitor's server.hostname "
        "and androidScheme); accepted by CORS and the sockets beside public_url, and only "
        "while package_name and cert_fingerprints are set.",
    )

    @property
    def configured(self) -> bool:
        """Whether the Android client is configured: a package name and a fingerprint are set.

        Returns:
            True when the Entrance should vouch for the app; False leaves every Android
            behaviour off.
        """
        return bool(self.package_name and self.cert_fingerprints)

    @field_validator("package_name")
    @classmethod
    def _package_is_an_application_id(cls, value: str) -> str:
        """Reject a package name that is not a dotted Java-style application id."""
        if value and _PACKAGE.fullmatch(value) is None:
            raise ValueError(f"[entrance.android] package_name {value!r} is not an application id.")
        return value

    @field_validator("cert_fingerprints")
    @classmethod
    def _fingerprints_are_sha256(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject a fingerprint that is not 32 colon-separated hex pairs; store them upper-case."""
        for fingerprint in value:
            if _FINGERPRINT.fullmatch(fingerprint) is None:
                raise ValueError(
                    "[entrance.android] cert_fingerprints entries must be SHA-256 fingerprints "
                    "written as 32 colon-separated hex pairs (AA:BB:...)."
                )
        # One spelling per fingerprint, so a duplicate written in another case is still one.
        return tuple(dict.fromkeys(fingerprint.upper() for fingerprint in value))

    @field_validator("webview_origin")
    @classmethod
    def _origin_is_a_bare_https_origin(cls, value: str) -> str:
        """Reject a WebView origin that is not `https://host[:port]` with nothing after it."""
        parts = urlsplit(value)
        bare = (
            parts.scheme == "https" and bool(parts.hostname) and value == f"https://{parts.netloc}"
        )
        if not bare or parts.username or parts.password:
            raise ValueError(f"[entrance.android] webview_origin {value!r} must be https://host.")
        return value

    @model_validator(mode="after")
    def _identity_is_whole(self) -> EntranceAndroidSection:
        """Refuse a package name without a fingerprint, or a fingerprint without a package."""
        if bool(self.package_name) != bool(self.cert_fingerprints):
            raise ValueError(
                "[entrance.android] needs package_name and cert_fingerprints together: the "
                "asset-links file and the passkey origins name both."
            )
        return self
