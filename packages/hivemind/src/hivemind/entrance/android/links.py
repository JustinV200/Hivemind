"""Serve ``/.well-known/assetlinks.json``: the Digital Asset Links that tie the app to the Entrance.

Android trusts a passkey for the Entrance's DNS name inside the app, and opens its invite links in
the app, only when the Entrance vouches for that app at ``/.well-known/assetlinks.json`` (ADR-0050,
ADR-0041). The file is Google's Digital Asset Links statement list: one statement, with the
relations ``delegate_permission/common.handle_all_urls`` (App Links) and
``delegate_permission/common.get_login_creds`` (the Credential Manager), naming the app's package
and the SHA-256 fingerprints of the signing certificates the operator configured. It is static,
unauthenticated and holds nothing secret, which is what lets a verifier fetch it before anyone has
logged in. It is not a ``/v1/`` route and is not in the OpenAPI document: it belongs to Android, not
to the Landing Board's contract. It is served only on a Hive whose ``[entrance.android]`` is
configured; otherwise the path is a 404 like any other, so an Entrance that has no app reveals
nothing about one.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.android``. Rendered by
    ``hivemind.entrance.runtime.build`` and mounted by ``hivemind.entrance.app`` on both
    listeners. Calls into FastAPI, the standard library and the manifest's Android section.

Key invariants:
    - The document is rendered once, from the manifest, and served unchanged.
    - Unconfigured, no document exists and no route is mounted.

See Also:
    - https://developers.google.com/digital-asset-links/v1/getting-started for the format.
    - docs/adr/0050-android-client-capacitor-and-push-delivery.md for why the file is served.
"""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.responses import Response

from hivemind.manifest.schema.entrance import EntranceAndroidSection

ASSET_LINKS_PATH = (
    "/.well-known/assetlinks.json"  # Where Android's verifier looks; fixed by Google.
)
# What the app may do for this site: open its links (invites) and use its credentials (passkeys).
RELATIONS = (
    "delegate_permission/common.handle_all_urls",
    "delegate_permission/common.get_login_creds",
)
CACHE_CONTROL = "public, max-age=3600"  # A verifier may reuse it for an hour; it rarely changes.

__all__ = ["ASSET_LINKS_PATH", "RELATIONS", "render_asset_links", "serve_asset_links"]


def render_asset_links(section: EntranceAndroidSection) -> bytes | None:
    """Render the Digital Asset Links document for the configured app.

    Args:
        section: The manifest's Android section.

    Returns:
        The JSON document, or None when the section is unconfigured (nothing is served).
    """
    if not section.configured:
        return None
    statements = [
        {
            "relation": list(RELATIONS),
            "target": {
                "namespace": "android_app",
                "package_name": section.package_name,
                "sha256_cert_fingerprints": list(section.cert_fingerprints),
            },
        }
    ]
    return json.dumps(statements, indent=2).encode("utf-8") + b"\n"


def serve_asset_links(app: FastAPI, document: bytes) -> None:
    """Mount ``GET /.well-known/assetlinks.json`` answering ``document`` unchanged.

    Args:
        app: The listener's application.
        document: The rendered document.
    """

    async def asset_links() -> Response:
        """Answer the Digital Asset Links statement for the Android app."""
        return Response(
            content=document,
            media_type="application/json",
            headers={"Cache-Control": CACHE_CONTROL},
        )

    # Not a /v1/ route: it is Android's, so it stays out of the Landing Board's document.
    app.add_api_route(ASSET_LINKS_PATH, asset_links, methods=["GET"], include_in_schema=False)
