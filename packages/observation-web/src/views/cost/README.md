# observation-web/src/views/cost

The Cost view, `#/cost`: what the Hive has spent and what moving a slot local would save. A strip
shows total spend, hosted spend (the calls that cost money) and the local saving (an upper bound).
Under it are three sortable tables: spend per goal split by slot and by provider (the unattributed
row, calls that named no goal, always last and explained in a sentence), spend per slot with its
saving and a gauge of its hosted share, and spend per provider. Money shows four decimals under a
dollar and two above. Under 720 px each table becomes stacked cards.

`slice/` is the view's slice (state, the fold of one call, the reducer); `use_cost.ts` seeds it from
`GET /v1/cost`, folds each `llm.call` frame with usage from `/v1/trail/stream` at once, and reads
the cost again once, a second after the first folded call of a burst (never without a frame);
`cost_screen.tsx` is the route component (`index.ts` exports it as `CostScreen`; `cost.tsx` keeps
the old `CostView` name for the outlet); `tables/` holds the row models, one generic sortable table
and the three tables' columns. The view is read-only and draws only what the generated types carry;
a call lands under the null goal until the Fanner lanes carry `goal_id`.
