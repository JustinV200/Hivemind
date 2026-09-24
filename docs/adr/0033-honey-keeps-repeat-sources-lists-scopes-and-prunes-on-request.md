# ADR-0033: Honey keeps every source a deposit deduplicated from, lists index folders by scope, and drops an old embedder's vectors only on the operator's word

- Status: Accepted
- Date: 2026-09-24

## Context

ADR-0031 dedupes Nectar (raw deposits) twice: by `source_key` (the same internal source
delivered twice, such as a Handoff arriving over Waggle and again from Bee Bread) and then by
content `sha256`. Phase 7's real runs showed three limits that come from that design and from the
store's growth:

- **Repeat provenance is lost.** Two tasks whose outcomes are byte-identical dedupe by content
  onto the first task's row. Only the `honey.nectar_deduplicated` event names the second task.
  Provenance (task, bee, Cell and time) is mandatory for every deposit (roadmap 7.3). Also,
  `HoneyStore.has_source("task_outcome:<second task>")` answers false for a deposit the store
  did take in. Any caller waiting for that answer waits forever.
- **Index folders stop at 5,000 rows.** The Honey browser (roadmap 7.10) derives `/cells`,
  `/bees` and `/tasks` from pages of Honey rows, because the store had no distinct-scopes read. A
  large store lists an incomplete folder and says so.
- **Old vectors are kept forever.** ADR-0032 stores each embedding model's vectors beside the
  others', so switching back needs no second re-embed, and nothing drops them in bulk. A Hive
  that changes embedder for good keeps the old model's vectors: about 3 KiB per Honey row per
  model at 768 dimensions, and they are never read again.

## Decision

**Repeat sources.** Migration `0002` adds `honey_nectar_sources`: one row for each additional
source whose deposit deduplicated onto an existing Nectar by content. A row records the source
key, task, Cell, bee, observed and received times, origin, origin tier and declared label. It is
written in the same transaction as the merge and its `honey.nectar_deduplicated` event. A
duplicate found by `source_key` is the same source delivered again, so it records nothing. A
duplicate whose provenance equals the stored row's own records nothing either, so the table grows
with distinct sources, never with retries. `has_source` answers from both tables. The Honey
browser's document view lists a row's extra sources. The Night Veil boundary is unchanged: a
deposit never dedupes across it (ADR-0031), so a Night Veil Cell's ephemeral rows never collect
ordinary sources, and a purge takes their source rows with them. Retrieval ranking does not read
how many sources confirmed a row. Weighting that is a calibration question for phase 8's
evaluation harness, which calibrates the rest of ranking per embedder.

**Index folders by scope.** The store answers "which scopes of this kind hold Honey this reader
may see, and how many rows each" with one `GROUP BY scope` query, under the reader's own filter
(the same `WHERE` clauses as every other read). `/cells`, `/bees` and `/tasks` list from that
answer, so they are complete at any size. The 5,000-row scan bound is removed.

**Pruning on the operator's word.** `hive honey reembed --prune` first drains the re-embedding
backlog for the bound `EMBEDDER`, then deletes the vectors of every other model. It prunes only
when every live Honey row has a vector for the bound model. Otherwise it refuses, says how many
rows still lack one, and deletes nothing. The deletion and a `honey.vectors_pruned` event (the
models dropped and their row counts) commit in one transaction. The Hive never prunes on its own.
ADR-0032's rule that nothing is dropped in bulk still holds for everything automatic: switching
back after a prune is a full re-embed, and the operator chose that.

## Consequences

Positive: provenance survives deduplication. "Has the store taken in this source" is answered
truthfully for every source, so waiters on an outcome's source key never hang. The browser's
folders are complete at any store size. A Hive that settled on a new embedder can reclaim the old
model's space.

Negative: one more table, and one more write on a content duplicate from a new source. A
pruned model's vectors cost a full re-embed to get back.

## Alternatives considered

- **A JSON list of extra sources on `honey_nectar`**: unindexable, so `has_source` would scan
  every row, and a list rewritten on every merge races two writers.
- **Stop deduping deposits from different sources**: identical text would ripen into duplicate
  Honey that crowds results, which is the failure ADR-0031's content dedupe exists to prevent.
- **A larger scan bound for the index folders**: moves the limit rather than removing it.
- **Automatic pruning once the new model covers every row**: it takes away ADR-0032's free
  switch back without the operator asking, and it cannot tell a trial of a new embedder from a
  permanent change.
