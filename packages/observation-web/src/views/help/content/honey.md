Context is treated like a cache hierarchy. What a bee's model sees is assembled fresh for each
awake episode from the tiers below; nothing accumulates, and nothing that leaves a tier is lost.

## The tiers

- **Working context**: one episode's prompt, built to a budget.
- **Hot state**: active goals, open tasks and Alarms, pending questions, recent decisions with their
  reasons, a summary of the fleet and Forage, pinned facts, short notes, and the Cell Wax of any
  Cell in play. Always loaded, bounded by construction.
- **Bee Bread**: the warm tier. Recent results, episodes and Handoffs, looked up by id, time or
  task rather than searched.
- **Honey**: the cold tier. Everything a House Bee has ripened from raw Nectar, retrieved on demand
  by search.

A Worker is never handed the whole Honey Store; it asks for the Honey relevant to its task and gets
back only that, so knowledge compounds across every task the Hive has ever run while any one bee's
context stays small.

## Nectar and ripening

Nectar is a raw finding a Worker brings back. A House Bee ripens it into Honey: chunked,
summarised, embedded, deduplicated and indexed. Cleared and expired Cell Wax is ripened too, so a
Cell's history compounds without living in hot state.

## Clearances

Every item in every tier carries a sensitivity label, and the [Honey screen](#/honey) shows it on
every item:

- **Wildflower (C0)**: public, nothing sensitive.
- **Apiary (C1)**: internal, not personal.
- **Royal (C2)**: personal or sensitive. Any detail about you, down to a first name or a habit.

Labels come from provenance: anything from a Real Cell, a message from you or a watch observation
is Royal. A model may raise a label; only a judge's verdict or a human may lower one. A Night Veil
bee may read Wildflower and Apiary and is never shown Royal data.

## Cell Wax

Cell Wax is the Queen's written caution about one Cell: a known limit, a risk, a quirk. Each note
has a severity (note, caution or block), who proposed it, a reason, an optional expiry and a
clearance. Anyone may propose wax (a bee, a Warden, the Patrol, you from the chat); only the Queen
writes, rejects or clears it. It costs nothing until that Cell is a candidate for work, when a
block excludes the Cell and a caution counts against it. The Cell page lists a Cell's wax, the
Fleet shows the highest live severity on each row, and a Cell's Honey folder has it under `/wax`.

## Handoffs

Before a bee's context overflows it writes a Handoff: its goal, progress, decisions and reasons,
what it tried that failed, what not to redo, and pinned facts verbatim. Then it resets and resumes
from it. The same Handoff is what makes rebinding to a different model, moving a Warden to another
machine, pausing during an outage and recovering a crashed Queen the same operation.

## The Basket

The Basket holds the bytes that work produces and Honey cannot represent: a compiled binary, a
container image, a dataset, a fixture screenshot, model weights. Every file is content-addressed,
signed and described by one Honey record, which is how it is found. A file in the Basket is inert:
holding it grants no right to run it, and only the Comb Registry makes one a tool.

## Thoughts are memory, not audit

What a bee saw and decided is an episode record in Bee Bread with a retention window, readable in
full on the [Thoughts screen](#/thoughts/queen) and redacted at the source (secrets, screenshot
bytes). The Pheromone Trail records that an episode happened and what it cost, never its text.
