# hivemind.observation.views

The views package holds one read model per Observation Hive view, one family per module: Cells
(`cells`), Wardens (`wardens`), tasks (`tasks`), the Forage ledger (`forage`), thoughts
(`episodes`), the trail (`trail`), providers and slots (`llm`), a resource a later phase fills
(`unbuilt`), and the live views' frames (`frames`). Phase 12 adds the Attendant, Capping and
Honey browser views. Each view carries only what its access allows; anything written from the
human's words is its own model behind `honey:clearance:c2`.
