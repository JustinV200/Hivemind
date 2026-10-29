# ADR-0069: The help's Ask answers from the help pages alone, on the cheapest chat binding

- Status: Accepted
- Date: 2026-10-29

## Context

The Observation Hive's Help screen (design.md section 5.12) carries the words a newcomer needs:
every bee term, how the Hive works, how to read each screen. A search box finds a section by its
words, but a person who does not yet know the word cannot search for it. A small assistant that
answers a question in plain language, from those same pages, closes that gap. It must not become
a second chat: the chat (`POST /v1/chat`) is the Observation Hive's one write path (codingrules
8.11), talks to the Queen, and spends from a goal's budget; the help answers about the Hive, never
to it. Three forces shape the design. The answer must be grounded, since a helper that invents a
screen or a setting is worse than none. It must be cheap, since every question is a model call the
operator pays for and the pages are a few hundred short sections. And it must be safe, since the
question is untrusted text typed by anyone holding a session, and codingrules 15 treats text
reaching a prompt as data, never instructions.

## Decision

`GET /v1/help/ask?q=` (`hivemind.entrance.help`) answers one question from the help pages the
front end bundles, and from nothing else. The pages are the source of truth in one place,
`packages/observation-web/src/views/help/content/`: the front end's build copies them beside
itself as `help/<page>.md`, and `hive serve` reads them from the build it serves, so the Hive
answers from exactly the words the Help screen shows and a Hive served without a build has no Ask
(its route is mounted only under `Switch.HELP`). Retrieval cuts the pages into the sections the
Help screen itself addresses, so every source is a `#/help/<page>?s=<section>` link; it ranks
them with a small BM25 in memory, or with the Hive's own `EMBEDDER` slot through the Fanner's lane
when that binds (the corpus embedded once, on the first question, never at start), falling back
to BM25 on any model error. The answer runs on the chat binding the Forage map prices lowest
(`cheapest_chat_binding`: token price, then grade, then manifest order, over every row a chat
slot's chain reaches; never a model id in code), bound through the Hive's registry and called
through the Fanner's lane, so it is priced and recorded as an `llm.call` and the Cost view counts
it. The call carries no tools and no schema; the prompt shows the passages as numbered RETRIEVED
data and the question as USER data, delimiters neutralised, and asks the model to cite by number
or say "I don't know". A Hive with no chat binding, a model that fails, or a question no page
matches answers with the passages as links (the `outcome` says which, in a word). It is a read
(`RouteEffect.READ`) any approved session may make, like the Honey search that also costs a model
call, held to a few questions a minute per session on top of the device's rate.

## Consequences

The Help screen gains an Ask that a person can use without the vocabulary, and every answer
points at the section to read, so a wrong or thin answer is checkable in one click. The front
end's write guard needs no new pattern and the route-table test stays as it is, because the Ask
writes nothing. Cost is bounded by the budget, the cheapest binding, a 400-token reply and a few
passages per question; an operator who wants a better answer binds a better model to the
cheapest slot or prices the map, never edits code. The assistant can take no action however the
question is phrased, since there is nothing for it to call. What becomes harder: the pages exist
in the build and in the source, so the Ask lags a page edit until the front end is rebuilt, which
is already true of the Help screen itself; and the keyword ranker knows no synonyms, so a question
in words the pages do not use finds nothing and says so.

## Alternatives considered

- Answer from Honey, the Hive's own knowledge, as well: Honey is scoped by capability and
  clearance per device and may hold personal content, which a help answer must never leak; the
  help pages are the same for every session.
- Put the Ask through the chat and let the Queen answer: a question about the Hive would then
  enter her inbox, be scored by her Attendant and spend from a goal's budget, for a reader's
  question that needs none of her state.
- Let the browser retrieve and send the passages: the server would then answer from whatever a
  client sent, and the model's grounding would be the client's to decide.
- Ship the pages a second time as Python package data: two copies of the same words drift, and a
  test that proves they match is a weaker rule than one source.
- Hard-code a small hosted model for the answer: codingrules 8.6 forbids a model id in code, and
  the cheapest binding is the manifest's to choose.
