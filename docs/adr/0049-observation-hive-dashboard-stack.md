# ADR-0049: The Observation Hive is one React app fed by the nine Entrance streams

- Status: Accepted
- Date: 2026-10-12

## Context

Phase 12 builds the Observation Hive, the read-only window onto a running Hive and the home of the
chat into the Queen's inbox. ADR-0001 already chose TypeScript (strict), React, Vite and Capacitor
for it; this ADR decides what sits on top of that choice, because the first views will set the
pattern every later one copies. (Numbers 0044 and 0045 belong to the phase 8 branch, which is not
merged yet, and are skipped here so nothing is renumbered at merge.)

The forces are these. The Observation Hive is a Landing Board client like any other (codingrules
8.15): it runs on an enrolled device, signs every request with a session-bound key (ADR-0041) and
holds no privilege of its own. Its views never poll; they subscribe to the nine streams listed in
`x-hive-streams` (chat, push, entrance, trail, telemetry, forage, tasks, episodes, cells), and the
persistent summary strip must stay live while the human navigates (8.11). The Entrance serves the
build as plain static files, with `index.html` at `/` and no fallback for other paths. The same
build must run as a web page, an installable PWA, and the APK that Capacitor wraps, whose pages
load from a `file://` origin. Its views are mostly live SVG diagrams and dense tables, and every
dependency it adds is audited (`pnpm audit`) for as long as the Hive ships it. The Landing Board
contract is published (ADR-0042), so a hand-written copy of a shape would drift without anyone
noticing. These choices are hard to reverse once a dozen views depend on them.

## Decision

**Stack.** TypeScript (strict) and React 19, built by Vite, as ADR-0001 chose. Tests are Vitest with
Testing Library; lint and format are ESLint and Prettier. There is no UI framework and no component
library. Styling is hand-written CSS using custom properties (the design tokens of
`docs/observation/design.md`) in `src/styles/`.

**Generated contract.** `src/landing_board/types.ts` is generated from `docs/entrance/openapi.json`
by `openapi-typescript`, a dev dependency run by `pnpm generate`. The file is committed, and CI
regenerates it and fails on a diff. Hand-written code never declares a Landing Board shape.

**One client module.** `src/landing_board/client/` is the only module that touches `fetch` or
`WebSocket`. It holds login (passkey plus password), the WebCrypto ECDSA P-256 binding key, request
signing (`hive-request-v1`), the socket's first-frame `SocketHello` (`hive-ws-v1`), the streams and
push registration. The binding key is generated non-extractable and kept in IndexedDB beside the
session token; deleted at logout. A token copied out of the browser is therefore useless without
the key (ADR-0041). The session is a bearer token plus signatures, never a cookie.

**State and streams.** There is no Redux, MobX or Zustand. One in-memory `HiveStore` exists per
session and is fed only by the nine Entrance streams. Each stream has a reducer that folds its
frames into one slice, and React reads a slice through `useSyncExternalStore`. A view mounts,
subscribes and never polls. A slice starts from the matching read route once, then frames take
over from the cursor that read returned. The reconnect policy is fixed:

- `4409` (fell behind): reconnect from the last cursor.
- `4410` (session ended) and `4411` (Entrance reduced): drop to the login screen.
- `4401` and `4403`: show the refusal and do not retry; `1001`, `1008` and network loss: back off
  1, 2, 4, 8 seconds, then 30 at most, and reset on the first accepted frame.

The summary strip is itself a slice, seeded by `/v1/summary` and kept live by the cells, telemetry
and episodes streams, so it never blinks out while a view changes.

**Routing.** Routes are hash-based (`#/fleet`, `#/cells/<id>`, `#/forage`, `#/thoughts/<bee>`,
`#/attendant/<principal>`, `#/chat`, `#/honey/<path>`, `#/cost`, `#/capping`, `#/enrol`,
`#/devices`) and handled by a small in-house router. The Entrance serves static files with no SPA
fallback, so only the hash survives a reload and a pasted link; the same build runs unchanged from
Capacitor's `file://` origin. There is no `react-router`.

**PWA.** The build ships `manifest.webmanifest` and a hand-written TypeScript service worker, a
second Vite entry. It caches the app shell only and never caches anything under `/v1/`, because
those responses are live, signed and personal.

**Loopback-only screens.** There is one build. The loopback-only screens (invite, pending, approve,
deny, unlock, revoke, reopen) render only when the session's `listener`
(`OpenedSessionView.listener`) is `loopback`. The route test of 8.15 proves the remote listener
answers those routes with a 404 anyway, so this gate is defence in depth and not the security
boundary.

**One codebase, three deliverables.** The web page, the PWA and the Android APK (Capacitor, decided
in its own ADR before step 12.12) are the same `vite build`. Nothing forks per target. Differences
sit behind a small `platform` seam: push registration, passkey plumbing and share-sheet intake.

## Consequences

Positive: a Landing Board change surfaces as a type error in the views that use it, and the CI diff
check makes the contract and the client move together. Only one module can reach the network, so
the signing scheme, the reconnect policy and the 8.11 test (no mutating route but chat) each have
one place to live and be tested. Frames are folded once and read by many views, so the summary
strip, the fleet list and a Cell page agree by construction, and a view with no slice of its own
cannot go stale. Hash routes need nothing from the Entrance and work from `file://`. A stolen
token or a copied IndexedDB entry cannot sign. The runtime dependency list stays at React and
React DOM, which keeps the audit short.

Negative: the team owns a small router, a store, a service worker and all the CSS, where a library
would have provided them. Hash URLs are uglier and cannot be server-rendered or indexed, which is
fine for a private dashboard. Folding every frame into memory costs RAM on a phone for a busy
Hive, so each reducer must bound its slice (a ring for the trail, one entry per Cell). A
non-extractable key cannot be backed up or moved: a browser that loses its IndexedDB must enrol
again. `openapi-typescript` becomes a build dependency whose output must be reviewed when it
changes. The service worker's "never cache `/v1/`" rule is a test, not a default, and must be kept
as one.

## Alternatives considered

`react-router` with a server SPA fallback: needs the Entrance to answer unknown paths with
`index.html`, which it does not do, and breaks under Capacitor's `file://` origin. Redux or
Zustand: a second state model beside the streams, which are already ordered deltas with cursors
and fold naturally into per-view reducers. Polling the read routes: forbidden by 8.11, slower to
show a change, and a load on the Queen's process. A component library (MUI, shadcn): hundreds of
transitive dependencies to audit for a UI whose substance is live SVG and tables the libraries do
not help with. Next.js or any server rendering: a Node server beside the Entrance for a page that
shows nothing without a signed session, and nothing a static build cannot do. Hand-written types:
drift from the published contract, which the CI diff check exists to prevent. Separate apps per
target: three codebases to keep in step where ADR-0001 chose one on purpose. Cookies for the
session: sent without a signature, so a stolen cookie works, which ADR-0041's binding key rules
out.
