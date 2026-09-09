# ADR-0013: One Attendant shape for every supervisor: deterministic first, a model only for ties

- Status: Accepted
- Date: 2026-09-08

## Context

Every supervisor in the Hive -- the Queen and every Warden -- receives a mixed inbox: Waggle
messages from bees, Alarms climbing the chain, Questions waiting on an answer, timers, watch
observations, and, for the Queen alone, messages from the human. Something has to decide which
item gets handled first, on every tick, without itself becoming a bottleneck or a place where
behaviour quietly depends on which model happens to be reachable that moment. README's "Core
concepts" section 1 says the Queen's inbox is "ordered by the Attendant... [which] scores them by
importance. Human input carries heavy weight but not absolute priority; a bee's question can be
more urgent, and the Queen decides when something goes to the human," and codingrules section 8.8
extends the same requirement to a Warden: "`supervision/attendant.py` scores every inbox item...
with one deterministic function parametrised by the principal... A Warden's Attendant runs the
same scoring over a smaller, more uniform inbox and consults a model only if its grant allows it;
by default it is autopilot-only." Roadmap step 3.13 has to fix this scorer once, in a package
(`hivemind.supervision`) that both `hivemind.queen.autopilot` and `hivemind.wardens.autopilot`
import, and that package can never import `hivemind.llm` (ADR-0011) -- so whatever seam lets a
model help with a genuinely ambiguous case has to be an injected Protocol, not a call the Attendant
makes itself.

## Decision

Every supervisor's inbox triage runs through the same `Attendant`
(`hivemind.supervision.attendant.Attendant`), parametrised by that supervisor's own
`WeightTable` and `Clock`, never by a bare `if` chain per kind. Scoring is a single pure function,
`score_item(weights, item, now)`, that sums a small, named set of factors -- a base weight per
`InboxKind`, a weight per `AlarmSeverity` when the item carries one, age since the item was
received, a flat bonus for an item that names a task, and an urgency term inversely proportional to
a latency budget -- then applies one multiplicative weight for the item's `principal`, and records
which factors contributed as a list of reasons alongside the resulting score. `Attendant.order` is
a stable sort by that score, descending; the ordering is fully deterministic given the `WeightTable`
and the instant `now` was read, with no model consulted for the ordinary case. The one seam where a
model may help is an **exact** tie: `TieBreaker` (`hivemind.supervision.attendant.TieBreaker`) is a
`Protocol` `Attendant.order` awaits only for a group of two or more items whose scores compare
equal, and only that group, never the whole inbox; a group of one is never a tie and never invokes
it. `WeightTable.queen_default()` gives the Queen's inbox a real hierarchy -- a `HUMAN_MESSAGE`'s
base weight sits below an `ALARM`'s, and a `CRITICAL` `AlarmSeverity` pushes an Alarm's score well
past any human message's, matching README's "heavy weight but not absolute priority."
`WeightTable.warden_default()` is nearly flat on purpose, because a Warden's inbox is smaller
and more homogeneous and has no human traffic to justify a fixed hierarchy; it keeps exactly one
rule, that a sub-bee's Alarm or Question outranks routine traffic and a `CRITICAL` Alarm outranks
everything, so a supervisor never queues an old heartbeat ahead of a fresh failure. The Queen is the only supervisor that may enable a model-backed `TieBreaker` on
`ModelSlot.ATTENDANT` without restriction; a Warden may enable one only within its own Forage
grant, and by default runs autopilot-only (no `TieBreaker` at all) -- both rules are enforced by
whoever constructs each `Attendant` (`queen/inbox/`, `wardens/inbox/`, later roadmap steps), not by
this package, which only defines the seam.

## Consequences

Positive: because `score_item` is pure and takes `now` explicitly, the entire ordering behaviour --
including every invariant README and codingrules state ("a CRITICAL Alarm outranks a human
message," "a human message outranks a heartbeat," "older wins among equals") -- is testable with
plain data and a `FakeClock`, no model and no real supervisor required. Recording which factors
contributed to a score (`Priority.reasons`) means the Observation Hive's Attendant view (a later
roadmap step) can show *why* one item outranked another, not just the bare number, which matters
for an operator's trust in an otherwise invisible ordering decision. Confining the model-backed
seam to exact ties keeps the Attendant's behaviour reachable and explainable even when no provider
is reachable at all: the deterministic fallback (received_at, then id) always produces a total
order, so `order` never blocks on a model it cannot reach. One `Attendant` shape shared by every
supervisor means a bug fix or a new scoring factor lands once, in a package neither
`hivemind.queen` nor `hivemind.wardens` needs to reimplement, and the Queen-versus-Warden
difference in policy is expressed entirely as data (two different `WeightTable`s and whether a
`TieBreaker` is wired in) rather than as two different code paths.

Negative: a purely additive-then-multiplicative formula is simpler to reason about and to test
than it is to guarantee "correct" in every conceivable inbox composition; a `WeightTable` tuned
for one Hive's traffic mix may need retuning for another's, and getting that tuning wrong fails
silently (the ordering is merely suboptimal, not obviously broken) unless a specific scenario is
covered by a test. Restricting the model-backed tie-break to exact score equality means a
near-tie that floating-point arithmetic happens to resolve as unequal by a tiny margin gets no
model input at all, even though a human glancing at the two items might consider them equally
urgent; the deterministic fallback is the only thing that decides such near-ties. Giving a Warden
the *option* of a model-backed tie-break, gated by its own grant, is one more piece of
configuration surface (whether the grant allows it, whether one is actually wired in) that has to
be reasoned about per Warden rather than a single Hive-wide answer.

## Alternatives considered

A model call on every inbox pass, ranking the whole inbox at once: closest to "the smartest
possible ordering," but reintroduces exactly the provider-dependency and latency ADR-0011 exists
to keep out of the tick loop, and makes the ordering non-deterministic and expensive to test.

No tie-break seam at all, always falling back to (received_at, id): simpler, and fully
deterministic with nothing left to configure, but discards README's own allowance ("an optional
model tie-break on `ModelSlot.ATTENDANT` that the Queen enables and a Warden may enable only within
its grant") for the genuinely rare case where two items are equally urgent by every factor the
formula tracks and a cheap model call could sensibly prefer one.

A separate scoring implementation for the Queen and for a Warden, rather than one `Attendant`
parametrised by a `WeightTable`: would let each be hand-tuned independently with no shared
abstraction to keep general, but duplicates the ordering, tie-break and reasons-recording logic in
two places that must then be kept in sync by hand every time either one's behaviour needs to
change, which is exactly the drift codingrules section 8.8's "the same protocol at every level"
principle exists to prevent.
