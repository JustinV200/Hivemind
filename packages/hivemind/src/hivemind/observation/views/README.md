# hivemind.observation.views

The views package holds one read model per Observation Hive view, one family per module: Cells
(`cells`), Wardens (`wardens`), tasks (`tasks`), the Forage ledger (`forage`), thoughts
(`episodes`), the trail (`trail`), providers and slots (`llm`), a resource a later phase fills
(`unbuilt`), the Honey browser (`honey/`, a package: item, folder, search and query models) and the
live views' frames (`frames`). The aggregated screens that join several of these (the summary
strip, Fleet, Cell page, Attendant, Capping and Cost views) live beside this package in
`hivemind.observation.api`. Each view carries only what its access allows; anything written from
the human's words is its own model behind `honey:clearance:c2`. The Honey browser's models are the
exception that is filtered per item: they hold only what the browser let the viewer's
`observe:honey:<scope>` capabilities and clearance allowance through, and each has a pure
constructor beside it (`of_entry`, `of_document`, `of_listing`, `of_hit`, `of_hits`).
