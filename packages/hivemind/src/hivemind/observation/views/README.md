# hivemind.observation.views

The views package holds one read model per Observation Hive view, one family per module: Cells
(`cells`), Wardens (`wardens`), tasks (`tasks`), the Forage ledger (`forage`), thoughts
(`episodes`), the trail (`trail`), providers and slots (`llm`), a resource a later phase fills
(`unbuilt`), and the live views' frames (`frames`). The aggregated screens that join several of
these (the summary strip, Fleet, Cell page, Attendant, Capping and Cost views) live beside this
package in `hivemind.observation.api`; the Honey browser comes with step 12.9. Each view carries only what its access allows; anything written from the
human's words is its own model behind `honey:clearance:c2`.
