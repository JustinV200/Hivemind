# observation-web

The Observation Hive front end: TypeScript (strict) + React, built with Vite. It is the Hive's
one live UI — the Queen's (the central orchestrator's) and every bee's thoughts, Cell diagrams,
the Forage split, the fleet list, Attendant (the inbox triage every supervisor uses) views and a
browsable Honey tree — reachable from any enrolled device, including an Android app built from
this same codebase. It is built out in phase 12 and, once built, is served by the Hive Entrance
(the Hive's gateway) as static files; all of its network access goes through one client module. This
package is scaffolded in roadmap step 0.1 so the toolchain (`pnpm lint`, `format`, `typecheck`,
`test`, `build`) works from day one. Step 12.2 adds the shell and live plumbing: the Landing Board
client, the store fed by the Entrance streams, the hash router, the summary strip, the PWA worker
and a placeholder per view. `pnpm generate` rebuilds `src/landing_board/types.ts` from
`docs/entrance/openapi.json`, and `pnpm generate:check` (run in CI) fails if it is out of date.

## Android

Step 12.12 wraps the same build as an APK with Capacitor: `capacitor.config.ts` pins the WebView
origin, `android/` is the Capacitor project (three small Kotlin plugins for passkeys, UnifiedPush and
the share sheet), `src/landing_board/client/platform/android/` is the Android half of the platform
seam and `src/share/` holds the phone-only screens (share confirmation, push offer, invite box).
`android/README.md` says how to build, sign and run it; CI builds it on `v*` tags
(`.github/workflows/android.yml`). The per-push gate is unchanged.
