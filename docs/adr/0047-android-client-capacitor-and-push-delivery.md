# ADR-0047: The Android client is Capacitor over the same build, with UnifiedPush delivery

- Status: Accepted
- Date: 2026-10-12

## Context

Step 12.12 puts the Observation Hive on a phone as an APK. ADR-0001 chose Capacitor and ADR-0046
decided that the web page, the PWA and the APK are one `vite build` with differences behind a
`platform` seam (`src/landing_board/client/platform/`: push registration, passkey plumbing, the
recorder). The roadmap leaves three things open that are hard to reverse once an APK is installed
on a phone: how a push reaches a phone whose app is closed, how a passkey works inside a native
shell, and how another app hands the Hive something to read. ADR-0041 also left two facts to this
ADR: the Android build's origin and its asset-links file.

The forces are these. The phone is an enrolled device like any other (codingrules 8.15): it enrols
with an invite, is approved on loopback, logs in with a device key plus the operator's password,
and holds no privilege the Landing Board does not grant. The Entrance is never on the open
internet; by default it is reachable only over a VPN overlay, on a DNS name with TLS (ADR-0041),
and WebAuthn binds a passkey to that name as the relying party. Push notices carry neither content
nor kind and are padded to one length (ADR-0042) precisely because they transit a push service the
Hive does not control. The Entrance already speaks RFC 8030, 8291 and 8292
(`entrance/push/web_push/`), and its subscription route takes an endpoint plus `p256dh` and
`auth` keys. A push-to-talk clip and a shared audio file are the same `/v1/chat/audio` call, which
transcribes on the Hive Stand (8.15). Images a client sends are Nectar labelled `C2`, and the
Landing Board has no route that takes one yet.

## Decision

**Capacitor over the same build.** `packages/observation-web/android/` is a Capacitor project that
wraps the output of the one `vite build`. Nothing forks per target: no Android-only view, no second
bundle. The Android implementation of the `platform` seam is chosen once at startup by Capacitor's
platform detection (`Capacitor.isNativePlatform()`), and everything above the seam is unchanged.
The WebView's origin is pinned by Capacitor's `server.hostname` and `androidScheme` to one fixed
value, so the bundled build never loads from the network. The Entrance adds that origin, and only
that one, to the origins its CORS and WebSocket checks accept, beside `public_url`. This is safe
because authentication is the signed request and the origin is a hint, not a credential.

**Push is UnifiedPush, not FCM.** A UnifiedPush distributor (the operator's own `ntfy` server on
the VPN, or any distributor app the operator installs) hands the app an endpoint that speaks Web
Push, together with the user agent's `p256dh` and `auth` keys. The app registers that endpoint and
those keys with `POST /v1/push/subscriptions` as `channel: web_push`, after reading the VAPID key
from `GET /v1/push/vapid-key` and giving it to the distributor. The Entrance's existing
`WebPush` channel then delivers to it unchanged: the same padded, encrypted, content-free notice,
the same keyed `Topic` for withdrawal, the same destination guard (the endpoint must be `https`, or
resolve inside `vpn_cidrs`, or be allowlisted). The Entrance gains no new channel and no Android
code. A distributor that cannot accept encrypted Web Push, and offers only a plaintext endpoint,
is unsupported, because the Entrance never sends an unencrypted notice. No Firebase project, no
Google Play services dependency, and no third party the operator did not choose sit on the path.
FCM is not built. When no distributor is installed, the app falls back to the live
`/v1/push/stream` socket while it is in the foreground, and posts an ongoing local notification for
as long as a question is waiting, withdrawn when the question is answered anywhere. The
notification says only that something is waiting; the app fetches the item over its own session
when the human opens it.

**Passkeys through the Android Credential Manager.** A Capacitor plugin implements the same
`passkey.ts` seam against `androidx.credentials` (create at enrolment, get at login and step-up),
taking the Entrance's `passkey_options` JSON and returning the assertion JSON `LoginRequest`
already expects. The relying party is the Entrance's DNS name (ADR-0041's `rp_id`), not the
WebView's origin. The Entrance publishes Digital Asset Links at `/.well-known/assetlinks.json`,
listing the app's package name and the SHA-256 fingerprints of the signing certificates the
operator configures, so the same passkey works in the app and on the web. A Credential Manager
assertion reports its origin as `android:apk-key-hash:<fingerprint>`, so the relying party's
accepted origins gain one entry per configured fingerprint. The file is static, unauthenticated
and holds nothing secret.

**The share sheet is an intake.** The app registers as a share target for text, images and audio,
so any app on the phone, a glasses companion app included, can hand the Hive something. Shared text
enters as a `HumanMessage` through `POST /v1/chat`. Shared audio goes through `POST /v1/chat/audio`
with `intent=chat`. Shared images need an image intake route that does not exist yet: it deposits
the image as Nectar labelled `C2` (8.15) and needs `entrance:submit`. That route is the one Entrance
addition 12.12 must make, additively under `/v1/`, with the committed OpenAPI document regenerated
(ADR-0042). Every shared item is shown to the human with a send confirmation before anything is
posted, and an item shared while logged out is held in memory only, never stored, until login.

**Push-to-talk uses the recorder seam in clip mode.** The Android recorder records while the button
is held and posts the finished clip to `/v1/chat/audio`, exactly as the web recorder does. Streaming
frames over the chat socket (`AudioChunkFrame`) is a later improvement that changes only the seam.

**The phone is an enrolled device.** QR enrolment scans the invite link `<base>/enrol#code=<code>`.
The app declares an Android App Link for the Entrance's DNS name and `/enrol`, so the link opens in
the app, and the join screen of 12.8a runs unchanged: key, redemption, pending, approval on
loopback. The base URL the app talks to is the one in that link, stored beside the session. The
app has no privilege beyond the Landing Board's, and the loopback-only screens do not render on it
because its session's `listener` is never `loopback`.

**CI builds the APK on tags only.** A Gradle build (Java 17, the Android SDK from its GitHub
action) runs on tags and in the release workflow, not on every push; it signs with a release key
from CI secrets and uploads the APK as a release artifact, which phase 14 ships. The existing web
job is unchanged and stays the per-push gate.

## Consequences

Positive: one codebase serves three deliverables, and a view written for the web works on the
phone unchanged. Push on Android costs the Entrance nothing new, because UnifiedPush rides the
channel already built and tested against the RFC vectors, and the privacy properties of ADR-0042
(no content, no kind, padded) hold on the phone for free. The operator controls every party on
the push path, which fits "never on the open internet". Passkeys are shared between the app and
the web, so an operator enrols one credential. A phone gets no new route or capability class.

Negative: UnifiedPush needs a distributor, which is an install the operator must make; without one
the phone gets no background push, only the foreground socket and the local notification, and that
is a real weakness against FCM's reach. Not every distributor speaks encrypted Web Push, so the
documentation must name the ones that do. Digital Asset Links and App Link verification assume the
verifier can reach `/.well-known/assetlinks.json` on the Entrance's name; on a VPN-only Entrance
that holds only while the phone is on the VPN, and a credential provider that checks associations
through a central cache may refuse a private host. The 12.12 real-device run must prove passkey
login against a VPN-only Entrance, and if a link does not verify, it opens in the browser and the
invite code is entered by hand. The Entrance gains three small additions (the image route, the
asset-links file and the accepted APK origins) and the OpenAPI document changes with the first.
Each release key's fingerprint is configuration, so rotating the signing key means updating the
Entrance. A Capacitor plugin for Credential Manager and one for UnifiedPush are native code the
project owns and must keep current with Android releases. The seam must stay thin, or the "nothing
forks" promise decays.

## Alternatives considered

FCM: needs a Google project and a Google services dependency on every phone, the notice transits
Google's servers (padded and encrypted, but the metadata is theirs), and it cannot be self-hosted,
which conflicts with a Hive that runs on the operator's own network. A native Kotlin app: a second
codebase for every view ADR-0046 builds once, with its own state, signing and client to keep in
step with the Landing Board. React Native: a different rendering model, so the SVG diagrams and
tables would be rewritten, and a second runtime and dependency tree to audit. A PWA only: no
reliable background push on Android without a distributor, no share target on every Android
version, and no Credential Manager integration; fine as the fallback it already is, not enough for
the exit criteria. WebAuthn through the WebView alone: the WebView's support for a credential
bound to the Entrance's relying party is unreliable from a bundled origin, and it does not share the
credential with the web through the system's credential providers.
