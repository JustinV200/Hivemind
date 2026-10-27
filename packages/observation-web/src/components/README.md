# observation-web/src/components

Shared components used across more than one view: the tier and mask badges, the status dot, the
gauge, the id token, the time stamp (relative, on the one shared clock in `clock.ts`), the view
title row with its comb glyph, the empty state, the view boundary that keeps the shell alive when
a view throws, the brand `Mark` and the `HexPattern` comb drawn behind the login card and empty
states (and, in its chrome tone, in the rail and the top bar), the `SortSelect` control every
sortable table draws while its rows are cards, the `useMinWidth` hook a view asks when a
control moves between elements by width, and the `usePaneWidth` hook a diagram asks for the
measured width of its pane, so its viewBox is that many units and its 12 unit text 12 px.
Each is one file, takes typed props from the generated Landing Board types and holds no state
beyond a clock tick or a caught failure. Their look is set by the tokens in `src/styles/`
and `docs/observation/design.md`. The summary strip is part of the shell and lives in `src/shell/`.
