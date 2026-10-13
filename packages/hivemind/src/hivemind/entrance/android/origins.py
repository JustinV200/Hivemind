"""Work out the extra origins the Android client brings: its APK key hashes and its WebView.

Two kinds of origin join the Entrance's own when ``[entrance.android]`` is configured (ADR-0050).
A passkey assertion made through the Android Credential Manager reports its origin as
``android:apk-key-hash:<hash>``, where the hash is the unpadded base64url of the SHA-256 of the
signing certificate, which is the very fingerprint the operator configures (and publishes in
``assetlinks.json``); so each configured fingerprint becomes one more origin the passkey relying
party accepts. The Capacitor WebView is pinned to one fixed origin (``webview_origin``), which
CORS and the sockets accept beside ``public_url``; that is safe because authentication is the
signed request, and an origin is a hint, not a credential. Unconfigured, every function here
returns nothing, so nothing is accepted that was not before.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.android``. Called by
    ``hivemind.entrance.runtime.build`` when it builds the relying party, the socket origins and
    the CORS list. Calls into the standard library and the manifest's Android section.

Key invariants:
    - An unconfigured section yields no origin at all.
    - An APK origin is derived only from a fingerprint the manifest validated (32 bytes).

See Also:
    - docs/adr/0050-android-client-capacitor-and-push-delivery.md, "Passkeys through the Android
      Credential Manager".
    - hivemind.entrance.auth.passkeys.RelyingParty for where the APK origins are accepted.
"""

from __future__ import annotations

import base64

from hivemind.manifest.schema.entrance import EntranceAndroidSection

APK_ORIGIN_PREFIX = "android:apk-key-hash:"  # What a Credential Manager assertion reports.
SHA256_BYTES = 32  # A certificate fingerprint is a SHA-256 digest.

__all__ = ["APK_ORIGIN_PREFIX", "apk_key_hash_origin", "apk_origins", "webview_origins"]


def apk_key_hash_origin(fingerprint: str) -> str:
    """Return the origin an Android app signed with ``fingerprint`` reports to WebAuthn.

    Args:
        fingerprint: A SHA-256 certificate fingerprint, ``AA:BB:...`` (32 pairs).

    Returns:
        ``android:apk-key-hash:`` and the unpadded base64url of the fingerprint's 32 bytes.

    Raises:
        ValueError: The fingerprint is not 32 colon-separated hex pairs.
    """
    pairs = fingerprint.split(":")
    if len(pairs) != SHA256_BYTES:
        raise ValueError("An APK key hash is the SHA-256 of a certificate: 32 bytes.")
    raw = bytes(int(pair, 16) for pair in pairs)
    return APK_ORIGIN_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def apk_origins(section: EntranceAndroidSection) -> tuple[str, ...]:
    """Return the passkey origins the configured signing certificates add.

    Args:
        section: The manifest's Android section.

    Returns:
        One ``android:apk-key-hash:`` origin per fingerprint; empty when unconfigured.
    """
    if not section.configured:
        return ()
    return tuple(apk_key_hash_origin(fingerprint) for fingerprint in section.cert_fingerprints)


def webview_origins(section: EntranceAndroidSection) -> tuple[str, ...]:
    """Return the origins the bundled WebView adds to CORS and the sockets.

    Args:
        section: The manifest's Android section.

    Returns:
        The pinned WebView origin; empty when the section is unconfigured.
    """
    return (section.webview_origin,) if section.configured else ()
