# hivemind.honey_store.ripening

The House Bee's ripening pipeline: it turns Nectar (raw deposits in the Honey Store, the Hive's
knowledge base) into Honey, one SUMMARY row and one CHUNK row per slice of text, each labelled,
scoped, full-text indexed and, when an EMBEDDER is bound, given a vector (ADR-0031, ADR-0032).
`Ripener.run_pass` runs on the House Bee's timer beside the Queen, never inside her tick; `hive
honey ripen --now` and `hive honey reembed` run the same passes on demand.

## Public API (roadmap 7.5)

- **`Ripener`** (`pipeline.py`): `ripen_pending()` (up to `max_nectar_per_pass` RECEIVED Nectar,
  oldest first), `embed_pending()` (up to `max_embed_per_pass` live rows with no vector for the
  current embedder's model, which is how a new embedder re-embeds the store progressively), and
  `run_pass()` (both). `RipenOutcome`/`PassOutcome` are their counts.
- **`RipenerDeps`** (`deps.py`): store, identity, clock, `[honey.ripening]`, and the optional
  RIPENER binding, call gate, EMBEDDER binding and embed gate.
- The stages, each usable alone: `decode_text`/`normalise_text`/`chunk_text` (`chunk.py`, pure),
  `summarise` (`summarise.py`), `ripened_drafts`/`metadata_draft` (`drafts.py`, pure),
  `embed_texts`/`embedding_text`/`embed_pending_rows` (`embed.py`),
  `drop_exact_duplicates`/`drop_near_duplicates` (`dedupe.py`) and `index_ripened`
  (`index.py`).
- **`prune_vectors(deps, kept_model) -> PruneOutcome`** (`prune.py`): on the operator's own word
  only (`hive honey reembed --prune`, never the House Bee's timer), drops every embedding model's
  vectors but `kept_model`'s -- but only once every live Honey row already has one for it.
  Refuses (and changes nothing) otherwise; `PruneOutcome.missing`/`.refused` say why, and
  `.dropped` gives the per-model counts on success. One `honey.vectors_pruned` event on success,
  none on a refusal (ADR-0033).

## One Nectar, stage by stage

```text
pending_nectar ─► nectar_content ─► decode_text ──(binary or blank)──► one metadata SUMMARY draft
                                        │ text                                     │
                                        ▼                                          │
                          chunk_text + summarise (RIPENER, or heuristic)           │
                                        ▼                                          │
                          SUMMARY draft + CHUNK drafts ─► drop_exact_duplicates ◄───┘
                                        ▼
                    embed_texts (EMBEDDER; None on any failure) ─► drop_near_duplicates
                                        ▼
       index_ripened: re-read the Nectar, raise to its current label, ripen (+ honey.ripened,
                      + the Ripener's own reading of the text), set_vectors, honey.label_raised
                      when the summary raised the label
```

The RIPENER labels the text itself, whatever its current label (ADR-0034). A label above the
current one raises it at once; a label below it is stored as the Nectar's `ripener_clearance`
(with the model's reason) in the same `ripen` transaction and never lowers anything: it can only
start a lowering proposal an independent judge or the human decides (`hivemind.honey_store.
lowering`). A heuristic summary stores no reading, so such a Nectar is never proposed.

A `HoneyStoreError`, a `NotFoundError` or a `ValueError` while ripening one Nectar marks it failed
(`honey.ripen_failed`) and DISCARDED at `max_attempts`; the pass carries on with the next one. A
model or embedding failure never fails a Nectar: summaries fall back to a heuristic and rows
without vectors stay pending, still found by full-text search.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/honey_store/ripening
```

`test_chunk.py` property-tests the chunker with hypothesis; `test_summarise.py` scripts a
`FakeLLMProvider` (a good reply, a malformed one, an outage); `test_pipeline.py` ripens a long
text, a short text, a binary blob and a duplicate on a real SQLite store with `FakeEmbedding` and
checks rows, full-text findability, vectors, deduplication and events. `test_prune.py` covers a
refusal while a row is missing a vector, a clean prune that leaves the kept model's vectors
intact and drops the others, and the one event each outcome does or does not record.
