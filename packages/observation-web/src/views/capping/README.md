# observation-web/src/views/capping

The Capping view, `#/capping`: the gate nothing lands past unchecked, Hive-wide and live. Capping
is the gate every action passes: a bee proposes, the gate checks by the proposal's risk tier, a
capped proposal is applied and verified, and a failed one is rejected or rolled back. The view
shows a rates strip per tier (proposals, waiting, approved, refused, rolled back, and the sampled
audits' coverage and findings), the queue of proposals filterable by tier and state with chips and
sortable by header (each row with its bee, task, Cell, the gate's verdict and the reason for it,
the rollback mark and the recording column), and the sampled audits in their own table. Every
item links to its bee's thoughts (`#/thoughts/<bee>`) and, where the schema names one, to the Cell
page. At phone width every table becomes stacked cards.

`slice/` is the view's slice (state, `fold.ts` for a `capping.*` trail frame, `counts.ts` for the
per-tier counts and audit rates, the reducer); `use_capping.ts` seeds it from `GET /v1/capping`
and re-reads once, a second after the first `capping.*` frame of a burst, never without a frame;
`capping_screen.tsx` is the route component (`index.ts` exports it as `CappingScreen`, and
`capping.tsx` keeps the old `CappingView` name for the shell's outlet); `rates/` holds the tier
model and the strip; `queue/` the row join, the filter chips, the sortable table and the links;
`audits/` the audit table. The view is read-only and draws only what the generated types carry.

Not in the schema yet, so shown as a note or omitted: a recording reference for an Exoskeleton
(`action_kind` `GUI`) action, so its recording column says "recording not available" and draws no
control; the judge's reasons as prose (the trail carries only the failing check as a code, whether
the judge could not answer, and the number of checks run); and a task page to link a task to. A
verdict whose proposal is outside the newest 200 of each server list is not drawn.
