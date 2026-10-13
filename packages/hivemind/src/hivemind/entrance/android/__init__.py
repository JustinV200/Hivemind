"""Vouch for the Android client at the Hive Entrance: asset links and the origins it brings.

Roadmap step 12.12 (ADR-0047): the Android build of the Observation Hive is a Capacitor APK that
must be recognised by the Entrance's DNS name. Three small additions follow from that, all driven
by ``[entrance.android]`` and all absent when it is unset: the Digital Asset Links file at
``/.well-known/assetlinks.json`` (``links``), the passkey origins the Credential Manager reports,
``android:apk-key-hash:<hash>`` per configured signing certificate, and the pinned WebView origin
that CORS and the sockets accept beside ``public_url`` (``origins``). Nothing here weakens a
check: an unconfigured section adds no file and no origin.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance``. Used by
    ``hivemind.entrance.runtime.build`` and ``hivemind.entrance.app``. Calls into the manifest's
    Android section.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - Unconfigured, no route, no passkey origin and no CORS or socket origin is added.

See Also:
    - docs/adr/0047-android-client-capacitor-and-push-delivery.md for the decisions.
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md for the relying party.

Public API:
    - ASSET_LINKS_PATH, RELATIONS, render_asset_links, serve_asset_links: the asset-links file
      (links).
    - APK_ORIGIN_PREFIX, apk_key_hash_origin, apk_origins, webview_origins: the extra origins
      (origins).
"""

from hivemind.entrance.android.links import (
    ASSET_LINKS_PATH,
    RELATIONS,
    render_asset_links,
    serve_asset_links,
)
from hivemind.entrance.android.origins import (
    APK_ORIGIN_PREFIX,
    apk_key_hash_origin,
    apk_origins,
    webview_origins,
)

__all__ = [
    "APK_ORIGIN_PREFIX",
    "ASSET_LINKS_PATH",
    "RELATIONS",
    "apk_key_hash_origin",
    "apk_origins",
    "render_asset_links",
    "serve_asset_links",
    "webview_origins",
]
