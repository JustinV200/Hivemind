# observation-web

The Observation Hive front end: TypeScript (strict) + React, built with Vite. It is the Hive's
one live UI — the Queen's (the central orchestrator's) and every bee's thoughts, Cell diagrams,
the Forage split, the fleet list, Attendant (the inbox triage every supervisor uses) views and a
browsable Honey tree — reachable from any enrolled device, including an Android app built from
this same codebase. It is built out in phase 12 and, once built, is served by the Hive Entrance
(the Hive's gateway) as static files; nothing here talks to the Hive directly until then. This
package is scaffolded in roadmap step 0.1 with a placeholder app so the toolchain (`pnpm lint`,
`format`, `typecheck`, `test`, `build`) works from day one.
