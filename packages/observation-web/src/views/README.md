# observation-web/src/views

One directory per Observation Hive view: fleet, cell, forage, thoughts, attendant, chat, honey,
cost, capping, devices and enrol, plus login. Each view is a read-only window onto one part of the
Hive's live state, except chat, whose only write goes into the Queen's (the central orchestrator's)
inbox. `thoughts/` is built (step 12.3) and is the pattern for the rest: a route component, a slice
registered in `src/app.tsx`, `useViewStream` for the live part, one file per concept. The other
directories still hold a placeholder (the title row) until their steps land.
