# ADR-0031: The Honey Store is SQLite with FTS5 and sqlite-vec, labelled and scoped at intake, ripened by the House Bee, searched hybrid under exact filters

- Status: Accepted
- Date: 2026-09-24

## Context

Phase 7 makes the cold tier of memory real (codingrules 8.9, roadmap phase 7): Workers deposit
Nectar (raw findings), a House Bee ripens it into Honey (chunked, summarised, embedded, deduped,
indexed knowledge), and the Queen and Workers query Honey before acting. The forces:

- One file per Hive (ADR-0006). Backing up, moving (Supersedure, 8.16) and absconding a Hive must
  stay a file operation, and `pollen` never needs any of this.
- Retrieval must be hybrid: exact terms (a path, a flag, an error string) are found by full-text
  search; paraphrase is found by embeddings. Either side may be missing: no `EMBEDDER` bound, the
  embedding provider down, or `sqlite-vec` unable to load on a host.
- Filtering is policy, not ranking. Clearance (`C0`/`C1`/`C2`), scope (`hive`, `cell:<id>`,
  `bee:<id>`, `task:<id>`), the Night Veil ceiling and taint (10.6d) must hold before anything is
  ranked, or a permitted hit can be crowded out by forbidden ones and a forbidden one can leak
  through a truncation boundary.
- The embedder can change (ADR-0032), so vectors from two models must coexist while a re-embed
  runs, and a query must never compare vectors from different models.
- Labels come from provenance at intake: anything from a Real Cell, a human message or watch mode
  is `C2`; a model may raise a label and never lower one; only a judge verdict or a human may lower
  one (codingrules 8.9, roadmap 7.3). A Night Veil Cell's execution records never outlive its
  teardown; Honey it ripened on its own local slots at `C0`/`C1` is the one export (ADR-0030).
- Ripening calls models (`RIPENER`, `EMBEDDER`); the Queen's tick must never wait on them, and
  autopilot never awaits a model at all (codingrules 8.8).

## Decision

**Storage.** The Honey Store is its own migration series (`honey_store`) in the Hive's one SQLite
file, opened on its own connection and `ConnectionThread`. Two tables of record:

- `honey_nectar`: one row per deposit, content bytes capped by `[honey.store] max_nectar_bytes`,
  its `NectarKind`, `NectarOrigin` (a bee over Waggle, a verified task's outcome, aged Bee Bread,
  cleared or expired Cell Wax, the human, watch mode), full provenance (task, Cell, bee, observed
  at), clearance, origin tier, scope, state (`RECEIVED`, `RIPENED`, `EPHEMERAL`, `DISCARDED`) and
  two dedupe keys: the content `sha256`, and an optional `source_key` for internal origins
  (`handoff:<event id>`, `bee_bread:<entry id>`, `wax:<wax id>`, `task_outcome:<task id>`), so the
  same Handoff arriving over Waggle and from Bee Bread is one row. A duplicate deposit merges by
  raising the stored label to the higher of the two, never lowering it.
- `honey`: the ripened rows. Each Nectar ripens into one `SUMMARY` row (title, summary, key facts)
  and one `CHUNK` row per chunk of its text, each carrying the Nectar's provenance, scope,
  clearance and origin tier, plus `tainted` and `retired_at` from day one so phase 10 has
  somewhere to write.

**Full-text search** is an external-content FTS5 table over `honey(title, summary, body)` with the
`porter unicode61` tokenizer, kept in sync by triggers. A query string never reaches `MATCH`: it is
reduced to at most 32 word tokens, each quoted, joined with `OR`, and ranked by `bm25`.

**Vectors** live in an ordinary table, `honey_vectors(honey rowid, model, dims, float32 blob)`,
one row per Honey row for the model that embedded it. Nearest-neighbour search is exact: SQL
computes `vec_distance_cosine` (sqlite-vec) over exactly the rows the reader may see, under the
same `WHERE` as every other filter (scope, clearance rank, taint, retirement, the query's own
model). When the extension cannot load, the same query returns the candidate vectors and the
store ranks them in Python: slower, identical results. We do not use a `vec0` virtual table:
sqlite-vec 0.1.x has no approximate index, so `vec0` is brute force too (measured here: 20,000
rows of 768 dimensions, `vec0` k-NN 48 ms against the plain scan's 37 ms), and `vec0` fixes one
dimension per table and filters only through metadata columns that every relabel, taint and
retirement would have to keep in sync, or a `rowid IN` subquery. The store's vector search is one
module behind the store protocol; an approximate `vec0` index goes there when sqlite-vec ships one
and a Hive outgrows exact search (around a million rows).

**Hybrid ranking** fuses normalised scores with manifest weights: `s_text = x / (1 + x)` for
`x = -bm25`, `s_vec = max(0, 1 - cosine distance)`, and `score = (w_text * s_text + w_vec *
s_vec) / (w_text + w_vec)` (`[honey.retrieval] fts_weight`, `vector_weight`); a row found by one
side scores zero on the other. With no usable vector side the vector weight drops to zero and the
response's `reason` says why. Hits under `min_score` are dropped, at most
`max_hits_per_nectar` hits come from one Nectar, and the rest are packed by score into the
caller's token budget (`HoneyQuery.max_tokens`, which the caller scales from its own model's
context window); `is_truncated` and `filtered_count` report what the budget and the policy
withheld, never the withheld content itself.

**Labels.** `honey_store/clearance.py` is pure. At intake a label is the higher of the depositor's
declared label and a floor from provenance: `C2` for a deposit from a borrowed (Real) Cell, from
the human or from watch mode; the declared label otherwise (`[honey.clearance] default_label` when
nothing was declared). The ripener may raise a label (`honey.label_raised`) and is never asked to
lower one. Lowering is a separate, recorded act with an approver of `JUDGE` (an independent review
on `ModelSlot.JUDGE` with its own rubric and no shared context) or `HUMAN` (the operator's own
command), and always lands as `honey.label_lowered`. A reader's ceiling is the lowest of what it
asked for, its task's clearance and its Cell tier's read allowance (`[honey.clearance.matrix]`),
so a Night Veil reader never sees `C2` whatever it asks.

**Scopes and visibility.** `honey_store/scope.py` derives a Honey row's scope from provenance:
findings and verified task outcomes are `hive` (shared knowledge); transcripts, tool results,
Handoffs and flight recordings are `task:<id>` (working material); Patrol summaries and Cell Wax
history are `cell:<id>`; a bee's own material with no task is `bee:<id>`; a human's note lands in
the folder it was proposed in. A reader sees a scope when one of its `honey:read:<pattern>`
capabilities matches it (a new `guard` family, a glob over the scope string). Until phase 10's
policy engine issues capability sets, the defaults are: the Queen, the House Bee and the
operator's CLI read everything; a Worker reads `hive`, its own Cell, its own task and goal, and
itself; a Warden reads `hive`, its own Cell and itself.

**Night Veil.** Intake accepts `RIPENED_HONEY` at `C0`/`C1` from a Night Veil Cell as ordinary
Nectar (labelled `origin_tier = NIGHT_VEIL`, the one intentional export). Every other deposit from
such a Cell is stored `EPHEMERAL` against the Cell, is never ripened or returned, and is deleted by
the store's `SideChannelPurger` during the Cell's teardown purge. Neither an ephemeral deposit nor
a query from a Night Veil reader writes a trail event at all: the Queen's inbox does not carry the
sending node's id, so such an event could only land on the Queen's own node and would outlive the
teardown, and the lifecycle skeleton of codingrules section 12 lists no `honey.*` kind.

**Who writes.** Every write reaches the store through the Queen's process: Nectar intake runs in
the Queen's tick for Waggle deposits and in-process for her own (a verified task's outcome, the
House Bee's Bee Bread and Cell Wax deposits, the human's proposed notes, which queue as rows the
House Bee drains). Ripening, which calls models, runs in the House Bee's own loop beside the
Queen, never inside her tick. `hive honey ripen --now` and `hive honey reembed` run the same House
Bee duties in-process against the same file. Every write is idempotent by key (Nectar `sha256` or
`source_key`, Honey `(nectar, part, chunk)`), so two runners never duplicate a row. Browsing never
writes. Nothing retrieved is ever executed: hits reach a model inside the delimited `retrieved`
section, labelled as untrusted reference data, and a bee's tool result carrying hits says the same.

## Consequences

Positive: a Hive's knowledge is one file, backed up and moved with everything else. Policy is
exact because every filter is a `WHERE` clause evaluated before ranking. The store keeps working
with no embedder, a dead embedder or no extension, and says which of those it is doing. A model
change re-embeds progressively without a migration or a moment with no vector search for the rows
already done. Idempotent writes make the CLI's maintenance commands safe beside a running Queen.

Negative: exact vector search is linear in the rows a reader may see; it is fine to a few hundred
thousand rows and will need the approximate index above past that. Summary and chunk rows
duplicate some text (the summary is repeated as context for its chunks). The provenance floor
makes everything a Drone does on the Hive Stand `C2`, so an ordinary `C1` goal cannot read Honey
produced on the Hive Stand until a judge or the operator lowers a row; the operator either runs
`C2` goals on the Hive Stand or reviews labels (`hive honey relabel`, `hive honey review`). That is
the rule working as written, and it is recorded here so nobody mistakes it for a retrieval bug.

## Alternatives considered

A dedicated vector database (Qdrant, Chroma, LanceDB): a second process or a second file to back
up and move, for scale a single-operator Hive does not have. `vec0` virtual tables: measured no
faster at this scale, and costlier to keep correct (above). Vectors as a BLOB column on `honey`:
one model per row, so a re-embed would overwrite vectors still in use by queries on the old model.
Reciprocal rank fusion: ignores how good a match is, so the nearest of a set of irrelevant vectors
would still score; the weighted sum lets `min_score` drop it. Ripening inside the Queen's tick: a
local ripener takes tens of seconds per summary, which would stall heartbeats and dispatch.
Letting the ripener lower labels: a model deciding data is less sensitive is exactly the failure
the label exists to prevent.
