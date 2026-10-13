# hivemind.entrance.android

What the Android client (roadmap step 12.12, ADR-0050) needs of the Entrance. The client is the
Observation Hive wrapped by Capacitor as an APK; three small things follow from that, all driven by
`[entrance.android]` and all absent when it is unset.

| Setting | What it adds |
|---|---|
| `package_name`, `cert_fingerprints` | `GET /.well-known/assetlinks.json` on both listeners: Google's Digital Asset Links with `delegate_permission/common.handle_all_urls` (App Links) and `delegate_permission/common.get_login_creds` (the Credential Manager). Static, unauthenticated, nothing secret; not a `/v1/` route, so not in the OpenAPI document. |
| `cert_fingerprints` | One passkey origin per fingerprint on the remote listener's relying party: `android:apk-key-hash:` and the unpadded base64url of the fingerprint's 32 bytes, which is what a Credential Manager assertion reports. The relying party stays the Entrance's DNS name (`rp_id`), so one passkey works in the app and on the web. Loopback's `localhost` relying party accepts none. |
| `webview_origin` | The Capacitor WebView's pinned origin (default `https://observation.hive.local`) joins CORS and the sockets' `Origin` check beside `public_url`, on both listeners. Safe because authentication is the signed request; an origin is a hint, not a credential. |

The section is *configured* only when a package name and at least one fingerprint are both set
(the manifest refuses one without the other). Unconfigured, the asset-links path is a 404 and no
origin is accepted that was not before: nothing here weakens a check.

Rotating the app's signing key means updating `cert_fingerprints`.

## Public API

`render_asset_links`, `serve_asset_links`, `ASSET_LINKS_PATH` (links); `apk_key_hash_origin`,
`apk_origins`, `webview_origins` (origins). The face lists every name.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/entrance/android
```

The tests run a real remote and loopback listener (`builders.entrance.images.android_rig`), drive
the real WebAuthn verification with a `SoftPasskey` reporting the APK origin, and open a real
socket from the WebView origin.
