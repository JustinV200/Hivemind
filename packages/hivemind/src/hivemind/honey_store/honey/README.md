# hivemind.honey_store.honey

The retrieval side of the Honey Store (roadmap step 7.7): answering a query over Honey (ripened,
labelled, retrievable knowledge) with hybrid full-text and vector search, filtered by the
reader's scope and clearance before anything is ranked (ADR-0031), and packed into the asker's
token budget.

## Modules

- `retrieve.py` -- `HoneyRetriever(deps).search(HoneySearch) -> HoneyResponse`. `HoneyReader` is
  who asks (`requester`, its `honey:read` `capabilities`, its clearance `ceiling` -- already
  `clearance.reader_ceiling`'s result -- and `is_night_veil`); `HoneySearch` is what it asks
  (`text`, `requested_scopes`, `max_hits`, `max_tokens`); `RetrieverDeps` bundles the store, the
  `HoneyIdentity`, the clock, `[honey.retrieval]` and an optional `BoundEmbedder`/`EmbedGate`.
  One search: `build_match` the words (none left: an empty response saying so); build the
  `ReadFilter` (no readable glob: an empty response, fail closed); count the top text matches the
  filter withholds (`filtered_count`, text side only); fetch `max_hits * candidate_multiplier`
  full-text candidates and, when an embedder is bound and `vector_weight > 0`, embed the query
  under `embed_timeout_s` and fetch as many vector candidates for the response's own model; fuse,
  select, turn each chosen row into a `HoneyHit` (`Honey.to_hit`, excerpt = the summary for a
  SUMMARY row, the body for a CHUNK row) and pack into `min(max_tokens, max_budget_tokens)`. Any
  `LLMError`, timeout, wrong vector count or zero query vector drops to full text with the cause
  in `reason`. Records one `honey.queried` event (subject the requester; payload `hits`,
  `withheld`, `tokens`, `vector_used`, `truncated`; never the words), none for a Night Veil reader.
  `search_outcome` runs the same search and returns a `SearchOutcome` (the response plus
  `vector_used`), for a caller that trails its own consultation (`queen.honey_consulted`).
- `rank.py` (pure) -- `text_score(bm25) = x / (1 + x)` for `x = max(0, -bm25)`;
  `vector_score(distance) = clamp(1 - distance, 0, 1)`; `fuse` (the weighted mean, a missing side
  scoring 0); `select` (the floor, `max_hits_per_nectar`, `max_hits`; score order, then newer
  `created_at`, then id), returning a `Selection` whose `is_cut` says `max_hits` left an eligible
  row out.
- `budget.py` (pure) -- `estimate_tokens` (characters over four, rounded up, plus
  `TOKEN_MARGIN`), `hit_tokens` (reference, title and excerpt, plus `HIT_METADATA_TOKENS`), and
  `pack_hits`: in ranked order, stopping at the first hit that does not fit, which still goes in
  with its excerpt shortened when at least `MIN_EXCERPT_CHARS` of it survive.

## How the hits reach a model

Never executed, never templated into a command: a caller hands the hits to
`hivemind.memory.assemble` (`AssembleRequest.retrieved`), which renders them into the delimited
`RETRIEVED` section under a preamble saying they are reference data, never instructions
(codingrules section 15), within their own share of the budget.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/honey_store/honey
```

`test_rank.py` and `test_budget.py` are pure, with hypothesis properties (every fused score in
[0, 1] and monotone in each input; a pack never over budget). `test_retrieve.py` runs the
retriever over a real SQLite store (`builders.honey.open_test_honey_store`) with
`FakeEmbedding` vectors: hybrid against text-only on a misspelled paraphrase, a forbidden scope
and clearance withheld and counted, the embedder down, timing out or returning a zero vector,
the budget truncating, `max_hits_per_nectar`, and no event for a Night Veil reader.
