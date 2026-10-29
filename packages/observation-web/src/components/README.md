# observation-web/src/components

Shared components used across more than one view: the tier and mask badges, the status dot, the
gauge, the id token, the time stamp (relative, on the one shared clock in `clock.ts`), the view
title row with its comb glyph, the empty state, the view boundary that keeps the shell alive when
a view throws, the brand `Mark` and the `BrandHeader` (mark and name) that opens the login and
join cards, the `HexPattern` comb drawn behind the login card and empty states (and, in its
chrome tone, in the rail and the top bar), the `SortSelect` control every sortable table draws
while its rows are cards, the `useMinWidth` hook a view asks when a control moves between
elements by width, and the `usePaneWidth` hook a diagram asks for the measured width of its pane,
so its viewBox is that many units and its 12 unit text 12 px. The `words/` sub-package holds the
shared words: `VIEW_HINTS` (the one-line gloss each view opens with and its nav item carries as
`title`) and the words tables that turn the schema's codes into phrases (`alarmKindWords`,
`inboxKindWords`, `riskTierWords`, `proposalStateWords`, `capabilityWords`, the clearance names
and meanings) with `plainCode` for a code no table knows, as design.md section 8 sets out.
Each is one file, takes typed props from the generated Landing Board types and holds no state
beyond a clock tick or a caught failure. Their look is set by the tokens in `src/styles/`
and `docs/observation/design.md`. The summary strip is part of the shell and lives in `src/shell/`.
