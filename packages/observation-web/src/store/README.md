# observation-web/src/store

One in-memory `HiveStore` per session, built from a table of slices (`slice.ts`: an initial state
and a pure reducer). The shell's three live in `slices/` (`session`, `connection`, `summary`, as
`SHELL_SLICES`); a view adds its own by merging its type into `registry.ts` and adding an entry to
the table in `src/app.tsx`, so the store itself never changes. React reads a slice with
`useSlice(name)` (`useSyncExternalStore`), and a view keeps itself live with
`useViewStream(path, slice, toEvent, resume?)`, which opens one Entrance stream while the view is
mounted, folds its frames into the named slice and reports its state to the connection slice
(`view_stream.ts`). `feed.ts` seeds the summary once and keeps it live from the cells, telemetry
and episodes streams; views never poll and never call `openStream`. Each reducer bounds its slice.

The summary slice counts what the server's `/v1/summary` counts: a Cell is active while its Warden is
in the ACTIVE mode; a Warden is an active bee in ACTIVE or WATCH, a sub-bee in SPAWNED, RUNNING or
HANDING_OFF. The telemetry stream never sends a finished Drone as DONE, so a Heartbeat (a Warden's
sample, then the `sub_bees` rows it lists) is folded as the Warden's whole current set and a bee it
no longer lists is dropped once the last row has arrived.
