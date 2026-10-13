# e2e: the Observation Hive in a real browser

The Node half of phase 12's exit-criteria run. `observation_run.mjs` drives a headless Chromium
(Playwright for Node) against a running Hive Entrance: it joins by invite link with a CDP virtual
authenticator standing in for the passkey prompt, logs in, and runs the steps a test sends it. The
Python half, `packages/hivemind/tests/e2e/test_phase12_observation_hive.py`, serves a real Hive
(`hive serve`'s composition), starts this script as a child process and makes the assertions.

## Running it

```sh
pnpm -C packages/observation-web build          # writes dist/, which the Entrance serves
uv run --frozen pytest packages/hivemind/tests/e2e/test_phase12_observation_hive.py -q -s -p no:cacheprovider
```

The test skips, with the reason, when `dist/index.html`, Node, Chromium or Playwright is missing.
`HIVE_E2E_OUT=<dir>` keeps the screenshots and `report.json` after the run (otherwise they live
under pytest's temporary directory).

| Variable                 | Default                                                      | Meaning                                |
| ------------------------ | ------------------------------------------------------------ | -------------------------------------- |
| `HIVE_PLAYWRIGHT_MODULE` | `playwright`, then `/opt/node-tools/node_modules/playwright` | Where to load Playwright for Node from |
| `HIVE_CHROMIUM`          | `/opt/pw-browsers/chromium`                                  | The Chromium executable                |
| `HIVE_E2E_OUT`           | pytest's temporary directory                                 | Where screenshots and the report go    |

## Protocol

One JSON object per line on stdin. The first is the spec, `{ baseUrl, password, outDir, steps? }`
(the browser must name the Entrance as `localhost`: that is the loopback listener's passkey relying
party). Every later line is a step, `{ op, ...args }`, so the test can act on the Hive between steps
(approve the device, submit a goal) while one page, session and passkey live on. Each step answers
with one line: `{ op, ok, result | error, requests, sockets, loads, marker, screenshot, overflowX,
connection }`. `{ "op": "end" }` writes `outDir/report.json` and prints the whole report.

`requests` is method and path only (never a query, header or body); `loads` counts full document
loads and `marker` is planted before the page's scripts run, so a step that reloaded the page shows
as `loads > 1` or a changed marker. The script never calls `location.reload`; it navigates by
setting the hash.

## Steps

`join`, `await_approval_and_login`, `read_strip`, `open`, `fleet_rows`, `cell_page`, `send_chat`,
`request_goal` (the composer's "Request as a goal" switch), `await_question_and_answer`,
`await_reply`, `thoughts`, `thoughts_wait`, `attendant`,
`attendant_wait`, `trail_scrolls` (skips: the app has no trail view), `honey`, `viewport`,
`chat_layout`, `console_errors`. Selectors are accessible roles and names where the app has them;
the fleet table, the badges and the attendant's hidden table are read by the app's own classes.
