# observation-web/src/views/forage

The Forage view, `#/forage`: how the Hive's capacity is divided, live. It draws the shared pool (the
Royal Reserve first) beside the flow from the Queen's shared pool to each Warden's grants and each
grant's spend (a switch picks seats, sub-bees, spend or tokens; the columns are labelled), then each
host's capacity (cores, memory, GPUs, model seats and sub-bee slots as thin stacked bars of in use,
granted, reserve and free) as a grid of compact comb-cell cards named from the fleet slice, spend against
the grants' caps, every live grant with those ending within ten minutes first, the Forage requests
with the Queen's decision, the Wardens, and the Forage map of model sources, sortable by header.

`slice/` is the view's slice (state, the reducer, the request list); `use_forage.ts` seeds it from
`GET /v1/forage`, `/v1/wardens` and `/v1/llm` and reads the ledger again only after a
`forage.capacity_reported` frame; `forage_screen.tsx` is the route component (`index.ts` exports it
as `ForageScreen`); `capacity/` and `flow/` each hold a pure model, a pure layout and the SVG that
draws it; `tables/` holds the grant, request, Warden and map tables and the expiry rule. The view is
read-only and draws only what the generated types carry; what they do not carry yet (the Fanner's
queues, a source's grade, abundance, cost and distance, the Nucs' local pools) is not drawn.
