# observation-web/src/landing_board/client/platform

The real bindings behind the `Browser` interface: `real_browser.ts` (fetch, WebSocket, crypto,
timers), `indexed_db.ts` (where the binding key lives), `passkey.ts` (navigator.credentials) and
`web_push.ts` (PushManager). This is ADR-0046's platform seam: the Capacitor APK will supply its
own versions of the passkey and push members without touching the rest of the client.
