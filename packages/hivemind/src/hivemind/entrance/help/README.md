# hivemind.entrance.help

The Help screen's Ask (ADR-0069): a person types a question about the Hive on the Observation
Hive's Help screen and `GET /v1/help/ask?q=` answers it from the help pages the front end bundles,
and from nothing else. The assistant has no tools, takes no action, keeps nothing between
questions, and reads the question and the pages as data.

## One door

```
GET /v1/help/ask?q=…        any approved device's session (observe is enough)
   │ gate: signed, APPROVED, the device's request rate
   │ HelpAskQuery: 3 to 500 characters, nothing else in the query
   └──► ask(services, session key, question)
          1. the session's question budget (QuestionBudget, 6 a minute): over it, 429
          2. retrieve the top passages (TOP_PASSAGES): none, NOTHING_FOUND and no model runs
          3. no answerer bound (no chat binding): NO_MODEL, the passages as links
          4. one call on the cheapest chat binding, through the Fanner's lane (one llm.call):
             its words with the passages it cites by [n], ANSWERED; a failure or a timeout,
             MODEL_FAILED, the passages still standing
           │
   200 HelpAnswerView: question, outcome, answer | null, sources (page, section, heading,
                      snippet, cited), slot
```

The row is mounted only while the Entrance was built with help pages (`Switch.HELP`), the way
voice needs a transcriber: `hive serve` reads `help/<page>.md` from the Observation Hive's build
(`packages/observation-web/dist`, where the front end's Vite plugin copies its
`src/views/help/content/`), so a Hive served without a build has no Ask and no Help screen.

## Retrieval

`retrieval/` cuts each page into the sections the Help screen itself addresses (`corpus`: a `##`
heading and the blocks under it, `###` headings kept inside; `slug_of` is the front end's
`slugOf`, so a source's `section` is the id `#/help/<page>?s=<section>` lands on), ranks them
with a small BM25 in memory (`rank`, a heading's words weighted, a heading the question names
doubled), and defines the `HelpRetriever` seam (`retriever`): `KeywordRetriever` always works;
`EmbeddingRetriever` uses the Hive's `EMBEDDER` slot through the Fanner's lane, embeds the corpus
once on the first question (never at start, so a hosted embedder is not paid for on every serve),
ranks by cosine, and falls back to keywords on any `LLMError`.

## The model

`cheapest_chat_binding` (`hivemind.llm.routing`) picks the `[llm.slots]` row a chat slot can reach
that the Forage map prices lowest (token price, then grade, then manifest order; an unpriced row
last, ranked by the grade floor of the slot it serves, so a manifest with no map never picks the
Queen's row; never the embedder or transcriber rows), and the composition root binds it through the
Hive's own registry. `answer` renders the shipped `help_answer` prompt with the passages as a
numbered RETRIEVED section and the question as a USER section, both with their delimiters
neutralised, and makes one call with `tools=()`, no schema, low effort, a 400-token budget and a
30 s deadline. The Fanner lane prices and records the call, so the Cost view counts it under the
binding's slot.

## How to test this

`tests/unit/entrance/help/` mirrors this package: the corpus, the ranker and both retrievers
(over the real pages for the keyword one), the answerer (no tools, labelled data, None on
failure), the door's four outcomes and the budget, and the route over real listeners with the
committed document. `tests/unit/cli/compose/test_help.py` proves `hive serve` wires it from a
build's pages, and `tests/unit/llm/routing/test_cheapest.py` the choice of binding.
