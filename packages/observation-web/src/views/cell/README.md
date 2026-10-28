# observation-web/src/views/cell

The Cell page, `#/cells/<id>`: one Cell, live. The sticky header holds the Cell id, kind, source and
headline status, then the `CombShieldLevel` badge and the Pheromone Mask badge (both large and
never hidden at any width; the mask reads `OFF` with a "not tracked yet" note while the schema has
no state), then the access level and mode. Under it, the live diagram (the Warden inside the Cell
frame when it runs on the Cell, above it when it runs on the Hive Stand, "not tracked" when the
schema does not say; every sub-bee with role glyph, status, current task and a context gauge; the
session, Exoskeleton, lease or Virtual Cell status and model hosting) and the panels: tasks and
goals, Forage (used against issued, pending requests), the Honey entry point, access and mode with
the last Patrol, Cell Wax, open Alarms and Capping. The diagram is a band across the page and the
panels flow into as many columns as fit under it; each bee is a comb cell (a wide hexagon) and the
Cell a hex-cornered frame.

Seeded by `GET /v1/cells/{cell_id}/page`. Kept live by three streams: a `CellFrame` for this Cell
replaces the page's Cell; a telemetry sample from this Cell's Warden updates one bee at once; a
trail event naming this Cell or its Warden makes the page read once more, a second later (one read
per burst), for the Alarms, Cell Wax and Capping lists that have no stream. After the seed, a read
happens only because of a trail frame.

`slice.ts` is the reducer; `use_cell.ts` the seed, the three streams and the re-read; `cell.tsx` the
route component (`CellPageView`); `header.tsx` the header; `cell.css` the styles. `diagram/` is the
SVG: `model.ts` (words), `layout.ts` (geometry, pure), `bee_node.tsx` and `cell_diagram.tsx`
(drawing), `glyphs.ts` and `diagram_table.tsx` (the numbers for a screen reader); the drawing measures its
pane with the shared `usePaneWidth` and is wide from 720 px, narrow on a canvas as wide as the
pane below it. `panels/` holds one file per panel.

Read-only, and it renders only what the generated types carry. A field the schema documents as
filled in a later phase (the last Patrol, the mask state) is shown as "not tracked yet"; the
session's kind and state, and Capping recording links, are not in the schema, so the diagram says
"not tracked yet" for the session and Exoskeleton Capping items say "recording not available".
