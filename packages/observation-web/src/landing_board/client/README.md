# observation-web/src/landing_board/client

The one client for the Landing Board, and the only code in the front end that touches `fetch` or
`WebSocket`. `keys.ts` holds the non-extractable P-256 binding key and the stored session;
`signing.ts` builds the signed strings (`hive-request-v1`, `hive-ws-v1`); `session.ts` runs login
(passkey plus password), restore, step-up and logout; `http.ts` signs every request and enforces
the write allow-list in `write_guard.ts`; `socket.ts` opens a stream, sends its first frame and
applies the reconnect policy by close code (a 4401 on a stream that had been live is checked with
one signed request, so a session that expired while the device was offline ends properly);
`push.ts` registers Web Push and follows the distributor's own changes on a platform that reports
them; `errors.ts` is the fixed code to sentence table. Everything the client needs from the page arrives through the `Browser`
interface (`browser.ts`), bound for real in `platform/`, so tests use plain fakes.
