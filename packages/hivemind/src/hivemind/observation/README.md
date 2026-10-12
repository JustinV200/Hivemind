# hivemind.observation

The Observation Hive's read side, in three parts. `views/` holds the read model of each resource
the Hive exposes (Cells, Wardens, tasks, the Forage split, thoughts, the trail, providers and slots,
and the live views' frames), which the Hive Entrance answers its read routes and streams with and
publishes in the OpenAPI document. `api/` holds the aggregated screens (roadmap 12.1): the summary
strip, the Fleet list, a Cell page, an Attendant view, the Thoughts page, the Capping view and the
Cost view, each a frozen model plus a pure builder over the records the Entrance gathers (open
Alarms and a Cell's model hosting are folded in their own modules). `metrics/` holds the
`MetricsSnapshot` of the Hive's figures (Cells by kind and status, Wardens by location, grants and
headroom, Alarms by kind and level, episodes per hour per bee, tasks by status, tool promotions,
denials, tokens and cost per goal, slot and provider, and the summary counters) and the
hand-written Prometheus text exposition (format 0.0.4) rendered from it.

Nothing here reads or writes a store: the builders are pure (codingrules 8.3) and the Entrance does
the reading, in `hivemind.entrance.reads.screens`. Every model carries ids, states, figures and
times; anything written from a human's words stays behind `honey:clearance:c2`, reached through the
ids a screen hands out. The Entrance imports all of it from this package's face only. The front end
that renders these models lives separately, in packages/observation-web.

Figures a store cannot answer yet are null and documented as such: the Pheromone Mask state (until
roadmap 6.13), the last Patrol's time (phase 11), the Warden's place for a Swarm Cell (phase 11),
and a live Attendant inbox (the supervisor's tick queue is memory-only, so the view is rebuilt from
durable state and says so).
