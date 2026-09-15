# ADR-0022: Hot state is packed by one relevance score, nothing dropped is lost, and Cell Wax is a capped hot-state item only the Queen writes

- Status: Accepted
- Date: 2026-09-15

## Context

Phase 3 assembled every awake episode's prompt from durable state (ADR-0019) but packed it by
recency alone and had nowhere to put what did not fit: an item that fell out of the budget was
simply absent until the next episode happened to reach it again. Phase 4 has to make the kernel
survive scale: ten thousand events through the Queen must leave the assembled prompt inside its
budget, every dropped item must still be findable, a bee whose context resets must be resumable
from its Handoff (a structured document a bee writes so a fresh bee can continue its work) alone,
and a provider's `ContextTooLong` must never crash a bee. Codingrules section 8.9 fixes the tier
shape (working, hot, Bee Bread, Honey) and section 8.9 also adds Cell Wax (Queen-written cautions
about one Cell) as a hot-state item with its own rule. This ADR records the decisions those
requirements force that would be expensive to reverse: how items are ordered, where dropped items
go, who may write wax, and what compaction is allowed to summarise from.

## Decision

**One pure score orders hot state.** `memory/relevance.py` exposes one function, `score`, from
recency decay (exponential, with a commented half-life), linkage to an active task, Alarm severity,
and pins that never decay; `memory/hot_state` packs highest score first until the budget fills,
drops lowest-scored first on overflow, then replaces any still-oversized item with a reference.
The function is pure and property-tested for monotonicity, so every consumer (the Queen, every
Warden, every Worker) agrees on what matters without a second heuristic anywhere.

**Bee Bread is a lookup index, not a search engine, and it is where dropped things land.**
`memory/bee_bread` indexes Brood Chamber history, trail events, stored Handoffs and deposited
transcripts by id, time and task; lookups filter by the reader's `HoneyClearance` allowance. Nothing
that leaves hot state is deleted: demotion (`memory/demote.py`, pure rules for task closed, Alarm
resolved, age past a manifest window) moves an item into Bee Bread, and an oversized tool result is
deposited there and referenced from the prompt. Full-text and semantic search belong to Honey (phase
7); giving the warm tier a search API now would have created a second retrieval path that phase 7
would then have to reconcile.

**Cell Wax is a hot-state item with its own relevance rule and its own cap, and only the Queen
writes it.** Anyone may propose wax (a bee or Warden over Waggle, the human from the chat); the
Queen writes, rejects or clears it: by autopilot for a Warden's `NOTE` or `CAUTION` about its own
Cell within the per-Cell cap, by awake decision for every `BLOCK`, every proposal about another
Cell and every clear. Wax scores into hot state only while its Cell is a candidate for placement or
assignment, so a caution about a flaky device costs no tokens until that device is in play. The
transition table (`PROPOSED → WRITTEN → CLEARED | EXPIRED`, `PROPOSED → REJECTED`) lives in
`memory/cell_wax` and every edge is a `memory.wax_*` event.

**Compaction summarises from source records, never from a summary, and copies pins verbatim.**
`memory/compact.py` runs on `ModelSlot.RIPENER`, produces one level of summary from the Bee Bread
entries it covers, and records `memory.compacted`. A House Bee sweep duty runs demotion, compaction
and wax expiry on a timer, so shrinking memory is routine maintenance rather than an emergency
response to an overflow.

**Overflow shrinks and retries; it never crashes.** `ContextTooLong` from any provider shrinks the
budget for that episode and retries, recording `memory.overflow`; three overflows in one episode
raise an Alarm rather than looping.

## Consequences

Positive: the budget holds by construction, and the flood test can assert it directly. Every
dropped item has exactly one place to be found again. Wax costs nothing until a Cell matters, and
the "only the Queen writes" rule means placement can trust `BLOCK` as an exclusion without asking
who wrote it. Summarising only from source records bounds the drift of a summary to one hop.

Negative: relevance weights are constants that will need tuning once real workloads exist; the ADR
fixes the shape (one pure function, monotone in each input), not the numbers. Bee Bread's
lookup-only surface means a bee that needs "everything about X" must wait for Honey in phase 7.
The wax cap per Cell is a manifest number, so a Cell with more genuine cautions than the cap loses
the lowest-severity ones from hot state (they stay in the table and ripen into Honey later).

## Alternatives considered

Accumulating a conversation per bee and truncating from the front: rejected by ADR-0019 already;
it makes the prompt depend on history order rather than on what matters now.

Recency-only packing with a larger budget: cheap, but a pinned fact or an open Alarm older than
the window would fall out exactly when it matters most, and no budget is large enough for ten
thousand events.

Letting Wardens write wax about their own Cells directly: faster, but then placement would be
reading notes from the same principal whose sub-bees it is about to place, and a Warden could
`BLOCK` its own Cell to shed work; routing every write through the Queen keeps one author and
one audit line.

Summarising a previous summary when the budget is tight: unbounded drift after a few rounds; one
level from source records is the only form whose error is bounded.
