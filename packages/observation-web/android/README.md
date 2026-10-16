# observation-web/android

A Capacitor project that wraps the same Observation Hive React app as an APK (roadmap 12.12,
ADR-0050). It is the output of one `vite build` plus three small Kotlin plugins; nothing forks per
target. The phone is an enrolled device like any other (codingrules 8.15): it joins with an invite,
is approved on the Hive Stand's loopback listener, and logs in with its device passkey plus the
operator's password. It holds no privilege beyond the Landing Board's.

## What is native here

| Piece | Where | What it does |
|---|---|---|
| `PasskeyPlugin` | `app/src/main/java/dev/hivemind/observation/passkey/` | Passkey create and get through `androidx.credentials`, against the Entrance's DNS name as relying party. |
| `UnifiedPushPlugin`, `PushReceiver` | `.../push/` | Registers with a UnifiedPush distributor, hands JavaScript the Web Push endpoint and keys, raises `endpoint` and `unregistered` events when the distributor later rotates the endpoint or drops the app (retained, and carried over a cold start), and raises a content-free "Something is waiting." notification when a push arrives. |
| `ShareIntakePlugin` | `.../share/` | Receives text, an image or audio from the share sheet; JavaScript asks the human before anything is sent. |
| `AndroidManifest.xml` | `app/src/main/` | The App Link for `<Entrance>/enrol`, the share-target filters, the receiver and the permissions (internet, microphone, notifications). No location, storage, contacts, foreground service or Google services. |

The TypeScript side is `src/landing_board/client/platform/android/` (the Android half of the
`platform` seam, chosen in `src/main.tsx` by `Capacitor.isNativePlatform()`) and `src/share/` (the
confirmation card, the waiting notification, the push offer and the invite box).

## Build

Needs Node 22+, pnpm, JDK 21 (Capacitor 8's template targets Java 21) and the Android SDK (platform
36, build-tools). From `packages/observation-web/`:

```
pnpm install
pnpm build                     # the one vite build
pnpm exec cap sync android     # copies dist/ into the project and regenerates capacitor.*.gradle
cd android
./gradlew assembleDebug        # or assembleRelease, see Signing
```

`cap sync` rewrites `app/capacitor.build.gradle`, `capacitor.settings.gradle` and the copied bundle
under `app/src/main/assets/`; the bundle and generated config are ignored by git, the Gradle files are
committed. Never edit the generated files by hand.

### Configuration

| Setting | How | Meaning |
|---|---|---|
| Entrance host | Gradle property `-PhiveEntranceHost=hive.example.ts.net` (CI: variable `HIVE_ENTRANCE_HOST`) | The DNS name in the App Link intent filter for `/enrol`. It must be the Entrance's name, the same one the passkey's relying party uses. Unset, it is a placeholder that matches no host, so no link opens the app. |
| Version | `-PhiveVersionName`, `-PhiveVersionCode` | Set from the tag in CI. |
| Pinned WebView origin | `capacitor.config.ts`: `https://observation.hive.local` | Fixed for every build; the Entrance accepts exactly this origin beside its `public_url`. |

The package name is `dev.hivemind.observation`. The Entrance's Digital Asset Links file must list it
with the SHA-256 fingerprint of the signing certificate, for both `delegate_permission/common.get_login_creds`
(passkeys) and `delegate_permission/common.handle_all_urls` (the App Link), and its accepted APK
origins must list `android:apk-key-hash:<fingerprint>` (ADR-0050).

### Signing

A release APK is signed from environment variables, never from a file in the repository:

```
export HIVE_KEYSTORE_PATH=/path/to/release.jks
export HIVE_KEYSTORE_PASSWORD=... HIVE_KEY_ALIAS=... HIVE_KEY_PASSWORD=...
./gradlew assembleRelease
keytool -printcert -jarfile app/build/outputs/apk/release/app-release.apk   # the SHA-256 for the Entrance
```

Without them `assembleRelease` produces an unsigned APK, which cannot be installed. For a local try
use `assembleDebug` (signed with the SDK's debug key; its fingerprint differs from the release key's,
so a debug build needs its own entry in the Entrance's configuration). CI
(`.github/workflows/android.yml`) builds on `v*` tags and manual runs, writes the keystore from the
secrets `HIVE_ANDROID_KEYSTORE_B64`, `HIVE_ANDROID_KEYSTORE_PASSWORD`, `HIVE_ANDROID_KEY_ALIAS` and
`HIVE_ANDROID_KEY_PASSWORD`, prints the certificate fingerprint and uploads the APK as an artifact.
Rotating the key means updating the Entrance's fingerprints.

## Run

1. Install the APK. Open the invite link the operator minted (a QR scan of
   `https://<Entrance>/enrol#code=<code>` opens it in the app when the App Link verifies). If it
   opens in the browser instead, open the app and paste the link into the box it shows.
2. Name the device, make the passkey, and have the operator approve it on the Hive Stand. Then log in.
3. Push: install a UnifiedPush distributor that supports encrypted Web Push (ntfy on the VPN is the
   reference one), tap "Turn on push notices" in the app. Without one the app still shows an ongoing
   notification while a question waits and the app is open, from the live push stream.
4. Share: share text, an image or an audio clip to "Observation Hive" from any app. The app shows what
   will be sent and where, and sends only on Send.

App Link and passkey verification need the phone to reach `/.well-known/assetlinks.json` on the
Entrance's name; on a VPN-only Entrance that means being on the VPN. If a link does not verify, paste
it by hand.

## Status

The native side (the three Kotlin plugins, the manifest and the Gradle files) was written without an
Android toolchain and has not been compiled or run. The connector and Credential Manager calls follow
the libraries' documented APIs at the versions pinned in `variables.gradle`; the first build and the
real-device run of ADR-0050 (passkey login against a VPN-only Entrance, a UnifiedPush delivery, a
share of each kind) are what verify them.
