# hivemind.observation

The observation package is the Observation Hive's read side. Today it holds the read models every
view renders (`views/`: Cells, Wardens, tasks, the Forage split, thoughts, the trail, providers,
and the live views' frames), which the Hive Entrance answers its read routes and streams with and
publishes in the OpenAPI document; the Entrance imports them from this package's face only.
Phase 12 adds the aggregated read API and the metrics. The front end that renders these views
lives separately, in packages/observation-web.
