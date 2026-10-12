# observation-web/src/store

One in-memory `HiveStore` per session, with a slice per concern (`session`, `connection`,
`summary`) and a pure reducer for each. React reads a slice through `useSyncExternalStore`
(`react.tsx`). `feed.ts` seeds the summary once and keeps it live from the cells, telemetry and
episodes streams; views never poll. Each reducer bounds its slice (one entry per Cell, a capped
record of bees).
