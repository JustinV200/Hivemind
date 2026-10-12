# observation-web/src/views/fleet

The Fleet view, `#/fleet`: every Cell in one table with a Real / Virtual / All filter (All by
default, remembered per viewer in `localStorage`). Each row shows the Cell's kind, source, status
and mode, its Comb Shield level (`TierBadge`) and Pheromone Mask (`MaskBadge`, `OFF` when none) as
badges that are drawn in every row at every width, its access level, two place badges (where its
Warden runs, where its models come from), its current task and role, open Alarms, the highest live
Cell Wax severity, its lease's age and links to its page, its Warden's Attendant view and the
thoughts of its Warden and sub-bees. The legend of `MEADOW`, `PROPOLIS` and `NIGHT VEIL` is fixed
above the table. A header click sorts (stable, ties on the Cell id, `aria-sort`). Under 1080 px the
table becomes stacked cards with the two badges first.

`slice.ts` is the slice and reducer (bounded to 2,000 rows); `fold.ts` folds a `CellFrame` into a
row at once; `use_fleet.ts` seeds from `GET /v1/fleet` and reads it again one second after a frame
(never on a timer); `fleet.tsx` is the route component; `legend.tsx` is the legend; `filter/` is
the Real / Virtual / All control; `table/` is the table, its rows, marks, formatters and sorting;
`fleet.css` is the view's stylesheet. The view is read-only and renders only what the generated
types carry.
