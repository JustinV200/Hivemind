# observation-web/src/landing_board/client/platform

The real bindings behind the `Browser` interface: `real_browser.ts` (fetch, WebSocket, crypto,
timers), `indexed_db.ts` (where the binding key lives), `passkey.ts` (navigator.credentials) and
`web_push.ts` (PushManager), and `recorder.ts` (MediaRecorder over the microphone, offered as
`Browser.recorder` and absent on a device that cannot record). This is ADR-0046's platform seam.

`android/` is the other side of the seam, the Capacitor APK's (ADR-0047): passkeys through the
Credential Manager, push through a UnifiedPush distributor, the share sheet, the waiting
notification and invite links, all behind native plugins and all built on `realBrowser` with
overrides. `choose.ts` picks one binding at startup (`src/main.tsx` passes Capacitor's platform
detection); the Android half is loaded only inside the APK. Nothing above the seam changes.
