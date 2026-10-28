# observation-web/src/landing_board/client/platform

The real bindings behind the `Browser` interface: `real_browser.ts` (fetch, WebSocket, crypto,
timers), `indexed_db.ts` (where the binding key lives), `passkey.ts` (navigator.credentials) and
`web_push.ts` (PushManager), and `recorder.ts` (MediaRecorder over the microphone, offered as
`Browser.recorder` and absent on a device that cannot record), and `stand_link.ts` (the Hive Stand
browser link: `/stand#key=<key>` moves this serve's stand key into the tab's `sessionStorage`,
offered as `Browser.standKey` and sent as `X-Hive-Stand`, ADR-0068). This is ADR-0049's platform
seam.

`android/` is the other side of the seam, the Capacitor APK's (ADR-0050): passkeys through the
Credential Manager, push through a UnifiedPush distributor, the share sheet, the waiting
notification and invite links, all behind native plugins and all built on `realBrowser` with
overrides. `choose.ts` picks one binding at startup (`src/main.tsx` passes Capacitor's platform
detection); the Android half is loaded only inside the APK. Nothing above the seam changes.
